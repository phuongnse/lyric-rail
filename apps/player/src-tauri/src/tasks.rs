use std::{
    cmp::Reverse,
    collections::{BinaryHeap, HashMap, HashSet, VecDeque},
    sync::{Mutex, OnceLock},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

pub const TASKS_EVENT: &str = "task-runtime-update";
const MAX_TASK_HISTORY: usize = 100;
// The catalog admits 100,000 songs; reserve room for scans, model work and transfers.
const MAX_ACTIVE_TASKS: usize = 100_256;
const MAX_VISIBLE_TASKS: usize = 200;
const MAX_OUTPUT_LINES: usize = 1_000;
const MAX_OUTPUT_BYTES: usize = 1024 * 1024;
const MAX_LINE_BYTES: usize = 16 * 1024;
const MAX_PENDING_LINES: usize = 200;
const MAX_PENDING_BYTES: usize = 256 * 1024;
const EMIT_INTERVAL: Duration = Duration::from_millis(100);
const ETA_MIN_SPAN: Duration = Duration::from_secs(5);
const ETA_MIN_PROGRESS: f32 = 1.0;

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum TaskKind {
    Processing,
    ModelInstall,
    ClipPreparation,
    LocalScan,
    DriveScan,
    DriveDownload,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum TaskStatus {
    Queued,
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ProgressMode {
    Indeterminate,
    Determinate,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum OutputStream {
    Progress,
    Stdout,
    Stderr,
    System,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskRecord {
    pub id: String,
    pub kind: TaskKind,
    pub title: String,
    pub status: TaskStatus,
    pub progress_mode: ProgressMode,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stage_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stage_title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stage_progress_percent: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub progress_percent: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub completed_units: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_units: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub unit_label: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub eta_seconds: Option<u64>,
    pub cancellable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub related_item_id: Option<String>,
    pub started_at_millis: u64,
    pub updated_at_millis: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub finished_at_millis: Option<u64>,
    pub output_line_count: usize,
    pub output_truncated: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status_message: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskOutputLine {
    pub sequence: u64,
    pub timestamp_millis: u64,
    pub task_id: String,
    pub stream: OutputStream,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stage: Option<String>,
    pub text: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskSnapshot {
    pub sequence: u64,
    pub tasks: Vec<TaskRecord>,
    pub active_task_count: usize,
    pub history_count: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskOutputSnapshot {
    pub sequence: u64,
    pub lines: Vec<TaskOutputLine>,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct TaskRuntimeUpdate {
    sequence: u64,
    tasks: Vec<TaskRecord>,
    output: Vec<TaskOutputLine>,
    output_gaps: Vec<String>,
    output_gap_all: bool,
    removed_task_ids: Vec<String>,
    tasks_reset: bool,
    active_task_count: usize,
    history_count: usize,
}

#[derive(Debug, Clone)]
pub struct TaskSpec {
    pub id: String,
    pub kind: TaskKind,
    pub title: String,
    pub status: TaskStatus,
    pub progress_mode: ProgressMode,
    pub cancellable: bool,
    pub related_item_id: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct TaskProgress {
    pub stage_key: Option<String>,
    pub stage_title: Option<String>,
    pub stage_progress_percent: Option<f32>,
    pub progress_percent: Option<f32>,
    pub completed_units: Option<u64>,
    pub total_units: Option<u64>,
    pub unit_label: Option<String>,
    pub message: Option<String>,
}

struct ProgressSample {
    at: Instant,
    progress: f32,
}

#[derive(Default)]
struct OutputRing {
    lines: VecDeque<TaskOutputLine>,
    bytes: usize,
    truncated: bool,
}

#[derive(Default)]
struct TaskInner {
    tasks: HashMap<String, TaskRecord>,
    terminal_order: VecDeque<String>,
    terminal_count: usize,
    output: HashMap<String, OutputRing>,
    samples: HashMap<String, VecDeque<ProgressSample>>,
    sequence: u64,
    changed_tasks: HashSet<String>,
    pending_output: VecDeque<TaskOutputLine>,
    pending_output_bytes: usize,
    output_gaps: HashSet<String>,
    output_gap_all: bool,
    removed_task_ids: HashSet<String>,
    tasks_reset: bool,
    emit_scheduled: bool,
    last_live_timestamp_millis: u64,
}

#[derive(Default)]
pub struct TaskStateStore(Mutex<TaskInner>);

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

fn live_timestamp(inner: &mut TaskInner) -> u64 {
    let timestamp = now_millis().max(inner.last_live_timestamp_millis);
    inner.last_live_timestamp_millis = timestamp;
    timestamp
}

fn bounded(value: impl Into<String>, max: usize) -> String {
    value.into().chars().take(max).collect()
}

fn valid_id(value: &str) -> bool {
    (3..=180).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte))
}

fn diagnostic_contract() -> &'static serde_json::Value {
    static CONTRACT: OnceLock<serde_json::Value> = OnceLock::new();
    CONTRACT.get_or_init(|| {
        serde_json::from_str(include_str!(
            "../../../../src/lyricrail/diagnostic_contract.json"
        ))
        .expect("embedded diagnostic contract")
    })
}

pub(crate) fn stage_title(key: &str) -> Option<String> {
    diagnostic_contract()["stages"]
        .get(key)
        .and_then(serde_json::Value::as_str)
        .map(str::to_owned)
}

pub(crate) fn redact_diagnostic_text(value: &str) -> String {
    let contract = diagnostic_contract();
    let withheld = contract["withheld"].as_str().expect("withheld message");
    let phases = contract["phases"].as_object().expect("phases");
    let stages = contract["stages"].as_object().expect("stages");
    if value == withheld
        || contract["messages"]
            .as_array()
            .unwrap()
            .iter()
            .any(|item| item.as_str() == Some(value))
        || phases.values().any(|item| item.as_str() == Some(value))
        || stages.contains_key(value)
        || stages.values().any(|item| item.as_str() == Some(value))
    {
        return value.to_owned();
    }
    if value.len() > MAX_LINE_BYTES {
        return withheld.into();
    }
    let Ok(serde_json::Value::Object(item)) = serde_json::from_str(value) else {
        return withheld.into();
    };
    if item.get("kind") != Some(&contract["progressKind"]) {
        return withheld.into();
    }
    let Some(phase) = item.get("phase").and_then(serde_json::Value::as_str) else {
        return withheld.into();
    };
    let Some(message) = phases.get(phase) else {
        return withheld.into();
    };
    let numbers = contract["numericFields"].as_object().unwrap();
    if item.keys().any(|key| {
        !matches!(key.as_str(), "kind" | "phase" | "message") && !numbers.contains_key(key)
    }) {
        return withheld.into();
    }
    let mut output =
        serde_json::json!({"kind": contract["progressKind"], "phase": phase, "message": message});
    for (field, maximum) in numbers {
        if let Some(value) = item.get(field) {
            let Some(number) = value.as_f64() else {
                return withheld.into();
            };
            if !number.is_finite() || number < 0.0 || number > maximum.as_f64().unwrap() {
                return withheld.into();
            }
            output[field] = value.clone();
        }
    }
    output.to_string()
}

fn terminal(status: &TaskStatus) -> bool {
    matches!(
        status,
        TaskStatus::Succeeded | TaskStatus::Failed | TaskStatus::Cancelled
    )
}

fn task_priority(status: &TaskStatus) -> u8 {
    match status {
        TaskStatus::Running => 3,
        TaskStatus::Succeeded | TaskStatus::Failed | TaskStatus::Cancelled => 2,
        TaskStatus::Queued => 1,
    }
}

fn select_visible_tasks<'a>(
    tasks: &'a HashMap<String, TaskRecord>,
    ids: impl Iterator<Item = &'a str>,
) -> Vec<TaskRecord> {
    let mut heap = BinaryHeap::<Reverse<(u8, u64, &'a str)>>::new();
    for id in ids {
        let Some(task) = tasks.get(id) else {
            continue;
        };
        let rank = (task_priority(&task.status), task.updated_at_millis, id);
        if heap.len() < MAX_VISIBLE_TASKS {
            heap.push(Reverse(rank));
        } else if heap.peek().is_some_and(|Reverse(lowest)| rank > *lowest) {
            heap.pop();
            heap.push(Reverse(rank));
        }
    }
    let mut selected = heap
        .into_iter()
        .filter_map(|Reverse((_, _, id))| tasks.get(id).cloned())
        .collect::<Vec<_>>();
    selected.sort_by(|left, right| {
        task_priority(&right.status)
            .cmp(&task_priority(&left.status))
            .then_with(|| right.updated_at_millis.cmp(&left.updated_at_millis))
            .then_with(|| right.id.cmp(&left.id))
    });
    selected
}

fn task_snapshot_inner(inner: &TaskInner) -> Vec<TaskRecord> {
    select_visible_tasks(&inner.tasks, inner.tasks.keys().map(String::as_str))
}

fn update_history(inner: &mut TaskInner, id: &str, was_terminal: bool, is_terminal: bool) {
    if was_terminal && !is_terminal {
        inner.terminal_count = inner.terminal_count.saturating_sub(1);
        inner.terminal_order.retain(|candidate| candidate != id);
    } else if !was_terminal && is_terminal {
        inner.terminal_count = inner.terminal_count.saturating_add(1);
        inner.terminal_order.push_back(id.to_owned());
    }
    while inner.terminal_count > MAX_TASK_HISTORY {
        let Some(removable) = inner.terminal_order.pop_front() else {
            inner.terminal_count = inner
                .tasks
                .values()
                .filter(|task| terminal(&task.status))
                .count();
            break;
        };
        if !inner
            .tasks
            .get(&removable)
            .is_some_and(|task| terminal(&task.status))
        {
            continue;
        }
        inner.tasks.remove(&removable);
        inner.terminal_count = inner.terminal_count.saturating_sub(1);
        if !inner.tasks_reset {
            inner.removed_task_ids.insert(removable.clone());
            if inner.removed_task_ids.len() > MAX_VISIBLE_TASKS * 2 {
                inner.removed_task_ids.clear();
                inner.tasks_reset = true;
            }
        }
        inner.output.remove(&removable);
        inner.samples.remove(&removable);
        inner.output_gaps.remove(&removable);
        inner
            .pending_output
            .retain(|line| line.task_id != removable);
        inner.pending_output_bytes = inner
            .pending_output
            .iter()
            .map(|line| line.text.len())
            .sum();
    }
}

fn update_eta(inner: &mut TaskInner, id: &str, progress: f32) -> Option<u64> {
    let now = Instant::now();
    let samples = inner.samples.entry(id.to_owned()).or_default();
    if samples
        .back()
        .is_some_and(|sample| progress + f32::EPSILON < sample.progress)
    {
        samples.clear();
    }
    samples.push_back(ProgressSample { at: now, progress });
    while samples
        .front()
        .is_some_and(|sample| now.duration_since(sample.at) > Duration::from_secs(60))
    {
        samples.pop_front();
    }
    let first = samples.front()?;
    let last = samples.back()?;
    let span = last.at.duration_since(first.at);
    let delta = last.progress - first.progress;
    if span < ETA_MIN_SPAN || delta < ETA_MIN_PROGRESS || progress >= 100.0 {
        return None;
    }
    let rate = delta as f64 / span.as_secs_f64();
    (rate.is_finite() && rate > 0.0)
        .then(|| (((100.0 - progress) as f64 / rate).ceil() as u64).min(30 * 24 * 60 * 60))
}

fn mark_changed(inner: &mut TaskInner, id: &str) {
    inner.sequence = inner.sequence.saturating_add(1);
    inner.changed_tasks.insert(id.to_owned());
}

fn queue_pending_output(inner: &mut TaskInner, line: TaskOutputLine) {
    inner.pending_output_bytes = inner.pending_output_bytes.saturating_add(line.text.len());
    inner.pending_output.push_back(line);
    while inner.pending_output.len() > MAX_PENDING_LINES
        || inner.pending_output_bytes > MAX_PENDING_BYTES
    {
        if let Some(removed) = inner.pending_output.pop_front() {
            inner.pending_output_bytes = inner
                .pending_output_bytes
                .saturating_sub(removed.text.len());
            if !inner.output_gap_all {
                inner.output_gaps.insert(removed.task_id);
                if inner.output_gaps.len() > MAX_VISIBLE_TASKS * 2 {
                    inner.output_gaps.clear();
                    inner.output_gap_all = true;
                }
            }
        }
    }
}

fn emit_pending(app: &AppHandle) {
    let event = {
        let state = app.state::<TaskStateStore>();
        let Ok(mut inner) = state.0.lock() else {
            return;
        };
        inner.emit_scheduled = false;
        if inner.changed_tasks.is_empty()
            && inner.pending_output.is_empty()
            && inner.output_gaps.is_empty()
            && !inner.output_gap_all
            && inner.removed_task_ids.is_empty()
            && !inner.tasks_reset
        {
            return;
        }
        let changed_tasks = std::mem::take(&mut inner.changed_tasks);
        let tasks = select_visible_tasks(&inner.tasks, changed_tasks.iter().map(String::as_str));
        let output = inner.pending_output.drain(..).collect();
        let mut output_gaps = inner.output_gaps.drain().collect::<Vec<_>>();
        output_gaps.sort();
        let output_gap_all = std::mem::take(&mut inner.output_gap_all);
        let mut removed_task_ids = inner.removed_task_ids.drain().collect::<Vec<_>>();
        removed_task_ids.sort();
        let tasks_reset = std::mem::take(&mut inner.tasks_reset);
        let active_task_count = inner.tasks.len().saturating_sub(inner.terminal_count);
        let history_count = inner.terminal_count;
        inner.pending_output_bytes = 0;
        TaskRuntimeUpdate {
            sequence: inner.sequence,
            tasks,
            output,
            output_gaps,
            output_gap_all,
            removed_task_ids,
            tasks_reset,
            active_task_count,
            history_count,
        }
    };
    let _ = app.emit(TASKS_EVENT, event);
}

fn schedule_emit(app: &AppHandle) {
    let should_schedule = {
        let state = app.state::<TaskStateStore>();
        let Ok(mut inner) = state.0.lock() else {
            return;
        };
        if inner.emit_scheduled {
            false
        } else {
            inner.emit_scheduled = true;
            true
        }
    };
    if should_schedule {
        let app = app.clone();
        tauri::async_runtime::spawn_blocking(move || {
            thread::sleep(EMIT_INTERVAL);
            emit_pending(&app);
        });
    }
}

pub fn snapshot(app: &AppHandle) -> TaskSnapshot {
    app.state::<TaskStateStore>()
        .0
        .lock()
        .map(|inner| TaskSnapshot {
            sequence: inner.sequence,
            tasks: task_snapshot_inner(&inner),
            active_task_count: inner.tasks.len().saturating_sub(inner.terminal_count),
            history_count: inner.terminal_count,
        })
        .unwrap_or(TaskSnapshot {
            sequence: 0,
            tasks: Vec::new(),
            active_task_count: 0,
            history_count: 0,
        })
}

pub fn output_snapshot(app: &AppHandle, task_id: &str, after: u64) -> TaskOutputSnapshot {
    app.state::<TaskStateStore>()
        .0
        .lock()
        .map(|inner| {
            let ring = inner.output.get(task_id);
            TaskOutputSnapshot {
                sequence: inner.sequence,
                lines: ring
                    .into_iter()
                    .flat_map(|ring| ring.lines.iter())
                    .filter(|line| line.sequence > after)
                    .cloned()
                    .collect(),
                truncated: ring.is_some_and(|ring| ring.truncated),
            }
        })
        .unwrap_or(TaskOutputSnapshot {
            sequence: 0,
            lines: Vec::new(),
            truncated: false,
        })
}

pub fn start(app: &AppHandle, spec: TaskSpec) -> Result<(), String> {
    if !valid_id(&spec.id) {
        return Err("Task ID is invalid".into());
    }
    let id = spec.id.clone();
    {
        let state = app.state::<TaskStateStore>();
        let mut inner = state
            .0
            .lock()
            .map_err(|_| "Task state lock is poisoned".to_string())?;
        let now = live_timestamp(&mut inner);
        let was_terminal = inner
            .tasks
            .get(&id)
            .is_some_and(|task| terminal(&task.status));
        let is_terminal = terminal(&spec.status);
        if !inner.tasks.contains_key(&id)
            && !terminal(&spec.status)
            && inner.tasks.len().saturating_sub(inner.terminal_count) >= MAX_ACTIVE_TASKS
        {
            return Err("Too many active tasks are already registered".into());
        }
        let created = inner.tasks.get(&id).map_or(now, |task| {
            if terminal(&task.status) {
                now
            } else {
                task.started_at_millis
            }
        });
        let output_line_count = inner.output.get(&id).map_or(0, |ring| ring.lines.len());
        let output_truncated = inner.output.get(&id).is_some_and(|ring| ring.truncated);
        inner.tasks.insert(
            id.clone(),
            TaskRecord {
                id: id.clone(),
                kind: spec.kind,
                title: bounded(spec.title, 160),
                status: spec.status,
                progress_mode: spec.progress_mode,
                stage_key: None,
                stage_title: None,
                stage_progress_percent: None,
                progress_percent: None,
                completed_units: None,
                total_units: None,
                unit_label: None,
                eta_seconds: None,
                cancellable: spec.cancellable,
                related_item_id: spec.related_item_id,
                started_at_millis: created,
                updated_at_millis: now,
                finished_at_millis: None,
                output_line_count,
                output_truncated,
                status_message: None,
            },
        );
        update_history(&mut inner, &id, was_terminal, is_terminal);
        mark_changed(&mut inner, &id);
    }
    schedule_emit(app);
    Ok(())
}

pub fn restore(app: &AppHandle, mut record: TaskRecord) -> Result<(), String> {
    if !valid_id(&record.id) {
        return Err("Task ID is invalid".into());
    }
    record.title = bounded(record.title, 160);
    record.stage_key = record.stage_key.filter(|key| stage_title(key).is_some());
    record.stage_title = record.stage_key.as_deref().and_then(stage_title);
    record.status_message = record
        .status_message
        .map(|value| bounded(redact_diagnostic_text(&value), 240));
    record.progress_percent = record.progress_percent.map(|value| value.clamp(0.0, 100.0));
    record.stage_progress_percent = record
        .stage_progress_percent
        .map(|value| value.clamp(0.0, 100.0));
    record.eta_seconds = None;
    record.cancellable = record.cancellable && !terminal(&record.status);
    record.updated_at_millis = record.updated_at_millis.max(record.started_at_millis);
    record.finished_at_millis = record
        .finished_at_millis
        .map(|value| value.max(record.updated_at_millis));
    let id = record.id.clone();
    {
        let state = app.state::<TaskStateStore>();
        let mut inner = state
            .0
            .lock()
            .map_err(|_| "Task state lock is poisoned".to_string())?;
        if inner.tasks.contains_key(&id) {
            return Ok(());
        }
        if !terminal(&record.status)
            && inner.tasks.len().saturating_sub(inner.terminal_count) >= MAX_ACTIVE_TASKS
        {
            return Err("Too many active tasks are already registered".into());
        }
        record.output_line_count = 0;
        record.output_truncated = false;
        let is_terminal = terminal(&record.status);
        inner.last_live_timestamp_millis = inner
            .last_live_timestamp_millis
            .max(record.updated_at_millis);
        inner.tasks.insert(id.clone(), record);
        update_history(&mut inner, &id, false, is_terminal);
        mark_changed(&mut inner, &id);
    }
    schedule_emit(app);
    Ok(())
}

pub fn progress(app: &AppHandle, id: &str, update: TaskProgress) {
    let output_stage = update.stage_key.clone();
    let output_message = update.message.clone();
    let Some(state) = app.try_state::<TaskStateStore>() else {
        return;
    };
    if let Ok(mut inner) = state.0.lock() {
        if inner
            .tasks
            .get(id)
            .is_some_and(|task| terminal(&task.status))
        {
            return;
        }
        let measured_progress = update.progress_percent.map(|value| value.clamp(0.0, 100.0));
        let previous_progress = inner.tasks.get(id).and_then(|task| task.progress_percent);
        let regressed = measured_progress
            .zip(previous_progress)
            .is_some_and(|(measured, previous)| measured + f32::EPSILON < previous);
        let progress = measured_progress
            .map(|value| previous_progress.map_or(value, |previous| previous.max(value)))
            .or(previous_progress);
        let eta = if regressed {
            inner.samples.remove(id);
            None
        } else {
            measured_progress
                .and(progress)
                .and_then(|value| update_eta(&mut inner, id, value))
        };
        if inner.tasks.contains_key(id) {
            let updated_at = live_timestamp(&mut inner);
            let task = inner.tasks.get_mut(id).expect("task existence checked");
            task.status = TaskStatus::Running;
            task.stage_key = update.stage_key.filter(|key| stage_title(key).is_some());
            task.stage_title = task.stage_key.as_deref().and_then(stage_title).or_else(|| {
                update.stage_title.filter(|title| {
                    diagnostic_contract()["messages"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .any(|value| value.as_str() == Some(title.as_str()))
                })
            });
            task.stage_progress_percent = update
                .stage_progress_percent
                .map(|value| value.clamp(0.0, 100.0));
            task.progress_percent = progress;
            task.progress_mode = if progress.is_some() || update.total_units.is_some() {
                ProgressMode::Determinate
            } else {
                ProgressMode::Indeterminate
            };
            task.completed_units = update.completed_units;
            task.total_units = update.total_units;
            task.unit_label = update
                .unit_label
                .filter(|value| matches!(value.as_str(), "bytes" | "files" | "frames" | "items"));
            task.eta_seconds = eta;
            task.status_message = update
                .message
                .map(|value| bounded(redact_diagnostic_text(&value), 240));
            task.updated_at_millis = updated_at;
            mark_changed(&mut inner, id);
        }
    }
    schedule_emit(app);
    if let Some(message) = output_message {
        append_output(
            app,
            id,
            OutputStream::Progress,
            output_stage.as_deref(),
            &message,
        );
    }
}

pub fn finish(app: &AppHandle, id: &str, status: TaskStatus, message: Option<String>) {
    if !terminal(&status) {
        return;
    }
    let Some(state) = app.try_state::<TaskStateStore>() else {
        return;
    };
    if let Ok(mut inner) = state.0.lock()
        && inner.tasks.contains_key(id)
    {
        let now = live_timestamp(&mut inner);
        let task = inner.tasks.get_mut(id).expect("task existence checked");
        let was_terminal = terminal(&task.status);
        task.status = status;
        task.progress_percent = task.progress_percent.map(|value| {
            if task.status == TaskStatus::Succeeded {
                100.0
            } else {
                value
            }
        });
        task.stage_progress_percent = task.stage_progress_percent.map(|value| {
            if task.status == TaskStatus::Succeeded {
                100.0
            } else {
                value
            }
        });
        task.eta_seconds = None;
        task.cancellable = false;
        task.status_message = message.map(|value| bounded(redact_diagnostic_text(&value), 240));
        task.updated_at_millis = now;
        task.finished_at_millis = Some(now);
        update_history(&mut inner, id, was_terminal, true);
        mark_changed(&mut inner, id);
    }
    schedule_emit(app);
}

fn record_output(
    inner: &mut TaskInner,
    task_id: &str,
    stream: OutputStream,
    stage: Option<&str>,
    text: &str,
    mut timestamp_millis: u64,
    update_timestamp: bool,
) {
    if !inner.tasks.contains_key(task_id) {
        return;
    }
    if update_timestamp {
        timestamp_millis = timestamp_millis.max(inner.last_live_timestamp_millis);
        inner.last_live_timestamp_millis = timestamp_millis;
    }
    inner.sequence = inner.sequence.saturating_add(1);
    let line = TaskOutputLine {
        sequence: inner.sequence,
        timestamp_millis,
        task_id: task_id.to_owned(),
        stream,
        stage: stage
            .filter(|key| stage_title(key).is_some())
            .map(str::to_owned),
        text: redact_diagnostic_text(text),
    };
    let line_bytes = line.text.len();
    let ring = inner.output.entry(task_id.to_owned()).or_default();
    ring.bytes = ring.bytes.saturating_add(line_bytes);
    ring.lines.push_back(line.clone());
    while ring.lines.len() > MAX_OUTPUT_LINES || ring.bytes > MAX_OUTPUT_BYTES {
        if let Some(removed) = ring.lines.pop_front() {
            ring.bytes = ring.bytes.saturating_sub(removed.text.len());
            ring.truncated = true;
        }
    }
    queue_pending_output(inner, line);
    let output_state = inner
        .output
        .get(task_id)
        .map(|ring| (ring.lines.len(), ring.truncated));
    if let Some(task) = inner.tasks.get_mut(task_id) {
        if let Some((line_count, truncated)) = output_state {
            task.output_line_count = line_count;
            task.output_truncated = truncated;
        }
        if update_timestamp {
            task.updated_at_millis = timestamp_millis;
        }
    }
    inner.changed_tasks.insert(task_id.to_owned());
}

fn append_output_inner(
    app: &AppHandle,
    task_id: &str,
    stream: OutputStream,
    stage: Option<&str>,
    text: &str,
    timestamp_millis: u64,
    update_timestamp: bool,
) {
    let Some(state) = app.try_state::<TaskStateStore>() else {
        return;
    };
    if let Ok(mut inner) = state.0.lock() {
        record_output(
            &mut inner,
            task_id,
            stream,
            stage,
            text,
            timestamp_millis,
            update_timestamp,
        );
    }
    schedule_emit(app);
}

pub fn append_output(
    app: &AppHandle,
    task_id: &str,
    stream: OutputStream,
    stage: Option<&str>,
    text: &str,
) {
    append_output_inner(app, task_id, stream, stage, text, now_millis(), true);
}

pub fn restore_output(
    app: &AppHandle,
    task_id: &str,
    stream: OutputStream,
    stage: Option<&str>,
    text: &str,
    timestamp_millis: u64,
) {
    append_output_inner(app, task_id, stream, stage, text, timestamp_millis, false);
}

pub fn mark_output_truncated(app: &AppHandle, task_id: &str) {
    let Some(state) = app.try_state::<TaskStateStore>() else {
        return;
    };
    if let Ok(mut inner) = state.0.lock() {
        if let Some(ring) = inner.output.get_mut(task_id) {
            ring.truncated = true;
        }
        if let Some(task) = inner.tasks.get_mut(task_id) {
            task.output_truncated = true;
        }
        mark_changed(&mut inner, task_id);
    }
    schedule_emit(app);
}

pub fn append_command(app: &AppHandle, task_id: &str, program: &str, arguments: &[String]) {
    let executable = std::path::Path::new(program)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("<executable>");
    append_output(
        app,
        task_id,
        OutputStream::System,
        None,
        &format!("Executable: {executable}"),
    );
    let mut redact_next = false;
    for argument in arguments {
        let lower = argument.to_ascii_lowercase();
        let sensitive_flag = [
            "--token",
            "--password",
            "--secret",
            "--authorization",
            "--credential",
            "--api-key",
            "--api_key",
            "--access-token",
            "--access_token",
            "--client-secret",
            "--client_secret",
        ]
        .contains(&lower.as_str());
        let rendered = if redact_next {
            redact_next = false;
            "<redacted>"
        } else if sensitive_flag {
            redact_next = true;
            argument
        } else {
            argument
        };
        append_output(
            app,
            task_id,
            OutputStream::System,
            None,
            &format!("Argument: {rendered}"),
        );
    }
}

pub fn task(app: &AppHandle, id: &str) -> Option<TaskRecord> {
    app.state::<TaskStateStore>()
        .0
        .lock()
        .ok()
        .and_then(|inner| inner.tasks.get(id).cloned())
}

#[cfg(test)]
mod tests {
    use super::{
        MAX_OUTPUT_BYTES, MAX_OUTPUT_LINES, MAX_PENDING_BYTES, MAX_PENDING_LINES,
        MAX_VISIBLE_TASKS, OutputRing, OutputStream, ProgressMode, ProgressSample, TaskInner,
        TaskKind, TaskOutputLine, TaskRecord, TaskStatus, now_millis, queue_pending_output,
        redact_diagnostic_text, task_snapshot_inner, terminal, update_eta, update_history,
    };
    use std::{
        collections::VecDeque,
        time::{Duration, Instant},
    };

    #[test]
    fn closed_diagnostics_are_projected_before_ring_and_event_storage_for_every_stream() {
        let cases: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../tests/fixtures/diagnostics-v1.json"
        ))
        .unwrap();
        let mut inner = TaskInner::default();
        inner.tasks.insert(
            "fixture-task".into(),
            record("fixture-task", TaskStatus::Running),
        );
        for case in cases.as_array().unwrap() {
            let input = case["input"].as_str().unwrap();
            let output = redact_diagnostic_text(input);
            assert!(!output.contains("TOPSECRET"));
            if case["expected"].is_object() {
                assert_eq!(
                    serde_json::from_str::<serde_json::Value>(&output).unwrap(),
                    case["expected"]
                );
            } else {
                assert_eq!(
                    output,
                    case["expected"]
                        .as_str()
                        .unwrap_or(super::diagnostic_contract()["withheld"].as_str().unwrap())
                );
            }
            for stream in [
                OutputStream::Stdout,
                OutputStream::Stderr,
                OutputStream::Progress,
                OutputStream::System,
            ] {
                super::record_output(
                    &mut inner,
                    "fixture-task",
                    stream.clone(),
                    Some("TOPSECRET"),
                    input,
                    1,
                    true,
                );
                super::record_output(
                    &mut inner,
                    "fixture-task",
                    stream,
                    Some("TOPSECRET"),
                    input,
                    1,
                    false,
                );
            }
        }
        for line in &inner.pending_output {
            assert!(!serde_json::to_string(line).unwrap().contains("TOPSECRET"));
        }
        for line in &inner.output["fixture-task"].lines {
            assert!(!serde_json::to_string(line).unwrap().contains("TOPSECRET"));
        }
    }

    fn record(id: &str, status: TaskStatus) -> TaskRecord {
        TaskRecord {
            id: id.into(),
            kind: TaskKind::Processing,
            title: id.into(),
            status,
            progress_mode: ProgressMode::Indeterminate,
            stage_key: None,
            stage_title: None,
            stage_progress_percent: None,
            progress_percent: None,
            completed_units: None,
            total_units: None,
            unit_label: None,
            eta_seconds: None,
            cancellable: false,
            related_item_id: None,
            started_at_millis: now_millis(),
            updated_at_millis: now_millis(),
            finished_at_millis: None,
            output_line_count: 0,
            output_truncated: false,
            status_message: None,
        }
    }

    #[test]
    fn terminal_history_is_bounded_without_rejecting_a_large_sequential_queue() {
        let mut inner = TaskInner::default();
        for index in 0..605 {
            let id = format!("task-{index}");
            inner
                .tasks
                .insert(id.clone(), record(&id, TaskStatus::Succeeded));
            update_history(&mut inner, &id, false, true);
        }
        for index in 0..300 {
            let id = format!("active-{index}");
            let mut active = record(&id, TaskStatus::Running);
            active.updated_at_millis = index;
            inner.tasks.insert(id.clone(), active);
            update_history(&mut inner, &id, false, false);
        }
        assert_eq!(
            inner
                .tasks
                .values()
                .filter(|task| task.status == TaskStatus::Running)
                .count(),
            300
        );
        assert_eq!(
            inner
                .tasks
                .values()
                .filter(|task| terminal(&task.status))
                .count(),
            100
        );
        assert!(inner.tasks_reset);
        assert_eq!(task_snapshot_inner(&inner).len(), MAX_VISIBLE_TASKS);
        assert_eq!(task_snapshot_inner(&inner)[0].id, "active-299");
        assert!(inner.tasks.contains_key("active-0"));
        assert!(
            !task_snapshot_inner(&inner)
                .iter()
                .any(|task| task.id == "active-0")
        );
        assert!(!terminal(&TaskStatus::Running));
    }

    #[test]
    fn eta_requires_a_real_sample_window_and_monotonic_progress() {
        let mut inner = TaskInner::default();
        let now = Instant::now();
        inner.samples.insert(
            "task".into(),
            VecDeque::from([
                ProgressSample {
                    at: now - Duration::from_secs(10),
                    progress: 10.0,
                },
                ProgressSample {
                    at: now - Duration::from_secs(5),
                    progress: 20.0,
                },
            ]),
        );
        let eta = update_eta(&mut inner, "task", 30.0).unwrap();
        assert!((30..=40).contains(&eta));
        assert_eq!(update_eta(&mut inner, "task", 5.0), None);
    }

    #[test]
    fn output_redaction_and_ring_have_hard_bounds() {
        let path =
            redact_diagnostic_text("failed C:\\Music Library\\Private Song.mp4 trailing-name.mp4");
        let remote = redact_diagnostic_text("download https://host/path?signed=x");
        let secret = redact_diagnostic_text(
            "api_key=TOP access_token=BEARER client_secret=HUSH x-goog-signature=GOOG x-amz-signature=AMZ signature=RAW Authorization: Bearer value",
        );
        assert!(!path.contains("Private Song.mp4"));
        assert!(!path.contains("trailing-name.mp4"));
        assert!(!remote.contains("host/path"));
        assert!(!secret.contains("TOP"));
        assert!(!secret.contains("BEARER"));
        assert!(!secret.contains("HUSH"));
        assert!(!secret.contains("GOOG"));
        assert!(!secret.contains("AMZ"));
        assert!(!secret.contains("RAW"));
        assert!(!secret.contains("value"));
        assert!(path.contains("Diagnostic text withheld"));
        assert!(remote.contains("Diagnostic text withheld"));
        assert!(secret.contains("Diagnostic text withheld"));

        let mut ring = OutputRing::default();
        for sequence in 0..(MAX_OUTPUT_LINES + 20) as u64 {
            let line = TaskOutputLine {
                sequence,
                timestamp_millis: 0,
                task_id: "task".into(),
                stream: OutputStream::Stdout,
                stage: None,
                text: "x".repeat(MAX_OUTPUT_BYTES / MAX_OUTPUT_LINES + 20),
            };
            ring.bytes += line.text.len();
            ring.lines.push_back(line);
            while ring.lines.len() > MAX_OUTPUT_LINES || ring.bytes > MAX_OUTPUT_BYTES {
                let removed = ring.lines.pop_front().unwrap();
                ring.bytes -= removed.text.len();
                ring.truncated = true;
            }
        }
        assert!(ring.lines.len() <= MAX_OUTPUT_LINES);
        assert!(ring.bytes <= MAX_OUTPUT_BYTES);
        assert!(ring.truncated);
    }

    #[test]
    fn burst_delivery_marks_a_replay_gap_without_growing_the_pending_buffer() {
        let mut inner = TaskInner::default();
        for sequence in 1..=500 {
            queue_pending_output(
                &mut inner,
                TaskOutputLine {
                    sequence,
                    timestamp_millis: sequence,
                    task_id: "burst-task".into(),
                    stream: OutputStream::Stdout,
                    stage: Some("fixture".into()),
                    text: "x".repeat(2_048),
                },
            );
        }
        assert!(inner.pending_output.len() <= MAX_PENDING_LINES);
        assert!(inner.pending_output_bytes <= MAX_PENDING_BYTES);
        assert!(inner.output_gaps.contains("burst-task"));
        assert_eq!(inner.pending_output.back().unwrap().sequence, 500);

        let mut many_tasks = TaskInner::default();
        for sequence in 1..=900 {
            queue_pending_output(
                &mut many_tasks,
                TaskOutputLine {
                    sequence,
                    timestamp_millis: sequence,
                    task_id: format!("task-{sequence}"),
                    stream: OutputStream::Stdout,
                    stage: None,
                    text: "x".repeat(2_048),
                },
            );
        }
        assert!(many_tasks.output_gap_all);
        assert!(many_tasks.output_gaps.is_empty());
    }

    #[test]
    fn absent_task_measurements_are_omitted_instead_of_serialized_as_json_null() {
        let encoded = serde_json::to_value(record("wire-task", TaskStatus::Running)).unwrap();
        assert_eq!(encoded["progressMode"], "indeterminate");
        for field in [
            "stageKey",
            "stageTitle",
            "stageProgressPercent",
            "progressPercent",
            "completedUnits",
            "totalUnits",
            "unitLabel",
            "etaSeconds",
            "relatedItemId",
            "finishedAtMillis",
            "statusMessage",
        ] {
            assert!(encoded.get(field).is_none(), "{field} should be omitted");
        }
    }
}
