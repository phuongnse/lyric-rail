use std::{
    collections::HashSet,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write, copy},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

use serde::Serialize;
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::{
    Error, LockedSecret, Result,
    crypto::{
        ARGON_ITERATIONS, ARGON_LANES, ARGON_MAX_ITERATIONS, ARGON_MAX_LANES, ARGON_MAX_MEMORY_KIB,
        ARGON_MEMORY_KIB, KEY_BYTES, NONCE_BYTES, RECOVERY_SALT_BYTES, TAG_BYTES, decrypt,
        derive_recovery_kek, derive_vault_kek, encrypt, random_array,
    },
    header::{FORMAT_MAJOR, FORMAT_MINOR, HEADER_SIZE, Header},
    schema::{
        Asset, Chunk, KeyEnvelope, KeySlot, Manifest, PackageInspection, PackageRequest,
        VerificationReport,
    },
};

const DEFAULT_CHUNK_SIZE: usize = 1024 * 1024;
const MAX_CHUNK_SIZE: usize = 8 * 1024 * 1024;
const MAX_ENVELOPE_SIZE: u64 = 1024 * 1024;
const MAX_MANIFEST_SIZE: u64 = 32 * 1024 * 1024;
const MAX_PACKAGE_SIZE: u64 = 256 * 1024 * 1024 * 1024;
const MAX_ASSETS: usize = 128;
const MAX_CHUNKS: usize = 1_000_000;
const MAX_IN_MEMORY_ASSET_BYTES: u64 = 512 * 1024 * 1024;
const MAX_METADATA_STRING_BYTES: usize = 1024 * 1024;
const MAX_METADATA_DEPTH: usize = 64;
const MAX_METADATA_NODES: usize = 100_000;
const MAX_SHORT_FIELD_BYTES: usize = 1024;

#[derive(Debug, Clone)]
pub struct PackagedAsset {
    pub logical_name: String,
    pub plaintext_length: u64,
    pub chunk_count: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RewrappedPackage {
    pub package_id: Uuid,
    pub asset_count: usize,
    pub chunk_count: usize,
    pub plaintext_bytes: u64,
    pub media_ciphertext_bytes: u64,
    pub old_package_bytes: u64,
    pub new_package_bytes: u64,
    pub overhead_delta_bytes: i64,
    pub vault_slots: usize,
    pub preserved_non_vault_slots: usize,
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
        return Err(Error::InvalidFormat(
            "CBOR document is not in the canonical LyricRail encoding".into(),
        ));
    }
    Ok(value)
}

fn fresh_nonce(used: &mut HashSet<[u8; NONCE_BYTES]>) -> [u8; NONCE_BYTES] {
    loop {
        let nonce = random_array::<NONCE_BYTES>();
        if used.insert(nonce) {
            return nonce;
        }
    }
}

fn checked_end(offset: u64, length: u64, context: &str) -> Result<u64> {
    offset
        .checked_add(length)
        .ok_or_else(|| Error::InvalidFormat(format!("{context} overflows u64")))
}

fn increment_chunk_count(total: &mut usize) -> Result<()> {
    *total = total
        .checked_add(1)
        .ok_or_else(|| Error::InvalidFormat("package chunk count overflows usize".into()))?;
    if *total > MAX_CHUNKS {
        return Err(Error::InvalidFormat(format!(
            "package exceeds {MAX_CHUNKS} chunks"
        )));
    }
    Ok(())
}

fn validate_logical_name(name: &str) -> Result<()> {
    if name.is_empty()
        || name.len() > 1024
        || name.starts_with('/')
        || name.starts_with('\\')
        || name.contains("..")
        || name.contains(':')
        || name.contains('\\')
        || name.split('/').any(|part| part.is_empty() || part == ".")
    {
        return Err(Error::InvalidAsset(format!(
            "logical name is not a safe package identifier: {name:?}"
        )));
    }
    Ok(())
}

fn validate_short_field(value: &str, field: &str, allow_empty: bool) -> Result<()> {
    if (!allow_empty && value.is_empty())
        || value.len() > MAX_SHORT_FIELD_BYTES
        || value.contains('\0')
    {
        return Err(Error::InvalidAsset(format!("invalid {field}")));
    }
    Ok(())
}

fn validate_metadata(value: &serde_json::Value) -> Result<()> {
    fn visit(value: &serde_json::Value, depth: usize, nodes: &mut usize) -> Result<()> {
        if depth > MAX_METADATA_DEPTH {
            return Err(Error::InvalidFormat(
                "metadata nesting exceeds the v1 limit".into(),
            ));
        }
        *nodes = nodes
            .checked_add(1)
            .ok_or_else(|| Error::InvalidFormat("metadata node count overflow".into()))?;
        if *nodes > MAX_METADATA_NODES {
            return Err(Error::InvalidFormat(
                "metadata node count exceeds the v1 limit".into(),
            ));
        }
        match value {
            serde_json::Value::String(text) => {
                if text.len() > MAX_METADATA_STRING_BYTES || text.contains('\0') {
                    return Err(Error::InvalidFormat(
                        "metadata string exceeds the v1 limit or contains NUL".into(),
                    ));
                }
            }
            serde_json::Value::Array(values) => {
                for item in values {
                    visit(item, depth + 1, nodes)?;
                }
            }
            serde_json::Value::Object(values) => {
                for (key, item) in values {
                    if key.len() > MAX_METADATA_STRING_BYTES || key.contains('\0') {
                        return Err(Error::InvalidFormat(
                            "metadata key exceeds the v1 limit or contains NUL".into(),
                        ));
                    }
                    visit(item, depth + 1, nodes)?;
                }
            }
            _ => {}
        }
        Ok(())
    }

    let mut nodes = 0;
    visit(value, 0, &mut nodes)
}

fn validate_request(request: &PackageRequest) -> Result<()> {
    if request.assets.is_empty() || request.assets.len() > MAX_ASSETS {
        return Err(Error::InvalidAsset(format!(
            "package must contain between 1 and {MAX_ASSETS} assets"
        )));
    }
    validate_short_field(&request.producer, "producer", false)?;
    validate_short_field(
        &request.minimum_player_version,
        "minimum player version",
        false,
    )?;
    validate_metadata(&request.metadata)?;
    let mut logical_names = HashSet::new();
    for asset in &request.assets {
        validate_logical_name(&asset.logical_name)?;
        validate_short_field(&asset.media_type, "asset media type", false)?;
        validate_short_field(&asset.kind, "asset kind", false)?;
        if asset
            .track_name
            .as_deref()
            .is_some_and(|value| validate_short_field(value, "track name", true).is_err())
            || asset
                .language
                .as_deref()
                .is_some_and(|value| validate_short_field(value, "language", true).is_err())
        {
            return Err(Error::InvalidAsset(
                "invalid asset track name or language".into(),
            ));
        }
        if !logical_names.insert(asset.logical_name.as_str()) {
            return Err(Error::InvalidAsset(format!(
                "duplicate logical asset name: {}",
                asset.logical_name
            )));
        }
    }
    Ok(())
}

fn chunk_aad(
    package_id: &[u8; 16],
    asset_id: &Uuid,
    index: u64,
    plaintext_offset: u64,
    plaintext_length: u32,
    content_encoding: &str,
) -> Vec<u8> {
    let mut aad = b"LyricRail/v1/chunk".to_vec();
    aad.extend_from_slice(package_id);
    aad.extend_from_slice(asset_id.as_bytes());
    aad.extend_from_slice(&index.to_le_bytes());
    aad.extend_from_slice(&plaintext_offset.to_le_bytes());
    aad.extend_from_slice(&plaintext_length.to_le_bytes());
    aad.extend_from_slice(content_encoding.as_bytes());
    aad
}

fn dek_aad(package_id: &[u8; 16]) -> Vec<u8> {
    let mut aad = b"LyricRail/v1/dek".to_vec();
    aad.extend_from_slice(package_id);
    aad
}

fn encoding_name(encoding: crate::schema::ContentEncoding) -> &'static str {
    match encoding {
        crate::schema::ContentEncoding::Identity => "identity",
    }
}

fn wrap_dek_for_vault(
    dek: &[u8; KEY_BYTES],
    package_id: &[u8; 16],
    vault_master_key: &[u8; KEY_BYTES],
) -> Result<KeySlot> {
    let key_id = random_array::<16>();
    let vault_kek = derive_vault_kek(vault_master_key, package_id, &key_id)?;
    let wrapping_nonce = random_array::<NONCE_BYTES>();
    Ok(KeySlot {
        mechanism: "os-vault-v1".to_owned(),
        key_id: Some(hex::encode(key_id)),
        salt: None,
        memory_kib: None,
        iterations: None,
        lanes: None,
        nonce: wrapping_nonce.to_vec(),
        wrapped_dek: encrypt(&vault_kek, &wrapping_nonce, &dek_aad(package_id), dek)?,
    })
}

pub fn pack(
    request: &PackageRequest,
    output: &Path,
    recovery_passphrase: &[u8],
) -> Result<Vec<PackagedAsset>> {
    pack_internal(request, output, None, Some(recovery_passphrase))
}

pub fn pack_for_vault(
    request: &PackageRequest,
    output: &Path,
    vault_master_key: &[u8; KEY_BYTES],
    recovery_passphrase: Option<&[u8]>,
) -> Result<Vec<PackagedAsset>> {
    pack_internal(request, output, Some(vault_master_key), recovery_passphrase)
}

fn pack_internal(
    request: &PackageRequest,
    output: &Path,
    vault_master_key: Option<&[u8; KEY_BYTES]>,
    recovery_passphrase: Option<&[u8]>,
) -> Result<Vec<PackagedAsset>> {
    validate_request(request)?;
    if vault_master_key.is_none() && recovery_passphrase.is_none() {
        return Err(Error::InvalidAsset(
            "package must have at least one key-protection slot".into(),
        ));
    }
    if recovery_passphrase.is_some_and(|passphrase| passphrase.len() < 12) {
        return Err(Error::InvalidAsset(
            "recovery passphrase must contain at least 12 bytes".into(),
        ));
    }
    if output.exists() {
        return Err(Error::InvalidAsset(format!(
            "refusing to overwrite existing output: {}",
            output.display()
        )));
    }
    let parent = output
        .parent()
        .ok_or_else(|| Error::InvalidAsset("output has no parent directory".into()))?;
    fs::create_dir_all(parent)?;

    let package_id = Uuid::new_v4();
    let package_id_bytes = *package_id.as_bytes();
    let dek = LockedSecret::<KEY_BYTES>::random()?;
    let mut slots = Vec::with_capacity(2);
    if let Some(master_key) = vault_master_key {
        slots.push(wrap_dek_for_vault(&dek, &package_id_bytes, master_key)?);
    }
    if let Some(passphrase) = recovery_passphrase {
        let salt = random_array::<RECOVERY_SALT_BYTES>();
        let wrapping_nonce = random_array::<NONCE_BYTES>();
        let recovery_kek = derive_recovery_kek(
            passphrase,
            &salt,
            ARGON_MEMORY_KIB,
            ARGON_ITERATIONS,
            ARGON_LANES,
        )?;
        slots.push(KeySlot {
            mechanism: "recovery-v1".to_owned(),
            key_id: None,
            salt: Some(salt.to_vec()),
            memory_kib: Some(ARGON_MEMORY_KIB),
            iterations: Some(ARGON_ITERATIONS),
            lanes: Some(ARGON_LANES),
            nonce: wrapping_nonce.to_vec(),
            wrapped_dek: encrypt(
                &recovery_kek,
                &wrapping_nonce,
                &dek_aad(&package_id_bytes),
                dek.as_ref(),
            )?,
        });
    }
    let envelope = KeyEnvelope {
        schema_version: 1,
        slots,
    };
    let envelope_bytes = cbor_encode(&envelope)?;
    if envelope_bytes.len() as u64 > MAX_ENVELOPE_SIZE {
        return Err(Error::InvalidFormat("key envelope exceeds v1 limit".into()));
    }

    let mut temporary = NamedTempFile::new_in(parent)?;
    temporary.write_all(&[0_u8; HEADER_SIZE])?;
    temporary.write_all(&envelope_bytes)?;
    let asset_region_start = HEADER_SIZE as u64 + envelope_bytes.len() as u64;
    let mut current_file_offset = asset_region_start;
    let mut assets = Vec::with_capacity(request.assets.len());
    let mut packaged = Vec::with_capacity(request.assets.len());
    let mut total_chunks = 0_usize;
    let mut dek_nonces = HashSet::new();

    for requested in &request.assets {
        validate_logical_name(&requested.logical_name)?;
        let metadata = fs::metadata(&requested.path).map_err(|error| {
            Error::InvalidAsset(format!("{}: {error}", requested.path.display()))
        })?;
        if !metadata.is_file() {
            return Err(Error::InvalidAsset(format!(
                "asset is not a regular file: {}",
                requested.path.display()
            )));
        }
        let asset_id = Uuid::new_v4();
        let mut source = File::open(&requested.path)?;
        let mut plaintext_offset = 0_u64;
        let mut chunks = Vec::new();
        let mut digest = Sha256::new();
        let mut buffer = vec![0_u8; DEFAULT_CHUNK_SIZE.min(MAX_CHUNK_SIZE)];
        loop {
            let count = source.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            digest.update(&buffer[..count]);
            let index = chunks.len() as u64;
            let plaintext_length = u32::try_from(count)
                .map_err(|_| Error::InvalidAsset("chunk length exceeds u32".into()))?;
            let nonce = fresh_nonce(&mut dek_nonces);
            let aad = chunk_aad(
                &package_id_bytes,
                &asset_id,
                index,
                plaintext_offset,
                plaintext_length,
                encoding_name(requested.content_encoding),
            );
            let ciphertext = encrypt(&dek, &nonce, &aad, &buffer[..count])?;
            let ciphertext_length = u32::try_from(ciphertext.len())
                .map_err(|_| Error::InvalidAsset("ciphertext length exceeds u32".into()))?;
            temporary.write_all(&ciphertext)?;
            chunks.push(Chunk {
                index,
                plaintext_offset,
                plaintext_length,
                file_offset: current_file_offset,
                ciphertext_length,
                nonce: nonce.to_vec(),
            });
            plaintext_offset = checked_end(plaintext_offset, count as u64, "asset length")?;
            current_file_offset = checked_end(
                current_file_offset,
                ciphertext.len() as u64,
                "package chunk offset",
            )?;
            increment_chunk_count(&mut total_chunks)?;
        }
        if plaintext_offset != metadata.len() {
            return Err(Error::InvalidAsset(format!(
                "asset changed while packaging: {}",
                requested.path.display()
            )));
        }
        assets.push(Asset {
            asset_id,
            logical_name: requested.logical_name.clone(),
            media_type: requested.media_type.clone(),
            kind: requested.kind.clone(),
            track_name: requested.track_name.clone(),
            language: requested.language.clone(),
            default: requested.default,
            content_encoding: requested.content_encoding,
            plaintext_length: plaintext_offset,
            sha256: digest.finalize().to_vec(),
            chunks,
        });
        packaged.push(PackagedAsset {
            logical_name: requested.logical_name.clone(),
            plaintext_length: plaintext_offset,
            chunk_count: assets.last().unwrap().chunks.len(),
        });
    }

    let manifest = Manifest {
        schema_version: 1,
        minimum_player_version: request.minimum_player_version.clone(),
        package_id,
        created_at_unix_ms: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| Error::InvalidFormat("system clock precedes Unix epoch".into()))?
            .as_millis()
            .try_into()
            .map_err(|_| Error::InvalidFormat("timestamp exceeds u64".into()))?,
        producer: request.producer.clone(),
        metadata: request.metadata.clone(),
        assets,
    };
    let manifest_plaintext = cbor_encode(&manifest)?;
    let manifest_length = checked_end(
        manifest_plaintext.len() as u64,
        TAG_BYTES as u64,
        "manifest ciphertext length",
    )?;
    if manifest_length > MAX_MANIFEST_SIZE {
        return Err(Error::InvalidFormat(
            "encrypted manifest exceeds v1 limit".into(),
        ));
    }
    let manifest_offset = current_file_offset;
    let package_length = checked_end(manifest_offset, manifest_length, "package length")?;
    if package_length > MAX_PACKAGE_SIZE {
        return Err(Error::InvalidFormat("package exceeds v1 size limit".into()));
    }
    let manifest_nonce = fresh_nonce(&mut dek_nonces);
    let header = Header {
        flags: 0,
        package_id: package_id_bytes,
        envelope_offset: HEADER_SIZE as u64,
        envelope_length: envelope_bytes.len() as u64,
        manifest_offset,
        manifest_length,
        manifest_nonce,
        package_length,
    };
    let header_bytes = header.encode();
    let mut manifest_aad = header_bytes.to_vec();
    manifest_aad.extend_from_slice(&envelope_bytes);
    let encrypted_manifest = encrypt(&dek, &manifest_nonce, &manifest_aad, &manifest_plaintext)?;
    temporary.write_all(&encrypted_manifest)?;
    temporary.as_file_mut().seek(SeekFrom::Start(0))?;
    temporary.write_all(&header_bytes)?;
    temporary.as_file_mut().sync_all()?;

    temporary
        .persist_noclobber(output)
        .map_err(|error| Error::Io(error.error))?;
    Ok(packaged)
}

/// Rewraps only the package DEK and authenticated manifest. Encrypted asset
/// bytes are streamed byte-for-byte into the replacement package.
pub fn rewrap_package_for_vaults(
    input: &Path,
    output: &Path,
    opening_keys: &[&[u8; KEY_BYTES]],
    target_keys: &[&[u8; KEY_BYTES]],
) -> Result<RewrappedPackage> {
    if opening_keys.is_empty() {
        return Err(Error::InvalidAsset(
            "at least one opening vault key is required".into(),
        ));
    }
    if target_keys.is_empty() {
        return Err(Error::InvalidAsset(
            "at least one target vault key is required".into(),
        ));
    }
    if target_keys.iter().enumerate().any(|(index, candidate)| {
        target_keys[..index]
            .iter()
            .any(|previous| previous.as_slice() == candidate.as_slice())
    }) {
        return Err(Error::InvalidAsset(
            "duplicate target vault keys would add redundant package overhead".into(),
        ));
    }
    if output.exists() {
        return Err(Error::InvalidAsset(format!(
            "refusing to overwrite existing output: {}",
            output.display()
        )));
    }

    // Authenticate every asset before creating a replacement. This prevents a
    // rotation from blessing pre-existing corruption with a fresh manifest.
    verify_package_with_vault_candidates(input, opening_keys)?;
    let mut reader = PackageReader::open_with_vault_candidates(input, opening_keys)?;
    let old_header = reader.header.clone();
    let old_package_bytes = old_header.package_length;
    let old_asset_region_start = checked_end(
        old_header.envelope_offset,
        old_header.envelope_length,
        "old asset region",
    )?;
    let media_ciphertext_bytes = old_header
        .manifest_offset
        .checked_sub(old_asset_region_start)
        .ok_or_else(|| Error::InvalidFormat("old asset region is inverted".into()))?;

    let (_prefix_file, _prefix_header, _header_bytes, _envelope_bytes, old_envelope) =
        read_header_and_envelope(input)?;
    let mut slots: Vec<KeySlot> = old_envelope
        .slots
        .into_iter()
        .filter(|slot| slot.mechanism != "os-vault-v1")
        .collect();
    let preserved_non_vault_slots = slots.len();
    if preserved_non_vault_slots + target_keys.len() > 16 {
        return Err(Error::InvalidFormat(
            "rewrapped key envelope would exceed the v1 slot limit".into(),
        ));
    }
    for target_key in target_keys {
        slots.push(wrap_dek_for_vault(
            &reader.dek,
            &old_header.package_id,
            target_key,
        )?);
    }
    let envelope = KeyEnvelope {
        schema_version: 1,
        slots,
    };
    let envelope_bytes = cbor_encode(&envelope)?;
    if envelope_bytes.len() as u64 > MAX_ENVELOPE_SIZE {
        return Err(Error::InvalidFormat(
            "rewrapped key envelope exceeds the v1 limit".into(),
        ));
    }

    let new_asset_region_start = checked_end(
        HEADER_SIZE as u64,
        envelope_bytes.len() as u64,
        "new asset region",
    )?;
    let mut manifest = reader.manifest.clone();
    let mut chunk_count = 0_usize;
    let mut plaintext_bytes = 0_u64;
    let mut used_nonces = HashSet::from([old_header.manifest_nonce]);
    for asset in &mut manifest.assets {
        plaintext_bytes = checked_end(
            plaintext_bytes,
            asset.plaintext_length,
            "rewrapped plaintext length",
        )?;
        for chunk in &mut asset.chunks {
            increment_chunk_count(&mut chunk_count)?;
            let relative_offset = chunk
                .file_offset
                .checked_sub(old_asset_region_start)
                .ok_or_else(|| Error::InvalidFormat("chunk precedes old asset region".into()))?;
            chunk.file_offset = checked_end(
                new_asset_region_start,
                relative_offset,
                "rewrapped chunk offset",
            )?;
            let nonce: [u8; NONCE_BYTES] =
                chunk.nonce.as_slice().try_into().map_err(|_| {
                    Error::InvalidFormat("chunk nonce has an invalid length".into())
                })?;
            used_nonces.insert(nonce);
        }
    }

    let manifest_plaintext = cbor_encode(&manifest)?;
    let manifest_length = checked_end(
        manifest_plaintext.len() as u64,
        TAG_BYTES as u64,
        "rewrapped manifest ciphertext length",
    )?;
    if manifest_length > MAX_MANIFEST_SIZE {
        return Err(Error::InvalidFormat(
            "rewrapped manifest exceeds the v1 limit".into(),
        ));
    }
    let manifest_offset = checked_end(
        new_asset_region_start,
        media_ciphertext_bytes,
        "rewrapped manifest offset",
    )?;
    let package_length = checked_end(manifest_offset, manifest_length, "rewrapped package length")?;
    if package_length > MAX_PACKAGE_SIZE {
        return Err(Error::InvalidFormat(
            "rewrapped package exceeds the v1 size limit".into(),
        ));
    }
    let manifest_nonce = fresh_nonce(&mut used_nonces);
    let header = Header {
        flags: old_header.flags,
        package_id: old_header.package_id,
        envelope_offset: HEADER_SIZE as u64,
        envelope_length: envelope_bytes.len() as u64,
        manifest_offset,
        manifest_length,
        manifest_nonce,
        package_length,
    };
    let header_bytes = header.encode();
    let mut manifest_aad = header_bytes.to_vec();
    manifest_aad.extend_from_slice(&envelope_bytes);
    let encrypted_manifest = encrypt(
        &reader.dek,
        &manifest_nonce,
        &manifest_aad,
        &manifest_plaintext,
    )?;

    let parent = output
        .parent()
        .ok_or_else(|| Error::InvalidAsset("output has no parent directory".into()))?;
    fs::create_dir_all(parent)?;
    let mut temporary = NamedTempFile::new_in(parent)?;
    temporary.write_all(&header_bytes)?;
    temporary.write_all(&envelope_bytes)?;
    reader.file.seek(SeekFrom::Start(old_asset_region_start))?;
    let copied = copy(
        &mut Read::by_ref(&mut reader.file).take(media_ciphertext_bytes),
        temporary.as_file_mut(),
    )?;
    if copied != media_ciphertext_bytes {
        return Err(Error::InvalidFormat(
            "source package ended inside its asset ciphertext region".into(),
        ));
    }
    temporary.write_all(&encrypted_manifest)?;
    temporary.as_file_mut().sync_all()?;

    // Authenticate the complete temporary package before it can become visible
    // at the requested destination.
    verify_package_with_vault_candidates(temporary.path(), target_keys)?;
    temporary
        .persist_noclobber(output)
        .map_err(|error| Error::Io(error.error))?;

    Ok(RewrappedPackage {
        package_id: manifest.package_id,
        asset_count: manifest.assets.len(),
        chunk_count,
        plaintext_bytes,
        media_ciphertext_bytes,
        old_package_bytes,
        new_package_bytes: package_length,
        overhead_delta_bytes: package_length as i64 - old_package_bytes as i64,
        vault_slots: target_keys.len(),
        preserved_non_vault_slots,
    })
}

type PackagePrefix = (File, Header, [u8; HEADER_SIZE], Vec<u8>, KeyEnvelope);

fn read_header_and_envelope(path: &Path) -> Result<PackagePrefix> {
    let mut file = OpenOptions::new().read(true).open(path)?;
    let actual_length = file.metadata()?.len();
    if actual_length < HEADER_SIZE as u64 || actual_length > MAX_PACKAGE_SIZE {
        return Err(Error::InvalidFormat(
            "package length is outside v1 bounds".into(),
        ));
    }
    let mut header_bytes = [0_u8; HEADER_SIZE];
    file.read_exact(&mut header_bytes)?;
    let header = Header::decode(&header_bytes)?;
    if header.package_length != actual_length {
        return Err(Error::InvalidFormat(format!(
            "declared package length {} does not match actual length {actual_length}",
            header.package_length
        )));
    }
    if header.envelope_offset != HEADER_SIZE as u64
        || header.envelope_length == 0
        || header.envelope_length > MAX_ENVELOPE_SIZE
    {
        return Err(Error::InvalidFormat("invalid key-envelope bounds".into()));
    }
    let envelope_end = checked_end(
        header.envelope_offset,
        header.envelope_length,
        "key envelope",
    )?;
    let manifest_end = checked_end(header.manifest_offset, header.manifest_length, "manifest")?;
    if envelope_end > header.manifest_offset
        || header.manifest_length < TAG_BYTES as u64
        || header.manifest_length > MAX_MANIFEST_SIZE
        || manifest_end != actual_length
    {
        return Err(Error::InvalidFormat(
            "invalid manifest or envelope layout".into(),
        ));
    }
    file.seek(SeekFrom::Start(header.envelope_offset))?;
    let mut envelope_bytes = vec![0_u8; header.envelope_length as usize];
    file.read_exact(&mut envelope_bytes)?;
    let envelope: KeyEnvelope = cbor_decode_canonical(&envelope_bytes)?;
    if envelope.schema_version != 1 || envelope.slots.is_empty() || envelope.slots.len() > 16 {
        return Err(Error::InvalidFormat(
            "unsupported key-envelope schema or slot count".into(),
        ));
    }
    for slot in &envelope.slots {
        if slot.mechanism.is_empty()
            || slot.mechanism.len() > 64
            || slot.nonce.len() != NONCE_BYTES
            || slot.wrapped_dek.len() != KEY_BYTES + TAG_BYTES
        {
            return Err(Error::InvalidFormat("invalid key-slot fields".into()));
        }
        if slot.mechanism == "recovery-v1"
            && (slot.key_id.is_some()
                || slot
                    .salt
                    .as_deref()
                    .is_none_or(|salt| salt.len() != RECOVERY_SALT_BYTES)
                || !matches!(
                    slot.memory_kib,
                    Some(ARGON_MEMORY_KIB..=ARGON_MAX_MEMORY_KIB)
                )
                || !matches!(
                    slot.iterations,
                    Some(ARGON_ITERATIONS..=ARGON_MAX_ITERATIONS)
                )
                || !matches!(slot.lanes, Some(ARGON_LANES..=ARGON_MAX_LANES)))
        {
            return Err(Error::InvalidFormat(
                "invalid recovery-v1 key-slot parameters".into(),
            ));
        }
        if slot.mechanism == "os-vault-v1"
            && (slot
                .key_id
                .as_deref()
                .and_then(|value| hex::decode(value).ok())
                .is_none_or(|value| value.len() != 16)
                || slot.salt.is_some()
                || slot.memory_kib.is_some()
                || slot.iterations.is_some()
                || slot.lanes.is_some())
        {
            return Err(Error::InvalidFormat(
                "invalid os-vault-v1 key-slot parameters".into(),
            ));
        }
    }
    Ok((file, header, header_bytes, envelope_bytes, envelope))
}

pub fn inspect_package(path: &Path) -> Result<PackageInspection> {
    let (_file, header, _header_bytes, _envelope_bytes, envelope) = read_header_and_envelope(path)?;
    Ok(PackageInspection {
        format_major: FORMAT_MAJOR,
        format_minor: FORMAT_MINOR,
        package_id: Uuid::from_bytes(header.package_id),
        package_length: header.package_length,
        key_mechanisms: envelope
            .slots
            .into_iter()
            .map(|slot| slot.mechanism)
            .collect(),
        encrypted_manifest_bytes: header.manifest_length,
    })
}

pub struct PackageReader {
    path: PathBuf,
    file: File,
    header: Header,
    dek: LockedSecret<KEY_BYTES>,
    pub manifest: Manifest,
}

fn unwrap_dek(
    slot: &KeySlot,
    package_id: &[u8; 16],
    kek: &[u8; KEY_BYTES],
) -> Result<LockedSecret<KEY_BYTES>> {
    let nonce: [u8; NONCE_BYTES] = slot
        .nonce
        .as_slice()
        .try_into()
        .map_err(|_| Error::InvalidFormat("key-slot nonce has an invalid length".into()))?;
    let dek_plain = decrypt(kek, &nonce, &dek_aad(package_id), &slot.wrapped_dek)
        .map_err(|_| Error::KeyUnwrap)?;
    if dek_plain.len() != KEY_BYTES {
        return Err(Error::KeyUnwrap);
    }
    LockedSecret::from_slice(&dek_plain)
}

impl PackageReader {
    pub fn open(path: &Path, recovery_passphrase: &[u8]) -> Result<Self> {
        let (file, header, header_bytes, envelope_bytes, envelope) =
            read_header_and_envelope(path)?;
        let slot = envelope
            .slots
            .iter()
            .find(|slot| slot.mechanism == "recovery-v1")
            .ok_or_else(|| Error::Unsupported("package has no recovery-v1 key slot".into()))?;
        let salt = slot
            .salt
            .as_deref()
            .ok_or_else(|| Error::InvalidFormat("recovery key slot is missing its salt".into()))?;
        let kek = derive_recovery_kek(
            recovery_passphrase,
            salt,
            slot.memory_kib.ok_or_else(|| {
                Error::InvalidFormat("recovery key slot is missing memoryKiB".into())
            })?,
            slot.iterations.ok_or_else(|| {
                Error::InvalidFormat("recovery key slot is missing iterations".into())
            })?,
            slot.lanes
                .ok_or_else(|| Error::InvalidFormat("recovery key slot is missing lanes".into()))?,
        )?;
        let dek = unwrap_dek(slot, &header.package_id, &kek)?;
        Self::finish_open(path, file, header, header_bytes, envelope_bytes, dek)
    }

    pub fn open_with_vault(path: &Path, vault_master_key: &[u8; KEY_BYTES]) -> Result<Self> {
        Self::open_with_vault_candidates(path, &[vault_master_key])
    }

    pub fn open_with_vault_candidates(
        path: &Path,
        vault_master_keys: &[&[u8; KEY_BYTES]],
    ) -> Result<Self> {
        if vault_master_keys.is_empty() {
            return Err(Error::InvalidAsset(
                "at least one vault key candidate is required".into(),
            ));
        }
        let (file, header, header_bytes, envelope_bytes, envelope) =
            read_header_and_envelope(path)?;
        let vault_slots: Vec<&KeySlot> = envelope
            .slots
            .iter()
            .filter(|slot| slot.mechanism == "os-vault-v1")
            .collect();
        if vault_slots.is_empty() {
            return Err(Error::Unsupported(
                "package has no os-vault-v1 key slot".into(),
            ));
        }
        let mut dek = None;
        'keys: for vault_master_key in vault_master_keys {
            for slot in &vault_slots {
                let key_id_text = slot.key_id.as_deref().ok_or_else(|| {
                    Error::InvalidFormat("os-vault key slot is missing its key ID".into())
                })?;
                let key_id_bytes = hex::decode(key_id_text)
                    .map_err(|_| Error::InvalidFormat("os-vault key ID is not valid hex".into()))?;
                let key_id: [u8; 16] = key_id_bytes.as_slice().try_into().map_err(|_| {
                    Error::InvalidFormat("os-vault key ID has an invalid length".into())
                })?;
                let kek = derive_vault_kek(vault_master_key, &header.package_id, &key_id)?;
                if let Ok(candidate) = unwrap_dek(slot, &header.package_id, &kek) {
                    dek = Some(candidate);
                    break 'keys;
                }
            }
        }
        let dek = dek.ok_or(Error::KeyUnwrap)?;
        Self::finish_open(path, file, header, header_bytes, envelope_bytes, dek)
    }

    fn finish_open(
        path: &Path,
        mut file: File,
        header: Header,
        header_bytes: [u8; HEADER_SIZE],
        envelope_bytes: Vec<u8>,
        dek: LockedSecret<KEY_BYTES>,
    ) -> Result<Self> {
        file.seek(SeekFrom::Start(header.manifest_offset))?;
        let mut encrypted_manifest = vec![0_u8; header.manifest_length as usize];
        file.read_exact(&mut encrypted_manifest)?;
        let mut manifest_aad = header_bytes.to_vec();
        manifest_aad.extend_from_slice(&envelope_bytes);
        let manifest_plaintext = decrypt(
            &dek,
            &header.manifest_nonce,
            &manifest_aad,
            &encrypted_manifest,
        )?;
        let manifest: Manifest = cbor_decode_canonical(&manifest_plaintext)?;
        validate_manifest(&header, &manifest)?;
        Ok(Self {
            path: path.to_path_buf(),
            file,
            header,
            dek,
            manifest,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn read_asset(&mut self, logical_name: &str) -> Result<Zeroizing<Vec<u8>>> {
        let asset = self
            .manifest
            .assets
            .iter()
            .find(|asset| asset.logical_name == logical_name)
            .cloned()
            .ok_or_else(|| Error::InvalidFormat(format!("asset not found: {logical_name}")))?;
        let capacity = usize::try_from(asset.plaintext_length)
            .map_err(|_| Error::InvalidFormat("asset is too large for this platform".into()))?;
        if asset.plaintext_length > MAX_IN_MEMORY_ASSET_BYTES {
            return Err(Error::InvalidFormat(
                "asset exceeds the in-memory read limit; use ranged reads".into(),
            ));
        }
        let mut output = Zeroizing::new(Vec::with_capacity(capacity));
        let mut digest = Sha256::new();
        for chunk in &asset.chunks {
            let plaintext = self.decrypt_chunk(&asset, chunk)?;
            digest.update(plaintext.as_slice());
            output.extend_from_slice(&plaintext);
        }
        let computed: [u8; 32] = digest.finalize().into();
        if computed.as_slice() != asset.sha256.as_slice() {
            return Err(Error::Authentication);
        }
        Ok(output)
    }

    pub fn read_asset_range(
        &mut self,
        logical_name: &str,
        offset: u64,
        length: usize,
    ) -> Result<Zeroizing<Vec<u8>>> {
        let asset = self
            .manifest
            .assets
            .iter()
            .find(|asset| asset.logical_name == logical_name)
            .cloned()
            .ok_or_else(|| Error::InvalidFormat(format!("asset not found: {logical_name}")))?;
        let requested_end = checked_end(offset, length as u64, "asset read")?;
        if requested_end > asset.plaintext_length {
            return Err(Error::InvalidFormat(
                "asset read exceeds declared length".into(),
            ));
        }
        let mut output = Zeroizing::new(Vec::with_capacity(length));
        for chunk in &asset.chunks {
            let chunk_end = checked_end(
                chunk.plaintext_offset,
                chunk.plaintext_length as u64,
                "chunk plaintext range",
            )?;
            if chunk_end <= offset || chunk.plaintext_offset >= requested_end {
                continue;
            }
            let plaintext = self.decrypt_chunk(&asset, chunk)?;
            let start = offset.saturating_sub(chunk.plaintext_offset) as usize;
            let end = (requested_end.min(chunk_end) - chunk.plaintext_offset) as usize;
            output.extend_from_slice(&plaintext[start..end]);
        }
        if output.len() != length {
            return Err(Error::InvalidFormat(
                "asset range is not fully backed by chunks".into(),
            ));
        }
        Ok(output)
    }

    fn decrypt_chunk(&mut self, asset: &Asset, chunk: &Chunk) -> Result<Zeroizing<Vec<u8>>> {
        self.file.seek(SeekFrom::Start(chunk.file_offset))?;
        let mut ciphertext = vec![0_u8; chunk.ciphertext_length as usize];
        self.file.read_exact(&mut ciphertext)?;
        let nonce: [u8; NONCE_BYTES] = chunk
            .nonce
            .as_slice()
            .try_into()
            .map_err(|_| Error::InvalidFormat("chunk nonce has an invalid length".into()))?;
        let aad = chunk_aad(
            &self.header.package_id,
            &asset.asset_id,
            chunk.index,
            chunk.plaintext_offset,
            chunk.plaintext_length,
            encoding_name(asset.content_encoding),
        );
        let plaintext = decrypt(&self.dek, &nonce, &aad, &ciphertext)?;
        if plaintext.len() != chunk.plaintext_length as usize {
            return Err(Error::InvalidFormat(
                "decrypted chunk length does not match manifest".into(),
            ));
        }
        Ok(plaintext)
    }
}

fn validate_manifest(header: &Header, manifest: &Manifest) -> Result<()> {
    if manifest.schema_version != 1 || manifest.package_id.as_bytes() != &header.package_id {
        return Err(Error::InvalidFormat(
            "manifest identity does not match header".into(),
        ));
    }
    if manifest.assets.is_empty() || manifest.assets.len() > MAX_ASSETS {
        return Err(Error::InvalidFormat(
            "manifest asset count is outside v1 bounds".into(),
        ));
    }
    validate_short_field(&manifest.producer, "producer", false)?;
    validate_short_field(
        &manifest.minimum_player_version,
        "minimum player version",
        false,
    )?;
    validate_metadata(&manifest.metadata)?;
    let asset_region_start = checked_end(
        header.envelope_offset,
        header.envelope_length,
        "asset region",
    )?;
    let mut file_ranges = Vec::new();
    let mut nonces = HashSet::from([header.manifest_nonce.to_vec()]);
    let mut asset_ids = HashSet::new();
    let mut logical_names = HashSet::new();
    let mut total_chunks = 0_usize;
    for asset in &manifest.assets {
        validate_logical_name(&asset.logical_name)?;
        validate_short_field(&asset.media_type, "asset media type", false)?;
        validate_short_field(&asset.kind, "asset kind", false)?;
        if asset
            .track_name
            .as_deref()
            .is_some_and(|value| validate_short_field(value, "track name", true).is_err())
            || asset
                .language
                .as_deref()
                .is_some_and(|value| validate_short_field(value, "language", true).is_err())
        {
            return Err(Error::InvalidFormat(
                "invalid asset track name or language".into(),
            ));
        }
        if asset.sha256.len() != 32
            || !asset_ids.insert(asset.asset_id)
            || !logical_names.insert(asset.logical_name.clone())
        {
            return Err(Error::InvalidFormat(
                "duplicate asset or invalid SHA-256".into(),
            ));
        }
        if asset.plaintext_length > 0 && asset.chunks.is_empty() {
            return Err(Error::InvalidFormat("non-empty asset has no chunks".into()));
        }
        let mut expected_plaintext_offset = 0_u64;
        for (position, chunk) in asset.chunks.iter().enumerate() {
            increment_chunk_count(&mut total_chunks)?;
            if chunk.index != position as u64
                || chunk.plaintext_offset != expected_plaintext_offset
                || chunk.plaintext_length == 0
                || chunk.plaintext_length as usize > MAX_CHUNK_SIZE
                || chunk.ciphertext_length as u64
                    != chunk.plaintext_length as u64 + TAG_BYTES as u64
                || chunk.nonce.len() != NONCE_BYTES
                || !nonces.insert(chunk.nonce.clone())
            {
                return Err(Error::InvalidFormat(
                    "invalid chunk graph or duplicate nonce".into(),
                ));
            }
            expected_plaintext_offset = checked_end(
                expected_plaintext_offset,
                chunk.plaintext_length as u64,
                "asset plaintext",
            )?;
            let file_end = checked_end(
                chunk.file_offset,
                chunk.ciphertext_length as u64,
                "chunk ciphertext",
            )?;
            if chunk.file_offset < asset_region_start || file_end > header.manifest_offset {
                return Err(Error::InvalidFormat(
                    "chunk points outside the asset region".into(),
                ));
            }
            file_ranges.push((chunk.file_offset, file_end));
        }
        if expected_plaintext_offset != asset.plaintext_length {
            return Err(Error::InvalidFormat(
                "chunks do not cover the declared asset length".into(),
            ));
        }
    }
    file_ranges.sort_unstable();
    let mut expected_file_offset = asset_region_start;
    for (start, end) in file_ranges {
        if start != expected_file_offset {
            return Err(Error::InvalidFormat(
                "asset ciphertext has a gap or overlap".into(),
            ));
        }
        expected_file_offset = end;
    }
    if expected_file_offset != header.manifest_offset {
        return Err(Error::InvalidFormat(
            "undeclared bytes exist before the manifest".into(),
        ));
    }
    Ok(())
}

pub fn verify_package(path: &Path, recovery_passphrase: &[u8]) -> Result<VerificationReport> {
    verify_reader(PackageReader::open(path, recovery_passphrase)?)
}

pub fn verify_package_with_vault(
    path: &Path,
    vault_master_key: &[u8; KEY_BYTES],
) -> Result<VerificationReport> {
    verify_reader(PackageReader::open_with_vault(path, vault_master_key)?)
}

pub fn verify_package_with_vault_candidates(
    path: &Path,
    vault_master_keys: &[&[u8; KEY_BYTES]],
) -> Result<VerificationReport> {
    verify_reader(PackageReader::open_with_vault_candidates(
        path,
        vault_master_keys,
    )?)
}

fn verify_reader(mut reader: PackageReader) -> Result<VerificationReport> {
    let assets = reader.manifest.assets.clone();
    let mut plaintext_bytes = 0_u64;
    let mut chunk_count = 0_usize;
    for asset in &assets {
        let mut digest = Sha256::new();
        for chunk in &asset.chunks {
            digest.update(reader.decrypt_chunk(asset, chunk)?.as_slice());
            chunk_count += 1;
        }
        let computed: [u8; 32] = digest.finalize().into();
        if computed.as_slice() != asset.sha256.as_slice() {
            return Err(Error::Authentication);
        }
        plaintext_bytes = checked_end(
            plaintext_bytes,
            asset.plaintext_length,
            "verified plaintext",
        )?;
    }
    let package_bytes = reader.header.package_length;
    Ok(VerificationReport {
        valid: true,
        package_id: reader.manifest.package_id,
        asset_count: assets.len(),
        chunk_count,
        plaintext_bytes,
        package_bytes,
        cryptographic_overhead_bytes: package_bytes.saturating_sub(plaintext_bytes),
    })
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use serde_json::{Value, json};

    use super::*;
    use crate::{
        crypto::{decrypt, encrypt},
        schema::{AssetRequest, ContentEncoding},
    };

    fn asset_request(logical_name: String) -> AssetRequest {
        AssetRequest {
            logical_name,
            path: PathBuf::from("unused-test-asset"),
            media_type: "application/octet-stream".into(),
            kind: "test".into(),
            track_name: None,
            language: None,
            default: false,
            content_encoding: ContentEncoding::Identity,
        }
    }

    fn package_request(asset_count: usize) -> PackageRequest {
        PackageRequest {
            metadata: json!({"fixture": true}),
            producer: "p".into(),
            minimum_player_version: "0.8.0".into(),
            assets: (0..asset_count)
                .map(|index| asset_request(format!("assets/{index}.bin")))
                .collect(),
        }
    }

    fn one_chunk_manifest(chunk_length: u32) -> (Header, Manifest) {
        let package_id = [0x11; 16];
        let asset_start = HEADER_SIZE as u64 + 64;
        let ciphertext_length = chunk_length.checked_add(TAG_BYTES as u32).unwrap();
        let manifest_offset = asset_start + u64::from(ciphertext_length);
        let header = Header {
            flags: 0,
            package_id,
            envelope_offset: HEADER_SIZE as u64,
            envelope_length: 64,
            manifest_offset,
            manifest_length: 256,
            manifest_nonce: [0x22; NONCE_BYTES],
            package_length: manifest_offset + 256,
        };
        let manifest = Manifest {
            schema_version: 1,
            minimum_player_version: "0.8.0".into(),
            package_id: Uuid::from_bytes(package_id),
            created_at_unix_ms: 0,
            producer: "LyricRail boundary test".into(),
            metadata: json!({}),
            assets: vec![Asset {
                asset_id: Uuid::from_bytes([0x33; 16]),
                logical_name: "assets/test.bin".into(),
                media_type: "application/octet-stream".into(),
                kind: "test".into(),
                track_name: None,
                language: None,
                default: false,
                content_encoding: ContentEncoding::Identity,
                plaintext_length: u64::from(chunk_length),
                sha256: vec![0; 32],
                chunks: vec![Chunk {
                    index: 0,
                    plaintext_offset: 0,
                    plaintext_length: chunk_length,
                    file_offset: asset_start,
                    ciphertext_length,
                    nonce: vec![0x44; NONCE_BYTES],
                }],
            }],
        };
        (header, manifest)
    }

    #[test]
    fn chunk_aad_rejects_every_committed_field_swap() {
        let key = [0x51; KEY_BYTES];
        let nonce = [0x52; NONCE_BYTES];
        let package_id = [0x53; 16];
        let asset_id = Uuid::from_bytes([0x54; 16]);
        let plaintext = b"authenticated chunk";
        let exact = chunk_aad(&package_id, &asset_id, 7, 1_048_576, 19, "identity");
        let ciphertext = encrypt(&key, &nonce, &exact, plaintext).unwrap();
        assert_eq!(
            decrypt(&key, &nonce, &exact, &ciphertext)
                .unwrap()
                .as_slice(),
            plaintext
        );

        let swaps = [
            chunk_aad(&[0x55; 16], &asset_id, 7, 1_048_576, 19, "identity"),
            chunk_aad(
                &package_id,
                &Uuid::from_bytes([0x56; 16]),
                7,
                1_048_576,
                19,
                "identity",
            ),
            chunk_aad(&package_id, &asset_id, 8, 1_048_576, 19, "identity"),
            chunk_aad(&package_id, &asset_id, 7, 1_048_577, 19, "identity"),
            chunk_aad(&package_id, &asset_id, 7, 1_048_576, 20, "identity"),
            chunk_aad(&package_id, &asset_id, 7, 1_048_576, 19, "other"),
        ];
        for swapped_aad in swaps {
            assert_ne!(swapped_aad, exact);
            assert!(decrypt(&key, &nonce, &swapped_aad, &ciphertext).is_err());
        }
    }

    #[test]
    fn request_asset_and_string_limits_are_exact() {
        assert!(validate_request(&package_request(MAX_ASSETS)).is_ok());
        assert!(validate_request(&package_request(MAX_ASSETS + 1)).is_err());

        let mut request = package_request(1);
        request.producer = "p".repeat(MAX_SHORT_FIELD_BYTES);
        request.assets[0].logical_name = "n".repeat(1024);
        request.metadata = Value::String("m".repeat(MAX_METADATA_STRING_BYTES));
        assert!(validate_request(&request).is_ok());

        request.producer.push('p');
        assert!(validate_request(&request).is_err());
        request.producer.pop();
        request.assets[0].logical_name.push('n');
        assert!(validate_request(&request).is_err());
        request.assets[0].logical_name.pop();
        request.metadata = Value::String("m".repeat(MAX_METADATA_STRING_BYTES + 1));
        assert!(validate_request(&request).is_err());
    }

    #[test]
    fn metadata_node_and_chunk_limits_are_exact() {
        let allowed = Value::Array(vec![Value::Null; MAX_METADATA_NODES - 1]);
        assert!(validate_metadata(&allowed).is_ok());
        let rejected = Value::Array(vec![Value::Null; MAX_METADATA_NODES]);
        assert!(validate_metadata(&rejected).is_err());

        let mut count = MAX_CHUNKS - 1;
        assert!(increment_chunk_count(&mut count).is_ok());
        assert_eq!(count, MAX_CHUNKS);
        assert!(increment_chunk_count(&mut count).is_err());

        let (header, manifest) = one_chunk_manifest(MAX_CHUNK_SIZE as u32);
        assert!(validate_manifest(&header, &manifest).is_ok());
        let (header, manifest) = one_chunk_manifest(MAX_CHUNK_SIZE as u32 + 1);
        assert!(validate_manifest(&header, &manifest).is_err());
    }

    #[test]
    fn v1_rejects_unsupported_content_compression() {
        let encoded = json!({
            "logicalName": "assets/test.bin",
            "path": "unused-test-asset",
            "mediaType": "application/octet-stream",
            "kind": "test",
            "contentEncoding": "zstd"
        });
        assert!(serde_json::from_value::<AssetRequest>(encoded).is_err());
    }
}
