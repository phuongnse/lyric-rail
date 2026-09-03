use std::{fs, io, path::PathBuf, process::ExitCode};

use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};
use lrail_format::{
    LockedString, PackageRequest, PackageRevisionRequest, export_recovery_bundle, inspect_package,
    inspect_recovery_bundle, load_vault_master, pack_for_device_vault, restore_recovery_bundle,
    revise_package_for_vault,
    runtime::{
        RUNTIME_MANIFEST_NAME, RUNTIME_SIGNATURE_NAME, RuntimeExecutables, create_runtime_manifest,
        generate_runtime_keypair, runtime_platform, sign_runtime_manifest, verify_runtime_pack,
    },
    verify_package, verify_package_matches_request_with_vault, verify_package_with_vault,
    verify_recovery_bundle,
};

const MAX_PACKAGE_REQUEST_BYTES: u64 = 64 * 1024 * 1024;

fn read_package_request(path: &std::path::Path) -> Result<PackageRequest> {
    let metadata = fs::symlink_metadata(path)
        .with_context(|| format!("unable to inspect {}", path.display()))?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() > MAX_PACKAGE_REQUEST_BYTES
    {
        bail!(
            "package request is not a bounded regular file: {}",
            path.display()
        );
    }
    let request_bytes =
        fs::read(path).with_context(|| format!("unable to read {}", path.display()))?;
    if request_bytes.len() as u64 > MAX_PACKAGE_REQUEST_BYTES {
        bail!("package request exceeds 64 MiB: {}", path.display());
    }
    serde_json::from_slice(&request_bytes)
        .with_context(|| format!("invalid package request {}", path.display()))
}

#[derive(Debug, Parser)]
#[command(
    name = "lrail",
    version,
    about = "LyricRail authenticated package tool"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Build an encrypted .lrail package from a strict JSON request.
    Pack {
        #[arg(long)]
        request: PathBuf,
        #[arg(long)]
        output: PathBuf,
        /// Also add a portable passphrase-recovery key slot.
        #[arg(long)]
        with_recovery: bool,
    },
    /// Create a transactional local lyric/thumbnail package revision.
    Revise {
        input: PathBuf,
        #[arg(long)]
        request: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Inspect the public header and key-envelope mechanisms.
    Inspect { input: PathBuf },
    /// Authenticate the manifest and every encrypted asset chunk.
    Verify {
        input: PathBuf,
        /// Ignore the local OS vault and prompt for the recovery passphrase.
        #[arg(long)]
        recovery: bool,
    },
    /// Authenticate a package and bind it to an exact strict pack request.
    VerifyRequest {
        input: PathBuf,
        #[arg(long)]
        request: PathBuf,
    },
    /// Export the current device library master as a passphrase-encrypted bundle.
    RecoveryExport {
        #[arg(long)]
        output: PathBuf,
    },
    /// Inspect public recovery-bundle metadata without decrypting the key.
    RecoveryInspect { input: PathBuf },
    /// Prompt for the passphrase and authenticate a recovery bundle.
    RecoveryVerify { input: PathBuf },
    /// Restore a missing device key only after every package in the library verifies.
    RecoveryRestore {
        input: PathBuf,
        #[arg(long)]
        library: PathBuf,
    },
    /// Generate an offline Ed25519 keypair for signing local-core runtime packs.
    RuntimeKeygen {
        #[arg(long)]
        private_key: PathBuf,
        #[arg(long)]
        public_key: PathBuf,
    },
    /// Inventory every runtime file, then create a signed manifest in its root.
    RuntimeManifest {
        #[arg(long)]
        root: PathBuf,
        /// Python executable path relative to the runtime root.
        #[arg(long)]
        python: PathBuf,
        /// FFmpeg executable path relative to the runtime root.
        #[arg(long)]
        ffmpeg: PathBuf,
        /// FFprobe executable path relative to the runtime root.
        #[arg(long)]
        ffprobe: PathBuf,
        /// Native lrail packager path relative to the runtime root.
        #[arg(long)]
        lrail: PathBuf,
        #[arg(long)]
        private_key: PathBuf,
        #[arg(long, default_value = env!("CARGO_PKG_VERSION"))]
        runtime_version: String,
        #[arg(long)]
        platform: Option<String>,
    },
    /// Verify a signed runtime pack and every file it contains.
    RuntimeVerify {
        #[arg(long)]
        root: PathBuf,
        #[arg(long)]
        public_key: PathBuf,
        #[arg(long, default_value = env!("CARGO_PKG_VERSION"))]
        runtime_version: String,
        #[arg(long)]
        platform: Option<String>,
    },
}

fn recovery_passphrase(confirm: bool) -> Result<LockedString> {
    let passphrase = LockedString::new(rpassword::prompt_password("Recovery passphrase: ")?)?;
    if passphrase.len() < 12 {
        bail!("recovery passphrase must contain at least 12 bytes");
    }
    if confirm {
        let repeated =
            LockedString::new(rpassword::prompt_password("Confirm recovery passphrase: ")?)?;
        let matches = passphrase.as_bytes() == repeated.as_bytes();
        if !matches {
            bail!("recovery passphrases do not match");
        }
    }
    Ok(passphrase)
}

fn run() -> Result<()> {
    match Cli::parse().command {
        Command::Pack {
            request,
            output,
            with_recovery,
        } => {
            let package_request = read_package_request(&request)?;
            let passphrase = if with_recovery {
                Some(recovery_passphrase(true)?)
            } else {
                None
            };
            let assets = pack_for_device_vault(
                &package_request,
                &output,
                passphrase.as_deref().map(str::as_bytes),
            )?;
            println!(
                "{}",
                serde_json::to_string_pretty(&serde_json::json!({
                    "kind": "lyricrail.package.created",
                    "output": output,
                    "assets": assets.into_iter().map(|asset| serde_json::json!({
                        "logicalName": asset.logical_name,
                        "plaintextBytes": asset.plaintext_length,
                        "chunks": asset.chunk_count,
                    })).collect::<Vec<_>>()
                }))?
            );
        }
        Command::Revise {
            input,
            request,
            output,
        } => {
            let request_bytes = fs::read(&request)
                .with_context(|| format!("unable to read {}", request.display()))?;
            let revision: PackageRevisionRequest = serde_json::from_slice(&request_bytes)
                .with_context(|| format!("invalid revision request {}", request.display()))?;
            let vault_master = load_vault_master()?;
            let report = revise_package_for_vault(&input, &output, &vault_master, &revision)?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Command::Inspect { input } => {
            println!(
                "{}",
                serde_json::to_string_pretty(&inspect_package(&input)?)?
            );
        }
        Command::Verify { input, recovery } => {
            let inspection = inspect_package(&input)?;
            let report = if !recovery
                && inspection
                    .key_mechanisms
                    .iter()
                    .any(|mechanism| mechanism == "os-vault-v1")
            {
                let vault_master = load_vault_master()?;
                verify_package_with_vault(&input, &vault_master)?
            } else {
                let passphrase = recovery_passphrase(false)?;
                verify_package(&input, passphrase.as_bytes())?
            };
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Command::VerifyRequest { input, request } => {
            let package_request = read_package_request(&request)?;
            let vault_master = load_vault_master()?;
            let report =
                verify_package_matches_request_with_vault(&input, &vault_master, &package_request)?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Command::RecoveryExport { output } => {
            let passphrase = recovery_passphrase(true)?;
            let report = export_recovery_bundle(&output, passphrase.as_bytes())?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Command::RecoveryInspect { input } => {
            println!(
                "{}",
                serde_json::to_string_pretty(&inspect_recovery_bundle(&input)?)?
            );
        }
        Command::RecoveryVerify { input } => {
            let passphrase = recovery_passphrase(false)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&verify_recovery_bundle(
                    &input,
                    passphrase.as_bytes()
                )?)?
            );
        }
        Command::RecoveryRestore { input, library } => {
            let passphrase = recovery_passphrase(false)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&restore_recovery_bundle(
                    &input,
                    passphrase.as_bytes(),
                    &library
                )?)?
            );
        }
        Command::RuntimeKeygen {
            private_key,
            public_key,
        } => {
            let key_id = generate_runtime_keypair(&private_key, &public_key)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&serde_json::json!({
                    "kind": "lyricrail.runtime.signing-key-created",
                    "keyId": key_id,
                    "privateKey": private_key,
                    "publicKey": public_key,
                }))?
            );
        }
        Command::RuntimeManifest {
            root,
            python,
            ffmpeg,
            ffprobe,
            lrail,
            private_key,
            runtime_version,
            platform,
        } => {
            let root = root
                .canonicalize()
                .with_context(|| format!("unable to open runtime root {}", root.display()))?;
            let private_key = private_key.canonicalize().with_context(|| {
                format!(
                    "unable to open runtime private key {}",
                    private_key.display()
                )
            })?;
            if private_key.starts_with(&root) {
                bail!("the runtime signing private key must be outside the runtime pack");
            }
            let manifest_path = root.join(RUNTIME_MANIFEST_NAME);
            let signature_path = root.join(RUNTIME_SIGNATURE_NAME);
            if manifest_path.exists() || signature_path.exists() {
                bail!("refusing to overwrite an existing runtime manifest or signature");
            }
            let platform = platform.unwrap_or_else(runtime_platform);
            let manifest = create_runtime_manifest(
                &root,
                &runtime_version,
                &platform,
                RuntimeExecutables {
                    python: python.to_string_lossy().replace('\\', "/"),
                    ffmpeg: ffmpeg.to_string_lossy().replace('\\', "/"),
                    ffprobe: ffprobe.to_string_lossy().replace('\\', "/"),
                    lrail: lrail.to_string_lossy().replace('\\', "/"),
                },
            )?;
            let mut manifest_bytes = serde_json::to_vec_pretty(&manifest)?;
            manifest_bytes.push(b'\n');
            let private_key_hex = LockedString::new(
                fs::read_to_string(&private_key)
                    .with_context(|| format!("unable to read {}", private_key.display()))?,
            )?;
            let signature_result = sign_runtime_manifest(&manifest_bytes, &private_key_hex);
            let signature_bytes = signature_result?;
            let mut manifest_file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&manifest_path)?;
            use std::io::Write;
            manifest_file.write_all(&manifest_bytes)?;
            manifest_file.sync_all()?;
            let mut signature_file = match fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&signature_path)
            {
                Ok(file) => file,
                Err(error) => {
                    let _ = fs::remove_file(&manifest_path);
                    return Err(error.into());
                }
            };
            signature_file.write_all(&signature_bytes)?;
            signature_file.write_all(b"\n")?;
            signature_file.sync_all()?;
            println!(
                "{}",
                serde_json::to_string_pretty(&serde_json::json!({
                    "kind": "lyricrail.runtime.manifest-created",
                    "root": root,
                    "runtimeVersion": runtime_version,
                    "platform": platform,
                    "files": manifest.files.len(),
                    "manifest": manifest_path,
                    "signature": signature_path,
                }))?
            );
        }
        Command::RuntimeVerify {
            root,
            public_key,
            runtime_version,
            platform,
        } => {
            let public_key_hex = fs::read_to_string(&public_key)
                .with_context(|| format!("unable to read {}", public_key.display()))?;
            let report = verify_runtime_pack(
                &root,
                &public_key_hex,
                &runtime_version,
                &platform.unwrap_or_else(runtime_platform),
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
    }
    Ok(())
}

fn main() -> ExitCode {
    let exit_code = match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error:#}");
            ExitCode::FAILURE
        }
    };
    if std::env::var_os("LYRICRAIL_PAUSE_ON_EXIT").as_deref() == Some("1".as_ref()) {
        eprintln!("Press Enter to close this native recovery window.");
        let mut line = String::new();
        let _ = io::stdin().read_line(&mut line);
    }
    exit_code
}
