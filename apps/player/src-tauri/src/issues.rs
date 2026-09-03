use std::{
    collections::HashMap,
    sync::Mutex,
    time::{SystemTime, UNIX_EPOCH},
};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

pub const ISSUES_EVENT: &str = "system-issues-changed";
pub const MODELS_MISSING_CODE: &str = "processing.models-missing";

const MAX_ISSUES: usize = 100;
const MAX_DETAIL_CHARS: usize = 4_000;

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum IssueSeverity {
    Error,
    Blocking,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum IssueState {
    Open,
    Resolving,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum IssueResolution {
    InstallModels,
    RetryItem,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct IssueAction {
    pub kind: IssueResolution,
    pub label: String,
    pub requires_confirmation: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SystemIssue {
    pub id: String,
    pub code: String,
    pub scope: String,
    pub severity: IssueSeverity,
    pub title: String,
    pub summary: String,
    pub detail: Option<String>,
    pub related_item_id: Option<String>,
    pub related_task_id: Option<String>,
    pub state: IssueState,
    pub progress_percent: Option<f32>,
    pub progress_message: Option<String>,
    pub occurrences: u32,
    pub created_at_millis: u64,
    pub updated_at_millis: u64,
    pub actions: Vec<IssueAction>,
}

struct IssueDefinition {
    severity: IssueSeverity,
    title: String,
    summary: String,
    detail: Option<String>,
    related_item_id: Option<String>,
    related_task_id: Option<String>,
    actions: Vec<IssueAction>,
}

#[derive(Default)]
struct IssueInner {
    issues: HashMap<String, SystemIssue>,
}

#[derive(Default)]
pub struct IssueStateStore(Mutex<IssueInner>);

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

fn bounded(value: impl Into<String>, max: usize) -> String {
    value.into().chars().take(max).collect()
}

fn redact_detail(value: &str) -> String {
    crate::tasks::redact_diagnostic_text(value)
}

fn issue_id(code: &str, scope: &str, related_item_id: Option<&str>) -> String {
    format!("{code}:{scope}:{}", related_item_id.unwrap_or("system"))
}

impl SystemIssue {
    fn new(code: &str, scope: &str, definition: IssueDefinition) -> Self {
        let created = now_millis();
        Self {
            id: issue_id(code, scope, definition.related_item_id.as_deref()),
            code: bounded(code, 80),
            scope: bounded(scope, 60),
            severity: definition.severity,
            title: bounded(definition.title, 120),
            summary: bounded(definition.summary, 500),
            detail: definition
                .detail
                .map(|value| bounded(redact_detail(&value), MAX_DETAIL_CHARS)),
            related_item_id: definition.related_item_id,
            related_task_id: definition.related_task_id.map(|value| bounded(value, 180)),
            state: IssueState::Open,
            progress_percent: None,
            progress_message: None,
            occurrences: 1,
            created_at_millis: created,
            updated_at_millis: created,
            actions: definition.actions.into_iter().take(3).collect(),
        }
    }
}

fn snapshot_inner(inner: &IssueInner) -> Vec<SystemIssue> {
    let mut issues = inner.issues.values().cloned().collect::<Vec<_>>();
    issues.sort_by_key(|issue| std::cmp::Reverse(issue.updated_at_millis));
    issues
}

fn merge_issue(inner: &mut IssueInner, mut issue: SystemIssue) {
    if let Some(existing) = inner.issues.get(&issue.id) {
        issue.created_at_millis = existing.created_at_millis;
        issue.occurrences = existing.occurrences.saturating_add(1);
    }
    issue.updated_at_millis = now_millis();
    if inner.issues.len() >= MAX_ISSUES && !inner.issues.contains_key(&issue.id) {
        let oldest = inner
            .issues
            .values()
            .min_by_key(|issue| issue.updated_at_millis)
            .map(|issue| issue.id.clone());
        if let Some(oldest) = oldest {
            inner.issues.remove(&oldest);
        }
    }
    inner.issues.insert(issue.id.clone(), issue);
}

fn emit_snapshot(app: &AppHandle) {
    let snapshot = snapshot(app);
    let _ = app.emit(ISSUES_EVENT, snapshot);
}

pub fn snapshot(app: &AppHandle) -> Vec<SystemIssue> {
    app.state::<IssueStateStore>()
        .0
        .lock()
        .map(|inner| snapshot_inner(&inner))
        .unwrap_or_default()
}

pub fn report(app: &AppHandle, issue: SystemIssue) {
    if let Ok(mut inner) = app.state::<IssueStateStore>().0.lock() {
        merge_issue(&mut inner, issue);
    }
    emit_snapshot(app);
}

pub fn ensure(app: &AppHandle, issue: SystemIssue) {
    let mut changed = false;
    if let Ok(mut inner) = app.state::<IssueStateStore>().0.lock()
        && !inner.issues.contains_key(&issue.id)
    {
        merge_issue(&mut inner, issue);
        changed = true;
    }
    if changed {
        emit_snapshot(app);
    }
}

pub fn dismiss(app: &AppHandle, id: &str) -> bool {
    let removed = app
        .state::<IssueStateStore>()
        .0
        .lock()
        .ok()
        .and_then(|mut inner| {
            inner
                .issues
                .get(id)
                .is_some_and(|issue| issue.severity != IssueSeverity::Blocking)
                .then(|| inner.issues.remove(id))
                .flatten()
        })
        .is_some();
    if removed {
        emit_snapshot(app);
    }
    removed
}

pub fn set_resolving(
    app: &AppHandle,
    id: &str,
    message: &str,
    related_task_id: Option<&str>,
) -> Result<(), String> {
    let state = app.state::<IssueStateStore>();
    let mut inner = state
        .0
        .lock()
        .map_err(|_| "Issue state lock is poisoned".to_string())?;
    let issue = inner
        .issues
        .get_mut(id)
        .ok_or_else(|| "Issue is no longer active".to_string())?;
    issue.state = IssueState::Resolving;
    issue.progress_percent = None;
    issue.progress_message = Some(bounded(message, 240));
    issue.related_task_id = related_task_id.map(|value| bounded(value, 180));
    issue.updated_at_millis = now_millis();
    drop(inner);
    emit_snapshot(app);
    Ok(())
}

pub fn resolution_failed(app: &AppHandle, id: &str, detail: &str) {
    if let Ok(mut inner) = app.state::<IssueStateStore>().0.lock()
        && let Some(issue) = inner.issues.get_mut(id)
    {
        issue.state = IssueState::Open;
        issue.progress_percent = None;
        issue.progress_message = None;
        issue.summary =
            "The resolution did not complete. Review the details, then try again.".to_string();
        issue.detail = Some(bounded(redact_detail(detail), MAX_DETAIL_CHARS));
        issue.updated_at_millis = now_millis();
    }
    emit_snapshot(app);
}

pub fn resolve(app: &AppHandle, id: &str) {
    if let Ok(mut inner) = app.state::<IssueStateStore>().0.lock() {
        inner.issues.remove(id);
    }
    emit_snapshot(app);
}

pub fn models_missing_issue(detail: &str, install_allowed: bool) -> SystemIssue {
    let actions = if install_allowed {
        vec![IssueAction {
            kind: IssueResolution::InstallModels,
            label: "Install processing models".into(),
            requires_confirmation: true,
        }]
    } else {
        Vec::new()
    };
    SystemIssue::new(
        MODELS_MISSING_CODE,
        "processing",
        IssueDefinition {
            severity: IssueSeverity::Blocking,
            title: "Processing setup required".into(),
            summary: if install_allowed {
                "Pinned karaoke models are not installed. Install and verify them, then LyricRail will retry affected songs automatically."
            } else {
                "The verified runtime is incomplete. Reinstall a signed LyricRail runtime pack before retrying this song."
            }
            .into(),
            detail: Some(detail.to_owned()),
            related_item_id: None,
            related_task_id: None,
            actions,
        },
    )
}

pub fn generic_issue(
    code: &str,
    scope: &str,
    title: &str,
    summary: &str,
    detail: Option<String>,
    action: Option<IssueAction>,
) -> SystemIssue {
    SystemIssue::new(
        code,
        scope,
        IssueDefinition {
            severity: IssueSeverity::Error,
            title: title.into(),
            summary: summary.into(),
            detail,
            related_item_id: None,
            related_task_id: None,
            actions: action.into_iter().collect(),
        },
    )
}

pub fn runtime_repair_issue(detail: &str) -> SystemIssue {
    SystemIssue::new(
        "processing.runtime-repair-required",
        "processing",
        IssueDefinition {
            severity: IssueSeverity::Blocking,
            title: "Processing runtime repair required".into(),
            summary: "Playback still works, but the local processing runtime failed integrity or startup validation. Repair or reinstall the verified runtime before retrying affected songs."
                .into(),
            detail: Some(detail.to_owned()),
            related_item_id: None,
            related_task_id: None,
            actions: Vec::new(),
        },
    )
}

pub fn processing_failure_issue(item_id: &str, title: &str, detail: &str) -> SystemIssue {
    SystemIssue::new(
        "processing.job-failed",
        "processing",
        IssueDefinition {
            severity: IssueSeverity::Error,
            title: format!("Processing failed: {title}"),
            summary: "Open the linked task output to see the failing stage, correct the cause, then retry this song."
                .into(),
            detail: Some(detail.to_owned()),
            related_item_id: Some(item_id.to_owned()),
            related_task_id: Some(item_id.to_owned()),
            actions: vec![IssueAction {
                kind: IssueResolution::RetryItem,
                label: "Retry song".into(),
                requires_confirmation: false,
            }],
        },
    )
}

pub fn resolve_processing_failure(app: &AppHandle, item_id: &str) {
    resolve(
        app,
        &issue_id("processing.job-failed", "processing", Some(item_id)),
    );
}

#[cfg(test)]
mod tests {
    use super::{
        IssueDefinition, IssueInner, IssueSeverity, SystemIssue, merge_issue, snapshot_inner,
    };

    #[test]
    fn duplicate_issue_keys_merge_without_flooding_the_center() {
        let mut inner = IssueInner::default();
        let issue = SystemIssue::new(
            "drive.unavailable",
            "drive",
            super::IssueDefinition {
                severity: IssueSeverity::Error,
                title: "Drive unavailable".into(),
                summary: "Reconnect Drive.".into(),
                detail: None,
                related_item_id: None,
                related_task_id: None,
                actions: Vec::new(),
            },
        );
        merge_issue(&mut inner, issue.clone());
        merge_issue(&mut inner, issue);
        let snapshot = snapshot_inner(&inner);
        assert_eq!(snapshot.len(), 1);
        assert_eq!(snapshot[0].occurrences, 2);
    }

    #[test]
    fn issue_copy_and_detail_are_bounded() {
        let issue = SystemIssue::new(
            &"c".repeat(200),
            "system",
            super::IssueDefinition {
                severity: IssueSeverity::Error,
                title: "title".into(),
                summary: "summary".into(),
                detail: Some("detail".repeat(2_000)),
                related_item_id: None,
                related_task_id: None,
                actions: Vec::new(),
            },
        );
        assert_eq!(issue.code.chars().count(), 80);
        assert_eq!(issue.detail.unwrap().chars().count(), 4_000);
    }

    #[test]
    fn every_native_issue_detail_redacts_paths_and_remote_addresses() {
        let path_issue = SystemIssue::new(
            "runtime.invalid",
            "processing",
            IssueDefinition {
                severity: IssueSeverity::Error,
                title: "Runtime invalid".into(),
                summary: "Repair runtime".into(),
                detail: Some("C:\\Music Library\\Private Song.mp4 trailing-name.mp4".into()),
                related_item_id: None,
                related_task_id: None,
                actions: Vec::new(),
            },
        );
        let remote_issue = SystemIssue::new(
            "remote.invalid",
            "processing",
            IssueDefinition {
                severity: IssueSeverity::Error,
                title: "Remote invalid".into(),
                summary: "Repair runtime".into(),
                detail: Some(
                    "https://host/path?token=x api_key=TOP access_token=BEARER client_secret=HUSH x-goog-signature=GOOG x-amz-signature=AMZ signature=RAW"
                        .into(),
                ),
                related_item_id: None,
                related_task_id: None,
                actions: Vec::new(),
            },
        );
        let path_detail = path_issue.detail.unwrap();
        let remote_detail = remote_issue.detail.unwrap();
        assert!(!path_detail.contains("Private Song.mp4"));
        assert!(!path_detail.contains("trailing-name.mp4"));
        assert!(!remote_detail.contains("token=x"));
        assert!(!remote_detail.contains("TOP"));
        assert!(!remote_detail.contains("BEARER"));
        assert!(!remote_detail.contains("HUSH"));
        assert!(!remote_detail.contains("GOOG"));
        assert!(!remote_detail.contains("AMZ"));
        assert!(!remote_detail.contains("RAW"));
        assert!(path_detail.contains("<local path>"));
        assert!(remote_detail.contains("<remote address>"));
    }

    #[test]
    fn processing_failure_is_linked_to_one_retryable_catalog_task() {
        let issue = super::processing_failure_issue(
            "catalog-item",
            "Song",
            "C:\\private\\job.log token=unsafe",
        );
        assert_eq!(issue.related_item_id.as_deref(), Some("catalog-item"));
        assert_eq!(issue.related_task_id.as_deref(), Some("catalog-item"));
        assert_eq!(issue.id, "processing.job-failed:processing:catalog-item");
        assert_eq!(issue.actions.len(), 1);
        assert_eq!(issue.actions[0].kind, super::IssueResolution::RetryItem);
        assert!(!issue.detail.unwrap().contains("unsafe"));
    }
}
