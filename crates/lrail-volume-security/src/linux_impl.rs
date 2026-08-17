use std::{
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
    time::Duration,
};

use crate::{
    VolumeProtectionState, VolumeSecurityError, VolumeSecurityEvidence, resolve_existing_path,
};

#[derive(Debug, Clone, PartialEq, Eq)]
struct MountRecord {
    major_minor: String,
    mount_point: PathBuf,
    fs_type: String,
    source: String,
}

pub fn inspect_encrypted_volume(
    path: &Path,
    _timeout: Duration,
) -> Result<VolumeSecurityEvidence, VolumeSecurityError> {
    let canonical = resolve_existing_path(path)?;
    let mountinfo = fs::read_to_string("/proc/self/mountinfo")
        .map_err(|error| VolumeSecurityError::PlatformQuery(error.to_string()))?;
    let mount = select_mount(&mountinfo, &canonical)?;
    let sysfs = PathBuf::from("/sys/dev/block").join(&mount.major_minor);
    let root = sysfs
        .canonicalize()
        .map_err(|_| VolumeSecurityError::UnsupportedVolume)?;
    let mut visited = HashSet::new();
    let encrypted = has_dm_crypt_ancestor(&root, &mut visited)?;
    let state = if encrypted {
        VolumeProtectionState::Protected
    } else {
        VolumeProtectionState::Unprotected
    };
    let detail = if encrypted {
        "The mounted workspace block-device chain contains an active dm-crypt/LUKS mapping."
            .to_owned()
    } else {
        "No active dm-crypt/LUKS mapping was found in the mounted workspace block-device chain."
            .to_owned()
    };
    Ok(VolumeSecurityEvidence {
        state,
        volume: Some(format!(
            "{} ({} on {}, {})",
            mount.major_minor,
            mount.source,
            mount.mount_point.display(),
            mount.fs_type
        )),
        detail,
    })
}

fn select_mount(input: &str, path: &Path) -> Result<MountRecord, VolumeSecurityError> {
    parse_mountinfo(input)
        .into_iter()
        .filter(|mount| path == mount.mount_point || path.starts_with(&mount.mount_point))
        .max_by_key(|mount| mount.mount_point.as_os_str().len())
        .ok_or_else(|| {
            VolumeSecurityError::MalformedEvidence(
                "no mountinfo entry contains the workspace path".to_owned(),
            )
        })
}

fn parse_mountinfo(input: &str) -> Vec<MountRecord> {
    input.lines().filter_map(parse_mount_line).collect()
}

fn parse_mount_line(line: &str) -> Option<MountRecord> {
    let (prefix, suffix) = line.split_once(" - ")?;
    let mut prefix_fields = prefix.split_ascii_whitespace();
    prefix_fields.next()?;
    prefix_fields.next()?;
    let major_minor = prefix_fields.next()?.to_owned();
    let (major, minor) = major_minor.split_once(':')?;
    if major.is_empty()
        || minor.is_empty()
        || !major.bytes().all(|byte| byte.is_ascii_digit())
        || !minor.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    prefix_fields.next()?;
    let mount_point = PathBuf::from(unescape_mountinfo(prefix_fields.next()?)?);
    let mut suffix_fields = suffix.split_ascii_whitespace();
    let fs_type = suffix_fields.next()?.to_owned();
    let source = unescape_mountinfo(suffix_fields.next()?)?;
    Some(MountRecord {
        major_minor,
        mount_point,
        fs_type,
        source,
    })
}

fn unescape_mountinfo(value: &str) -> Option<String> {
    let bytes = value.as_bytes();
    let mut output = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'\\' {
            if index + 3 >= bytes.len()
                || !bytes[index + 1..=index + 3]
                    .iter()
                    .all(|byte| matches!(byte, b'0'..=b'7'))
            {
                return None;
            }
            let value = (bytes[index + 1] - b'0') * 64
                + (bytes[index + 2] - b'0') * 8
                + (bytes[index + 3] - b'0');
            output.push(value);
            index += 4;
        } else {
            output.push(bytes[index]);
            index += 1;
        }
    }
    String::from_utf8(output).ok()
}

fn has_dm_crypt_ancestor(
    device: &Path,
    visited: &mut HashSet<PathBuf>,
) -> Result<bool, VolumeSecurityError> {
    let canonical = device
        .canonicalize()
        .map_err(|error| VolumeSecurityError::PlatformQuery(error.to_string()))?;
    if !visited.insert(canonical.clone()) {
        return Ok(false);
    }
    let uuid_path = canonical.join("dm/uuid");
    if let Ok(uuid) = fs::read_to_string(uuid_path) {
        let uuid = uuid.trim().to_ascii_uppercase();
        if uuid.starts_with("CRYPT-LUKS") || uuid.starts_with("CRYPT-PLAIN") {
            return Ok(true);
        }
    }
    let slaves = canonical.join("slaves");
    let entries = match fs::read_dir(slaves) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(VolumeSecurityError::PlatformQuery(error.to_string())),
    };
    for entry in entries {
        let entry = entry.map_err(|error| VolumeSecurityError::PlatformQuery(error.to_string()))?;
        if has_dm_crypt_ancestor(&entry.path(), visited)? {
            return Ok(true);
        }
    }
    Ok(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    const SAMPLE: &str = concat!(
        "31 23 0:27 / / rw,relatime - ext4 /dev/mapper/root rw\n",
        "42 31 253:2 / /work\\040space rw,relatime - ext4 /dev/dm-2 rw\n",
        "43 31 8:1 / /work rw,relatime - ext4 /dev/sda1 rw\n",
    );

    #[test]
    fn parser_decodes_mountinfo_and_uses_longest_mount() {
        let selected = select_mount(SAMPLE, Path::new("/work space/project")).unwrap();
        assert_eq!(selected.major_minor, "253:2");
        assert_eq!(selected.mount_point, Path::new("/work space"));
    }

    #[test]
    fn malformed_escape_is_rejected_without_guessing() {
        assert!(parse_mount_line("1 2 8:1 / /bad\\04 rw - ext4 /dev/sda1 rw").is_none());
    }

    #[test]
    fn detects_nested_crypt_mapping_below_an_lvm_device() {
        let temporary = tempfile::tempdir().unwrap();
        let lvm = temporary.path().join("dm-1");
        let crypt = temporary.path().join("dm-0");
        fs::create_dir_all(lvm.join("slaves")).unwrap();
        fs::create_dir_all(crypt.join("dm")).unwrap();
        fs::create_dir_all(crypt.join("slaves")).unwrap();
        fs::write(crypt.join("dm/uuid"), "CRYPT-LUKS2-deadbeef-root\n").unwrap();
        symlink(&crypt, lvm.join("slaves/dm-0")).unwrap();

        assert!(has_dm_crypt_ancestor(&lvm, &mut HashSet::new()).unwrap());
    }

    #[test]
    fn ordinary_block_chain_never_becomes_protected_by_guessing() {
        let temporary = tempfile::tempdir().unwrap();
        let lvm = temporary.path().join("dm-1");
        let disk = temporary.path().join("sda1");
        fs::create_dir_all(lvm.join("dm")).unwrap();
        fs::create_dir_all(lvm.join("slaves")).unwrap();
        fs::create_dir_all(disk.join("slaves")).unwrap();
        fs::write(lvm.join("dm/uuid"), "LVM-deadbeef\n").unwrap();
        symlink(&disk, lvm.join("slaves/sda1")).unwrap();

        assert!(!has_dm_crypt_ancestor(&lvm, &mut HashSet::new()).unwrap());
    }
}
