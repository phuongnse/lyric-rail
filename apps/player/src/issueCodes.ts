export const PRODUCER_ISSUE_CODES = [
  "processing.models-missing", "processing.runtime-repair-required", "processing.runtime-startup", "processing.job-failed",
  "drive.unavailable", "runtime.invalid", "remote.invalid",
  "tasks.task-output-could-not-be-replayed", "system.lyricrail-could-not-refresh", "system.action-failed",
  "library.package-import-failed", "library.startup-package-import-failed", "library.local-source-scan-failed", "library.library-search-failed",
  "library.library-item-could-not-be-deleted", "library.edit-video-unavailable", "library.video-changes-could-not-be-saved", "library.library-source-could-not-be-removed", "library.compatible-preview-failed", "library.files-could-not-be-added", "library.folder-could-not-be-added", "library.library-sources-could-not-be-rescanned",
  "recovery.library-refresh-after-recovery-failed", "recovery.recovery-bundle-could-not-be-exported", "recovery.recovery-bundle-could-not-be-restored",
  "drive.drive-source-scan-failed", "drive.drive-unavailable", "drive.google-drive-could-not-connect",
  "view.fullscreen-could-not-be-changed", "lyrics.lyrics-could-not-be-queued", "clip.songs-could-not-be-added",
  "processing.ai-processing-could-not-start", "processing.song-retry-failed",
  "playback.song-could-not-be-opened", "playback.playback-could-not-start", "playback.audio-track-could-not-resume", "playback.video-playback-failed", "playback.audio-playback-failed", "player.playback-failed",
  "settings.settings-location-could-not-be-selected", "settings.settings-could-not-be-saved", "issues.issue-could-not-be-dismissed", "issues.diagnostics-could-not-be-copied",
  "tasks.could-not-cancel-clip-preparation", "tasks.could-not-cancel-video-preview", "tasks.linked-task-output-could-not-be-opened", "tasks.linked-task-output-is-no-longer-available", "tasks.could-not-cancel-task", "tasks.could-not-pause-task", "tasks.could-not-resume-task", "tasks.task-could-not-be-cancelled", "tasks.task-could-not-be-paused", "tasks.task-could-not-be-resumed", "tasks.task-output-could-not-be-copied",
] as const;

export const PRODUCER_ISSUE_SCOPES = ["system", "tasks", "library", "recovery", "drive", "view", "lyrics", "clip", "playback", "processing", "settings", "issues", "player"] as const;

export const SAFE_ISSUE_ACTION_KINDS = ["install-models", "retry-item", "reconnect-drive"] as const;
