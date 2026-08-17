use std::path::{Path, PathBuf};

use lrail_volume_security::{VolumeProtectionState, inspect_encrypted_volume};
use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WorkspaceVolumeStatus {
    pub(crate) state: VolumeProtectionState,
    pub(crate) platform: &'static str,
    pub(crate) path: PathBuf,
    pub(crate) volume: Option<String>,
    pub(crate) detail: String,
    pub(crate) enforced: bool,
}

impl WorkspaceVolumeStatus {
    pub(crate) fn is_protected(&self) -> bool {
        self.state == VolumeProtectionState::Protected
    }
}

pub(crate) fn inspect_workspace_volume(path: &Path) -> WorkspaceVolumeStatus {
    use std::{sync::mpsc, time::Duration};

    let worker_path = path.to_path_buf();
    let (sender, receiver) = mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let result = inspect_encrypted_volume(&worker_path, Duration::from_secs(5));
        let _ = sender.send(result);
    });
    let result = receiver
        .recv_timeout(Duration::from_secs(10))
        .map_err(|error| match error {
            mpsc::RecvTimeoutError::Timeout => {
                "Encrypted-volume status check exceeded the 10-second safety limit".to_owned()
            }
            mpsc::RecvTimeoutError::Disconnected => {
                "Encrypted-volume status worker exited unexpectedly".to_owned()
            }
        })
        .and_then(|result| result.map_err(|error| error.to_string()));

    match result {
        Ok(evidence) => WorkspaceVolumeStatus {
            state: evidence.state,
            platform: std::env::consts::OS,
            path: path.to_path_buf(),
            volume: evidence.volume,
            detail: evidence.detail,
            enforced: cfg!(not(debug_assertions)),
        },
        Err(error) => WorkspaceVolumeStatus {
            state: VolumeProtectionState::Unknown,
            platform: std::env::consts::OS,
            path: path.to_path_buf(),
            volume: None,
            detail: format!("Unable to verify encrypted-volume protection: {error}"),
            enforced: cfg!(not(debug_assertions)),
        },
    }
}

pub(crate) fn require_protected_workspace(status: &WorkspaceVolumeStatus) -> Result<(), String> {
    if status.enforced && !status.is_protected() {
        return Err(format!(
            "Production is blocked because the cleartext workspace volume is not confirmed encrypted. {} Workspace: {}",
            status.detail,
            status.path.display()
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn release_gate_accepts_only_protected_evidence() {
        let mut status = WorkspaceVolumeStatus {
            state: VolumeProtectionState::Protected,
            platform: "test",
            path: PathBuf::from("workspace"),
            volume: None,
            detail: "fixture".to_owned(),
            enforced: true,
        };
        assert!(require_protected_workspace(&status).is_ok());
        status.state = VolumeProtectionState::Unprotected;
        assert!(require_protected_workspace(&status).is_err());
    }

    #[test]
    fn unknown_evidence_fails_closed_in_release_mode() {
        let status = WorkspaceVolumeStatus {
            state: VolumeProtectionState::Unknown,
            platform: "test",
            path: PathBuf::from("workspace"),
            volume: None,
            detail: "fixture".to_owned(),
            enforced: true,
        };
        assert!(require_protected_workspace(&status).is_err());
    }
}
