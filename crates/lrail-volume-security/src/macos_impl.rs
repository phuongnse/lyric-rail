use std::{
    io::{Cursor, Read},
    path::Path,
    process::{Command, Output, Stdio},
    thread,
    time::{Duration, Instant},
};

use plist::{Dictionary, Value};

use crate::{
    VolumeProtectionState, VolumeSecurityError, VolumeSecurityEvidence, resolve_existing_path,
};

const DISKUTIL: &str = "/usr/sbin/diskutil";

pub fn inspect_encrypted_volume(
    path: &Path,
    timeout: Duration,
) -> Result<VolumeSecurityEvidence, VolumeSecurityError> {
    let canonical = resolve_existing_path(path)?;
    let info_output = run_bounded(
        Command::new(DISKUTIL)
            .arg("info")
            .arg("-plist")
            .arg(&canonical),
        timeout,
    )?;
    let info = parse_plist(&info_output, "diskutil info")?;
    let info_dict = info.as_dictionary().ok_or_else(|| {
        VolumeSecurityError::MalformedEvidence("diskutil info was not a dictionary".to_owned())
    })?;
    let device = string_field(info_dict, "DeviceIdentifier")
        .ok_or_else(|| {
            VolumeSecurityError::MalformedEvidence(
                "diskutil info omitted DeviceIdentifier".to_owned(),
            )
        })?
        .to_owned();

    let (file_vault, migrating) = if bool_field(info_dict, "FileVault").is_some() {
        (
            bool_field(info_dict, "FileVault"),
            bool_field(info_dict, "CryptoMigrationOn").unwrap_or(false),
        )
    } else {
        let list_output = run_bounded(
            Command::new(DISKUTIL).arg("apfs").arg("list").arg("-plist"),
            timeout,
        )?;
        let list = parse_plist(&list_output, "diskutil apfs list")?;
        let evidence_dict = find_device_dictionary(&list, &device).ok_or_else(|| {
            VolumeSecurityError::MalformedEvidence(format!(
                "diskutil APFS inventory did not contain {device}"
            ))
        })?;
        (
            bool_field(evidence_dict, "FileVault"),
            bool_field(evidence_dict, "CryptoMigrationOn").unwrap_or(false),
        )
    };

    let state = if migrating {
        VolumeProtectionState::Unknown
    } else {
        match file_vault {
            Some(true) => VolumeProtectionState::Protected,
            Some(false) => VolumeProtectionState::Unprotected,
            None => VolumeProtectionState::Unknown,
        }
    };
    let detail = match state {
        VolumeProtectionState::Protected => {
            "FileVault is active for the APFS volume containing the workspace.".to_owned()
        }
        VolumeProtectionState::Unprotected => {
            "FileVault is not active for the APFS volume containing the workspace.".to_owned()
        }
        VolumeProtectionState::Unknown if migrating => {
            "APFS reports an active crypto migration; full FileVault protection cannot be confirmed."
                .to_owned()
        }
        VolumeProtectionState::Unknown => {
            "APFS did not provide a canonical FileVault status for the workspace volume."
                .to_owned()
        }
    };
    Ok(VolumeSecurityEvidence {
        state,
        volume: Some(device),
        detail,
    })
}

fn run_bounded(command: &mut Command, timeout: Duration) -> Result<Output, VolumeSecurityError> {
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| VolumeSecurityError::PlatformQuery(error.to_string()))?;
    let stdout = child.stdout.take().ok_or_else(|| {
        VolumeSecurityError::PlatformQuery("diskutil stdout pipe was not created".to_owned())
    })?;
    let stderr = child.stderr.take().ok_or_else(|| {
        VolumeSecurityError::PlatformQuery("diskutil stderr pipe was not created".to_owned())
    })?;
    let stdout_reader = thread::spawn(move || read_pipe(stdout));
    let stderr_reader = thread::spawn(move || read_pipe(stderr));
    let deadline = Instant::now() + timeout.max(Duration::from_millis(1));
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let output = Output {
                    status,
                    stdout: join_pipe(stdout_reader, "stdout")?,
                    stderr: join_pipe(stderr_reader, "stderr")?,
                };
                if !output.status.success() {
                    return Err(VolumeSecurityError::PlatformQuery(format!(
                        "diskutil exited with {}: {}",
                        output.status,
                        String::from_utf8_lossy(&output.stderr).trim()
                    )));
                }
                return Ok(output);
            }
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(VolumeSecurityError::Timeout);
            }
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(VolumeSecurityError::PlatformQuery(error.to_string()));
            }
        }
    }
}

fn read_pipe(mut pipe: impl Read) -> std::io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    pipe.read_to_end(&mut bytes)?;
    Ok(bytes)
}

fn join_pipe(
    reader: thread::JoinHandle<std::io::Result<Vec<u8>>>,
    label: &str,
) -> Result<Vec<u8>, VolumeSecurityError> {
    reader
        .join()
        .map_err(|_| {
            VolumeSecurityError::PlatformQuery(format!("diskutil {label} reader panicked"))
        })?
        .map_err(|error| {
            VolumeSecurityError::PlatformQuery(format!("cannot read diskutil {label}: {error}"))
        })
}

fn parse_plist(output: &Output, operation: &str) -> Result<Value, VolumeSecurityError> {
    Value::from_reader(Cursor::new(&output.stdout)).map_err(|error| {
        VolumeSecurityError::MalformedEvidence(format!("{operation} plist is invalid: {error}"))
    })
}

fn string_field<'a>(dictionary: &'a Dictionary, key: &str) -> Option<&'a str> {
    dictionary.get(key).and_then(Value::as_string)
}

fn bool_field(dictionary: &Dictionary, key: &str) -> Option<bool> {
    dictionary.get(key).and_then(Value::as_boolean)
}

fn find_device_dictionary<'a>(value: &'a Value, device: &str) -> Option<&'a Dictionary> {
    match value {
        Value::Dictionary(dictionary) => {
            if string_field(dictionary, "DeviceIdentifier") == Some(device) {
                return Some(dictionary);
            }
            dictionary
                .values()
                .find_map(|value| find_device_dictionary(value, device))
        }
        Value::Array(values) => values
            .iter()
            .find_map(|value| find_device_dictionary(value, device)),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_nested_apfs_volume_by_exact_device_identifier() {
        let fixture = Value::Dictionary(Dictionary::from_iter([(
            "Containers".to_owned(),
            Value::Array(vec![Value::Dictionary(Dictionary::from_iter([(
                "Volumes".to_owned(),
                Value::Array(vec![Value::Dictionary(Dictionary::from_iter([
                    (
                        "DeviceIdentifier".to_owned(),
                        Value::String("disk3s5".to_owned()),
                    ),
                    ("FileVault".to_owned(), Value::Boolean(true)),
                ]))]),
            )]))]),
        )]));
        let found = find_device_dictionary(&fixture, "disk3s5").expect("volume");
        assert_eq!(bool_field(found, "FileVault"), Some(true));
        assert!(find_device_dictionary(&fixture, "disk3s1").is_none());
    }
}
