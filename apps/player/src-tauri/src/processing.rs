use std::{
    collections::{HashMap, VecDeque},
    fs,
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::Mutex,
    thread,
    time::{SystemTime, UNIX_EPOCH},
};

use chrono::DateTime;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager};

use crate::{
    CatalogState,
    catalog::{
        CatalogItem, ItemLocation, ItemStatus, ProcessingEvidenceStatus, ProcessingTaskEvidence,
    },
    issues,
    local_source::scan_files,
    model_installer,
    runtime::{ResolvedRuntime, resolve_runtime, runtime_available_hint},
    tasks::{
        self, OutputStream, ProgressMode, TaskKind, TaskProgress, TaskRecord, TaskSpec, TaskStatus,
    },
};

struct WorkerProcess {
    generation: u64,
    child: Child,
    stdin: Option<ChildStdin>,
    #[cfg(windows)]
    _job: ProcessJob,
}

impl Drop for WorkerProcess {
    fn drop(&mut self) {
        self.stdin.take();
        #[cfg(unix)]
        {
            if let Ok(process_id) = i32::try_from(self.child.id()) {
                unsafe {
                    libc::kill(-process_id, libc::SIGTERM);
                }
            }
        }
        #[cfg(windows)]
        {
            let _ = self.child.kill();
        }
        let _ = self.child.wait();
    }
}

#[derive(Debug)]
struct PendingJob {
    transient_lyrics: Option<PathBuf>,
    job_id: Option<String>,
    cancel_requested: bool,
}

#[derive(Default)]
struct ProcessingInner {
    process: Option<WorkerProcess>,
    last_worker_generation: u64,
    pending: HashMap<String, PendingJob>,
    waiting: VecDeque<WorkerRequest>,
    active_request: Option<String>,
}

#[derive(Default)]
pub struct ProcessingState {
    inner: Mutex<ProcessingInner>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkerRequest {
    request_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    resume_job_id: Option<String>,
    media_path: PathBuf,
    lyrics_path: PathBuf,
    #[serde(skip_serializing_if = "Option::is_none")]
    start_seconds: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    end_seconds: Option<f64>,
    title: String,
    artist: Option<String>,
    composer: Option<String>,
}

#[derive(Debug)]
struct DispatchFailure {
    request_id: String,
    transient_lyrics: Option<PathBuf>,
    message: String,
}

#[derive(Debug)]
struct DispatchFailureProjection<'a> {
    request_id: &'a str,
    message: &'a str,
    evidence_status: ProcessingEvidenceStatus,
    item_status: ItemStatus,
    task_status: TaskStatus,
    report_issue: bool,
}

fn dispatch_failure_projection(
    failure: &DispatchFailure,
    index: usize,
) -> DispatchFailureProjection<'_> {
    DispatchFailureProjection {
        request_id: &failure.request_id,
        message: &failure.message,
        evidence_status: ProcessingEvidenceStatus::Failed,
        item_status: ItemStatus::Failed,
        task_status: TaskStatus::Failed,
        report_issue: index < 100,
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkerEvent {
    kind: String,
    #[serde(default)]
    request_id: String,
    #[serde(default)]
    job_id: Option<String>,
    #[serde(default)]
    status: Option<String>,
    #[serde(default)]
    progress_percent: Option<f32>,
    #[serde(default)]
    stage: Option<String>,
    #[serde(default)]
    stage_title: Option<String>,
    #[serde(default)]
    stage_progress_percent: Option<f32>,
    #[serde(default)]
    output_stream: Option<String>,
    #[serde(default)]
    output_text: Option<String>,
    #[serde(default)]
    package_path: Option<PathBuf>,
    #[serde(default)]
    error: Option<Value>,
}

const MAX_DURABLE_MANIFEST_BYTES: u64 = 2 * 1024 * 1024;
const MAX_DURABLE_LYRIC_BYTES: u64 = 4 * 1024 * 1024;
const MAX_DURABLE_LOG_BYTES: u64 = 1024 * 1024;
const MAX_DURABLE_LOG_LINES: usize = 1_000;
const WORKER_EXIT_MESSAGE: &str = "Processing worker exited unexpectedly before reporting completion. Retry this task; its diagnostic output has been preserved.";
const VALID_DURABLE_STAGES: &[&str] = &[
    "probe",
    "extract_audio",
    "separate_stems",
    "load_lyrics",
    "align_lyrics",
    "classify_roles",
    "prepare_visuals",
    "render_subtitles",
    "render_player_media",
    "create_thumbnail",
    "package_lrail",
    "cleanup_intermediates",
];

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct DurableJobManifest {
    schema_version: u32,
    kind: String,
    job_id: String,
    status: String,
    current_stage: Option<String>,
    progress_percent: f32,
    request: DurableRequest,
    stages: Vec<DurableStage>,
}

#[derive(Debug, Deserialize)]
struct DurableRequest {
    lyrics: DurableLyrics,
}

#[derive(Debug, Deserialize)]
struct DurableLyrics {
    snapshot: String,
    sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct DurableStage {
    key: String,
    title: String,
    status: String,
    progress_percent: f32,
}

struct DurableOutputLine {
    timestamp_millis: u64,
    stream: OutputStream,
    stage: Option<String>,
    text: String,
}

fn processing_evidence(
    existing: Option<ProcessingTaskEvidence>,
    job_id: Option<String>,
    status: ProcessingEvidenceStatus,
    progress_percent: f32,
    stage: Option<(String, Option<String>, Option<f32>)>,
    finished: bool,
) -> ProcessingTaskEvidence {
    let now = wall_clock_millis();
    let started_at_millis = existing
        .as_ref()
        .map_or(now, |evidence| evidence.started_at_millis);
    let (stage_key, stage_title, stage_progress_percent) = stage
        .map_or((None, None, None), |(key, title, progress)| {
            (Some(key), title, progress)
        });
    ProcessingTaskEvidence {
        job_id: job_id.or_else(|| {
            existing
                .as_ref()
                .and_then(|evidence| evidence.job_id.clone())
        }),
        status,
        progress_percent: existing.as_ref().map_or(progress_percent, |evidence| {
            evidence.progress_percent.max(progress_percent)
        }),
        stage_key: stage_key.or_else(|| {
            existing
                .as_ref()
                .and_then(|evidence| evidence.stage_key.clone())
        }),
        stage_title: stage_title.or_else(|| {
            existing
                .as_ref()
                .and_then(|evidence| evidence.stage_title.clone())
        }),
        stage_progress_percent: stage_progress_percent.or_else(|| {
            existing
                .as_ref()
                .and_then(|evidence| evidence.stage_progress_percent)
        }),
        started_at_millis,
        updated_at_millis: now,
        finished_at_millis: finished.then_some(now),
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProcessingStatus {
    pub worker_running: bool,
    pub pending_jobs: usize,
    pub runtime_available: bool,
    pub runtime_error: Option<String>,
}

fn worker_command(runtime: &ResolvedRuntime, data_root: &Path, playback_state: &Path) -> Command {
    let mut command = Command::new(&runtime.python);
    command
        .current_dir(&runtime.root)
        .env("LYRICRAIL_HOME", &runtime.root)
        .env("LYRICRAIL_DATA_HOME", data_root)
        .env("LYRICRAIL_PLAYBACK_STATE_FILE", playback_state)
        .env("LYRICRAIL_PERSISTENT_WORKER", "1")
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONUNBUFFERED", "1")
        .arg(if runtime.integrity == "signed-verified" {
            "-I"
        } else {
            "-s"
        })
        .arg("-X")
        .arg("utf8");
    if runtime.integrity != "signed-verified" {
        command
            .env("PYTHONPATH", runtime.root.join("src"))
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8:strict");
    }
    if let Some(path) = &runtime.ffmpeg {
        command.env("LYRICRAIL_FFMPEG", path);
    }
    if let Some(path) = &runtime.ffprobe {
        command.env("LYRICRAIL_FFPROBE", path);
    }
    if let Some(path) = &runtime.lrail {
        command.env("LYRICRAIL_LRAIL", path);
    }
    command
        .arg("-m")
        .arg("lyricrail")
        .arg("worker")
        .arg("--root")
        .arg(&runtime.root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    command
}

#[cfg(windows)]
struct ProcessJob(std::os::windows::io::OwnedHandle);

#[cfg(windows)]
impl Drop for ProcessJob {
    fn drop(&mut self) {
        use std::os::windows::io::AsRawHandle;
        let _ = self.0.as_raw_handle();
    }
}

#[cfg(windows)]
impl ProcessJob {
    fn assign_and_lower_priority(child: &Child) -> std::io::Result<Self> {
        use std::{
            ffi::c_void,
            mem::{size_of, zeroed},
            os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
            ptr,
        };
        use windows_sys::Win32::System::{
            JobObjects::{
                AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
                SetInformationJobObject,
            },
            Threading::{BELOW_NORMAL_PRIORITY_CLASS, SetPriorityClass},
        };
        let raw = unsafe { CreateJobObjectW(ptr::null(), ptr::null()) };
        if raw.is_null() {
            return Err(std::io::Error::last_os_error());
        }
        let owned = unsafe { OwnedHandle::from_raw_handle(raw.cast()) };
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if unsafe {
            SetInformationJobObject(
                owned.as_raw_handle().cast(),
                JobObjectExtendedLimitInformation,
                (&raw const limits).cast::<c_void>(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        } == 0
            || unsafe {
                AssignProcessToJobObject(owned.as_raw_handle().cast(), child.as_raw_handle().cast())
            } == 0
            || unsafe {
                SetPriorityClass(child.as_raw_handle().cast(), BELOW_NORMAL_PRIORITY_CLASS)
            } == 0
        {
            return Err(std::io::Error::last_os_error());
        }
        Ok(Self(owned))
    }
}

#[cfg(unix)]
fn lower_priority(child: &Child) -> std::io::Result<()> {
    let result = unsafe { libc::setpriority(libc::PRIO_PROCESS, child.id(), 10) };
    if result == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

fn save_and_emit(app: &AppHandle) {
    let state = app.state::<CatalogState>();
    if let Ok(catalog) = state.0.lock() {
        let _ = catalog.save();
        let _ = app.emit("library-changed", catalog.snapshot());
    }
}

fn display_error(value: Option<Value>) -> String {
    match value {
        Some(Value::String(message)) => message,
        Some(Value::Object(value)) => value
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("Processing failed")
            .to_owned(),
        _ => "Processing failed".into(),
    }
}

fn error_code(value: Option<&Value>) -> Option<&str> {
    value
        .and_then(Value::as_object)
        .and_then(|value| value.get("code"))
        .and_then(Value::as_str)
}

fn safe_runtime_detail(value: &str) -> String {
    let mut detail = resolve_runtime()
        .map(|runtime| value.replace(&runtime.root.display().to_string(), "<runtime>"))
        .unwrap_or_else(|_| value.to_owned());
    if let Some(profile) = std::env::var_os("USERPROFILE") {
        detail = detail.replace(&PathBuf::from(profile).display().to_string(), "<user>");
    }
    detail
        .split_whitespace()
        .map(|part| {
            if part.starts_with("http://") || part.starts_with("https://") {
                "<remote address>"
            } else {
                part
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(4_000)
        .collect()
}

fn item_title(app: &AppHandle, item_id: &str) -> String {
    app.state::<CatalogState>()
        .0
        .lock()
        .ok()
        .and_then(|catalog| catalog.item(item_id).map(|item| item.title.clone()))
        .unwrap_or_else(|| "karaoke song".into())
}

fn apply_current_generation<T>(
    observed_generation: u64,
    current_generation: Option<u64>,
    action: impl FnOnce() -> T,
) -> Option<T> {
    (current_generation == Some(observed_generation)).then(action)
}

fn with_current_worker<T>(
    app: &AppHandle,
    generation: u64,
    action: impl FnOnce(&mut ProcessingInner) -> T,
) -> Option<T> {
    let state = app.state::<ProcessingState>();
    let mut inner = state.inner.lock().ok()?;
    let current_generation = inner.process.as_ref().map(|process| process.generation);
    apply_current_generation(generation, current_generation, || action(&mut inner))
}

fn handle_event(app: &AppHandle, event: WorkerEvent, generation: u64) {
    match event.kind.as_str() {
        "lyricrail.worker.output" => {
            if !event.request_id.is_empty()
                && let Some(text) = event.output_text.as_deref()
            {
                let stream = match event.output_stream.as_deref() {
                    Some("stderr") => OutputStream::Stderr,
                    Some("progress") => OutputStream::Progress,
                    Some("system") => OutputStream::System,
                    _ => OutputStream::Stdout,
                };
                let _ = with_current_worker(app, generation, |inner| {
                    if inner.active_request.as_deref() == Some(&event.request_id) {
                        tasks::append_output(
                            app,
                            &event.request_id,
                            stream,
                            event.stage.as_deref(),
                            text,
                        );
                    }
                });
            }
        }
        "lyricrail.worker.progress" => {
            let Some((cancel_job, assigned_job)) = with_current_worker(app, generation, |inner| {
                if inner.active_request.as_deref() != Some(&event.request_id) {
                    return None;
                }
                let pending = inner.pending.get_mut(&event.request_id)?;
                let changed = event.job_id.is_some() && pending.job_id != event.job_id;
                pending.job_id.clone_from(&event.job_id);
                let job_id = pending
                    .cancel_requested
                    .then(|| pending.job_id.clone())
                    .flatten();
                if job_id.is_some() {
                    pending.cancel_requested = false;
                }
                Some((job_id, changed.then(|| pending.job_id.clone()).flatten()))
            })
            .flatten() else {
                return;
            };
            if let Some(job_id) = cancel_job {
                request_cancel(app.clone(), job_id);
            }
            let progress = event.progress_percent.unwrap_or(0.0);
            if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
                let assigned_changed = assigned_job.is_some();
                if let Some(job_id) = assigned_job {
                    catalog.set_processing_job_id(&event.request_id, Some(job_id));
                }
                let existing = catalog
                    .item(&event.request_id)
                    .and_then(|item| item.processing_task_evidence.clone());
                let stage_changed = existing
                    .as_ref()
                    .and_then(|evidence| evidence.stage_key.as_ref())
                    != event.stage.as_ref();
                let evidence = processing_evidence(
                    existing,
                    event.job_id.clone(),
                    ProcessingEvidenceStatus::Running,
                    progress,
                    event
                        .stage
                        .clone()
                        .map(|key| (key, event.stage_title.clone(), event.stage_progress_percent)),
                    false,
                );
                catalog.set_processing_task_evidence(&event.request_id, evidence);
                catalog.set_progress(
                    &event.request_id,
                    ItemStatus::Processing,
                    progress,
                    event.stage.clone(),
                );
                if assigned_changed || stage_changed {
                    let _ = catalog.save();
                }
            }
            tasks::progress(
                app,
                &event.request_id,
                TaskProgress {
                    stage_key: event.stage.clone(),
                    stage_title: event.stage_title,
                    stage_progress_percent: event.stage_progress_percent,
                    progress_percent: Some(progress),
                    message: event.stage,
                    ..Default::default()
                },
            );
        }
        "lyricrail.worker.completed" if event.status.as_deref() == Some("succeeded") => {
            let mut completed_ok = false;
            let mut failure_detail = None;
            let Some((transient, dispatch_failures)) =
                with_current_worker(app, generation, |inner| {
                    if inner.active_request.as_deref() != Some(&event.request_id) {
                        return None;
                    }
                    let pending = inner.pending.remove(&event.request_id);
                    inner.active_request = None;
                    let failures = dispatch_next(app, inner).err().unwrap_or_default();
                    Some((
                        pending.and_then(|pending| pending.transient_lyrics),
                        failures,
                    ))
                })
                .flatten()
            else {
                return;
            };
            if let Some(path) = transient {
                let _ = fs::remove_file(path);
            }
            let packaged = event
                .package_path
                .and_then(|path| scan_files(vec![path]).ok())
                .and_then(|mut items| (items.len() == 1).then(|| items.remove(0)));
            if let Some(mut package) = packaged {
                package.processing_job_id.clone_from(&event.job_id);
                if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
                    let existing = catalog
                        .item(&event.request_id)
                        .and_then(|item| item.processing_task_evidence.clone());
                    package.processing_task_evidence = Some(processing_evidence(
                        existing.clone(),
                        event.job_id.clone(),
                        ProcessingEvidenceStatus::Succeeded,
                        100.0,
                        None,
                        true,
                    ));
                    match catalog.complete_processing(&event.request_id, package) {
                        Ok(_) => completed_ok = true,
                        Err(error) => {
                            failure_detail = Some(error.clone());
                            catalog.set_processing_task_evidence(
                                &event.request_id,
                                processing_evidence(
                                    existing,
                                    event.job_id.clone(),
                                    ProcessingEvidenceStatus::Failed,
                                    0.0,
                                    None,
                                    true,
                                ),
                            );
                            catalog.set_progress(
                                &event.request_id,
                                ItemStatus::Failed,
                                0.0,
                                Some(error),
                            );
                        }
                    }
                }
            } else if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
                failure_detail = Some("Worker completed without a valid package".into());
                let existing = catalog
                    .item(&event.request_id)
                    .and_then(|item| item.processing_task_evidence.clone());
                catalog.set_processing_task_evidence(
                    &event.request_id,
                    processing_evidence(
                        existing,
                        event.job_id.clone(),
                        ProcessingEvidenceStatus::Failed,
                        0.0,
                        None,
                        true,
                    ),
                );
                catalog.set_progress(
                    &event.request_id,
                    ItemStatus::Failed,
                    0.0,
                    Some("Worker completed without a valid package".into()),
                );
            }
            save_and_emit(app);
            tasks::finish(
                app,
                &event.request_id,
                if completed_ok {
                    TaskStatus::Succeeded
                } else {
                    TaskStatus::Failed
                },
                Some(
                    if completed_ok {
                        "Authenticated package verified and added to Library"
                    } else {
                        "Worker output could not be added as a valid package"
                    }
                    .into(),
                ),
            );
            if !completed_ok {
                issues::report(
                    app,
                    issues::processing_failure_issue(
                        &event.request_id,
                        &item_title(app, &event.request_id),
                        failure_detail
                            .as_deref()
                            .unwrap_or("Worker output could not be added as a valid package"),
                    ),
                );
            }
            handle_dispatch_failures(app, dispatch_failures);
        }
        "lyricrail.worker.completed" | "lyricrail.worker.failed" => {
            let failure_message = display_error(event.error.clone());
            let cancelled = event.status.as_deref() == Some("cancelled");
            let Some((transient, dispatch_failures)) =
                with_current_worker(app, generation, |inner| {
                    if inner.active_request.as_deref() != Some(&event.request_id) {
                        return None;
                    }
                    let pending = inner.pending.remove(&event.request_id);
                    inner.active_request = None;
                    let failures = dispatch_next(app, inner).err().unwrap_or_default();
                    Some((
                        pending.and_then(|pending| pending.transient_lyrics),
                        failures,
                    ))
                })
                .flatten()
            else {
                return;
            };
            if let Some(path) = transient {
                let _ = fs::remove_file(path);
            }
            if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
                if let Some(job_id) = event.job_id.clone() {
                    catalog.set_processing_job_id(&event.request_id, Some(job_id));
                }
                let existing = catalog
                    .item(&event.request_id)
                    .and_then(|item| item.processing_task_evidence.clone());
                catalog.set_processing_task_evidence(
                    &event.request_id,
                    processing_evidence(
                        existing,
                        event.job_id.clone(),
                        if cancelled {
                            ProcessingEvidenceStatus::Cancelled
                        } else {
                            ProcessingEvidenceStatus::Failed
                        },
                        0.0,
                        None,
                        true,
                    ),
                );
                catalog.set_progress(
                    &event.request_id,
                    ItemStatus::Failed,
                    0.0,
                    Some(failure_message.clone()),
                );
            }
            save_and_emit(app);
            tasks::finish(
                app,
                &event.request_id,
                if cancelled {
                    TaskStatus::Cancelled
                } else {
                    TaskStatus::Failed
                },
                Some(failure_message.clone()),
            );
            if !cancelled {
                issues::report(
                    app,
                    issues::processing_failure_issue(
                        &event.request_id,
                        &item_title(app, &event.request_id),
                        &failure_message,
                    ),
                );
            }
            handle_dispatch_failures(app, dispatch_failures);
        }
        "lyricrail.worker.fatal" => {
            let models_missing =
                error_code(event.error.as_ref()) == Some("PROCESSING_MODELS_MISSING");
            let detail = display_error(event.error);
            let Some(pending) = with_current_worker(app, generation, |inner| {
                inner.waiting.clear();
                inner.active_request = None;
                std::mem::take(&mut inner.pending)
            }) else {
                return;
            };
            if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
                for id in pending.keys() {
                    let existing = catalog
                        .item(id)
                        .and_then(|item| item.processing_task_evidence.clone());
                    catalog.set_processing_task_evidence(
                        id,
                        processing_evidence(
                            existing,
                            None,
                            ProcessingEvidenceStatus::Failed,
                            0.0,
                            None,
                            true,
                        ),
                    );
                    catalog.set_progress(
                        id,
                        if models_missing {
                            ItemStatus::SetupRequired
                        } else {
                            ItemStatus::Failed
                        },
                        0.0,
                        Some(
                            if models_missing {
                                "Processing models are not installed; open Issues to resolve setup"
                            } else {
                                "Processing runtime failed its startup checks; open Issues for details"
                            }
                            .into(),
                        ),
                    );
                }
            }
            save_and_emit(app);
            for id in pending.keys() {
                tasks::finish(
                    app,
                    id,
                    TaskStatus::Failed,
                    Some(
                        if models_missing {
                            "Processing setup required"
                        } else {
                            "Processing runtime unavailable"
                        }
                        .into(),
                    ),
                );
            }
            if models_missing {
                issues::report(
                    app,
                    issues::models_missing_issue(
                        &safe_runtime_detail(&detail),
                        model_installer::install_is_allowed(),
                    ),
                );
            } else {
                issues::report(
                    app,
                    issues::generic_issue(
                        "processing.runtime-startup",
                        "processing",
                        "Processing runtime unavailable",
                        "The local worker could not start. Review the technical detail, correct the runtime, then retry the song.",
                        Some(safe_runtime_detail(&detail)),
                        None,
                    ),
                );
            }
        }
        _ => {}
    }
}

fn start_readers(
    app: AppHandle,
    generation: u64,
    stdout: impl std::io::Read + Send + 'static,
    stderr: impl std::io::Read + Send + 'static,
) {
    let stdout_app = app.clone();
    let disconnect_app = stdout_app.clone();
    thread::spawn(move || {
        read_worker_stdout(
            stdout,
            |line, truncated| {
                if truncated {
                    if let Some(task_id) = active_task_id(&stdout_app, generation) {
                        tasks::append_output(
                            &stdout_app,
                            &task_id,
                            OutputStream::Stderr,
                            None,
                            "Worker event exceeded its bounded line limit and was ignored",
                        );
                    }
                } else if let Ok(event) = serde_json::from_str::<WorkerEvent>(line) {
                    handle_event(&stdout_app, event, generation);
                }
            },
            || handle_worker_disconnect(&disconnect_app, generation),
        );
    });
    let stderr_app = app;
    thread::spawn(move || {
        read_bounded_lines(stderr, 16 * 1024, |line, truncated| {
            if let Some(task_id) = active_task_id(&stderr_app, generation) {
                let rendered = if truncated {
                    format!("{line} ... <line truncated>")
                } else {
                    line.to_owned()
                };
                tasks::append_output(&stderr_app, &task_id, OutputStream::Stderr, None, &rendered);
            }
        });
    });
}

fn read_worker_stdout(
    stdout: impl Read,
    on_line: impl FnMut(&str, bool),
    on_disconnect: impl FnOnce(),
) {
    read_bounded_lines(stdout, 1024 * 1024, on_line);
    on_disconnect();
}

fn active_task_id(app: &AppHandle, generation: u64) -> Option<String> {
    with_current_worker(app, generation, |inner| inner.active_request.clone()).flatten()
}

fn take_worker_disconnect_failure(inner: &mut ProcessingInner) -> Option<DispatchFailure> {
    let request_id = inner.active_request.take()?;
    inner
        .pending
        .remove(&request_id)
        .map(|pending| DispatchFailure {
            request_id,
            transient_lyrics: pending.transient_lyrics,
            message: WORKER_EXIT_MESSAGE.into(),
        })
}

fn recover_worker_disconnect(
    inner: &mut ProcessingInner,
    observed_generation: u64,
    current_generation: Option<u64>,
    release_and_dispatch: impl FnOnce(&mut ProcessingInner) -> Result<(), Vec<DispatchFailure>>,
) -> Option<Vec<DispatchFailure>> {
    apply_current_generation(observed_generation, current_generation, || {
        let mut failures = take_worker_disconnect_failure(inner)
            .into_iter()
            .collect::<Vec<_>>();
        failures.extend(release_and_dispatch(inner).err().unwrap_or_default());
        failures
    })
}

fn handle_worker_disconnect(app: &AppHandle, generation: u64) {
    let failures = app
        .state::<ProcessingState>()
        .inner
        .lock()
        .map(|mut inner| {
            let current_generation = inner.process.as_ref().map(|process| process.generation);
            recover_worker_disconnect(&mut inner, generation, current_generation, |inner| {
                inner.process = None;
                dispatch_next(app, inner)
            })
            .unwrap_or_default()
        })
        .unwrap_or_default();
    handle_dispatch_failures(app, failures);
}

fn read_bounded_lines(
    mut reader: impl Read,
    maximum_bytes: usize,
    mut on_line: impl FnMut(&str, bool),
) {
    let mut block = [0_u8; 4096];
    let mut pending = Vec::with_capacity(maximum_bytes.min(block.len()));
    let mut truncated = false;
    loop {
        let count = match reader.read(&mut block) {
            Ok(0) | Err(_) => break,
            Ok(count) => count,
        };
        for byte in &block[..count] {
            if matches!(*byte, b'\n' | b'\r') {
                if !pending.is_empty() || truncated {
                    let line = String::from_utf8_lossy(&pending);
                    on_line(&line, truncated);
                }
                pending.clear();
                truncated = false;
            } else if pending.len() < maximum_bytes {
                pending.push(*byte);
            } else {
                truncated = true;
            }
        }
    }
    if !pending.is_empty() || truncated {
        let line = String::from_utf8_lossy(&pending);
        on_line(&line, truncated);
    }
}

fn ensure_worker(app: &AppHandle, inner: &mut ProcessingInner) -> Result<(), String> {
    if let Some(process) = inner.process.as_mut() {
        match process.child.try_wait() {
            Ok(None) => return Ok(()),
            Ok(Some(_)) => inner.process = None,
            Err(error) => return Err(format!("Unable to inspect processing worker: {error}")),
        }
    }
    let runtime = resolve_runtime()?;
    let data_root = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Unable to resolve Player data directory: {error}"))?;
    for directory in ["output", "cache", "logs", "input", "credentials"] {
        fs::create_dir_all(data_root.join(directory))
            .map_err(|error| format!("Unable to create processing directory: {error}"))?;
    }
    let playback_state = data_root.join("playback.state");
    fs::write(&playback_state, b"idle")
        .map_err(|error| format!("Unable to initialize playback state: {error}"))?;
    let mut command = worker_command(&runtime, &data_root, &playback_state);
    let generation = inner
        .last_worker_generation
        .checked_add(1)
        .ok_or_else(|| "Processing worker generation counter is exhausted".to_string())?;
    inner.last_worker_generation = generation;
    if let Some(task_id) = inner
        .waiting
        .front()
        .map(|request| request.request_id.as_str())
    {
        let program = command.get_program().to_string_lossy().into_owned();
        let arguments = command
            .get_args()
            .map(|argument| argument.to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        tasks::append_command(app, task_id, &program, &arguments);
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("Unable to start processing worker: {error}"))?;
    #[cfg(windows)]
    let job = match ProcessJob::assign_and_lower_priority(&child) {
        Ok(job) => job,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("Unable to contain processing worker: {error}"));
        }
    };
    #[cfg(unix)]
    lower_priority(&child).map_err(|error| format!("Unable to lower worker priority: {error}"))?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "Processing worker has no stdin".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "Processing worker has no stdout".to_string())?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "Processing worker has no stderr".to_string())?;
    start_readers(app.clone(), generation, stdout, stderr);
    inner.process = Some(WorkerProcess {
        generation,
        child,
        stdin: Some(stdin),
        #[cfg(windows)]
        _job: job,
    });
    Ok(())
}

fn send_worker_request(
    process: Option<&mut WorkerProcess>,
    request: &WorkerRequest,
) -> Result<(), String> {
    let process = process.ok_or_else(|| "Processing worker is unavailable".to_string())?;
    let stdin = process
        .stdin
        .as_mut()
        .ok_or_else(|| "Processing worker stdin is closed".to_string())?;
    let encoded = encode_worker_request(request)?;
    if encoded.len() > 1024 * 1024 {
        return Err("Processing request exceeds the 1 MiB worker boundary".into());
    }
    stdin
        .write_all(&encoded)
        .and_then(|()| stdin.flush())
        .map_err(|error| format!("Unable to queue processing request: {error}"))
}

fn encode_worker_request(request: &WorkerRequest) -> Result<Vec<u8>, String> {
    let mut encoded = serde_json::to_vec(request)
        .map_err(|error| format!("Unable to encode processing request: {error}"))?;
    encoded.push(b'\n');
    Ok(encoded)
}

fn commit_waiting_request(
    waiting: &mut VecDeque<WorkerRequest>,
    active_request: &mut Option<String>,
    send: impl FnOnce(&WorkerRequest) -> Result<(), String>,
) -> Result<bool, String> {
    if active_request.is_some() {
        return Ok(false);
    }
    let Some(request) = waiting.front() else {
        return Ok(false);
    };
    send(request)?;
    let request = waiting
        .pop_front()
        .expect("the sent request remains at the queue front");
    *active_request = Some(request.request_id);
    Ok(true)
}

fn drain_dispatch_failures(inner: &mut ProcessingInner, message: &str) -> Vec<DispatchFailure> {
    inner
        .waiting
        .drain(..)
        .map(|request| {
            let transient_lyrics = inner
                .pending
                .remove(&request.request_id)
                .and_then(|pending| pending.transient_lyrics);
            DispatchFailure {
                request_id: request.request_id,
                transient_lyrics,
                message: message.to_owned(),
            }
        })
        .collect()
}

fn dispatch_next(app: &AppHandle, inner: &mut ProcessingInner) -> Result<(), Vec<DispatchFailure>> {
    if inner.active_request.is_some() || inner.waiting.is_empty() {
        return Ok(());
    }
    if let Err(error) = ensure_worker(app, inner) {
        return Err(drain_dispatch_failures(inner, &error));
    }
    let first = {
        let ProcessingInner {
            process,
            waiting,
            active_request,
            ..
        } = inner;
        commit_waiting_request(waiting, active_request, |request| {
            send_worker_request(process.as_mut(), request)
        })
    };
    let dispatched = match first {
        Ok(dispatched) => dispatched,
        Err(_) => {
            inner.process = None;
            if let Err(error) = ensure_worker(app, inner) {
                return Err(drain_dispatch_failures(inner, &error));
            }
            let retry = {
                let ProcessingInner {
                    process,
                    waiting,
                    active_request,
                    ..
                } = inner;
                commit_waiting_request(waiting, active_request, |request| {
                    send_worker_request(process.as_mut(), request)
                })
            };
            match retry {
                Ok(dispatched) => dispatched,
                Err(error) => {
                    inner.process = None;
                    return Err(drain_dispatch_failures(inner, &error));
                }
            }
        }
    };
    if dispatched && let Some(task_id) = inner.active_request.as_deref() {
        tasks::progress(
            app,
            task_id,
            TaskProgress {
                stage_key: Some("worker-dispatch".into()),
                stage_title: Some("Start local processing job".into()),
                message: Some("Request sent to the contained local worker".into()),
                ..Default::default()
            },
        );
    }
    Ok(())
}

fn handle_dispatch_failures(app: &AppHandle, failures: Vec<DispatchFailure>) {
    if failures.is_empty() {
        return;
    }
    for failure in &failures {
        if let Some(path) = &failure.transient_lyrics {
            let _ = fs::remove_file(path);
        }
    }
    if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
        for (index, failure) in failures.iter().enumerate() {
            let projection = dispatch_failure_projection(failure, index);
            let existing = catalog
                .item(projection.request_id)
                .and_then(|item| item.processing_task_evidence.clone());
            catalog.set_processing_task_evidence(
                projection.request_id,
                processing_evidence(existing, None, projection.evidence_status, 0.0, None, true),
            );
            catalog.set_progress(
                projection.request_id,
                projection.item_status,
                0.0,
                Some(projection.message.to_owned()),
            );
        }
    }
    save_and_emit(app);
    for (index, failure) in failures.iter().enumerate() {
        let projection = dispatch_failure_projection(failure, index);
        tasks::finish(
            app,
            projection.request_id,
            projection.task_status,
            Some(projection.message.to_owned()),
        );
    }
    for (index, failure) in failures.into_iter().enumerate() {
        let projection = dispatch_failure_projection(&failure, index);
        if !projection.report_issue {
            continue;
        }
        issues::report(
            app,
            issues::processing_failure_issue(
                projection.request_id,
                &item_title(app, projection.request_id),
                projection.message,
            ),
        );
    }
}

fn worker_request(item: CatalogItem) -> Result<WorkerRequest, String> {
    let (media_path, lyrics_path, start_seconds, end_seconds) = item
        .locations
        .iter()
        .find_map(|location| match location {
            ItemLocation::LocalMedia {
                path,
                lyrics_path: Some(lyrics),
                trim_start_millis,
                trim_end_millis,
                ..
            } => Some((
                path.clone(),
                lyrics.clone(),
                trim_start_millis.map(|value| value as f64 / 1000.0),
                trim_end_millis.map(|value| value as f64 / 1000.0),
            )),
            _ => None,
        })
        .ok_or_else(|| "Local media is still waiting for lyrics".to_string())?;
    Ok(WorkerRequest {
        request_id: item.id.clone(),
        resume_job_id: item.processing_job_id.clone(),
        media_path,
        lyrics_path,
        start_seconds,
        end_seconds,
        title: item.title,
        artist: item.artist,
        composer: item.composer,
    })
}

fn wall_clock_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

fn parse_timestamp_millis(value: &str) -> Option<u64> {
    let millis = DateTime::parse_from_rfc3339(value).ok()?.timestamp_millis();
    u64::try_from(millis).ok()
}

fn valid_durable_job_id(value: &str) -> bool {
    (3..=180).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

fn canonical_bounded_file(base: &Path, path: &Path, max_bytes: u64) -> Result<PathBuf, String> {
    let canonical = path
        .canonicalize()
        .map_err(|_| "Durable processing evidence is unavailable".to_string())?;
    if !canonical.starts_with(base) {
        return Err("Durable processing evidence escaped its job directory".into());
    }
    let metadata = fs::metadata(&canonical)
        .map_err(|_| "Durable processing evidence cannot be inspected".to_string())?;
    if !metadata.is_file() || metadata.len() > max_bytes {
        return Err("Durable processing evidence is not a bounded regular file".into());
    }
    Ok(canonical)
}

fn validate_durable_manifest(
    manifest: &DurableJobManifest,
    expected_job_id: &str,
) -> Result<(), String> {
    const JOB_STATUSES: &[&str] = &[
        "planned",
        "queued",
        "running",
        "cancelling",
        "succeeded",
        "failed",
        "blocked",
        "cancelled",
    ];
    const STAGE_STATUSES: &[&str] = &[
        "pending",
        "running",
        "succeeded",
        "failed",
        "blocked",
        "cancelled",
        "skipped",
    ];
    if manifest.schema_version != 1
        || manifest.kind != "lyricrail.job"
        || manifest.job_id != expected_job_id
        || !JOB_STATUSES.contains(&manifest.status.as_str())
        || !manifest.progress_percent.is_finite()
        || !(0.0..=100.0).contains(&manifest.progress_percent)
        || manifest.stages.len() > 64
    {
        return Err("Durable processing manifest failed validation".into());
    }
    let mut seen = std::collections::HashSet::new();
    for stage in &manifest.stages {
        if !VALID_DURABLE_STAGES.contains(&stage.key.as_str())
            || !seen.insert(stage.key.as_str())
            || !STAGE_STATUSES.contains(&stage.status.as_str())
            || stage.title.len() > 180
            || !stage.progress_percent.is_finite()
            || !(0.0..=100.0).contains(&stage.progress_percent)
        {
            return Err("Durable processing stage failed validation".into());
        }
    }
    if manifest.current_stage.as_deref().is_some_and(|stage| {
        !manifest
            .stages
            .iter()
            .any(|candidate| candidate.key == stage)
    }) {
        return Err("Durable processing current stage is invalid".into());
    }
    Ok(())
}

fn load_durable_manifest(
    data_root: &Path,
    item: &CatalogItem,
) -> Result<(DurableJobManifest, PathBuf), String> {
    let job_id = item
        .processing_job_id
        .as_deref()
        .filter(|value| valid_durable_job_id(value))
        .ok_or_else(|| "Catalog does not contain a valid durable job ID".to_string())?;
    if item
        .processing_task_evidence
        .as_ref()
        .and_then(|evidence| evidence.job_id.as_deref())
        != Some(job_id)
    {
        return Err("Private catalog task evidence does not match its job ID".into());
    }
    let output_root = data_root
        .join("output")
        .canonicalize()
        .map_err(|_| "Durable processing output is unavailable".to_string())?;
    let job_root = output_root
        .join(job_id)
        .canonicalize()
        .map_err(|_| "Durable processing job is unavailable".to_string())?;
    if !job_root.starts_with(&output_root) {
        return Err("Durable processing job escaped the output directory".into());
    }
    let manifest_path = canonical_bounded_file(
        &job_root,
        &job_root.join("job.json"),
        MAX_DURABLE_MANIFEST_BYTES,
    )?;
    let manifest: DurableJobManifest = serde_json::from_slice(
        &fs::read(manifest_path)
            .map_err(|_| "Durable processing manifest cannot be read".to_string())?,
    )
    .map_err(|_| "Durable processing manifest is invalid JSON".to_string())?;
    validate_durable_manifest(&manifest, job_id)?;
    if manifest.request.lyrics.snapshot != "inputs/lyrics.txt"
        || manifest.request.lyrics.sha256.len() != 64
        || !manifest
            .request
            .lyrics
            .sha256
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        return Err("Durable processing lyric evidence is invalid".into());
    }
    let expected = manifest.request.lyrics.sha256.to_ascii_lowercase();
    let catalog_hash = hex::encode(Sha256::digest(item.lyric_text.as_bytes()));
    if catalog_hash != expected {
        return Err("Durable processing lyric evidence does not match the private catalog".into());
    }
    let lyric_candidate = job_root.join("inputs").join("lyrics.txt");
    if lyric_candidate.exists() {
        let lyric_path =
            canonical_bounded_file(&job_root, &lyric_candidate, MAX_DURABLE_LYRIC_BYTES)?;
        let lyric_bytes = fs::read(lyric_path)
            .map_err(|_| "Durable processing lyric evidence cannot be read".to_string())?;
        if hex::encode(Sha256::digest(&lyric_bytes)) != expected {
            return Err("Durable processing lyric snapshot failed authentication".into());
        }
    } else {
        let verified_cleanup = manifest.status == "succeeded"
            && item.status == ItemStatus::Ready
            && manifest
                .stages
                .iter()
                .any(|stage| stage.key == "cleanup_intermediates" && stage.status == "succeeded");
        if !verified_cleanup {
            return Err("Durable processing lyric snapshot is missing".into());
        }
    }
    Ok((manifest, job_root))
}

fn read_durable_output(
    job_root: &Path,
    fallback_timestamp: u64,
) -> Result<(Vec<DurableOutputLine>, bool), String> {
    let maximum_timestamp = wall_clock_millis().saturating_add(5 * 60 * 1_000);
    let candidate = job_root.join("logs").join("pipeline.log");
    if !candidate.exists() {
        return Ok((Vec::new(), false));
    }
    let path = candidate
        .canonicalize()
        .map_err(|_| "Durable processing log is unavailable".to_string())?;
    if !path.starts_with(job_root) {
        return Err("Durable processing log escaped its job directory".into());
    }
    let metadata = fs::metadata(&path)
        .map_err(|_| "Durable processing log cannot be inspected".to_string())?;
    if !metadata.is_file() {
        return Err("Durable processing log is not a regular file".into());
    }
    let start = metadata.len().saturating_sub(MAX_DURABLE_LOG_BYTES);
    let mut file =
        fs::File::open(path).map_err(|_| "Durable processing log cannot be opened".to_string())?;
    file.seek(SeekFrom::Start(start))
        .map_err(|_| "Durable processing log cannot be read".to_string())?;
    let mut bytes = Vec::with_capacity((metadata.len() - start) as usize);
    file.read_to_end(&mut bytes)
        .map_err(|_| "Durable processing log cannot be read".to_string())?;
    if start > 0 {
        if let Some(first_line) = bytes.iter().position(|byte| *byte == b'\n') {
            bytes.drain(..=first_line);
        } else {
            bytes.clear();
        }
    }
    let text = String::from_utf8_lossy(&bytes);
    let mut raw_lines = text.lines().collect::<Vec<_>>();
    let mut truncated = start > 0;
    if raw_lines.len() > MAX_DURABLE_LOG_LINES {
        raw_lines.drain(..raw_lines.len() - MAX_DURABLE_LOG_LINES);
        truncated = true;
    }
    let lines = raw_lines
        .into_iter()
        .map(|line| {
            let mut fields = line.split_whitespace();
            let timestamp_millis = fields
                .next()
                .and_then(parse_timestamp_millis)
                .filter(|timestamp| *timestamp <= maximum_timestamp)
                .unwrap_or(fallback_timestamp);
            let stream = match fields.next() {
                Some("ERROR") => OutputStream::Stderr,
                Some("WARNING") => OutputStream::System,
                _ => OutputStream::Progress,
            };
            let stage = fields
                .next()
                .filter(|value| *value != "-" && VALID_DURABLE_STAGES.contains(value))
                .map(str::to_owned);
            DurableOutputLine {
                timestamp_millis,
                stream,
                stage,
                text: line.to_owned(),
            }
        })
        .collect();
    Ok((lines, truncated))
}

fn durable_task_record(item: &CatalogItem, now: u64) -> Result<TaskRecord, String> {
    let evidence = item
        .processing_task_evidence
        .as_ref()
        .ok_or_else(|| "Private catalog task evidence is missing".to_string())?;
    if !evidence.progress_percent.is_finite()
        || !(0.0..=100.0).contains(&evidence.progress_percent)
        || evidence
            .stage_progress_percent
            .is_some_and(|value| !value.is_finite() || !(0.0..=100.0).contains(&value))
        || evidence
            .stage_key
            .as_ref()
            .is_some_and(|value| value.len() > 100)
        || evidence
            .stage_title
            .as_ref()
            .is_some_and(|value| value.len() > 180)
        || evidence
            .job_id
            .as_deref()
            .is_some_and(|value| !valid_durable_job_id(value))
    {
        return Err("Private catalog task evidence is invalid".into());
    }
    let maximum_timestamp = now.saturating_add(5 * 60 * 1_000);
    if evidence.started_at_millis > maximum_timestamp
        || evidence.updated_at_millis < evidence.started_at_millis
        || evidence.updated_at_millis > maximum_timestamp
        || evidence.finished_at_millis.is_some_and(|finished| {
            finished < evidence.updated_at_millis || finished > maximum_timestamp
        })
    {
        return Err("Durable processing timestamps are inconsistent".into());
    }
    let active = matches!(
        evidence.status,
        ProcessingEvidenceStatus::Queued | ProcessingEvidenceStatus::Running
    );
    let status = match evidence.status {
        ProcessingEvidenceStatus::Cancelled => TaskStatus::Cancelled,
        ProcessingEvidenceStatus::Succeeded if item.status == ItemStatus::Ready => {
            TaskStatus::Succeeded
        }
        _ => TaskStatus::Failed,
    };
    let finished = evidence
        .finished_at_millis
        .unwrap_or(if active {
            now
        } else {
            evidence.updated_at_millis
        })
        .max(evidence.started_at_millis);
    Ok(TaskRecord {
        id: item.id.clone(),
        kind: TaskKind::Processing,
        title: format!("Create karaoke: {}", item.title),
        status,
        progress_mode: ProgressMode::Determinate,
        stage_key: evidence.stage_key.clone(),
        stage_title: evidence.stage_title.clone(),
        stage_progress_percent: evidence.stage_progress_percent,
        progress_percent: Some(evidence.progress_percent),
        completed_units: None,
        total_units: None,
        unit_label: None,
        eta_seconds: None,
        cancellable: false,
        related_item_id: Some(item.id.clone()),
        started_at_millis: evidence.started_at_millis,
        updated_at_millis: if active {
            now
        } else {
            evidence.updated_at_millis
        },
        finished_at_millis: Some(finished),
        output_line_count: 0,
        output_truncated: false,
        status_message: item.status_message.clone().or_else(|| {
            Some(if active {
                "The previous processing session ended; choose Retry when ready".into()
            } else {
                "Processing finished".into()
            })
        }),
    })
}

pub fn restore_durable_tasks(app: &AppHandle, data_root: &Path, items: &[CatalogItem]) -> usize {
    let now = wall_clock_millis();
    let mut restored = 0;
    for item in items
        .iter()
        .filter(|item| item.processing_task_evidence.is_some())
    {
        let Ok(record) = durable_task_record(item, now) else {
            continue;
        };
        let fallback_timestamp = record.updated_at_millis;
        if tasks::restore(app, record).is_err() {
            continue;
        }
        let job_root = item
            .processing_task_evidence
            .as_ref()
            .and_then(|evidence| evidence.job_id.as_ref())
            .and_then(|_| load_durable_manifest(data_root, item).ok())
            .map(|(_, job_root)| job_root);
        if let Some(job_root) = job_root
            && let Ok((lines, truncated)) = read_durable_output(&job_root, fallback_timestamp)
        {
            for line in lines {
                tasks::restore_output(
                    app,
                    &item.id,
                    line.stream,
                    line.stage.as_deref(),
                    &line.text,
                    line.timestamp_millis,
                );
            }
            if truncated {
                tasks::mark_output_truncated(app, &item.id);
            }
        }
        restored += 1;
    }
    restored
}

pub fn enqueue_item(
    app: &AppHandle,
    item: CatalogItem,
    transient_lyrics: Option<PathBuf>,
) -> Result<(), String> {
    let task_title = item.title.clone();
    let request = worker_request(item)?;
    let request_id = request.request_id.clone();
    let resume_job_id = request.resume_job_id.clone();
    tasks::start(
        app,
        TaskSpec {
            id: request_id.clone(),
            kind: TaskKind::Processing,
            title: format!("Create karaoke: {task_title}"),
            status: TaskStatus::Queued,
            progress_mode: ProgressMode::Determinate,
            cancellable: true,
            related_item_id: Some(request_id.clone()),
        },
    )?;
    let evidence_result = (|| {
        let state = app.state::<CatalogState>();
        let mut catalog = state
            .0
            .lock()
            .map_err(|_| "Catalog lock is poisoned".to_string())?;
        catalog.set_processing_task_evidence(
            &request_id,
            processing_evidence(
                None,
                resume_job_id,
                ProcessingEvidenceStatus::Queued,
                0.0,
                None,
                false,
            ),
        );
        Ok::<(), String>(())
    })();
    if let Err(error) = evidence_result {
        tasks::finish(app, &request_id, TaskStatus::Failed, Some(error.clone()));
        return Err(error);
    }
    let state = app.state::<ProcessingState>();
    let mut inner = state
        .inner
        .lock()
        .map_err(|_| "Processing state lock is poisoned".to_string())?;
    if inner.pending.contains_key(&request_id) {
        return Ok(());
    }
    inner.pending.insert(
        request_id.clone(),
        PendingJob {
            transient_lyrics,
            job_id: request.resume_job_id.clone(),
            cancel_requested: false,
        },
    );
    inner.waiting.push_back(request);
    if let Err(failures) = dispatch_next(app, &mut inner) {
        let error = failures.first().map_or_else(
            || "Unable to dispatch processing request".into(),
            |failure| failure.message.clone(),
        );
        drop(inner);
        handle_dispatch_failures(app, failures);
        return Err(error);
    }
    drop(inner);
    if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
        catalog.set_progress(
            &request_id,
            ItemStatus::Queued,
            0.0,
            Some("Waiting for the local worker".into()),
        );
    }
    Ok(())
}

pub fn status(state: &ProcessingState) -> ProcessingStatus {
    let runtime = runtime_available_hint();
    let (worker_running, pending_jobs) = state
        .inner
        .lock()
        .map(|mut inner| {
            let running = inner
                .process
                .as_mut()
                .is_some_and(|process| matches!(process.child.try_wait(), Ok(None)));
            (running, inner.pending.len())
        })
        .unwrap_or((false, 0));
    ProcessingStatus {
        worker_running,
        pending_jobs,
        runtime_available: runtime.is_ok(),
        runtime_error: runtime.err(),
    }
}

pub fn retry_setup_required(app: &AppHandle) -> Result<usize, String> {
    let items = {
        let state = app.state::<CatalogState>();
        let mut catalog = state
            .0
            .lock()
            .map_err(|_| "Catalog lock is poisoned".to_string())?;
        catalog.queue_setup_required_after_verification()
    };
    save_and_emit(app);
    let count = items.len();
    for item in items {
        if let Err(error) = enqueue_item(app, item.clone(), None) {
            if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
                catalog.set_progress(&item.id, ItemStatus::Failed, 0.0, Some(error));
            }
        }
    }
    save_and_emit(app);
    Ok(count)
}

fn request_cancel(app: AppHandle, job_id: String) {
    thread::spawn(move || {
        let Ok(runtime) = resolve_runtime() else {
            return;
        };
        let Ok(data_root) = app.path().app_data_dir() else {
            return;
        };
        let mut command = Command::new(&runtime.python);
        command
            .current_dir(&runtime.root)
            .env("LYRICRAIL_HOME", &runtime.root)
            .env("LYRICRAIL_DATA_HOME", data_root)
            .env("PYTHONNOUSERSITE", "1")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .arg(if runtime.integrity == "signed-verified" {
                "-I"
            } else {
                "-s"
            });
        if runtime.integrity != "signed-verified" {
            command.env("PYTHONPATH", runtime.root.join("src"));
        }
        let _ = command
            .args(["-m", "lyricrail", "cancel"])
            .arg(job_id)
            .arg("--root")
            .arg(runtime.root)
            .arg("--json")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    });
}

pub fn cancel_item(app: &AppHandle, item_id: &str) -> Result<(), String> {
    let (job_id, cancelled_waiting, transient) = {
        let state = app.state::<ProcessingState>();
        let mut inner = state
            .inner
            .lock()
            .map_err(|_| "Processing state lock is poisoned".to_string())?;
        let waiting = inner.active_request.as_deref() != Some(item_id);
        if waiting {
            inner
                .waiting
                .retain(|request| request.request_id != item_id);
            let pending = inner
                .pending
                .remove(item_id)
                .ok_or_else(|| "This item is not queued or processing".to_string())?;
            (None, true, pending.transient_lyrics)
        } else {
            let pending = inner
                .pending
                .get_mut(item_id)
                .ok_or_else(|| "This item is not queued or processing".to_string())?;
            pending.cancel_requested = true;
            (pending.job_id.clone(), false, None)
        }
    };
    if let Some(path) = transient {
        let _ = fs::remove_file(path);
    }
    if let Some(job_id) = job_id.clone() {
        request_cancel(app.clone(), job_id);
    }
    if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
        let existing = catalog
            .item(item_id)
            .and_then(|item| item.processing_task_evidence.clone());
        let previous_progress = catalog
            .item(item_id)
            .map_or(0.0, |item| item.progress_percent);
        catalog.set_processing_task_evidence(
            item_id,
            processing_evidence(
                existing,
                job_id,
                if cancelled_waiting {
                    ProcessingEvidenceStatus::Cancelled
                } else {
                    ProcessingEvidenceStatus::Running
                },
                0.0,
                (!cancelled_waiting).then(|| {
                    (
                        "cancelling".into(),
                        Some("Cancelling processing".into()),
                        None,
                    )
                }),
                cancelled_waiting,
            ),
        );
        catalog.set_progress(
            item_id,
            if cancelled_waiting {
                ItemStatus::Failed
            } else {
                ItemStatus::Processing
            },
            if cancelled_waiting {
                0.0
            } else {
                previous_progress
            },
            Some(
                if cancelled_waiting {
                    "Cancelled before processing; choose Retry"
                } else {
                    "Cancellation requested"
                }
                .into(),
            ),
        );
    }
    save_and_emit(app);
    if cancelled_waiting {
        tasks::finish(
            app,
            item_id,
            TaskStatus::Cancelled,
            Some("Cancelled before processing started".into()),
        );
    } else {
        tasks::progress(
            app,
            item_id,
            TaskProgress {
                stage_key: Some("cancelling".into()),
                stage_title: Some("Cancelling processing".into()),
                message: Some("Cancellation requested".into()),
                ..Default::default()
            },
        );
    }
    Ok(())
}

pub fn set_playback_state(app: &AppHandle, playing: bool) -> Result<(), String> {
    let path = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?
        .join("playback.state");
    fs::write(
        path,
        if playing {
            b"playing".as_slice()
        } else {
            b"idle".as_slice()
        },
    )
    .map_err(|error| format!("Unable to update playback priority: {error}"))
}

#[cfg(test)]
mod tests {
    use super::{
        PendingJob, ProcessingInner, WorkerRequest, apply_current_generation,
        commit_waiting_request, dispatch_failure_projection, drain_dispatch_failures,
        durable_task_record, encode_worker_request, error_code, load_durable_manifest,
        read_bounded_lines, read_durable_output, read_worker_stdout, recover_worker_disconnect,
        take_worker_disconnect_failure, worker_command, worker_request,
    };
    use crate::catalog::{
        CatalogItem, ItemLocation, ItemStatus, MediaOrigin, ProcessingEvidenceStatus,
        ProcessingTaskEvidence,
    };
    use crate::runtime::ResolvedRuntime;
    use crate::tasks::TaskStatus;
    use sha2::{Digest, Sha256};
    use std::{
        collections::{HashMap, VecDeque},
        fs,
        path::PathBuf,
    };

    #[test]
    fn worker_command_forces_utf8_before_module_execution_in_every_integrity_mode() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("LyricRail-Đ");
        let data_root = temporary.path().join("data");
        let playback_state = data_root.join("playback.state");
        let runtime = ResolvedRuntime {
            root: root.clone(),
            python: PathBuf::from("python.exe"),
            ffmpeg: None,
            ffprobe: None,
            lrail: None,
            integrity: "development-unverified",
        };
        let development = worker_command(&runtime, &data_root, &playback_state);
        let arguments = development
            .get_args()
            .map(|value| value.to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        assert_eq!(
            arguments,
            vec![
                "-s",
                "-X",
                "utf8",
                "-m",
                "lyricrail",
                "worker",
                "--root",
                root.to_string_lossy().as_ref(),
            ]
        );
        let environment = development
            .get_envs()
            .map(|(key, value)| {
                (
                    key.to_string_lossy().into_owned(),
                    value.map(|value| value.to_string_lossy().into_owned()),
                )
            })
            .collect::<HashMap<_, _>>();
        assert_eq!(
            environment.get("PYTHONUTF8").and_then(Option::as_deref),
            Some("1")
        );
        assert_eq!(
            environment
                .get("PYTHONIOENCODING")
                .and_then(Option::as_deref),
            Some("utf-8:strict")
        );
        assert_eq!(
            environment.get("PYTHONPATH").and_then(Option::as_deref),
            Some(root.join("src").to_string_lossy().as_ref())
        );

        let signed = worker_command(
            &ResolvedRuntime {
                integrity: "signed-verified",
                ..runtime
            },
            &data_root,
            &playback_state,
        );
        let signed_arguments = signed
            .get_args()
            .map(|value| value.to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        assert_eq!(
            &signed_arguments[..5],
            ["-I", "-X", "utf8", "-m", "lyricrail"]
        );
        let signed_environment = signed
            .get_envs()
            .map(|(key, _)| key.to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        assert!(!signed_environment.iter().any(|key| key == "PYTHONPATH"));
        assert!(!signed_environment.iter().any(|key| key == "PYTHONUTF8"));
        assert!(
            !signed_environment
                .iter()
                .any(|key| key == "PYTHONIOENCODING")
        );
    }

    #[test]
    fn unexpected_worker_exit_terminalizes_only_the_active_request_once() {
        let mut inner = ProcessingInner {
            waiting: VecDeque::from([WorkerRequest {
                request_id: "next-item".into(),
                resume_job_id: None,
                media_path: PathBuf::from("next.mp4"),
                lyrics_path: PathBuf::from("next.txt"),
                start_seconds: None,
                end_seconds: None,
                title: "Next".into(),
                artist: None,
                composer: None,
            }]),
            active_request: Some("active-item".into()),
            ..Default::default()
        };
        for request_id in ["active-item", "next-item"] {
            inner.pending.insert(
                request_id.into(),
                PendingJob {
                    transient_lyrics: Some(PathBuf::from(format!("{request_id}.txt"))),
                    job_id: None,
                    cancel_requested: false,
                },
            );
        }

        let failure = take_worker_disconnect_failure(&mut inner).unwrap();
        assert_eq!(failure.request_id, "active-item");
        assert!(failure.message.contains("exited unexpectedly"));
        assert_eq!(
            failure.transient_lyrics,
            Some(PathBuf::from("active-item.txt"))
        );
        assert!(take_worker_disconnect_failure(&mut inner).is_none());
        assert!(inner.active_request.is_none());
        assert!(!inner.pending.contains_key("active-item"));
        assert!(inner.pending.contains_key("next-item"));
        assert_eq!(inner.waiting.front().unwrap().request_id, "next-item");
    }

    #[test]
    fn stale_worker_eof_cannot_release_replacement_or_fail_its_request() {
        let request = |request_id: &str| WorkerRequest {
            request_id: request_id.into(),
            resume_job_id: None,
            media_path: PathBuf::from(format!("{request_id}.mp4")),
            lyrics_path: PathBuf::from(format!("{request_id}.txt")),
            start_seconds: None,
            end_seconds: None,
            title: request_id.into(),
            artist: None,
            composer: None,
        };
        let mut inner = ProcessingInner {
            waiting: VecDeque::from([request("request-b"), request("request-c")]),
            active_request: Some("request-a".into()),
            ..Default::default()
        };
        for request_id in ["request-a", "request-b", "request-c"] {
            inner.pending.insert(
                request_id.into(),
                PendingJob {
                    transient_lyrics: None,
                    job_id: None,
                    cancel_requested: false,
                },
            );
        }

        // A terminal event for worker A has already removed request A and
        // dispatched B through replacement worker generation 2.
        inner.pending.remove("request-a");
        inner.active_request = None;
        assert!(
            commit_waiting_request(&mut inner.waiting, &mut inner.active_request, |_| Ok(()))
                .unwrap()
        );
        let stale = recover_worker_disconnect(&mut inner, 1, Some(2), |_| {
            panic!("stale EOF must not release or dispatch the replacement worker")
        });
        assert!(stale.is_none());
        assert_eq!(inner.active_request.as_deref(), Some("request-b"));
        assert!(inner.pending.contains_key("request-b"));
        assert_eq!(inner.waiting.front().unwrap().request_id, "request-c");

        // Generation 2 now exits without a terminal event. B fails exactly
        // once and the untouched C request is dispatched in queue order.
        let mut released_generation_two = false;
        let current = recover_worker_disconnect(&mut inner, 2, Some(2), |inner| {
            released_generation_two = true;
            commit_waiting_request(&mut inner.waiting, &mut inner.active_request, |_| Ok(()))
                .map(|_| ())
                .map_err(|message| {
                    vec![super::DispatchFailure {
                        request_id: "request-c".into(),
                        transient_lyrics: None,
                        message,
                    }]
                })
        })
        .unwrap();
        assert!(released_generation_two);
        assert_eq!(current.len(), 1);
        assert_eq!(current[0].request_id, "request-b");
        let projection = dispatch_failure_projection(&current[0], 0);
        assert_eq!(projection.request_id, "request-b");
        assert_eq!(projection.evidence_status, ProcessingEvidenceStatus::Failed);
        assert_eq!(projection.item_status, ItemStatus::Failed);
        assert_eq!(projection.task_status, TaskStatus::Failed);
        assert!(projection.report_issue);
        assert_eq!(inner.active_request.as_deref(), Some("request-c"));
        assert!(inner.pending.contains_key("request-c"));
        assert!(inner.waiting.is_empty());

        let duplicate = recover_worker_disconnect(&mut inner, 2, Some(3), |_| {
            panic!("old generation must not terminalize request C")
        });
        assert!(duplicate.is_none());
        assert_eq!(inner.active_request.as_deref(), Some("request-c"));
    }

    #[test]
    fn buffered_terminal_event_then_reader_eof_ignores_the_replacement_worker() {
        use std::cell::{Cell, RefCell};

        let request = |request_id: &str| WorkerRequest {
            request_id: request_id.into(),
            resume_job_id: None,
            media_path: PathBuf::from(format!("{request_id}.mp4")),
            lyrics_path: PathBuf::from(format!("{request_id}.txt")),
            start_seconds: None,
            end_seconds: None,
            title: request_id.into(),
            artist: None,
            composer: None,
        };
        let mut initial = ProcessingInner {
            waiting: VecDeque::from([request("request-b")]),
            active_request: Some("request-a".into()),
            ..Default::default()
        };
        for request_id in ["request-a", "request-b"] {
            initial.pending.insert(
                request_id.into(),
                PendingJob {
                    transient_lyrics: None,
                    job_id: None,
                    cancel_requested: false,
                },
            );
        }
        let inner = RefCell::new(initial);
        let current_generation = Cell::new(1_u64);
        let terminal_seen = Cell::new(false);
        let stale_eof_ignored = Cell::new(false);
        read_worker_stdout(
            br#"{"kind":"lyricrail.worker.completed","requestId":"request-a","status":"succeeded"}
"#
            .as_slice(),
            |line, truncated| {
                assert!(!truncated);
                let event: super::WorkerEvent = serde_json::from_str(line).unwrap();
                assert_eq!(event.request_id, "request-a");
                let mut state = inner.borrow_mut();
                state.pending.remove(&event.request_id);
                state.active_request = None;
                let ProcessingInner {
                    waiting,
                    active_request,
                    ..
                } = &mut *state;
                assert!(commit_waiting_request(waiting, active_request, |_| Ok(())).unwrap());
                current_generation.set(2);
                terminal_seen.set(true);
            },
            || {
                assert!(terminal_seen.get());
                let result = recover_worker_disconnect(
                    &mut inner.borrow_mut(),
                    1,
                    Some(current_generation.get()),
                    |_| panic!("reader A EOF must not release worker B"),
                );
                stale_eof_ignored.set(result.is_none());
            },
        );
        assert!(stale_eof_ignored.get());
        let state = inner.into_inner();
        assert_eq!(state.active_request.as_deref(), Some("request-b"));
        assert!(state.pending.contains_key("request-b"));
        assert!(state.waiting.is_empty());
    }

    #[test]
    fn parsed_stale_event_cannot_cross_the_generation_transition_barrier() {
        use std::sync::{Arc, Barrier, Mutex};
        use std::thread;

        #[derive(Default)]
        struct EventState {
            current_generation: Option<u64>,
            stale_mutations: usize,
            current_mutations: usize,
        }

        let state = Arc::new(Mutex::new(EventState {
            current_generation: Some(1),
            ..Default::default()
        }));
        let parsed = Arc::new(Barrier::new(2));
        let replacement_committed = Arc::new(Barrier::new(2));
        let event_state = Arc::clone(&state);
        let event_parsed = Arc::clone(&parsed);
        let event_replacement = Arc::clone(&replacement_committed);
        let event = thread::spawn(move || {
            // Reader A has parsed a complete structured event but has not
            // acquired the ProcessingInner-equivalent transition lock yet.
            event_parsed.wait();
            event_replacement.wait();
            let mut state = event_state.lock().unwrap();
            let current_generation = state.current_generation;
            apply_current_generation(1, current_generation, || {
                state.stale_mutations += 1;
            })
        });

        parsed.wait();
        state.lock().unwrap().current_generation = Some(2);
        replacement_committed.wait();
        assert!(event.join().unwrap().is_none());
        let mut state = state.lock().unwrap();
        assert_eq!(state.stale_mutations, 0);
        let current_generation = state.current_generation;
        assert!(
            apply_current_generation(2, current_generation, || {
                state.current_mutations += 1;
            })
            .is_some()
        );
        assert_eq!(state.current_mutations, 1);
    }

    #[test]
    fn terminal_or_fatal_event_before_eof_does_not_create_a_duplicate_failure() {
        let mut inner = ProcessingInner::default();
        let transition = recover_worker_disconnect(&mut inner, 7, Some(7), |_| Ok(())).unwrap();
        assert!(transition.is_empty());
        assert!(inner.active_request.is_none());
        assert!(inner.pending.is_empty());
    }

    #[cfg(windows)]
    #[test]
    fn rust_json_crosses_hidden_windows_python_in_dev_and_isolated_utf8_modes() {
        use std::io::Write;
        use std::os::windows::process::CommandExt;
        use std::process::{Command, Stdio};

        let python = std::env::var_os("LYRICRAIL_PYTHON")
            .map(PathBuf::from)
            .filter(|path| path.is_file())
            .or_else(|| {
                let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("../../..")
                    .join(".venv/Scripts/python.exe");
                path.is_file().then_some(path)
            })
            .unwrap_or_else(|| PathBuf::from("python.exe"));
        let request = WorkerRequest {
            request_id: "local-rust-utf8".into(),
            resume_job_id: None,
            media_path: PathBuf::from(r"\\?\C:\Music\Đan Nguyên - Truyện Tình Nghèo.mp4"),
            lyrics_path: PathBuf::from(r"C:\LyricRail\Lời bài hát.txt"),
            start_seconds: Some(1.25),
            end_seconds: Some(9.75),
            title: "Đan Nguyên – Truyện Tình Nghèo".into(),
            artist: Some("Băng Tâm".into()),
            composer: None,
        };
        let encoded = encode_worker_request(&request).unwrap();
        let probe = concat!(
            "import json,sys; request=json.load(sys.stdin); ",
            "sys.stderr.write('\\u0110\\u01b0\\u1eddng ki\\u1ec3m tra stderr\\n'); sys.stderr.flush(); ",
            "print(json.dumps({'stdinEncoding':sys.stdin.encoding,'stdinErrors':sys.stdin.errors,",
            "'stdoutEncoding':sys.stdout.encoding,'stdoutErrors':sys.stdout.errors,",
            "'stderrEncoding':sys.stderr.encoding,'stderrErrors':sys.stderr.errors,",
            "'request':request},ensure_ascii=True),flush=True)"
        );
        for (mode, strict_development) in [("-s", true), ("-I", false)] {
            let mut command = Command::new(&python);
            command
                .arg(mode)
                .arg("-X")
                .arg("utf8")
                .arg("-c")
                .arg(probe)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .creation_flags(0x0800_0000);
            if strict_development {
                command
                    .env("PYTHONUTF8", "1")
                    .env("PYTHONIOENCODING", "utf-8:strict");
            } else {
                command
                    .env("PYTHONUTF8", "0")
                    .env("PYTHONIOENCODING", "cp1252:strict");
            }
            let mut child = command.spawn().unwrap();
            child.stdin.take().unwrap().write_all(&encoded).unwrap();
            let output = child.wait_with_output().unwrap();
            assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stderr)
            );
            assert_eq!(
                String::from_utf8(output.stderr)
                    .unwrap()
                    .replace("\r\n", "\n"),
                "Đường kiểm tra stderr\n"
            );
            let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
            for stream in ["stdinEncoding", "stdoutEncoding", "stderrEncoding"] {
                assert_eq!(
                    payload[stream]
                        .as_str()
                        .unwrap()
                        .to_ascii_lowercase()
                        .replace('_', "-"),
                    "utf-8"
                );
            }
            if strict_development {
                for stream in ["stdinErrors", "stdoutErrors"] {
                    assert_eq!(payload[stream], "strict");
                }
                assert_eq!(payload["stderrErrors"], "backslashreplace");
            }
            assert_eq!(payload["request"]["requestId"], request.request_id);
            assert_eq!(
                payload["request"]["mediaPath"],
                request.media_path.to_string_lossy().as_ref()
            );
            assert_eq!(payload["request"]["title"], request.title);
            assert_eq!(payload["request"]["artist"], "Băng Tâm");
        }
    }

    #[test]
    fn worker_request_contains_paths_as_json_data_not_a_shell_command() {
        let request = WorkerRequest {
            request_id: "item".into(),
            resume_job_id: Some("existing-job".into()),
            media_path: PathBuf::from("song & echo unsafe.mp4"),
            lyrics_path: PathBuf::from("lyrics ; rm.txt"),
            start_seconds: Some(12.345),
            end_seconds: Some(98.765),
            title: "Title".into(),
            artist: None,
            composer: None,
        };
        let encoded = serde_json::to_string(&request).unwrap();
        assert!(encoded.contains("song & echo unsafe.mp4"));
        assert!(encoded.contains("lyrics ; rm.txt"));
        assert!(encoded.contains("resumeJobId"));
        assert!(encoded.contains("\"startSeconds\":12.345"));
        assert!(encoded.contains("\"endSeconds\":98.765"));
    }

    #[test]
    fn closed_worker_pipe_cannot_remove_the_next_waiting_request() {
        let request = WorkerRequest {
            request_id: "next-item".into(),
            resume_job_id: None,
            media_path: PathBuf::from("media.mp4"),
            lyrics_path: PathBuf::from("lyrics.txt"),
            start_seconds: None,
            end_seconds: None,
            title: "Song".into(),
            artist: None,
            composer: None,
        };
        let mut waiting = VecDeque::from([request]);
        let mut active = None;
        let failed = commit_waiting_request(&mut waiting, &mut active, |_| {
            Err("controlled closed pipe".into())
        });
        assert!(failed.is_err());
        assert_eq!(waiting.front().unwrap().request_id, "next-item");
        assert!(active.is_none());

        let mut failed_inner = ProcessingInner {
            waiting: waiting.clone(),
            ..Default::default()
        };
        failed_inner.pending.insert(
            "next-item".into(),
            PendingJob {
                transient_lyrics: Some(PathBuf::from("transient.txt")),
                job_id: None,
                cancel_requested: false,
            },
        );
        let terminal = drain_dispatch_failures(&mut failed_inner, "controlled closed pipe");
        assert_eq!(terminal.len(), 1);
        assert_eq!(terminal[0].request_id, "next-item");
        assert_eq!(
            terminal[0].transient_lyrics,
            Some(PathBuf::from("transient.txt"))
        );
        assert!(failed_inner.waiting.is_empty());
        assert!(failed_inner.pending.is_empty());

        let sent = commit_waiting_request(&mut waiting, &mut active, |_| Ok(())).unwrap();
        assert!(sent);
        assert!(waiting.is_empty());
        assert_eq!(active.as_deref(), Some("next-item"));
    }

    #[test]
    fn local_clip_trim_is_propagated_to_the_existing_local_worker_request() {
        let item = CatalogItem {
            id: "local-clip-item".into(),
            package_id: None,
            title: "Local clip".into(),
            artist: None,
            composer: None,
            first_lyric_line: Some("Exact lyric".into()),
            lyric_text: "Exact lyric".into(),
            status: ItemStatus::Queued,
            progress_percent: 0.0,
            status_message: None,
            processing_job_id: None,
            processing_task_evidence: None,
            has_thumbnail: false,
            locations: vec![ItemLocation::LocalMedia {
                source_id: None,
                path: PathBuf::from("selected-source.mp4"),
                lyrics_path: Some(PathBuf::from("lyrics.txt")),
                origin: MediaOrigin::Disk,
                trim_start_millis: Some(12_345),
                trim_end_millis: Some(67_890),
                available: true,
            }],
        };
        let request = worker_request(item).unwrap();
        assert_eq!(request.start_seconds, Some(12.345));
        assert_eq!(request.end_seconds, Some(67.89));
        assert_eq!(request.media_path, PathBuf::from("selected-source.mp4"));
    }

    #[test]
    fn structured_worker_startup_error_identifies_missing_models() {
        let error = serde_json::json!({
            "code": "PROCESSING_MODELS_MISSING",
            "message": "Model provenance gate failed"
        });
        assert_eq!(error_code(Some(&error)), Some("PROCESSING_MODELS_MISSING"));
        assert_eq!(error_code(Some(&serde_json::json!("plain"))), None);
    }

    #[test]
    fn hostile_worker_lines_are_truncated_without_unbounded_allocation() {
        let input = format!("{}\nok\nunterminated", "x".repeat(100_000));
        let mut lines = Vec::new();
        read_bounded_lines(input.as_bytes(), 64, |line, truncated| {
            lines.push((line.to_owned(), truncated));
        });
        assert_eq!(lines.len(), 3);
        assert_eq!(lines[0].0.len(), 64);
        assert!(lines[0].1);
        assert_eq!(lines[1], ("ok".into(), false));
        assert_eq!(lines[2], ("unterminated".into(), false));
    }

    #[test]
    fn durable_task_reconstruction_is_bound_to_catalog_lyrics_and_bounded_logs() {
        let temporary = tempfile::tempdir().unwrap();
        let data_root = temporary.path();
        let job_id = "20260902-010203-song-abc123";
        let job_root = data_root.join("output").join(job_id);
        fs::create_dir_all(job_root.join("inputs")).unwrap();
        fs::create_dir_all(job_root.join("logs")).unwrap();
        let lyrics = "Exact immutable lyric\n";
        fs::write(job_root.join("inputs").join("lyrics.txt"), lyrics).unwrap();
        let lyric_hash = hex::encode(Sha256::digest(lyrics.as_bytes()));
        fs::write(
            job_root.join("job.json"),
            serde_json::to_vec(&serde_json::json!({
                "schemaVersion": 1,
                "kind": "lyricrail.job",
                "jobId": job_id,
                "status": "running",
                "currentStage": "probe",
                "progressPercent": 77.7,
                "createdAt": "2026-09-02T01:02:03.000+07:00",
                "startedAt": "2026-09-02T01:02:04.000+07:00",
                "finishedAt": null,
                "updatedAt": "2026-09-02T01:02:05.000+07:00",
                "request": {"lyrics": {"snapshot": "inputs/lyrics.txt", "sha256": lyric_hash}},
                "stages": [{
                    "key": "probe",
                    "title": "Analyze source media",
                    "status": "running",
                    "progressPercent": 50.0
                }]
            }))
            .unwrap(),
        )
        .unwrap();
        fs::write(
            job_root.join("logs").join("pipeline.log"),
            "2026-09-02T01:02:04.500+07:00 INFO    probe                    probing source\n",
        )
        .unwrap();
        let item = CatalogItem {
            id: "catalog-task-id".into(),
            package_id: None,
            title: "Song".into(),
            artist: None,
            composer: None,
            first_lyric_line: Some("Exact immutable lyric".into()),
            lyric_text: lyrics.into(),
            status: ItemStatus::Failed,
            progress_percent: 0.0,
            status_message: Some("The previous processing session ended".into()),
            processing_job_id: Some(job_id.into()),
            processing_task_evidence: Some(ProcessingTaskEvidence {
                job_id: Some(job_id.into()),
                status: ProcessingEvidenceStatus::Running,
                progress_percent: 12.5,
                stage_key: Some("probe".into()),
                stage_title: Some("Analyze source media".into()),
                stage_progress_percent: Some(50.0),
                started_at_millis: 1_780_000_000_000,
                updated_at_millis: 1_780_000_001_000,
                finished_at_millis: None,
            }),
            has_thumbnail: false,
            locations: Vec::new(),
        };

        let (_manifest, restored_root) = load_durable_manifest(data_root, &item).unwrap();
        let task = durable_task_record(&item, 1_800_000_000_000).unwrap();
        assert_eq!(task.id, item.id);
        assert_eq!(task.status, TaskStatus::Failed);
        assert_eq!(task.progress_percent, Some(12.5));
        assert_eq!(task.stage_progress_percent, Some(50.0));
        assert!(task.finished_at_millis.unwrap() >= task.started_at_millis);
        let (lines, truncated) =
            read_durable_output(&restored_root, task.updated_at_millis).unwrap();
        assert!(!truncated);
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0].stage.as_deref(), Some("probe"));

        fs::write(job_root.join("inputs").join("lyrics.txt"), "tampered").unwrap();
        assert!(load_durable_manifest(data_root, &item).is_err());
    }
}
