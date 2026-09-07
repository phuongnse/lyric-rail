use keyring::v1::{Entry, Error as KeyringError};
use zeroize::Zeroizing;

use crate::{
    Error, LockedSecret, Result,
    crypto::KEY_BYTES,
    package::{PackagedAsset, pack_for_vault},
    schema::{PackageRequest, PackageRevisionReport, PackageRevisionRequest},
};

pub(crate) const VAULT_SERVICE: &str = "com.lyricrail.keys";
pub(crate) const VAULT_ACCOUNT: &str = "library-master-v1";
pub(crate) const ROTATION_OLD_ACCOUNT: &str = "library-master-v1-rotation-old";
pub(crate) const ROTATION_NEW_ACCOUNT: &str = "library-master-v1-rotation-new";

fn entry(account: &str) -> Result<Entry> {
    Entry::new(VAULT_SERVICE, account).map_err(|error| Error::Vault(error.to_string()))
}

fn decode_master(secret: Vec<u8>) -> Result<LockedSecret<KEY_BYTES>> {
    let secret = Zeroizing::new(secret);
    if secret.len() != KEY_BYTES {
        return Err(Error::Vault(
            "stored library master key has an invalid length".into(),
        ));
    }
    LockedSecret::from_slice(&secret)
}

pub(crate) fn load_vault_account(account: &str) -> Result<Option<LockedSecret<KEY_BYTES>>> {
    match entry(account)?.get_secret() {
        Ok(secret) => decode_master(secret).map(Some),
        Err(KeyringError::NoEntry) => Ok(None),
        Err(error) => Err(Error::Vault(error.to_string())),
    }
}

pub(crate) fn set_vault_account(account: &str, key: &[u8; KEY_BYTES]) -> Result<()> {
    entry(account)?
        .set_secret(key)
        .map_err(|error| Error::Vault(error.to_string()))
}

pub(crate) fn delete_vault_account(account: &str) -> Result<()> {
    match entry(account)?.delete_credential() {
        Ok(()) | Err(KeyringError::NoEntry) => Ok(()),
        Err(error) => Err(Error::Vault(error.to_string())),
    }
}

pub(crate) fn load_or_create_vault_master_unlocked() -> Result<LockedSecret<KEY_BYTES>> {
    if let Some(secret) = load_vault_account(VAULT_ACCOUNT)? {
        return Ok(secret);
    }
    let key = LockedSecret::<KEY_BYTES>::random()?;
    set_vault_account(VAULT_ACCOUNT, &key)?;
    Ok(key)
}

pub fn load_vault_master() -> Result<LockedSecret<KEY_BYTES>> {
    let _guard = acquire_vault_operation_lock()?;
    load_vault_account(VAULT_ACCOUNT)?
        .ok_or_else(|| Error::Vault("library master key is not initialized".into()))
}

pub fn load_or_create_vault_master() -> Result<LockedSecret<KEY_BYTES>> {
    let _guard = acquire_vault_operation_lock()?;
    load_or_create_vault_master_unlocked()
}

/// Atomically selects the current OS-vault master and packages while holding
/// the same cross-process lock used by master-key rotation.
pub fn pack_for_device_vault(
    request: &PackageRequest,
    output: &std::path::Path,
    recovery_passphrase: Option<&[u8]>,
) -> Result<Vec<PackagedAsset>> {
    let _guard = acquire_vault_operation_lock()?;
    let vault_master = load_or_create_vault_master_unlocked()?;
    pack_for_vault(request, output, &vault_master, recovery_passphrase)
}

fn with_locked_vault_master<T>(
    load: impl FnOnce() -> Result<Option<LockedSecret<KEY_BYTES>>>,
    operation: impl FnOnce(&LockedSecret<KEY_BYTES>) -> Result<T>,
) -> Result<T> {
    let _guard = acquire_vault_operation_lock()?;
    let master =
        load()?.ok_or_else(|| Error::Vault("library master key is not initialized".into()))?;
    operation(&master)
}

/// Selects the current device key under the rotation lock and keeps that lock
/// until the new revision has been authenticated and published.
pub fn revise_package_for_device_vault(
    input: &std::path::Path,
    output: &std::path::Path,
    request: &PackageRevisionRequest,
) -> Result<PackageRevisionReport> {
    with_locked_vault_master(
        || load_vault_account(VAULT_ACCOUNT),
        |master| crate::package::revise_package_for_vault(input, output, master, request),
    )
}

/// Serializes in-place revision, including rollback verification, with rotation.
pub fn revise_package_in_place_for_device_vault(
    input: &std::path::Path,
    request: &PackageRevisionRequest,
) -> Result<PackageRevisionReport> {
    with_locked_vault_master(
        || load_vault_account(VAULT_ACCOUNT),
        |master| crate::package::revise_package_in_place_for_vault(input, master, request),
    )
}

pub struct VaultOperationGuard {
    #[cfg(windows)]
    handle: windows_sys::Win32::Foundation::HANDLE,
    #[cfg(unix)]
    file: std::fs::File,
}

#[cfg(windows)]
pub(crate) fn acquire_vault_operation_lock() -> Result<VaultOperationGuard> {
    use std::ptr;
    use windows_sys::Win32::{
        Foundation::{CloseHandle, WAIT_ABANDONED, WAIT_OBJECT_0},
        System::Threading::{CreateMutexW, INFINITE, WaitForSingleObject},
    };

    let name: Vec<u16> = "Local\\LyricRail.LibraryMaster.v1\0"
        .encode_utf16()
        .collect();
    // SAFETY: security attributes are null, the name is NUL-terminated, and
    // the returned handle is closed by VaultOperationGuard::drop.
    let handle = unsafe { CreateMutexW(ptr::null(), 0, name.as_ptr()) };
    if handle.is_null() {
        return Err(Error::Io(std::io::Error::last_os_error()));
    }
    // SAFETY: handle is a live mutex handle returned by CreateMutexW.
    let wait = unsafe { WaitForSingleObject(handle, INFINITE) };
    if wait != WAIT_OBJECT_0 && wait != WAIT_ABANDONED {
        // SAFETY: handle is live and owned by this function.
        unsafe { CloseHandle(handle) };
        return Err(Error::Io(std::io::Error::last_os_error()));
    }
    Ok(VaultOperationGuard { handle })
}

#[cfg(unix)]
pub(crate) fn acquire_vault_operation_lock() -> Result<VaultOperationGuard> {
    use std::{fs::OpenOptions, os::fd::AsRawFd, os::unix::fs::OpenOptionsExt};

    // The effective UID makes the path specific to the same OS identity that
    // owns the credential-vault entry.
    let path = std::env::temp_dir().join(format!(
        "lyricrail-library-master-{}.lock",
        // SAFETY: geteuid has no preconditions.
        unsafe { libc::geteuid() }
    ));
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .open(path)?;
    // SAFETY: file is open and its descriptor remains live in the guard.
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) } != 0 {
        return Err(Error::Io(std::io::Error::last_os_error()));
    }
    Ok(VaultOperationGuard { file })
}

#[cfg(not(any(windows, unix)))]
pub(crate) fn acquire_vault_operation_lock() -> Result<VaultOperationGuard> {
    compile_error!("LyricRail requires a cross-process vault lock implementation on this target");
}

impl Drop for VaultOperationGuard {
    fn drop(&mut self) {
        #[cfg(windows)]
        {
            use windows_sys::Win32::{Foundation::CloseHandle, System::Threading::ReleaseMutex};
            // SAFETY: this guard owns the mutex and handle until both calls.
            unsafe {
                let _ = ReleaseMutex(self.handle);
                let _ = CloseHandle(self.handle);
            }
        }
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            // SAFETY: the descriptor remains live until the field is dropped.
            unsafe {
                let _ = libc::flock(self.file.as_raw_fd(), libc::LOCK_UN);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{KEY_BYTES, decode_master};

    #[test]
    fn revision_selects_key_only_after_rotation_and_holds_lock_through_publication() {
        use crate::{AssetRequest, ContentEncoding, PackageRequest, PackageRevisionRequest};
        use std::{
            sync::{Arc, Mutex, mpsc},
            thread,
            time::Duration,
        };
        let directory = tempfile::tempdir().unwrap();
        let lyrics = directory.path().join("lyrics.txt");
        std::fs::write(&lyrics, b"Exact words").unwrap();
        let asset = AssetRequest {
            logical_name: "lyrics/authoritative.txt".into(),
            path: lyrics,
            media_type: "text/plain; charset=utf-8".into(),
            kind: "authoritative-lyrics".into(),
            track_name: None,
            language: None,
            default: false,
            content_encoding: ContentEncoding::Identity,
        };
        let package = directory.path().join("song.lrail");
        let old = [0x21; KEY_BYTES];
        let new = [0x22; KEY_BYTES];
        let request = PackageRequest {
            metadata: serde_json::json!({}),
            assets: vec![asset.clone()],
            producer: "fixture".into(),
            minimum_player_version: "0.8.0".into(),
        };
        crate::pack_for_vault(&request, &package, &old, None).unwrap();
        let current = Arc::new(Mutex::new(old));
        let rotation_guard = super::acquire_vault_operation_lock().unwrap();
        let (loaded_tx, loaded_rx) = mpsc::channel();
        let (publishing_tx, publishing_rx) = mpsc::channel();
        let (finish_tx, finish_rx) = mpsc::channel();
        let worker_key = current.clone();
        let worker_package = package.clone();
        let worker = thread::spawn(move || {
            super::with_locked_vault_master(
                || {
                    loaded_tx.send(()).unwrap();
                    decode_master(worker_key.lock().unwrap().to_vec()).map(Some)
                },
                |master| {
                    publishing_tx.send(()).unwrap();
                    finish_rx.recv().unwrap();
                    crate::revise_package_in_place_for_vault(
                        &worker_package,
                        master,
                        &PackageRevisionRequest {
                            metadata: None,
                            producer: None,
                            assets: vec![asset],
                        },
                    )
                },
            )
            .unwrap();
        });
        assert!(loaded_rx.recv_timeout(Duration::from_millis(100)).is_err());
        let rotated = directory.path().join("rotated.lrail");
        crate::rewrap_package_for_vaults(&package, &rotated, &[&old], &[&new]).unwrap();
        std::fs::copy(&rotated, &package).unwrap();
        *current.lock().unwrap() = new;
        drop(rotation_guard);
        loaded_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        publishing_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        let (locked_tx, locked_rx) = mpsc::channel();
        let contender = thread::spawn(move || {
            let _guard = super::acquire_vault_operation_lock().unwrap();
            locked_tx.send(()).unwrap();
        });
        assert!(locked_rx.recv_timeout(Duration::from_millis(100)).is_err());
        finish_tx.send(()).unwrap();
        worker.join().unwrap();
        contender.join().unwrap();
        crate::verify_package_with_vault(&package, &new).unwrap();
        assert!(crate::verify_package_with_vault(&package, &old).is_err());
    }

    #[test]
    fn credential_bytes_enter_locked_memory_and_invalid_lengths_fail_closed() {
        let master = decode_master(vec![0x63; KEY_BYTES]).unwrap();
        assert_eq!(master.as_slice(), &[0x63; KEY_BYTES]);
        assert!(decode_master(vec![0x63; KEY_BYTES - 1]).is_err());
        assert!(decode_master(vec![0x63; KEY_BYTES + 1]).is_err());
    }
}
