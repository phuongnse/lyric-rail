//! Signed, exhaustive runtime-pack manifests used by LyricRail Studio.

use std::{
    collections::HashSet,
    env, fs,
    fs::{File, OpenOptions},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
};

use anyhow::{Context, Result, bail, ensure};
use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use zeroize::Zeroizing;

use crate::LockedSecret;

pub const RUNTIME_MANIFEST_NAME: &str = "runtime-manifest.json";
pub const RUNTIME_SIGNATURE_NAME: &str = "runtime-manifest.sig";
const SIGNING_DOMAIN: &[u8] = b"LyricRail runtime manifest v1\0";
const MAX_MANIFEST_BYTES: u64 = 16 * 1024 * 1024;
const MAX_RUNTIME_FILES: usize = 100_000;
const MAX_RUNTIME_DEPTH: usize = 32;
const MAX_RUNTIME_PATH_BYTES: usize = 512;
const MAX_RUNTIME_FILE_BYTES: u64 = 64 * 1024 * 1024 * 1024;
const MAX_RUNTIME_TOTAL_BYTES: u64 = 128 * 1024 * 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeFile {
    pub path: String,
    pub size_bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeManifest {
    pub schema_version: u16,
    pub runtime_version: String,
    pub platform: String,
    pub executables: RuntimeExecutables,
    pub files: Vec<RuntimeFile>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeExecutables {
    pub python: String,
    pub ffmpeg: String,
    pub ffprobe: String,
    pub lrail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeSignature {
    pub schema_version: u16,
    pub algorithm: String,
    pub key_id: String,
    pub manifest_sha256: String,
    pub signature: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeVerification {
    pub runtime_version: String,
    pub platform: String,
    pub key_id: String,
    pub manifest_sha256: String,
    pub file_count: usize,
    pub total_bytes: u64,
    pub python_executable: PathBuf,
    pub ffmpeg_executable: PathBuf,
    pub ffprobe_executable: PathBuf,
    pub lrail_executable: PathBuf,
}

pub fn runtime_platform() -> String {
    format!("{}-{}", env::consts::OS, env::consts::ARCH)
}

fn runtime_path(path: &Path) -> Result<String> {
    let mut parts = Vec::new();
    for component in path.components() {
        match component {
            Component::Normal(value) => {
                let value = value
                    .to_str()
                    .context("runtime paths must be valid UTF-8")?;
                ensure!(
                    !value.is_empty() && value != "." && value != "..",
                    "runtime path contains an unsafe component"
                );
                parts.push(value);
            }
            _ => bail!("runtime paths must be relative and normalized"),
        }
    }
    ensure!(!parts.is_empty(), "runtime path must not be empty");
    let normalized = parts.join("/");
    ensure!(
        normalized.len() <= MAX_RUNTIME_PATH_BYTES,
        "runtime path exceeds {MAX_RUNTIME_PATH_BYTES} bytes"
    );
    Ok(normalized)
}

fn validate_runtime_path(path: &str) -> Result<PathBuf> {
    ensure!(
        !path.is_empty()
            && path.len() <= MAX_RUNTIME_PATH_BYTES
            && !path.contains('\\')
            && !path.contains('\0'),
        "invalid runtime path"
    );
    let parsed = Path::new(path);
    ensure!(
        runtime_path(parsed)? == path,
        "runtime path is not canonical"
    );
    Ok(parsed.to_owned())
}

fn sha256_file(path: &Path, expected_size: u64) -> Result<String> {
    ensure!(
        expected_size <= MAX_RUNTIME_FILE_BYTES,
        "runtime file {} exceeds the per-file limit",
        path.display()
    );
    let mut file = File::open(path)
        .with_context(|| format!("unable to open runtime file {}", path.display()))?;
    let opened = file
        .metadata()
        .with_context(|| format!("unable to stat runtime file {}", path.display()))?;
    ensure!(opened.is_file(), "runtime entry is not a regular file");
    ensure!(
        opened.len() == expected_size,
        "runtime file changed while it was being verified: {}",
        path.display()
    );
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .with_context(|| format!("unable to hash runtime file {}", path.display()))?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    let finished = file
        .metadata()
        .with_context(|| format!("unable to restat runtime file {}", path.display()))?;
    ensure!(
        finished.len() == expected_size,
        "runtime file changed while it was being verified: {}",
        path.display()
    );
    Ok(hex::encode(hasher.finalize()))
}

fn is_root_manifest(relative: &Path) -> bool {
    relative.components().count() == 1
        && relative
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value == RUNTIME_MANIFEST_NAME || value == RUNTIME_SIGNATURE_NAME)
}

fn ensure_allowed_runtime_entry(relative: &Path) -> Result<()> {
    let normalized = runtime_path(relative)?;
    let lower = normalized.to_ascii_lowercase();
    let first = lower.split('/').next().unwrap_or_default();
    ensure!(
        !matches!(
            first,
            "credentials" | "input" | "output" | "cache" | "logs" | ".git"
        ),
        "mutable or secret directory is forbidden in a runtime pack: {normalized}"
    );
    ensure!(
        !(relative.components().count() == 1 && first.starts_with(".env")),
        "environment files are forbidden in a runtime pack: {normalized}"
    );
    ensure!(
        !lower.ends_with("runtime-signing-private.key"),
        "runtime signing private keys are forbidden in a runtime pack"
    );
    Ok(())
}

#[cfg(windows)]
fn ensure_not_reparse_point(metadata: &fs::Metadata, path: &Path) -> Result<()> {
    use std::os::windows::fs::MetadataExt;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    ensure!(
        metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT == 0,
        "runtime packs cannot contain reparse points: {}",
        path.display()
    );
    Ok(())
}

#[cfg(not(windows))]
fn ensure_not_reparse_point(_metadata: &fs::Metadata, _path: &Path) -> Result<()> {
    Ok(())
}

fn visit_runtime(
    root: &Path,
    directory: &Path,
    depth: usize,
    files: &mut Vec<RuntimeFile>,
    total_bytes: &mut u64,
) -> Result<()> {
    ensure!(
        depth <= MAX_RUNTIME_DEPTH,
        "runtime pack exceeds the maximum directory depth"
    );
    let mut entries = fs::read_dir(directory)
        .with_context(|| format!("unable to enumerate {}", directory.display()))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path)
            .with_context(|| format!("unable to inspect {}", path.display()))?;
        ensure_not_reparse_point(&metadata, &path)?;
        ensure!(
            !metadata.file_type().is_symlink(),
            "runtime packs cannot contain symlinks: {}",
            path.display()
        );
        let relative = path
            .strip_prefix(root)
            .context("runtime inventory escaped its root")?;
        ensure_allowed_runtime_entry(relative)?;
        if metadata.is_dir() {
            visit_runtime(root, &path, depth + 1, files, total_bytes)?;
            continue;
        }
        ensure!(
            metadata.is_file(),
            "runtime packs may contain only files and directories: {}",
            path.display()
        );
        if is_root_manifest(relative) {
            continue;
        }
        ensure!(
            files.len() < MAX_RUNTIME_FILES,
            "runtime pack exceeds {MAX_RUNTIME_FILES} files"
        );
        *total_bytes = total_bytes
            .checked_add(metadata.len())
            .context("runtime byte count overflow")?;
        ensure!(
            *total_bytes <= MAX_RUNTIME_TOTAL_BYTES,
            "runtime pack exceeds the total size limit"
        );
        files.push(RuntimeFile {
            path: runtime_path(relative)?,
            size_bytes: metadata.len(),
            sha256: sha256_file(&path, metadata.len())?,
        });
    }
    Ok(())
}

pub fn inventory_runtime(root: &Path) -> Result<(Vec<RuntimeFile>, u64)> {
    let root = root
        .canonicalize()
        .with_context(|| format!("unable to open runtime root {}", root.display()))?;
    ensure!(root.is_dir(), "runtime root must be a directory");
    let mut files = Vec::new();
    let mut total_bytes = 0_u64;
    visit_runtime(&root, &root, 0, &mut files, &mut total_bytes)?;
    files.sort_by(|left, right| left.path.cmp(&right.path));
    Ok((files, total_bytes))
}

fn validate_manifest(manifest: &RuntimeManifest) -> Result<Vec<PathBuf>> {
    ensure!(
        manifest.schema_version == 1,
        "unsupported runtime manifest schema"
    );
    ensure!(
        !manifest.runtime_version.is_empty() && manifest.runtime_version.len() <= 64,
        "invalid runtime version"
    );
    ensure!(
        !manifest.platform.is_empty() && manifest.platform.len() <= 64,
        "invalid runtime platform"
    );
    ensure!(
        !manifest.files.is_empty() && manifest.files.len() <= MAX_RUNTIME_FILES,
        "invalid runtime file count"
    );
    let executable_paths = [
        &manifest.executables.python,
        &manifest.executables.ffmpeg,
        &manifest.executables.ffprobe,
        &manifest.executables.lrail,
    ];
    let executables = executable_paths
        .iter()
        .map(|path| validate_runtime_path(path))
        .collect::<Result<Vec<_>>>()?;
    let executable_set = executable_paths
        .iter()
        .map(|path| path.as_str())
        .collect::<HashSet<_>>();
    ensure!(
        executable_set.len() == executable_paths.len(),
        "runtime executable paths must be unique"
    );
    let mut previous: Option<&str> = None;
    let mut seen = HashSet::with_capacity(manifest.files.len());
    let mut listed_executables = HashSet::new();
    let mut total = 0_u64;
    for file in &manifest.files {
        validate_runtime_path(&file.path)?;
        ensure!(
            previous.is_none_or(|value| value < file.path.as_str()),
            "runtime files must be uniquely sorted"
        );
        ensure!(seen.insert(&file.path), "duplicate runtime file path");
        ensure!(
            file.size_bytes <= MAX_RUNTIME_FILE_BYTES,
            "runtime file exceeds the per-file limit"
        );
        ensure!(
            file.sha256.len() == 64 && hex::decode(&file.sha256).is_ok(),
            "runtime file has an invalid SHA-256"
        );
        total = total
            .checked_add(file.size_bytes)
            .context("runtime byte count overflow")?;
        ensure!(
            total <= MAX_RUNTIME_TOTAL_BYTES,
            "runtime pack exceeds the total size limit"
        );
        if executable_set.contains(file.path.as_str()) {
            listed_executables.insert(file.path.as_str());
        }
        previous = Some(&file.path);
    }
    ensure!(
        listed_executables.len() == executable_paths.len(),
        "one or more runtime executables are not covered by the manifest"
    );
    Ok(executables)
}

pub fn create_runtime_manifest(
    root: &Path,
    runtime_version: &str,
    platform: &str,
    executables: RuntimeExecutables,
) -> Result<RuntimeManifest> {
    let (files, _) = inventory_runtime(root)?;
    let manifest = RuntimeManifest {
        schema_version: 1,
        runtime_version: runtime_version.to_owned(),
        platform: platform.to_owned(),
        executables,
        files,
    };
    validate_manifest(&manifest)?;
    Ok(manifest)
}

fn decode_hex_array<const N: usize>(value: &str, label: &str) -> Result<[u8; N]> {
    let decoded = hex::decode(value.trim()).with_context(|| format!("invalid {label} hex"))?;
    decoded
        .try_into()
        .map_err(|_| anyhow::anyhow!("{label} must contain exactly {N} bytes"))
}

fn key_id(public_key: &[u8; 32]) -> String {
    let digest = Sha256::digest(public_key);
    hex::encode(&digest[..16])
}

fn signed_message(manifest_bytes: &[u8]) -> Vec<u8> {
    let mut message = Vec::with_capacity(SIGNING_DOMAIN.len() + manifest_bytes.len());
    message.extend_from_slice(SIGNING_DOMAIN);
    message.extend_from_slice(manifest_bytes);
    message
}

fn create_new_file(path: &Path, private: bool) -> Result<File> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    if private {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let file = options
        .open(path)
        .with_context(|| format!("refusing to overwrite {}", path.display()))?;
    #[cfg(not(unix))]
    let _ = private;
    Ok(file)
}

pub fn generate_runtime_keypair(private_key: &Path, public_key: &Path) -> Result<String> {
    ensure!(
        private_key.parent().is_some_and(Path::is_dir),
        "private-key parent directory does not exist"
    );
    ensure!(
        public_key.parent().is_some_and(Path::is_dir),
        "public-key parent directory does not exist"
    );
    let secret = LockedSecret::<32>::random()?;
    let signing = SigningKey::from_bytes(&secret);
    let verifying = signing.verifying_key().to_bytes();
    let id = key_id(&verifying);
    let mut private_file = create_new_file(private_key, true)?;
    let private_hex = Zeroizing::new(hex::encode(secret.as_slice()));
    private_file.write_all(private_hex.as_bytes())?;
    private_file.write_all(b"\n")?;
    private_file.sync_all()?;
    let mut public_file = match create_new_file(public_key, false) {
        Ok(file) => file,
        Err(error) => {
            let _ = fs::remove_file(private_key);
            return Err(error);
        }
    };
    public_file.write_all(hex::encode(verifying).as_bytes())?;
    public_file.write_all(b"\n")?;
    public_file.sync_all()?;
    Ok(id)
}

pub fn sign_runtime_manifest(manifest_bytes: &[u8], private_key_hex: &str) -> Result<Vec<u8>> {
    ensure!(
        manifest_bytes.len() as u64 <= MAX_MANIFEST_BYTES,
        "runtime manifest exceeds the size limit"
    );
    let manifest: RuntimeManifest =
        serde_json::from_slice(manifest_bytes).context("invalid runtime manifest JSON")?;
    validate_manifest(&manifest)?;
    let decoded =
        Zeroizing::new(hex::decode(private_key_hex.trim()).context("invalid private key hex")?);
    ensure!(
        decoded.len() == 32,
        "private key must contain exactly 32 bytes"
    );
    let secret = LockedSecret::<32>::from_slice(&decoded)?;
    let signing = SigningKey::from_bytes(&secret);
    let public = signing.verifying_key().to_bytes();
    let signature = signing.sign(&signed_message(manifest_bytes));
    let envelope = RuntimeSignature {
        schema_version: 1,
        algorithm: "Ed25519".to_owned(),
        key_id: key_id(&public),
        manifest_sha256: hex::encode(Sha256::digest(manifest_bytes)),
        signature: hex::encode(signature.to_bytes()),
    };
    serde_json::to_vec_pretty(&envelope).context("unable to encode runtime signature")
}

pub fn verify_runtime_pack(
    root: &Path,
    public_key_hex: &str,
    expected_version: &str,
    expected_platform: &str,
) -> Result<RuntimeVerification> {
    let manifest_path = root.join(RUNTIME_MANIFEST_NAME);
    let signature_path = root.join(RUNTIME_SIGNATURE_NAME);
    let manifest_metadata = fs::metadata(&manifest_path)
        .with_context(|| format!("missing {}", manifest_path.display()))?;
    ensure!(
        manifest_metadata.is_file() && manifest_metadata.len() <= MAX_MANIFEST_BYTES,
        "runtime manifest is invalid or oversized"
    );
    let manifest_bytes = fs::read(&manifest_path)?;
    let signature_bytes = fs::read(&signature_path)
        .with_context(|| format!("missing {}", signature_path.display()))?;
    ensure!(
        signature_bytes.len() <= 4096,
        "runtime signature envelope is oversized"
    );
    let envelope: RuntimeSignature =
        serde_json::from_slice(&signature_bytes).context("invalid runtime signature envelope")?;
    ensure!(
        envelope.schema_version == 1 && envelope.algorithm == "Ed25519",
        "unsupported runtime signature"
    );
    let public_bytes = decode_hex_array::<32>(public_key_hex, "public key")?;
    ensure!(
        envelope.key_id == key_id(&public_bytes),
        "runtime signature key ID does not match Studio"
    );
    let observed_manifest_hash = hex::encode(Sha256::digest(&manifest_bytes));
    ensure!(
        envelope.manifest_sha256 == observed_manifest_hash,
        "runtime manifest digest does not match its signature envelope"
    );
    let signature_bytes = decode_hex_array::<64>(&envelope.signature, "signature")?;
    let signature = Signature::from_bytes(&signature_bytes);
    let verifying = VerifyingKey::from_bytes(&public_bytes).context("invalid public key")?;
    verifying
        .verify_strict(&signed_message(&manifest_bytes), &signature)
        .context("runtime manifest signature verification failed")?;

    let manifest: RuntimeManifest =
        serde_json::from_slice(&manifest_bytes).context("invalid runtime manifest JSON")?;
    let executable_paths = validate_manifest(&manifest)?;
    ensure!(
        manifest.runtime_version == expected_version,
        "runtime version {} does not match Studio {}",
        manifest.runtime_version,
        expected_version
    );
    ensure!(
        manifest.platform == expected_platform,
        "runtime platform {} does not match {}",
        manifest.platform,
        expected_platform
    );
    let (observed_files, total_bytes) = inventory_runtime(root)?;
    ensure!(
        observed_files == manifest.files,
        "runtime contents do not match the signed manifest"
    );
    let resolved_executables = executable_paths
        .into_iter()
        .map(|path| root.join(path))
        .collect::<Vec<_>>();
    ensure!(
        resolved_executables.iter().all(|path| path.is_file()),
        "one or more verified runtime executables are missing"
    );
    Ok(RuntimeVerification {
        runtime_version: manifest.runtime_version,
        platform: manifest.platform,
        key_id: envelope.key_id,
        manifest_sha256: observed_manifest_hash,
        file_count: manifest.files.len(),
        total_bytes,
        python_executable: resolved_executables[0].clone(),
        ffmpeg_executable: resolved_executables[1].clone(),
        ffprobe_executable: resolved_executables[2].clone(),
        lrail_executable: resolved_executables[3].clone(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use zeroize::Zeroize;

    fn signed_pack() -> (tempfile::TempDir, PathBuf, String) {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("pack");
        let keys = temporary.path().join("keys");
        fs::create_dir_all(root.join("runtime")).unwrap();
        fs::create_dir_all(root.join("config")).unwrap();
        fs::create_dir_all(&keys).unwrap();
        fs::write(root.join("runtime/python.exe"), b"python-runtime").unwrap();
        fs::write(root.join("runtime/ffmpeg.exe"), b"ffmpeg-runtime").unwrap();
        fs::write(root.join("runtime/ffprobe.exe"), b"ffprobe-runtime").unwrap();
        fs::write(root.join("runtime/lrail.exe"), b"lrail-runtime").unwrap();
        fs::write(root.join("config/pipeline.json"), b"{}\n").unwrap();
        let private = keys.join("runtime-private.key");
        let public = keys.join("runtime-public.key");
        generate_runtime_keypair(&private, &public).unwrap();
        let public_hex = fs::read_to_string(public).unwrap();
        let manifest = create_runtime_manifest(
            &root,
            "0.8.0",
            &runtime_platform(),
            RuntimeExecutables {
                python: "runtime/python.exe".into(),
                ffmpeg: "runtime/ffmpeg.exe".into(),
                ffprobe: "runtime/ffprobe.exe".into(),
                lrail: "runtime/lrail.exe".into(),
            },
        )
        .unwrap();
        let mut manifest_bytes = serde_json::to_vec_pretty(&manifest).unwrap();
        manifest_bytes.push(b'\n');
        let mut private_hex = fs::read_to_string(private).unwrap();
        let signature = sign_runtime_manifest(&manifest_bytes, &private_hex).unwrap();
        private_hex.zeroize();
        fs::write(root.join(RUNTIME_MANIFEST_NAME), manifest_bytes).unwrap();
        fs::write(root.join(RUNTIME_SIGNATURE_NAME), signature).unwrap();
        (temporary, root, public_hex)
    }

    #[test]
    fn signed_runtime_roundtrip_rejects_tampering_and_extra_files() {
        let (_temporary, root, public_hex) = signed_pack();
        let verified =
            verify_runtime_pack(&root, &public_hex, "0.8.0", &runtime_platform()).unwrap();
        assert_eq!(verified.file_count, 5);
        assert!(verified.python_executable.ends_with("runtime/python.exe"));

        fs::write(root.join("config/pipeline.json"), b"tampered\n").unwrap();
        assert!(
            verify_runtime_pack(&root, &public_hex, "0.8.0", &runtime_platform())
                .unwrap_err()
                .to_string()
                .contains("contents do not match")
        );
        fs::write(root.join("config/pipeline.json"), b"{}\n").unwrap();
        fs::write(root.join("unlisted.py"), b"print('unsafe')\n").unwrap();
        assert!(
            verify_runtime_pack(&root, &public_hex, "0.8.0", &runtime_platform())
                .unwrap_err()
                .to_string()
                .contains("contents do not match")
        );
    }

    #[test]
    fn signed_runtime_is_bound_to_studio_version_platform_and_key() {
        let (temporary, root, public_hex) = signed_pack();
        let version_error =
            verify_runtime_pack(&root, &public_hex, "0.9.0", &runtime_platform()).unwrap_err();
        assert!(version_error.to_string().contains("does not match Studio"));
        let platform_error =
            verify_runtime_pack(&root, &public_hex, "0.8.0", "other-platform").unwrap_err();
        assert!(platform_error.to_string().contains("does not match"));

        let other_private = temporary.path().join("other-private.key");
        let other_public = temporary.path().join("other-public.key");
        generate_runtime_keypair(&other_private, &other_public).unwrap();
        let other_public = fs::read_to_string(other_public).unwrap();
        assert!(
            verify_runtime_pack(&root, &other_public, "0.8.0", &runtime_platform())
                .unwrap_err()
                .to_string()
                .contains("key ID")
        );
    }

    #[test]
    fn manifest_validation_rejects_traversal_and_key_overwrite() {
        let temporary = tempfile::tempdir().unwrap();
        let private = temporary.path().join("private.key");
        let public = temporary.path().join("public.key");
        generate_runtime_keypair(&private, &public).unwrap();
        assert!(generate_runtime_keypair(&private, &public).is_err());

        let invalid = RuntimeManifest {
            schema_version: 1,
            runtime_version: "0.8.0".into(),
            platform: runtime_platform(),
            executables: RuntimeExecutables {
                python: "../python.exe".into(),
                ffmpeg: "bin/ffmpeg".into(),
                ffprobe: "bin/ffprobe".into(),
                lrail: "bin/lrail".into(),
            },
            files: vec![RuntimeFile {
                path: "../python.exe".into(),
                size_bytes: 1,
                sha256: "00".repeat(32),
            }],
        };
        let bytes = serde_json::to_vec(&invalid).unwrap();
        let private_hex = fs::read_to_string(private).unwrap();
        assert!(sign_runtime_manifest(&bytes, &private_hex).is_err());
    }

    #[test]
    fn runtime_inventory_rejects_mutable_data_and_secret_files() {
        for forbidden in ["credentials/token.json", ".env.production"] {
            let temporary = tempfile::tempdir().unwrap();
            let root = temporary.path().join("pack");
            let path = root.join(forbidden);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, b"secret").unwrap();
            assert!(inventory_runtime(&root).is_err(), "accepted {forbidden}");
        }
    }
}
