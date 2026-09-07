use std::path::{Path, PathBuf};

use serde::Serialize;
use sha2::{Digest, Sha256};
use tauri::Emitter;
use tauri::{AppHandle, Manager};

use crate::{CatalogState, ItemLocation, drive_cache, drive_provider, range_cache::RemoteObject};

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecoveryToolLaunch {
    pub process_id: u32,
    pub operation: &'static str,
}

fn canonical_file(path: &Path, extension: &str, label: &str) -> Result<PathBuf, String> {
    let path = path
        .canonicalize()
        .map_err(|error| format!("Unable to open {label}: {error}"))?;
    if !path.is_file()
        || !path
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case(extension))
    {
        return Err(format!("{label} must be a regular .{extension} file"));
    }
    Ok(path)
}

fn new_output(path: &Path, extension: &str) -> Result<PathBuf, String> {
    if !path
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case(extension))
    {
        return Err(format!("Output must use .{extension}"));
    }
    let parent = path
        .parent()
        .ok_or_else(|| "Output has no parent directory".to_string())?
        .canonicalize()
        .map_err(|error| format!("Unable to open output directory: {error}"))?;
    let output = parent.join(
        path.file_name()
            .ok_or_else(|| "Output has no file name".to_string())?,
    );
    if output.exists() {
        return Err("Refusing to overwrite recovery output".into());
    }
    Ok(output)
}

#[cfg(windows)]
fn launch(
    app: AppHandle,
    operation: &'static str,
    arguments: Vec<String>,
) -> Result<RecoveryToolLaunch, String> {
    use std::os::windows::process::CommandExt;
    use std::{process::Command, thread};

    let runtime = crate::runtime::resolve_runtime()?;
    let lrail = runtime
        .lrail
        .ok_or_else(|| "The verified runtime has no native lrail tool".to_string())?;
    let mut child = Command::new(lrail)
        .current_dir(runtime.root)
        .env("LYRICRAIL_PAUSE_ON_EXIT", "1")
        .args(arguments)
        .creation_flags(0x0000_0010)
        .spawn()
        .map_err(|error| format!("Unable to open native recovery window: {error}"))?;
    let process_id = child.id();
    thread::spawn(move || {
        let success = child.wait().is_ok_and(|status| status.success());
        let _ = app.emit(
            "recovery-tool-completed",
            serde_json::json!({"operation": operation, "success": success}),
        );
    });
    Ok(RecoveryToolLaunch {
        process_id,
        operation,
    })
}

#[cfg(target_os = "macos")]
fn launch(
    app: AppHandle,
    operation: &'static str,
    arguments: Vec<String>,
) -> Result<RecoveryToolLaunch, String> {
    use std::{process::Command, thread};

    const SCRIPT: &str = r#"
on run argv
    set commandText to ""
    repeat with argumentText in argv
        set commandText to commandText & quoted form of (contents of argumentText) & " "
    end repeat
    tell application "Terminal"
        activate
        set recoveryTab to do script commandText
        repeat while busy of recoveryTab
            delay 0.2
        end repeat
    end tell
end run
"#;
    let runtime = crate::runtime::resolve_runtime()?;
    let lrail = runtime
        .lrail
        .ok_or_else(|| "The verified runtime has no native lrail tool".to_string())?;
    let mut child = Command::new("/usr/bin/osascript")
        .arg("-e")
        .arg(SCRIPT)
        .arg(lrail)
        .args(arguments)
        .spawn()
        .map_err(|error| format!("Unable to open the macOS recovery terminal: {error}"))?;
    let process_id = child.id();
    thread::spawn(move || {
        let success = child.wait().is_ok_and(|status| status.success());
        let _ = app.emit(
            "recovery-tool-completed",
            serde_json::json!({"operation": operation, "success": success}),
        );
    });
    Ok(RecoveryToolLaunch {
        process_id,
        operation,
    })
}

#[cfg(all(unix, not(target_os = "macos")))]
fn launch(
    app: AppHandle,
    operation: &'static str,
    arguments: Vec<String>,
) -> Result<RecoveryToolLaunch, String> {
    use std::{io::ErrorKind, process::Command, thread};

    let runtime = crate::runtime::resolve_runtime()?;
    let lrail = runtime
        .lrail
        .ok_or_else(|| "The verified runtime has no native lrail tool".to_string())?;
    let candidates: [(&str, &[&str]); 4] = [
        ("/usr/bin/x-terminal-emulator", &["-e"]),
        ("/usr/bin/gnome-terminal", &["--wait", "--"]),
        ("/usr/bin/konsole", &["-e"]),
        ("/usr/bin/xterm", &["-e"]),
    ];
    let mut last_error = None;
    for (terminal, prefix) in candidates {
        match Command::new(terminal)
            .args(prefix)
            .arg(&lrail)
            .args(&arguments)
            .spawn()
        {
            Ok(mut child) => {
                let process_id = child.id();
                thread::spawn(move || {
                    let success = child.wait().is_ok_and(|status| status.success());
                    let _ = app.emit(
                        "recovery-tool-completed",
                        serde_json::json!({"operation": operation, "success": success}),
                    );
                });
                return Ok(RecoveryToolLaunch {
                    process_id,
                    operation,
                });
            }
            Err(error) if error.kind() == ErrorKind::NotFound => last_error = Some(error),
            Err(error) => {
                return Err(format!(
                    "Unable to open the Linux recovery terminal: {error}"
                ));
            }
        }
    }
    Err(format!(
        "No supported native Linux terminal is installed for the recovery prompt{}",
        last_error
            .map(|error| format!(": {error}"))
            .unwrap_or_default()
    ))
}

pub fn export(app: AppHandle, output: PathBuf) -> Result<RecoveryToolLaunch, String> {
    let output = new_output(&output, "lrail-recovery")?;
    launch(
        app,
        "export",
        vec![
            "recovery-export".into(),
            "--output".into(),
            output.to_string_lossy().into_owned(),
        ],
    )
}

pub fn restore_local(
    app: AppHandle,
    bundle: PathBuf,
    library: PathBuf,
) -> Result<RecoveryToolLaunch, String> {
    let bundle = canonical_file(&bundle, "lrail-recovery", "recovery bundle")?;
    let library = library
        .canonicalize()
        .map_err(|error| format!("Unable to open package library: {error}"))?;
    if !library.is_dir() {
        return Err("Recovery library must be a directory".into());
    }
    launch(
        app,
        "restore",
        vec![
            "recovery-restore".into(),
            bundle.to_string_lossy().into_owned(),
            "--library".into(),
            library.to_string_lossy().into_owned(),
        ],
    )
}

pub fn restore_cloud(
    app: AppHandle,
    bundle: PathBuf,
    item_id: String,
) -> Result<RecoveryToolLaunch, String> {
    let bundle = canonical_file(&bundle, "lrail-recovery", "recovery bundle")?;
    let item = app
        .state::<CatalogState>()
        .0
        .lock()
        .map_err(|_| "Catalog lock is poisoned".to_string())?
        .item(&item_id)
        .cloned()
        .ok_or_else(|| "Select one Drive package for recovery verification".to_string())?;
    let (file_id, size, version) = item
        .locations
        .iter()
        .find_map(|location| match location {
            ItemLocation::GoogleDrive {
                file_id,
                size,
                version,
                ..
            } => Some((file_id.clone(), *size, version.clone())),
            _ => None,
        })
        .ok_or_else(|| "Recovery verification requires a Drive package".to_string())?;
    let provider = drive_provider(&app)?;
    let cache = drive_cache(&app, provider)?;
    let object = RemoteObject {
        cache_key: format!("google-drive:{file_id}"),
        length: size,
        version: version.clone(),
    };
    let mut digest = Sha256::new();
    digest.update(file_id.as_bytes());
    digest.update([0]);
    digest.update(version.as_bytes());
    let directory = app
        .path()
        .app_cache_dir()
        .map_err(|error| error.to_string())?
        .join("recovery-library")
        .join(hex::encode(digest.finalize()));
    std::fs::create_dir_all(&directory)
        .map_err(|error| format!("Unable to create recovery library: {error}"))?;
    let package = directory.join("verification.lrail");
    if !package.is_file() {
        cache.materialize(&object, &package)?;
    }
    restore_local(app, bundle, directory)
}
