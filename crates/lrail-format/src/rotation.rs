use std::{
    collections::HashSet,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::{
    Error, LockedSecret, Result,
    crypto::KEY_BYTES,
    inspect_package, rewrap_package_for_vaults,
    vault::{
        ROTATION_NEW_ACCOUNT, ROTATION_OLD_ACCOUNT, VAULT_ACCOUNT, acquire_vault_operation_lock,
        delete_vault_account, load_vault_account, set_vault_account,
    },
    verify_package_with_vault,
};

const JOURNAL_SCHEMA_VERSION: u16 = 1;
const ACTIVE_JOURNAL_NAME: &str = "library-master-rotation-v1.jsonl";
const MAX_JOURNAL_BYTES: u64 = 64 * 1024 * 1024;
const MAX_LIBRARY_PACKAGES: usize = 10_000;
const MAX_LIBRARY_DEPTH: usize = 64;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RotationReport {
    pub rotation_id: Uuid,
    pub library_root: PathBuf,
    pub package_count: usize,
    pub resumed: bool,
    pub dual_wrapped_packages: usize,
    pub new_only_packages: usize,
    pub archived_journal: PathBuf,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RotationStatus {
    pub rotation_id: Uuid,
    pub library_root: PathBuf,
    pub package_count: usize,
    pub dual_wrapped_packages: usize,
    pub new_only_packages: usize,
    pub current_key_switched: bool,
    pub packages_verified_with_new_key: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PackageRecord {
    relative_components: Vec<String>,
    package_id: Uuid,
    non_vault_mechanisms: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "event", rename_all = "kebab-case", deny_unknown_fields)]
enum JournalEvent {
    Started {
        schema_version: u16,
        rotation_id: Uuid,
        library_root: String,
        old_key_fingerprint: String,
        new_key_fingerprint: String,
        packages: Vec<PackageRecord>,
    },
    DualCommitted {
        rotation_id: Uuid,
        package_index: usize,
        package_id: Uuid,
    },
    CurrentSwitched {
        rotation_id: Uuid,
    },
    NewOnlyCommitted {
        rotation_id: Uuid,
        package_index: usize,
        package_id: Uuid,
    },
    PackagesVerifiedWithNewKey {
        rotation_id: Uuid,
    },
}

struct JournalState {
    rotation_id: Uuid,
    library_root: PathBuf,
    old_key_fingerprint: String,
    new_key_fingerprint: String,
    packages: Vec<PackageRecord>,
    dual: HashSet<usize>,
    new_only: HashSet<usize>,
    switched: bool,
    completed: bool,
}

trait KeyStore {
    fn load(&self, account: &str) -> Result<Option<LockedSecret<KEY_BYTES>>>;
    fn set(&self, account: &str, key: &[u8; KEY_BYTES]) -> Result<()>;
    fn delete(&self, account: &str) -> Result<()>;
}

struct OsKeyStore;

impl KeyStore for OsKeyStore {
    fn load(&self, account: &str) -> Result<Option<LockedSecret<KEY_BYTES>>> {
        load_vault_account(account)
    }

    fn set(&self, account: &str, key: &[u8; KEY_BYTES]) -> Result<()> {
        set_vault_account(account, key)
    }

    fn delete(&self, account: &str) -> Result<()> {
        delete_vault_account(account)
    }
}

trait RotationObserver {
    fn checkpoint(&mut self, _name: &'static str) -> Result<()> {
        Ok(())
    }
}

struct NoopObserver;
impl RotationObserver for NoopObserver {}

fn rotation_error(message: impl Into<String>) -> Error {
    Error::Rotation(message.into())
}

fn key_fingerprint(key: &[u8; KEY_BYTES]) -> String {
    hex::encode(Sha256::digest(key))
}

fn key_matches(key: &LockedSecret<KEY_BYTES>, fingerprint: &str) -> bool {
    key_fingerprint(key) == fingerprint
}

fn ensure_state_directory(path: &Path) -> Result<PathBuf> {
    fs::create_dir_all(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    let canonical = path.canonicalize()?;
    if !canonical.is_dir() {
        return Err(rotation_error("rotation state path is not a directory"));
    }
    Ok(canonical)
}

fn canonical_library_root(path: &Path) -> Result<PathBuf> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(rotation_error(
            "library root must be a real directory, not a symlink",
        ));
    }
    let root = path.canonicalize()?;
    if root.to_str().is_none() {
        return Err(rotation_error("library root is not valid UTF-8"));
    }
    Ok(root)
}

fn validate_component(component: &str) -> Result<()> {
    if component.is_empty()
        || component == "."
        || component == ".."
        || component.contains(['/', '\\', '\0'])
    {
        return Err(rotation_error("unsafe package path component in journal"));
    }
    Ok(())
}

fn walk_library(
    root: &Path,
    directory: &Path,
    components: &mut Vec<String>,
    records: &mut Vec<PackageRecord>,
    verification_key: Option<&[u8; KEY_BYTES]>,
) -> Result<()> {
    if components.len() > MAX_LIBRARY_DEPTH {
        return Err(rotation_error(
            "library directory nesting exceeds the limit",
        ));
    }
    let mut entries = fs::read_dir(directory)?.collect::<std::io::Result<Vec<_>>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| rotation_error("library contains a non-UTF-8 path"))?;
        validate_component(&name)?;
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path)?;
        if metadata.file_type().is_symlink() {
            return Err(rotation_error(format!(
                "library contains a symlink or junction: {}",
                path.display()
            )));
        }
        if metadata.is_dir() {
            components.push(name);
            walk_library(root, &path, components, records, verification_key)?;
            components.pop();
            continue;
        }
        if !metadata.is_file()
            || path
                .extension()
                .and_then(|value| value.to_str())
                .is_none_or(|value| !value.eq_ignore_ascii_case("lrail"))
        {
            continue;
        }
        if records.len() >= MAX_LIBRARY_PACKAGES {
            return Err(rotation_error(format!(
                "library exceeds the {MAX_LIBRARY_PACKAGES} package rotation limit"
            )));
        }
        let canonical = path.canonicalize()?;
        if !canonical.starts_with(root) {
            return Err(rotation_error("package resolves outside the library root"));
        }
        let inspection = inspect_package(&canonical)?;
        if let Some(key) = verification_key {
            verify_package_with_vault(&canonical, key)?;
        }
        let mut non_vault_mechanisms = inspection
            .key_mechanisms
            .into_iter()
            .filter(|mechanism| mechanism != "os-vault-v1")
            .collect::<Vec<_>>();
        non_vault_mechanisms.sort();
        let mut relative_components = components.clone();
        relative_components.push(name);
        records.push(PackageRecord {
            relative_components,
            package_id: inspection.package_id,
            non_vault_mechanisms,
        });
    }
    Ok(())
}

fn discover_library(
    root: &Path,
    verification_key: Option<&[u8; KEY_BYTES]>,
) -> Result<Vec<PackageRecord>> {
    let mut records = Vec::new();
    walk_library(root, root, &mut Vec::new(), &mut records, verification_key)?;
    records.sort_by(|left, right| left.relative_components.cmp(&right.relative_components));
    Ok(records)
}

pub(crate) fn verify_library_for_key(
    root: &Path,
    key: &[u8; KEY_BYTES],
) -> Result<(PathBuf, usize)> {
    let root = canonical_library_root(root)?;
    let packages = discover_library(&root, Some(key))?;
    Ok((root, packages.len()))
}

fn record_path(root: &Path, record: &PackageRecord) -> Result<PathBuf> {
    let mut path = root.to_path_buf();
    for component in &record.relative_components {
        validate_component(component)?;
        path.push(component);
    }
    if path.components().any(|component| {
        !matches!(
            component,
            Component::Prefix(_) | Component::RootDir | Component::Normal(_)
        )
    }) {
        return Err(rotation_error("journal package path is not normalized"));
    }
    Ok(path)
}

fn validate_inventory(root: &Path, expected: &[PackageRecord]) -> Result<()> {
    let actual = discover_library(root, None)?;
    if actual != expected {
        return Err(rotation_error(
            "the .lrail library inventory changed during rotation; restore the original set before resuming",
        ));
    }
    Ok(())
}

fn serialize_event(event: &JournalEvent) -> Result<Vec<u8>> {
    let mut bytes = serde_json::to_vec(event)?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn create_journal(path: &Path, event: &JournalEvent) -> Result<()> {
    let bytes = serialize_event(event)?;
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    Ok(())
}

fn append_event(path: &Path, event: &JournalEvent) -> Result<()> {
    let bytes = serialize_event(event)?;
    let mut file = OpenOptions::new().append(true).open(path)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    Ok(())
}

fn load_journal(path: &Path) -> Result<JournalState> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() == 0
        || metadata.len() > MAX_JOURNAL_BYTES
    {
        return Err(rotation_error("active rotation journal has invalid bounds"));
    }
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    File::open(path)?.read_to_end(&mut bytes)?;
    let complete_length = bytes
        .iter()
        .rposition(|byte| *byte == b'\n')
        .map(|index| index + 1)
        .ok_or_else(|| rotation_error("active rotation journal has no durable event"))?;
    let complete = std::str::from_utf8(&bytes[..complete_length])
        .map_err(|_| rotation_error("active rotation journal is not UTF-8"))?;
    let mut events = complete
        .lines()
        .map(|line| {
            if line.is_empty() {
                return Err(rotation_error(
                    "active rotation journal contains an empty event",
                ));
            }
            serde_json::from_str::<JournalEvent>(line).map_err(Error::from)
        })
        .collect::<Result<Vec<_>>>()?;
    let first = events
        .first()
        .cloned()
        .ok_or_else(|| rotation_error("active rotation journal is empty"))?;
    let JournalEvent::Started {
        schema_version,
        rotation_id,
        library_root,
        old_key_fingerprint,
        new_key_fingerprint,
        packages,
    } = first
    else {
        return Err(rotation_error(
            "rotation journal does not begin with started",
        ));
    };
    if schema_version != JOURNAL_SCHEMA_VERSION
        || old_key_fingerprint.len() != 64
        || new_key_fingerprint.len() != 64
        || old_key_fingerprint == new_key_fingerprint
        || packages.len() > MAX_LIBRARY_PACKAGES
    {
        return Err(rotation_error("rotation journal start event is invalid"));
    }
    for record in &packages {
        if record.relative_components.is_empty() {
            return Err(rotation_error(
                "rotation journal contains an empty package path",
            ));
        }
        for component in &record.relative_components {
            validate_component(component)?;
        }
    }
    let mut state = JournalState {
        rotation_id,
        library_root: PathBuf::from(library_root),
        old_key_fingerprint,
        new_key_fingerprint,
        packages,
        dual: HashSet::new(),
        new_only: HashSet::new(),
        switched: false,
        completed: false,
    };
    events.remove(0);
    for event in events {
        match event {
            JournalEvent::Started { .. } => {
                return Err(rotation_error("rotation journal has multiple start events"));
            }
            JournalEvent::DualCommitted {
                rotation_id,
                package_index,
                package_id,
            } => {
                if rotation_id != state.rotation_id
                    || state.switched
                    || state.completed
                    || state
                        .packages
                        .get(package_index)
                        .is_none_or(|record| record.package_id != package_id)
                    || !state.dual.insert(package_index)
                {
                    return Err(rotation_error("invalid dual-commit journal sequence"));
                }
            }
            JournalEvent::CurrentSwitched { rotation_id } => {
                if rotation_id != state.rotation_id
                    || state.switched
                    || state.completed
                    || state.dual.len() != state.packages.len()
                {
                    return Err(rotation_error("invalid current-switch journal sequence"));
                }
                state.switched = true;
            }
            JournalEvent::NewOnlyCommitted {
                rotation_id,
                package_index,
                package_id,
            } => {
                if rotation_id != state.rotation_id
                    || !state.switched
                    || state.completed
                    || !state.dual.contains(&package_index)
                    || state
                        .packages
                        .get(package_index)
                        .is_none_or(|record| record.package_id != package_id)
                    || !state.new_only.insert(package_index)
                {
                    return Err(rotation_error("invalid new-only journal sequence"));
                }
            }
            JournalEvent::PackagesVerifiedWithNewKey { rotation_id } => {
                if rotation_id != state.rotation_id
                    || !state.switched
                    || state.completed
                    || state.new_only.len() != state.packages.len()
                {
                    return Err(rotation_error("invalid completion journal sequence"));
                }
                state.completed = true;
            }
        }
    }
    Ok(state)
}

fn journal_has_no_durable_event(path: &Path) -> Result<bool> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() > MAX_JOURNAL_BYTES
    {
        return Err(rotation_error("active rotation journal has invalid bounds"));
    }
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    File::open(path)?.read_to_end(&mut bytes)?;
    Ok(!bytes.contains(&b'\n'))
}

fn sidecar_paths(path: &Path, rotation_id: Uuid, index: usize) -> Result<(PathBuf, PathBuf)> {
    let parent = path
        .parent()
        .ok_or_else(|| rotation_error("package has no parent directory"))?;
    let stem = format!(".lyricrail-rotate-{rotation_id}-{index}");
    Ok((
        parent.join(format!("{stem}.next")),
        parent.join(format!("{stem}.backup")),
    ))
}

fn safe_remove_sidecar(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() || !metadata.is_file() {
                return Err(rotation_error(format!(
                    "rotation sidecar is not a regular file: {}",
                    path.display()
                )));
            }
            fs::remove_file(path)?;
            Ok(())
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

fn package_matches_profile(
    path: &Path,
    record: &PackageRecord,
    target_keys: &[&[u8; KEY_BYTES]],
) -> Result<bool> {
    let inspection = inspect_package(path)?;
    if inspection.package_id != record.package_id {
        return Err(rotation_error(format!(
            "package identity changed at {}",
            path.display()
        )));
    }
    let vault_slots = inspection
        .key_mechanisms
        .iter()
        .filter(|mechanism| mechanism.as_str() == "os-vault-v1")
        .count();
    let mut non_vault = inspection
        .key_mechanisms
        .into_iter()
        .filter(|mechanism| mechanism != "os-vault-v1")
        .collect::<Vec<_>>();
    non_vault.sort();
    if vault_slots != target_keys.len() || non_vault != record.non_vault_mechanisms {
        return Ok(false);
    }
    for key in target_keys {
        match verify_package_with_vault(path, key) {
            Ok(_) => {}
            Err(Error::KeyUnwrap) => return Ok(false),
            Err(error) => return Err(error),
        }
    }
    Ok(true)
}

fn verify_opening_package(
    path: &Path,
    record: &PackageRecord,
    opening_keys: &[&[u8; KEY_BYTES]],
) -> Result<()> {
    let inspection = inspect_package(path)?;
    if inspection.package_id != record.package_id {
        return Err(rotation_error(format!(
            "package identity changed at {}",
            path.display()
        )));
    }
    crate::verify_package_with_vault_candidates(path, opening_keys)?;
    Ok(())
}

#[cfg(windows)]
fn commit_replacement(original: &Path, replacement: &Path, backup: &Path) -> Result<()> {
    use std::{os::windows::ffi::OsStrExt, ptr};
    use windows_sys::Win32::Storage::FileSystem::ReplaceFileW;

    let wide = |path: &Path| {
        path.as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>()
    };
    let original = wide(original);
    let replacement = wide(replacement);
    let backup = wide(backup);
    // SAFETY: all path buffers are NUL-terminated and live for the call. Null
    // exclude/preserved pointers are explicitly supported by ReplaceFileW.
    let replaced = unsafe {
        ReplaceFileW(
            original.as_ptr(),
            replacement.as_ptr(),
            backup.as_ptr(),
            0,
            ptr::null(),
            ptr::null(),
        )
    };
    if replaced == 0 {
        return Err(Error::Io(std::io::Error::last_os_error()));
    }
    Ok(())
}

#[cfg(unix)]
fn commit_replacement(original: &Path, replacement: &Path, backup: &Path) -> Result<()> {
    fs::hard_link(original, backup)?;
    if let Err(error) = fs::rename(replacement, original) {
        let _ = safe_remove_sidecar(backup);
        return Err(error.into());
    }
    Ok(())
}

fn append_transition_event(
    journal: &Path,
    state: &mut JournalState,
    index: usize,
    new_only: bool,
) -> Result<()> {
    let package_id = state.packages[index].package_id;
    let event = if new_only {
        JournalEvent::NewOnlyCommitted {
            rotation_id: state.rotation_id,
            package_index: index,
            package_id,
        }
    } else {
        JournalEvent::DualCommitted {
            rotation_id: state.rotation_id,
            package_index: index,
            package_id,
        }
    };
    append_event(journal, &event)?;
    if new_only {
        state.new_only.insert(index);
    } else {
        state.dual.insert(index);
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn transition_package(
    root: &Path,
    journal: &Path,
    state: &mut JournalState,
    index: usize,
    opening_keys: &[&[u8; KEY_BYTES]],
    target_keys: &[&[u8; KEY_BYTES]],
    new_only: bool,
    observer: &mut dyn RotationObserver,
) -> Result<()> {
    let committed = if new_only {
        state.new_only.contains(&index)
    } else {
        state.dual.contains(&index)
    };
    let record = state
        .packages
        .get(index)
        .cloned()
        .ok_or_else(|| rotation_error("journal package index is out of bounds"))?;
    let original = record_path(root, &record)?;
    let (next, backup) = sidecar_paths(&original, state.rotation_id, index)?;

    if committed {
        if !package_matches_profile(&original, &record, target_keys)? {
            return Err(rotation_error(format!(
                "journal says package transition committed but package profile differs: {}",
                original.display()
            )));
        }
        safe_remove_sidecar(&next)?;
        safe_remove_sidecar(&backup)?;
        return Ok(());
    }

    let original_exists = original.try_exists()?;
    let backup_exists = backup.try_exists()?;
    if !original_exists {
        if !backup_exists {
            return Err(rotation_error(format!(
                "package and recovery backup are both missing: {}",
                original.display()
            )));
        }
        fs::rename(&backup, &original)?;
    } else if backup_exists {
        if package_matches_profile(&original, &record, target_keys)? {
            append_transition_event(journal, state, index, new_only)?;
            observer.checkpoint("after-transition-event-before-cleanup")?;
            safe_remove_sidecar(&next)?;
            safe_remove_sidecar(&backup)?;
            return Ok(());
        }
        verify_opening_package(&original, &record, opening_keys)?;
        verify_opening_package(&backup, &record, opening_keys)?;
        safe_remove_sidecar(&backup)?;
    }

    if package_matches_profile(&original, &record, target_keys)? {
        append_transition_event(journal, state, index, new_only)?;
        observer.checkpoint("after-transition-event-before-cleanup")?;
        safe_remove_sidecar(&next)?;
        safe_remove_sidecar(&backup)?;
        return Ok(());
    }
    verify_opening_package(&original, &record, opening_keys)?;

    if next.try_exists()? && !package_matches_profile(&next, &record, target_keys)? {
        safe_remove_sidecar(&next)?;
    }
    if !next.try_exists()? {
        rewrap_package_for_vaults(&original, &next, opening_keys, target_keys)?;
    }
    if !package_matches_profile(&next, &record, target_keys)? {
        return Err(rotation_error(format!(
            "verified replacement does not match the requested key profile: {}",
            next.display()
        )));
    }
    observer.checkpoint("after-replacement-verified-before-commit")?;
    commit_replacement(&original, &next, &backup)?;
    observer.checkpoint("after-replacement-commit-before-event")?;
    if !package_matches_profile(&original, &record, target_keys)? {
        return Err(rotation_error(format!(
            "committed package failed post-replacement verification: {}",
            original.display()
        )));
    }
    append_transition_event(journal, state, index, new_only)?;
    observer.checkpoint("after-transition-event-before-cleanup")?;
    safe_remove_sidecar(&next)?;
    safe_remove_sidecar(&backup)?;
    Ok(())
}

fn resolve_rotation_keys(
    store: &dyn KeyStore,
    state: &JournalState,
) -> Result<(Option<LockedSecret<KEY_BYTES>>, LockedSecret<KEY_BYTES>)> {
    let mut current = store.load(VAULT_ACCOUNT)?;
    let old = store.load(ROTATION_OLD_ACCOUNT)?;
    let pending_new = store.load(ROTATION_NEW_ACCOUNT)?;

    let old = match old {
        Some(key) if key_matches(&key, &state.old_key_fingerprint) => Some(key),
        Some(_) => {
            return Err(rotation_error(
                "rotation-old vault account has the wrong key",
            ));
        }
        None if !state.switched && !state.completed => {
            return Err(rotation_error("rotation-old vault account is missing"));
        }
        None => None,
    };
    let pending_new = match pending_new {
        Some(key) if key_matches(&key, &state.new_key_fingerprint) => Some(key),
        Some(_) => {
            return Err(rotation_error(
                "rotation-new vault account has the wrong key",
            ));
        }
        None => None,
    };

    if !state.switched && !state.completed {
        let old_key = old
            .as_ref()
            .ok_or_else(|| rotation_error("old rotation key is unavailable"))?;
        let current_is_old = current
            .as_ref()
            .is_some_and(|key| key_matches(key, &state.old_key_fingerprint));
        let current_is_new = current
            .as_ref()
            .is_some_and(|key| key_matches(key, &state.new_key_fingerprint));
        if current.is_some() && !current_is_old && !current_is_new {
            return Err(rotation_error("current vault account has an unknown key"));
        }
        if current.is_none() {
            store.set(VAULT_ACCOUNT, old_key)?;
        }
        let new_key = if let Some(key) = pending_new {
            key
        } else if current_is_new {
            current.take().expect("current new key was checked above")
        } else {
            return Err(rotation_error("pending new rotation key is missing"));
        };
        return Ok((old, new_key));
    }

    let new_key = if let Some(key) = pending_new {
        key
    } else {
        current
            .as_ref()
            .filter(|key| key_matches(key, &state.new_key_fingerprint))
            .ok_or_else(|| rotation_error("new rotation key is unavailable"))?;
        current.expect("current key was checked above")
    };
    match store.load(VAULT_ACCOUNT)? {
        Some(key) if key_matches(&key, &state.new_key_fingerprint) => {}
        Some(_) => return Err(rotation_error("current vault account is not the new key")),
        None => store.set(VAULT_ACCOUNT, &new_key)?,
    }
    Ok((old, new_key))
}

fn archive_journal(state_directory: &Path, journal: &Path, rotation_id: Uuid) -> Result<PathBuf> {
    let history = state_directory.join("rotation-history");
    fs::create_dir_all(&history)?;
    let destination = history.join(format!("library-master-rotation-{rotation_id}.jsonl"));
    if destination.try_exists()? {
        return Err(rotation_error(
            "rotation history destination already exists",
        ));
    }
    fs::rename(journal, &destination)?;
    Ok(destination)
}

fn start_rotation(root: &Path, journal: &Path, store: &dyn KeyStore) -> Result<JournalState> {
    let current = match store.load(VAULT_ACCOUNT)? {
        Some(key) => key,
        None => {
            let key = LockedSecret::<KEY_BYTES>::random()?;
            store.set(VAULT_ACCOUNT, &key)?;
            key
        }
    };
    let stale_old = store.load(ROTATION_OLD_ACCOUNT)?;
    let stale_new = store.load(ROTATION_NEW_ACCOUNT)?;
    if let Some(old) = stale_old.as_ref() {
        if key_fingerprint(old) != key_fingerprint(&current) {
            return Err(rotation_error(
                "orphaned rotation-old key differs from the current key",
            ));
        }
    }
    if stale_old.is_some() || stale_new.is_some() {
        store.delete(ROTATION_OLD_ACCOUNT)?;
        store.delete(ROTATION_NEW_ACCOUNT)?;
    }

    let packages = discover_library(root, Some(&current))?;
    let new_key = LockedSecret::<KEY_BYTES>::random()?;
    let rotation_id = Uuid::new_v4();
    let old_key_fingerprint = key_fingerprint(&current);
    let new_key_fingerprint = key_fingerprint(&new_key);
    store.set(ROTATION_OLD_ACCOUNT, &current)?;
    store.set(ROTATION_NEW_ACCOUNT, &new_key)?;
    let root_text = root
        .to_str()
        .ok_or_else(|| rotation_error("library root is not UTF-8"))?
        .to_owned();
    create_journal(
        journal,
        &JournalEvent::Started {
            schema_version: JOURNAL_SCHEMA_VERSION,
            rotation_id,
            library_root: root_text,
            old_key_fingerprint: old_key_fingerprint.clone(),
            new_key_fingerprint: new_key_fingerprint.clone(),
            packages: packages.clone(),
        },
    )?;
    Ok(JournalState {
        rotation_id,
        library_root: root.to_path_buf(),
        old_key_fingerprint,
        new_key_fingerprint,
        packages,
        dual: HashSet::new(),
        new_only: HashSet::new(),
        switched: false,
        completed: false,
    })
}

fn rotate_with_store(
    library_root: &Path,
    state_directory: &Path,
    store: &dyn KeyStore,
    observer: &mut dyn RotationObserver,
) -> Result<RotationReport> {
    let root = canonical_library_root(library_root)?;
    let state_directory = ensure_state_directory(state_directory)?;
    let journal = state_directory.join(ACTIVE_JOURNAL_NAME);
    let mut resumed = journal.try_exists()?;
    let mut state = if resumed && journal_has_no_durable_event(&journal)? {
        // Package transitions only begin after the first event is synced. A
        // zero/partial first write therefore contains no committed work.
        safe_remove_sidecar(&journal)?;
        resumed = false;
        start_rotation(&root, &journal, store)?
    } else if resumed {
        load_journal(&journal)?
    } else {
        start_rotation(&root, &journal, store)?
    };
    let journal_root = canonical_library_root(&state.library_root)?;
    if journal_root != root {
        return Err(rotation_error(format!(
            "active rotation belongs to {}, not {}",
            journal_root.display(),
            root.display()
        )));
    }
    state.library_root = root.clone();
    validate_inventory(&root, &state.packages)?;

    let (old_key, new_key) = resolve_rotation_keys(store, &state)?;
    if !state.switched && !state.completed {
        let old_key = old_key
            .as_ref()
            .ok_or_else(|| rotation_error("old key is required before the switch"))?;
        let opening = vec![&**old_key];
        let targets = vec![&**old_key, &*new_key];
        for index in 0..state.packages.len() {
            transition_package(
                &root, &journal, &mut state, index, &opening, &targets, false, observer,
            )?;
        }
        validate_inventory(&root, &state.packages)?;
        for record in &state.packages {
            if !package_matches_profile(&record_path(&root, record)?, record, &targets)? {
                return Err(rotation_error(
                    "a package did not verify under both old and new keys",
                ));
            }
        }
        let current = store.load(VAULT_ACCOUNT)?;
        match current {
            Some(key) if key_matches(&key, &state.new_key_fingerprint) => {}
            Some(key) if key_matches(&key, &state.old_key_fingerprint) => {
                store.set(VAULT_ACCOUNT, &new_key)?;
                observer.checkpoint("after-current-key-switch-before-event")?;
            }
            None => {
                store.set(VAULT_ACCOUNT, &new_key)?;
                observer.checkpoint("after-current-key-switch-before-event")?;
            }
            Some(_) => return Err(rotation_error("current vault account has an unknown key")),
        }
        append_event(
            &journal,
            &JournalEvent::CurrentSwitched {
                rotation_id: state.rotation_id,
            },
        )?;
        state.switched = true;
    }

    if !state.completed {
        let mut opening = Vec::with_capacity(2);
        if let Some(old_key) = old_key.as_ref() {
            opening.push(&**old_key);
        }
        opening.push(&*new_key);
        let targets = vec![&*new_key];
        for index in 0..state.packages.len() {
            transition_package(
                &root, &journal, &mut state, index, &opening, &targets, true, observer,
            )?;
        }
        validate_inventory(&root, &state.packages)?;
        for record in &state.packages {
            if !package_matches_profile(&record_path(&root, record)?, record, &targets)? {
                return Err(rotation_error(
                    "a package failed final verification with the new key",
                ));
            }
        }
        append_event(
            &journal,
            &JournalEvent::PackagesVerifiedWithNewKey {
                rotation_id: state.rotation_id,
            },
        )?;
        state.completed = true;
        observer.checkpoint("after-completion-event-before-key-cleanup")?;
    }

    store.set(VAULT_ACCOUNT, &new_key)?;
    store.delete(ROTATION_OLD_ACCOUNT)?;
    store.delete(ROTATION_NEW_ACCOUNT)?;
    let archived_journal = archive_journal(&state_directory, &journal, state.rotation_id)?;
    Ok(RotationReport {
        rotation_id: state.rotation_id,
        library_root: root,
        package_count: state.packages.len(),
        resumed,
        dual_wrapped_packages: state.dual.len(),
        new_only_packages: state.new_only.len(),
        archived_journal,
    })
}

/// Rotates the device library master while holding the same cross-process lock
/// used by device-vault packaging. An existing journal is resumed in place.
pub fn rotate_library_master(
    library_root: &Path,
    state_directory: &Path,
) -> Result<RotationReport> {
    let _guard = acquire_vault_operation_lock()?;
    rotate_with_store(
        library_root,
        state_directory,
        &OsKeyStore,
        &mut NoopObserver,
    )
}

pub fn library_master_rotation_status(state_directory: &Path) -> Result<Option<RotationStatus>> {
    let journal = state_directory.join(ACTIVE_JOURNAL_NAME);
    if !journal.try_exists()? {
        return Ok(None);
    }
    let state = load_journal(&journal)?;
    Ok(Some(RotationStatus {
        rotation_id: state.rotation_id,
        library_root: state.library_root,
        package_count: state.packages.len(),
        dual_wrapped_packages: state.dual.len(),
        new_only_packages: state.new_only.len(),
        current_key_switched: state.switched,
        packages_verified_with_new_key: state.completed,
    }))
}

#[cfg(test)]
mod tests {
    use std::{cell::RefCell, collections::HashMap};

    use serde_json::json;
    use tempfile::TempDir;

    use super::*;
    use crate::{
        AssetRequest, ContentEncoding, HEADER_SIZE, Header, PackageRequest, pack_for_vault,
    };

    #[derive(Default)]
    struct TestStore {
        accounts: RefCell<HashMap<String, [u8; KEY_BYTES]>>,
    }

    impl TestStore {
        fn with_current(key: [u8; KEY_BYTES]) -> Self {
            Self {
                accounts: RefCell::new(HashMap::from([(VAULT_ACCOUNT.to_owned(), key)])),
            }
        }

        fn bytes(&self, account: &str) -> Option<[u8; KEY_BYTES]> {
            self.accounts.borrow().get(account).copied()
        }
    }

    impl KeyStore for TestStore {
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

        fn delete(&self, account: &str) -> Result<()> {
            self.accounts.borrow_mut().remove(account);
            Ok(())
        }
    }

    struct FailOnce {
        target: &'static str,
        failed: bool,
    }

    impl RotationObserver for FailOnce {
        fn checkpoint(&mut self, name: &'static str) -> Result<()> {
            if !self.failed && name == self.target {
                self.failed = true;
                return Err(rotation_error(format!("injected interruption at {name}")));
            }
            Ok(())
        }
    }

    struct Fixture {
        _directory: TempDir,
        library: PathBuf,
        state: PathBuf,
        package: PathBuf,
        old_key: [u8; KEY_BYTES],
        original_asset_ciphertext: Vec<u8>,
        store: TestStore,
    }

    fn asset_ciphertext(path: &Path) -> Vec<u8> {
        let bytes = fs::read(path).unwrap();
        let encoded: [u8; HEADER_SIZE] = bytes[..HEADER_SIZE].try_into().unwrap();
        let header = Header::decode(&encoded).unwrap();
        let start = (header.envelope_offset + header.envelope_length) as usize;
        bytes[start..header.manifest_offset as usize].to_vec()
    }

    fn fixture() -> Fixture {
        let directory = tempfile::tempdir().unwrap();
        let library = directory.path().join("library");
        let nested = library.join("nested");
        let state = directory.path().join("state");
        fs::create_dir_all(&nested).unwrap();
        fs::create_dir_all(&state).unwrap();
        let source = directory.path().join("media.bin");
        fs::write(&source, vec![0x5a; 131_333]).unwrap();
        let package = nested.join("fixture.lrail");
        let request = PackageRequest {
            metadata: json!({"rotation": true}),
            producer: "LyricRail rotation tests".into(),
            minimum_player_version: "0.8.0".into(),
            assets: vec![AssetRequest {
                logical_name: "media/main.bin".into(),
                path: source,
                media_type: "application/octet-stream".into(),
                kind: "media".into(),
                track_name: None,
                language: None,
                default: true,
                content_encoding: ContentEncoding::Identity,
            }],
        };
        let old_key = [0x41; KEY_BYTES];
        pack_for_vault(&request, &package, &old_key, None).unwrap();
        let original_asset_ciphertext = asset_ciphertext(&package);
        Fixture {
            _directory: directory,
            library,
            state,
            package,
            old_key,
            original_asset_ciphertext,
            store: TestStore::with_current(old_key),
        }
    }

    #[test]
    fn every_durable_rotation_boundary_resumes_without_reencoding_media() {
        for checkpoint in [
            "after-replacement-verified-before-commit",
            "after-replacement-commit-before-event",
            "after-transition-event-before-cleanup",
            "after-current-key-switch-before-event",
            "after-completion-event-before-key-cleanup",
        ] {
            let fixture = fixture();
            let mut failure = FailOnce {
                target: checkpoint,
                failed: false,
            };
            assert!(
                rotate_with_store(
                    &fixture.library,
                    &fixture.state,
                    &fixture.store,
                    &mut failure,
                )
                .is_err(),
                "checkpoint {checkpoint} did not interrupt"
            );
            assert!(
                library_master_rotation_status(&fixture.state)
                    .unwrap()
                    .is_some()
            );

            let report = rotate_with_store(
                &fixture.library,
                &fixture.state,
                &fixture.store,
                &mut NoopObserver,
            )
            .unwrap();
            assert!(report.resumed);
            assert_eq!(report.package_count, 1);
            assert_eq!(report.dual_wrapped_packages, 1);
            assert_eq!(report.new_only_packages, 1);
            assert!(report.archived_journal.is_file());
            assert!(
                library_master_rotation_status(&fixture.state)
                    .unwrap()
                    .is_none()
            );
            let new_key = fixture.store.bytes(VAULT_ACCOUNT).unwrap();
            assert!(
                verify_package_with_vault(&fixture.package, &new_key)
                    .unwrap()
                    .valid
            );
            assert!(matches!(
                verify_package_with_vault(&fixture.package, &fixture.old_key),
                Err(Error::KeyUnwrap)
            ));
            assert_eq!(
                asset_ciphertext(&fixture.package),
                fixture.original_asset_ciphertext
            );
            assert!(fixture.store.bytes(ROTATION_OLD_ACCOUNT).is_none());
            assert!(fixture.store.bytes(ROTATION_NEW_ACCOUNT).is_none());
        }
    }

    #[test]
    fn resume_uses_current_new_key_if_pending_copy_was_lost_after_switch() {
        let fixture = fixture();
        let mut failure = FailOnce {
            target: "after-current-key-switch-before-event",
            failed: false,
        };
        assert!(
            rotate_with_store(
                &fixture.library,
                &fixture.state,
                &fixture.store,
                &mut failure,
            )
            .is_err()
        );
        fixture.store.delete(ROTATION_NEW_ACCOUNT).unwrap();
        rotate_with_store(
            &fixture.library,
            &fixture.state,
            &fixture.store,
            &mut NoopObserver,
        )
        .unwrap();
        let current = fixture.store.bytes(VAULT_ACCOUNT).unwrap();
        assert!(verify_package_with_vault(&fixture.package, &current).is_ok());
    }

    #[test]
    fn loss_of_every_new_key_copy_fails_closed_and_restores_old_current_key() {
        let fixture = fixture();
        let mut failure = FailOnce {
            target: "after-current-key-switch-before-event",
            failed: false,
        };
        assert!(
            rotate_with_store(
                &fixture.library,
                &fixture.state,
                &fixture.store,
                &mut failure,
            )
            .is_err()
        );
        fixture.store.delete(VAULT_ACCOUNT).unwrap();
        fixture.store.delete(ROTATION_NEW_ACCOUNT).unwrap();
        let error = rotate_with_store(
            &fixture.library,
            &fixture.state,
            &fixture.store,
            &mut NoopObserver,
        )
        .unwrap_err();
        assert!(matches!(error, Error::Rotation(_)));
        assert_eq!(fixture.store.bytes(VAULT_ACCOUNT), Some(fixture.old_key));
        assert!(verify_package_with_vault(&fixture.package, &fixture.old_key).is_ok());
        assert!(
            library_master_rotation_status(&fixture.state)
                .unwrap()
                .is_some()
        );
    }
}
