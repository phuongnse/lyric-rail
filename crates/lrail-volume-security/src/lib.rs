use std::path::{Path, PathBuf};

use serde::Serialize;
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum VolumeProtectionState {
    Protected,
    Unprotected,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VolumeSecurityEvidence {
    pub state: VolumeProtectionState,
    pub volume: Option<String>,
    pub detail: String,
}

#[derive(Debug, Error)]
pub enum VolumeSecurityError {
    #[error("workspace path has no existing ancestor")]
    NoExistingAncestor,
    #[error("cannot resolve workspace path: {0}")]
    ResolvePath(String),
    #[error("workspace is not on a supported local volume")]
    UnsupportedVolume,
    #[error("Windows denied read access to the BitLocker WMI provider")]
    WmiAccessDenied,
    #[error("BitLocker WMI operation failed: {0}")]
    Wmi(String),
    #[error("encrypted-volume status check exceeded its safety limit")]
    Timeout,
    #[error("volume broker is unavailable: {0}")]
    BrokerUnavailable(String),
    #[error("volume broker identity verification failed: {0}")]
    BrokerIdentity(String),
    #[error("volume broker protocol rejected the response: {0}")]
    BrokerProtocol(String),
    #[error("volume broker could not query BitLocker status")]
    BrokerQueryFailed,
    #[error("encrypted-volume detection is unsupported on this platform")]
    UnsupportedPlatform,
    #[error("platform volume-security query failed: {0}")]
    PlatformQuery(String),
    #[error("platform returned incomplete or malformed volume-security evidence: {0}")]
    MalformedEvidence(String),
}

pub fn resolve_existing_path(path: &Path) -> Result<PathBuf, VolumeSecurityError> {
    let mut existing = path.to_path_buf();
    while !existing.exists() {
        if !existing.pop() {
            return Err(VolumeSecurityError::NoExistingAncestor);
        }
    }
    existing
        .canonicalize()
        .map_err(|error| VolumeSecurityError::ResolvePath(error.to_string()))
}

#[cfg(windows)]
pub fn resolve_drive_letter(path: &Path) -> Result<(PathBuf, String), VolumeSecurityError> {
    use std::path::{Component, Prefix};

    let canonical = resolve_existing_path(path)?;
    let drive = match canonical.components().next() {
        Some(Component::Prefix(prefix)) => match prefix.kind() {
            Prefix::Disk(letter) | Prefix::VerbatimDisk(letter) => {
                format!("{}:", char::from(letter).to_ascii_uppercase())
            }
            _ => return Err(VolumeSecurityError::UnsupportedVolume),
        },
        _ => return Err(VolumeSecurityError::UnsupportedVolume),
    };
    Ok((canonical, drive))
}

#[cfg(windows)]
mod windows_protocol;

#[cfg(windows)]
pub use windows_protocol::{
    BROKER_PIPE_NAME, BROKER_PROTOCOL_VERSION, BROKER_REQUEST_LEN, BROKER_RESPONSE_LEN,
    BROKER_SERVICE_NAME, BitLockerStatus, BrokerEvidence, BrokerRequest, BrokerResponse,
    classify_bitlocker, decode_request, decode_response, encode_request, encode_response,
    failure_response, response_status, success_response,
};

#[cfg(windows)]
mod windows_impl;

#[cfg(windows)]
pub use windows_impl::{inspect_encrypted_volume, query_bitlocker_wmi, query_broker};

#[cfg(target_os = "macos")]
mod macos_impl;

#[cfg(target_os = "macos")]
pub use macos_impl::inspect_encrypted_volume;

#[cfg(target_os = "linux")]
mod linux_impl;

#[cfg(target_os = "linux")]
pub use linux_impl::inspect_encrypted_volume;

#[cfg(not(any(windows, target_os = "macos", target_os = "linux")))]
pub fn inspect_encrypted_volume(
    _path: &Path,
    _timeout: std::time::Duration,
) -> Result<VolumeSecurityEvidence, VolumeSecurityError> {
    Err(VolumeSecurityError::UnsupportedPlatform)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn protection_state_serializes_to_stable_lowercase_values() {
        assert_eq!(
            serde_json::to_string(&VolumeProtectionState::Protected).unwrap(),
            "\"protected\""
        );
        assert_eq!(
            serde_json::to_string(&VolumeProtectionState::Unknown).unwrap(),
            "\"unknown\""
        );
    }
}
