use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;

use crate::{
    Error, LockedSecret, Result,
    crypto::{
        ARGON_ITERATIONS, ARGON_LANES, ARGON_MAX_ITERATIONS, ARGON_MAX_LANES, ARGON_MAX_MEMORY_KIB,
        ARGON_MEMORY_KIB, KEY_BYTES, NONCE_BYTES, RECOVERY_SALT_BYTES, TAG_BYTES, decrypt,
        derive_recovery_kek, encrypt, random_array,
    },
    rotation::verify_library_for_key,
    vault::{
        ROTATION_NEW_ACCOUNT, ROTATION_OLD_ACCOUNT, VAULT_ACCOUNT, acquire_vault_operation_lock,
        load_vault_account, set_vault_account,
    },
};

const RECOVERY_MAGIC: [u8; 8] = *b"LRAILRK\0";
const RECOVERY_HEADER_BYTES: usize = 32;
const RECOVERY_SCHEMA_VERSION: u16 = 1;
const MAX_RECOVERY_DOCUMENT_BYTES: u32 = 64 * 1024;
const RECOVERY_EXTENSION: &str = "lrail-recovery";
const RECOVERY_KDF: &str = "argon2id-v1.3";
const RECOVERY_DOMAIN: &[u8] = b"LyricRail/v1/library-recovery-bundle";

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecoveryBundleInspection {
    pub schema_version: u16,
    pub created_at_unix_ms: u64,
    pub key_fingerprint: String,
    pub kdf: String,
    pub memory_kib: u32,
    pub iterations: u32,
    pub lanes: u32,
    pub bundle_bytes: u64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecoveryBundleExport {
    pub output: PathBuf,
    pub inspection: RecoveryBundleInspection,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecoveryBundleVerification {
    pub valid: bool,
    pub key_fingerprint: String,
    pub created_at_unix_ms: u64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecoveryRestoreReport {
    pub library_root: PathBuf,
    pub package_count: usize,
    pub key_fingerprint: String,
    pub already_active: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RecoveryDocument {
    schema_version: u16,
    created_at_unix_ms: u64,
    service: String,
    account: String,
    kdf: String,
    salt: Vec<u8>,
    memory_kib: u32,
    iterations: u32,
    lanes: u32,
    nonce: Vec<u8>,
    key_fingerprint: String,
    ciphertext: Vec<u8>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RecoveryAad<'a> {
    schema_version: u16,
    created_at_unix_ms: u64,
    service: &'a str,
    account: &'a str,
    kdf: &'a str,
    salt: &'a [u8],
    memory_kib: u32,
    iterations: u32,
    lanes: u32,
    nonce: &'a [u8],
    key_fingerprint: &'a str,
}

fn recovery_error(message: impl Into<String>) -> Error {
    Error::Recovery(message.into())
}

fn cbor_encode<T: Serialize>(value: &T) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    ciborium::into_writer(value, &mut bytes)
        .map_err(|error| Error::CborEncode(error.to_string()))?;
    Ok(bytes)
}

fn cbor_decode_canonical<T>(bytes: &[u8]) -> Result<T>
where
    T: serde::de::DeserializeOwned + Serialize,
{
    let value: T =
        ciborium::from_reader(bytes).map_err(|error| Error::CborDecode(error.to_string()))?;
    if cbor_encode(&value)? != bytes {
        return Err(recovery_error(
            "recovery bundle CBOR is not in canonical LyricRail encoding",
        ));
    }
    Ok(value)
}

fn fingerprint(key: &[u8; KEY_BYTES]) -> String {
    hex::encode(Sha256::digest(key))
}

fn aad(document: &RecoveryDocument) -> Result<Vec<u8>> {
    let metadata = RecoveryAad {
        schema_version: document.schema_version,
        created_at_unix_ms: document.created_at_unix_ms,
        service: &document.service,
        account: &document.account,
        kdf: &document.kdf,
        salt: &document.salt,
        memory_kib: document.memory_kib,
        iterations: document.iterations,
        lanes: document.lanes,
        nonce: &document.nonce,
        key_fingerprint: &document.key_fingerprint,
    };
    let mut output = RECOVERY_DOMAIN.to_vec();
    output.extend_from_slice(&cbor_encode(&metadata)?);
    Ok(output)
}

fn validate_document(document: &RecoveryDocument) -> Result<()> {
    let fingerprint_bytes = hex::decode(&document.key_fingerprint)
        .map_err(|_| recovery_error("recovery bundle key fingerprint is not valid hex"))?;
    if document.schema_version != RECOVERY_SCHEMA_VERSION
        || document.service != crate::vault::VAULT_SERVICE
        || document.account != VAULT_ACCOUNT
        || document.kdf != RECOVERY_KDF
        || document.salt.len() != RECOVERY_SALT_BYTES
        || document.memory_kib < ARGON_MEMORY_KIB
        || document.memory_kib > ARGON_MAX_MEMORY_KIB
        || document.iterations < ARGON_ITERATIONS
        || document.iterations > ARGON_MAX_ITERATIONS
        || document.lanes == 0
        || document.lanes > ARGON_MAX_LANES
        || document.nonce.len() != NONCE_BYTES
        || fingerprint_bytes.len() != 32
        || document.ciphertext.len() != KEY_BYTES + TAG_BYTES
    {
        return Err(recovery_error(
            "recovery bundle fields are outside version 1 bounds",
        ));
    }
    Ok(())
}

fn encode_header(document_length: u32) -> [u8; RECOVERY_HEADER_BYTES] {
    let mut header = [0_u8; RECOVERY_HEADER_BYTES];
    header[..8].copy_from_slice(&RECOVERY_MAGIC);
    header[8..10].copy_from_slice(&RECOVERY_SCHEMA_VERSION.to_le_bytes());
    header[12..16].copy_from_slice(&document_length.to_le_bytes());
    header
}

fn read_document(path: &Path) -> Result<(RecoveryDocument, u64)> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() < RECOVERY_HEADER_BYTES as u64
        || metadata.len() > RECOVERY_HEADER_BYTES as u64 + MAX_RECOVERY_DOCUMENT_BYTES as u64
    {
        return Err(recovery_error("recovery bundle file has invalid bounds"));
    }
    let mut file = File::open(path)?;
    let mut header = [0_u8; RECOVERY_HEADER_BYTES];
    file.read_exact(&mut header)?;
    if header[..8] != RECOVERY_MAGIC
        || u16::from_le_bytes(header[8..10].try_into().expect("fixed header slice"))
            != RECOVERY_SCHEMA_VERSION
        || header[10..12].iter().any(|byte| *byte != 0)
        || header[16..].iter().any(|byte| *byte != 0)
    {
        return Err(recovery_error(
            "recovery bundle magic, version, or reserved bytes are invalid",
        ));
    }
    let document_length =
        u32::from_le_bytes(header[12..16].try_into().expect("fixed header slice"));
    if document_length == 0
        || document_length > MAX_RECOVERY_DOCUMENT_BYTES
        || RECOVERY_HEADER_BYTES as u64 + document_length as u64 != metadata.len()
    {
        return Err(recovery_error(
            "recovery bundle document length does not match the file",
        ));
    }
    let mut document_bytes = vec![0_u8; document_length as usize];
    file.read_exact(&mut document_bytes)?;
    let document: RecoveryDocument = cbor_decode_canonical(&document_bytes)?;
    validate_document(&document)?;
    Ok((document, metadata.len()))
}

fn decrypt_master(
    document: &RecoveryDocument,
    passphrase: &[u8],
) -> Result<LockedSecret<KEY_BYTES>> {
    if passphrase.len() < 12 {
        return Err(recovery_error(
            "recovery passphrase must contain at least 12 bytes",
        ));
    }
    let nonce: [u8; NONCE_BYTES] = document
        .nonce
        .as_slice()
        .try_into()
        .map_err(|_| recovery_error("recovery nonce has an invalid length"))?;
    let kek = derive_recovery_kek(
        passphrase,
        &document.salt,
        document.memory_kib,
        document.iterations,
        document.lanes,
    )?;
    let plaintext = decrypt(&kek, &nonce, &aad(document)?, &document.ciphertext)
        .map_err(|_| Error::KeyUnwrap)?;
    let master = LockedSecret::<KEY_BYTES>::from_slice(&plaintext)?;
    if fingerprint(&master) != document.key_fingerprint {
        return Err(Error::KeyUnwrap);
    }
    Ok(master)
}

fn create_bundle_for_master(
    master: &[u8; KEY_BYTES],
    output: &Path,
    passphrase: &[u8],
) -> Result<RecoveryBundleExport> {
    if output.extension().and_then(|value| value.to_str()) != Some(RECOVERY_EXTENSION) {
        return Err(recovery_error(format!(
            "recovery bundle output must use .{RECOVERY_EXTENSION}"
        )));
    }
    if output.try_exists()? {
        return Err(recovery_error(format!(
            "refusing to overwrite existing recovery bundle: {}",
            output.display()
        )));
    }
    if passphrase.len() < 12 {
        return Err(recovery_error(
            "recovery passphrase must contain at least 12 bytes",
        ));
    }
    let parent = output
        .parent()
        .ok_or_else(|| recovery_error("recovery bundle output has no parent directory"))?;
    fs::create_dir_all(parent)?;
    let salt = random_array::<RECOVERY_SALT_BYTES>();
    let nonce = random_array::<NONCE_BYTES>();
    let kek = derive_recovery_kek(
        passphrase,
        &salt,
        ARGON_MEMORY_KIB,
        ARGON_ITERATIONS,
        ARGON_LANES,
    )?;
    let mut document = RecoveryDocument {
        schema_version: RECOVERY_SCHEMA_VERSION,
        created_at_unix_ms: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| recovery_error("system clock precedes Unix epoch"))?
            .as_millis()
            .try_into()
            .map_err(|_| recovery_error("recovery timestamp exceeds u64"))?,
        service: crate::vault::VAULT_SERVICE.to_owned(),
        account: VAULT_ACCOUNT.to_owned(),
        kdf: RECOVERY_KDF.to_owned(),
        salt: salt.to_vec(),
        memory_kib: ARGON_MEMORY_KIB,
        iterations: ARGON_ITERATIONS,
        lanes: ARGON_LANES,
        nonce: nonce.to_vec(),
        key_fingerprint: fingerprint(master),
        ciphertext: Vec::new(),
    };
    document.ciphertext = encrypt(&kek, &nonce, &aad(&document)?, master)?;
    validate_document(&document)?;
    let document_bytes = cbor_encode(&document)?;
    let document_length = u32::try_from(document_bytes.len())
        .map_err(|_| recovery_error("recovery document exceeds u32"))?;
    if document_length > MAX_RECOVERY_DOCUMENT_BYTES {
        return Err(recovery_error("recovery document exceeds the v1 limit"));
    }
    let mut temporary = NamedTempFile::new_in(parent)?;
    temporary.write_all(&encode_header(document_length))?;
    temporary.write_all(&document_bytes)?;
    temporary.as_file_mut().sync_all()?;
    temporary
        .persist_noclobber(output)
        .map_err(|error| Error::Io(error.error))?;
    let inspection = inspect_recovery_bundle(output)?;
    Ok(RecoveryBundleExport {
        output: output.to_path_buf(),
        inspection,
    })
}

pub fn inspect_recovery_bundle(path: &Path) -> Result<RecoveryBundleInspection> {
    let (document, bundle_bytes) = read_document(path)?;
    Ok(RecoveryBundleInspection {
        schema_version: document.schema_version,
        created_at_unix_ms: document.created_at_unix_ms,
        key_fingerprint: document.key_fingerprint,
        kdf: document.kdf,
        memory_kib: document.memory_kib,
        iterations: document.iterations,
        lanes: document.lanes,
        bundle_bytes,
    })
}

pub fn verify_recovery_bundle(
    path: &Path,
    passphrase: &[u8],
) -> Result<RecoveryBundleVerification> {
    let (document, _) = read_document(path)?;
    let master = decrypt_master(&document, passphrase)?;
    Ok(RecoveryBundleVerification {
        valid: true,
        key_fingerprint: fingerprint(&master),
        created_at_unix_ms: document.created_at_unix_ms,
    })
}

pub fn export_recovery_bundle(output: &Path, passphrase: &[u8]) -> Result<RecoveryBundleExport> {
    let _guard = acquire_vault_operation_lock()?;
    if load_vault_account(ROTATION_OLD_ACCOUNT)?.is_some()
        || load_vault_account(ROTATION_NEW_ACCOUNT)?.is_some()
    {
        return Err(recovery_error(
            "cannot export recovery material while master rotation is active",
        ));
    }
    let master = load_vault_account(VAULT_ACCOUNT)?
        .ok_or_else(|| recovery_error("library master key is not initialized"))?;
    create_bundle_for_master(&master, output, passphrase)
}

trait RecoveryStore {
    fn load(&self, account: &str) -> Result<Option<LockedSecret<KEY_BYTES>>>;
    fn set(&self, account: &str, key: &[u8; KEY_BYTES]) -> Result<()>;
}

struct OsRecoveryStore;

impl RecoveryStore for OsRecoveryStore {
    fn load(&self, account: &str) -> Result<Option<LockedSecret<KEY_BYTES>>> {
        load_vault_account(account)
    }

    fn set(&self, account: &str, key: &[u8; KEY_BYTES]) -> Result<()> {
        set_vault_account(account, key)
    }
}

fn restore_with_store(
    bundle: &Path,
    passphrase: &[u8],
    library_root: &Path,
    store: &dyn RecoveryStore,
) -> Result<RecoveryRestoreReport> {
    if store.load(ROTATION_OLD_ACCOUNT)?.is_some() || store.load(ROTATION_NEW_ACCOUNT)?.is_some() {
        return Err(recovery_error(
            "cannot restore recovery material while master rotation is active",
        ));
    }
    let (document, _) = read_document(bundle)?;
    let candidate = decrypt_master(&document, passphrase)?;
    let (library_root, package_count) = verify_library_for_key(library_root, &candidate)?;
    if package_count == 0 {
        return Err(recovery_error(
            "restore requires at least one package that verifies with the recovered key",
        ));
    }
    let current = store.load(VAULT_ACCOUNT)?;
    let already_active = match current {
        Some(current) if fingerprint(&current) == document.key_fingerprint => true,
        Some(_) => {
            return Err(recovery_error(
                "a different current library master exists; refusing to overwrite it",
            ));
        }
        None => {
            store.set(VAULT_ACCOUNT, &candidate)?;
            false
        }
    };
    let stored = store
        .load(VAULT_ACCOUNT)?
        .ok_or_else(|| recovery_error("restored library master could not be read back"))?;
    if fingerprint(&stored) != document.key_fingerprint {
        return Err(recovery_error(
            "restored library master does not match the verified bundle",
        ));
    }
    Ok(RecoveryRestoreReport {
        library_root,
        package_count,
        key_fingerprint: document.key_fingerprint,
        already_active,
    })
}

pub fn restore_recovery_bundle(
    bundle: &Path,
    passphrase: &[u8],
    library_root: &Path,
) -> Result<RecoveryRestoreReport> {
    let _guard = acquire_vault_operation_lock()?;
    restore_with_store(bundle, passphrase, library_root, &OsRecoveryStore)
}

#[cfg(test)]
mod tests {
    use std::{cell::RefCell, collections::HashMap};

    use serde_json::json;

    use super::*;
    use crate::{
        AssetRequest, ContentEncoding, PackageRequest, pack_for_vault, verify_package_with_vault,
    };

    #[derive(Default)]
    struct TestStore {
        accounts: RefCell<HashMap<String, [u8; KEY_BYTES]>>,
    }

    impl TestStore {
        fn bytes(&self, account: &str) -> Option<[u8; KEY_BYTES]> {
            self.accounts.borrow().get(account).copied()
        }
    }

    impl RecoveryStore for TestStore {
        fn load(&self, account: &str) -> Result<Option<LockedSecret<KEY_BYTES>>> {
            self.accounts
                .borrow()
                .get(account)
                .map(|key| LockedSecret::from_slice(key))
                .transpose()
        }

        fn set(&self, account: &str, key: &[u8; KEY_BYTES]) -> Result<()> {
            self.accounts.borrow_mut().insert(account.to_owned(), *key);
            Ok(())
        }
    }

    fn add_package(
        directory: &Path,
        library: &Path,
        name: &str,
        master: &[u8; KEY_BYTES],
    ) -> PathBuf {
        let media = directory.join(format!("{name}.bin"));
        fs::write(&media, vec![0x6d; 65_777]).unwrap();
        let package = library.join(format!("{name}.lrail"));
        let request = PackageRequest {
            metadata: json!({"recovery": true}),
            producer: "LyricRail recovery tests".into(),
            minimum_player_version: "0.8.0".into(),
            assets: vec![AssetRequest {
                logical_name: "media/main.bin".into(),
                path: media,
                media_type: "application/octet-stream".into(),
                kind: "media".into(),
                track_name: None,
                language: None,
                default: true,
                content_encoding: ContentEncoding::Identity,
            }],
        };
        pack_for_vault(&request, &package, master, None).unwrap();
        package
    }

    fn library_fixture(directory: &Path, master: &[u8; KEY_BYTES]) -> (PathBuf, PathBuf) {
        let library = directory.join("library");
        fs::create_dir_all(&library).unwrap();
        let package = add_package(directory, &library, "fixture", master);
        (library, package)
    }

    fn write_document(path: &Path, document: &RecoveryDocument) {
        let bytes = cbor_encode(document).unwrap();
        let mut file = encode_header(bytes.len() as u32).to_vec();
        file.extend_from_slice(&bytes);
        fs::write(path, file).unwrap();
    }

    #[test]
    fn recovery_bundle_roundtrip_wrong_passphrase_corruption_and_safe_restore() {
        let directory = tempfile::tempdir().unwrap();
        let master = [0x71; KEY_BYTES];
        let different = [0x18; KEY_BYTES];
        let passphrase = b"a long unique recovery passphrase";
        let bundle = directory.path().join("library.lrail-recovery");
        let (library, package) = library_fixture(directory.path(), &master);

        let exported = create_bundle_for_master(&master, &bundle, passphrase).unwrap();
        assert_eq!(exported.output, bundle);
        assert!(exported.inspection.bundle_bytes < 4096);
        assert_eq!(exported.inspection.key_fingerprint, fingerprint(&master));
        assert!(verify_recovery_bundle(&bundle, passphrase).unwrap().valid);
        assert!(matches!(
            verify_recovery_bundle(&bundle, b"this is the wrong passphrase"),
            Err(Error::KeyUnwrap)
        ));
        assert!(create_bundle_for_master(&master, &bundle, passphrase).is_err());

        let corrupted = directory.path().join("corrupted.lrail-recovery");
        let mut corrupted_bytes = fs::read(&bundle).unwrap();
        *corrupted_bytes.last_mut().unwrap() ^= 0x80;
        fs::write(&corrupted, corrupted_bytes).unwrap();
        assert!(verify_recovery_bundle(&corrupted, passphrase).is_err());

        let store = TestStore::default();
        let restored = restore_with_store(&bundle, passphrase, &library, &store).unwrap();
        assert_eq!(restored.package_count, 1);
        assert!(!restored.already_active);
        assert_eq!(store.bytes(VAULT_ACCOUNT), Some(master));
        assert!(verify_package_with_vault(&package, &master).is_ok());
        assert!(
            restore_with_store(&bundle, passphrase, &library, &store)
                .unwrap()
                .already_active
        );

        let conflicting = TestStore::default();
        conflicting.set(VAULT_ACCOUNT, &different).unwrap();
        assert!(restore_with_store(&bundle, passphrase, &library, &conflicting).is_err());
        assert_eq!(conflicting.bytes(VAULT_ACCOUNT), Some(different));
    }

    #[test]
    fn restore_requires_verified_packages_and_refuses_active_rotation() {
        let directory = tempfile::tempdir().unwrap();
        let master = [0x44; KEY_BYTES];
        let passphrase = b"another long unique recovery passphrase";
        let bundle = directory.path().join("library.lrail-recovery");
        create_bundle_for_master(&master, &bundle, passphrase).unwrap();

        let empty_library = directory.path().join("empty");
        fs::create_dir(&empty_library).unwrap();
        let store = TestStore::default();
        assert!(restore_with_store(&bundle, passphrase, &empty_library, &store).is_err());
        assert!(store.bytes(VAULT_ACCOUNT).is_none());

        let (library, _) = library_fixture(directory.path(), &master);
        store.set(ROTATION_OLD_ACCOUNT, &master).unwrap();
        assert!(restore_with_store(&bundle, passphrase, &library, &store).is_err());
        assert!(store.bytes(VAULT_ACCOUNT).is_none());
    }

    #[test]
    fn parser_bounds_aad_randomization_and_whole_library_verification_are_enforced() {
        let directory = tempfile::tempdir().unwrap();
        let master = [0x52; KEY_BYTES];
        let other_master = [0xa7; KEY_BYTES];
        let passphrase = b"a third long unique recovery passphrase";
        let bundle = directory.path().join("first.lrail-recovery");
        let second = directory.path().join("second.lrail-recovery");
        create_bundle_for_master(&master, &bundle, passphrase).unwrap();
        create_bundle_for_master(&master, &second, passphrase).unwrap();
        assert_ne!(fs::read(&bundle).unwrap(), fs::read(&second).unwrap());

        let original = fs::read(&bundle).unwrap();
        let appended = directory.path().join("appended.lrail-recovery");
        let mut bytes = original.clone();
        bytes.push(0);
        fs::write(&appended, bytes).unwrap();
        assert!(inspect_recovery_bundle(&appended).is_err());

        let truncated = directory.path().join("truncated.lrail-recovery");
        fs::write(&truncated, &original[..original.len() - 1]).unwrap();
        assert!(inspect_recovery_bundle(&truncated).is_err());

        let reserved = directory.path().join("reserved.lrail-recovery");
        let mut bytes = original;
        bytes[31] = 1;
        fs::write(&reserved, bytes).unwrap();
        assert!(inspect_recovery_bundle(&reserved).is_err());

        let (document, _) = read_document(&bundle).unwrap();
        let mut salt_swap = document.clone();
        salt_swap.salt[0] ^= 1;
        let salt_path = directory.path().join("salt-swap.lrail-recovery");
        write_document(&salt_path, &salt_swap);
        assert!(verify_recovery_bundle(&salt_path, passphrase).is_err());

        let mut nonce_swap = document.clone();
        nonce_swap.nonce[0] ^= 1;
        let nonce_path = directory.path().join("nonce-swap.lrail-recovery");
        write_document(&nonce_path, &nonce_swap);
        assert!(verify_recovery_bundle(&nonce_path, passphrase).is_err());

        let mut fingerprint_swap = document;
        fingerprint_swap.key_fingerprint = "11".repeat(32);
        let fingerprint_path = directory.path().join("fingerprint-swap.lrail-recovery");
        write_document(&fingerprint_path, &fingerprint_swap);
        assert!(verify_recovery_bundle(&fingerprint_path, passphrase).is_err());

        let (library, _) = library_fixture(directory.path(), &master);
        add_package(directory.path(), &library, "foreign", &other_master);
        let store = TestStore::default();
        assert!(restore_with_store(&bundle, passphrase, &library, &store).is_err());
        assert!(store.bytes(VAULT_ACCOUNT).is_none());
    }
}
