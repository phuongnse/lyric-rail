mod volume_security;

use std::{
    env, fs,
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
};

use lrail_format::{
    PackageRequest, RotationReport, RotationStatus, inspect_package,
    library_master_rotation_status, load_vault_master, pack_for_device_vault,
    rotate_library_master as rotate_library_master_core,
    runtime::{
        RUNTIME_MANIFEST_NAME, RUNTIME_SIGNATURE_NAME, runtime_platform, verify_runtime_pack,
    },
    verify_package_with_vault,
};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};
use volume_security::{
    WorkspaceVolumeStatus, inspect_workspace_volume, require_protected_workspace,
};

const RUNTIME_PUBLIC_KEY: &str = include_str!("../../../../config/runtime-signing-public.key");

struct PipelineProcess {
    child: Child,
    #[cfg(windows)]
    job: ProcessJob,
}

#[derive(Default)]
struct PipelineState {
    child: Mutex<Option<PipelineProcess>>,
}

#[derive(Default)]
struct VaultRotationState {
    active: Mutex<bool>,
}

#[derive(Debug, Clone)]
struct ResolvedRuntime {
    root: PathBuf,
    python: PathBuf,
    ffmpeg: Option<PathBuf>,
    ffprobe: Option<PathBuf>,
    lrail: Option<PathBuf>,
    integrity: &'static str,
    key_id: Option<String>,
    manifest_sha256: Option<String>,
    file_count: Option<usize>,
}

#[cfg(windows)]
struct ProcessJob(std::os::windows::io::OwnedHandle);

#[cfg(windows)]
impl ProcessJob {
    fn new_and_assign(child: &Child) -> std::io::Result<Self> {
        use std::{
            ffi::c_void,
            mem::{size_of, zeroed},
            os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
            ptr,
        };
        use windows_sys::Win32::System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
            SetInformationJobObject,
        };

        // SAFETY: null name/security attributes are allowed. The returned handle
        // is transferred immediately into OwnedHandle.
        let raw_job = unsafe { CreateJobObjectW(ptr::null(), ptr::null()) };
        if raw_job.is_null() {
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: raw_job is a newly owned, valid Windows handle.
        let owned = unsafe { OwnedHandle::from_raw_handle(raw_job.cast()) };
        // SAFETY: this Windows structure is plain data with an all-zero baseline.
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: the pointer and byte count describe the live limits structure.
        let configured = unsafe {
            SetInformationJobObject(
                owned.as_raw_handle().cast(),
                JobObjectExtendedLimitInformation,
                (&raw const limits).cast::<c_void>(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if configured == 0 {
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: the job and child process handles are valid for this call.
        let assigned = unsafe {
            AssignProcessToJobObject(owned.as_raw_handle().cast(), child.as_raw_handle().cast())
        };
        if assigned == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(Self(owned))
    }

    fn terminate(&self) -> std::io::Result<()> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::TerminateJobObject;

        // SAFETY: the job handle remains owned by self during this call.
        let terminated = unsafe { TerminateJobObject(self.0.as_raw_handle().cast(), 1) };
        if terminated == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PipelineRequest {
    media_path: PathBuf,
    lyrics_path: PathBuf,
    title: Option<String>,
    artist: Option<String>,
    start_seconds: Option<f64>,
    end_seconds: Option<f64>,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PipelineLogEvent {
    stream: &'static str,
    line: String,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PipelineCompletedEvent {
    success: bool,
    exit_code: Option<i32>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct StudioStatus {
    version: &'static str,
    pipeline_active: bool,
    vault_initialized: bool,
    vault_rotation_active: bool,
    vault_rotation_status: Option<RotationStatus>,
    vault_rotation_error: Option<String>,
    pipeline_available: bool,
    pipeline_root: Option<PathBuf>,
    runtime_error: Option<String>,
    runtime_integrity: Option<&'static str>,
    runtime_key_id: Option<String>,
    runtime_manifest_sha256: Option<String>,
    runtime_file_count: Option<usize>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RecoveryToolLaunch {
    process_id: u32,
    operation: &'static str,
}

fn clean_optional_text(value: Option<String>, field: &str) -> Result<Option<String>, String> {
    let Some(value) = value else {
        return Ok(None);
    };
    let value = value.trim().to_owned();
    if value.is_empty() {
        return Ok(None);
    }
    if value.len() > 240 || value.contains(['\r', '\n', '\0']) {
        return Err(format!(
            "{field} is too long or contains invalid characters"
        ));
    }
    Ok(Some(value))
}

fn canonical_file(path: &Path, label: &str) -> Result<PathBuf, String> {
    let canonical = path
        .canonicalize()
        .map_err(|error| format!("Unable to open {label} {}: {error}", path.display()))?;
    if !canonical.is_file() {
        return Err(format!(
            "{label} is not a regular file: {}",
            canonical.display()
        ));
    }
    Ok(canonical)
}

fn canonical_runtime_root(root: &Path) -> Result<PathBuf, String> {
    let root = root
        .canonicalize()
        .map_err(|error| format!("Unable to open runtime root {}: {error}", root.display()))?;
    if !root.join("config/pipeline.json").is_file() {
        return Err(format!(
            "Runtime root {} does not contain config/pipeline.json",
            root.display()
        ));
    }
    Ok(root)
}

fn project_root() -> Result<PathBuf, String> {
    if let Some(configured) = env::var_os("LYRICRAIL_HOME") {
        return canonical_runtime_root(&PathBuf::from(configured));
    }

    let mut candidates = Vec::new();
    if let Ok(executable) = env::current_exe()
        && let Some(directory) = executable.parent()
    {
        candidates.push(directory.join("runtime"));
        candidates.push(directory.to_owned());
    }
    if cfg!(debug_assertions) {
        let current = env::current_dir().map_err(|error| error.to_string())?;
        candidates.extend(current.ancestors().map(Path::to_owned));
    }
    for root in candidates {
        if root.join("config/pipeline.json").is_file() {
            return canonical_runtime_root(&root);
        }
    }
    Err(
        "Unable to locate a LyricRail runtime. Install a signed runtime pack or set LYRICRAIL_HOME."
            .into(),
    )
}

fn development_python_runtime(root: &Path) -> Result<PathBuf, String> {
    let configured = env::var_os("LYRICRAIL_PYTHON").map(PathBuf::from);
    let candidates = [
        configured,
        Some(root.join("runtime/python").join(if cfg!(windows) {
            "python.exe"
        } else {
            "python"
        })),
        Some(root.join(".venv").join(if cfg!(windows) {
            "Scripts/python.exe"
        } else {
            "bin/python"
        })),
    ];
    candidates
        .into_iter()
        .flatten()
        .find(|path| path.is_file())
        .ok_or_else(|| {
            "The pinned LyricRail Python runtime is unavailable. Set LYRICRAIL_PYTHON.".into()
        })
}

fn development_lrail_runtime(root: &Path) -> Option<PathBuf> {
    [
        root.join("target/debug")
            .join(if cfg!(windows) { "lrail.exe" } else { "lrail" }),
        root.join("runtime/bin")
            .join(if cfg!(windows) { "lrail.exe" } else { "lrail" }),
    ]
    .into_iter()
    .find(|path| path.is_file())
}

fn resolve_runtime() -> Result<ResolvedRuntime, String> {
    let root = project_root()?;
    let has_manifest = root.join(RUNTIME_MANIFEST_NAME).is_file();
    let has_signature = root.join(RUNTIME_SIGNATURE_NAME).is_file();
    if has_manifest || has_signature {
        if !(has_manifest && has_signature) {
            return Err("The runtime manifest/signature pair is incomplete".into());
        }
        let verified = verify_runtime_pack(
            &root,
            RUNTIME_PUBLIC_KEY.trim(),
            env!("CARGO_PKG_VERSION"),
            &runtime_platform(),
        )
        .map_err(|error| format!("Runtime integrity verification failed: {error:#}"))?;
        return Ok(ResolvedRuntime {
            root,
            python: verified.python_executable,
            ffmpeg: Some(verified.ffmpeg_executable),
            ffprobe: Some(verified.ffprobe_executable),
            lrail: Some(verified.lrail_executable),
            integrity: "signed-verified",
            key_id: Some(verified.key_id),
            manifest_sha256: Some(verified.manifest_sha256),
            file_count: Some(verified.file_count),
        });
    }
    if !cfg!(debug_assertions) {
        return Err(
            "Release Studio requires a signed runtime-manifest.json and runtime-manifest.sig"
                .into(),
        );
    }
    Ok(ResolvedRuntime {
        python: development_python_runtime(&root)?,
        lrail: development_lrail_runtime(&root),
        root,
        ffmpeg: None,
        ffprobe: None,
        integrity: "development-unverified",
        key_id: None,
        manifest_sha256: None,
        file_count: None,
    })
}

fn canonical_new_file(path: &Path, extension: &str, label: &str) -> Result<PathBuf, String> {
    if path.extension().and_then(|value| value.to_str()) != Some(extension) {
        return Err(format!("{label} must use .{extension}"));
    }
    let file_name = path
        .file_name()
        .ok_or_else(|| format!("{label} has no file name"))?;
    let parent = path
        .parent()
        .ok_or_else(|| format!("{label} has no parent directory"))?
        .canonicalize()
        .map_err(|error| format!("Unable to open {label} parent: {error}"))?;
    if !parent.is_dir() {
        return Err(format!("{label} parent is not a directory"));
    }
    let output = parent.join(file_name);
    if output.exists() {
        return Err(format!("Refusing to overwrite existing {label}"));
    }
    Ok(output)
}

#[cfg(windows)]
fn spawn_recovery_tool(
    operation: &'static str,
    arguments: impl IntoIterator<Item = String>,
) -> Result<RecoveryToolLaunch, String> {
    use std::os::windows::process::CommandExt;

    let runtime = resolve_runtime()?;
    let lrail = runtime
        .lrail
        .ok_or_else(|| "The verified runtime does not contain the native lrail tool".to_string())?;
    let child = Command::new(lrail)
        .current_dir(runtime.root)
        .env("LYRICRAIL_PAUSE_ON_EXIT", "1")
        .args(arguments)
        .creation_flags(0x0000_0010)
        .spawn()
        .map_err(|error| format!("Unable to open native recovery window: {error}"))?;
    Ok(RecoveryToolLaunch {
        process_id: child.id(),
        operation,
    })
}

#[cfg(not(windows))]
fn spawn_recovery_tool(
    _operation: &'static str,
    _arguments: impl IntoIterator<Item = String>,
) -> Result<RecoveryToolLaunch, String> {
    Err("Native recovery windows are currently implemented only for the Windows RC".into())
}

fn forward_lines<R: std::io::Read + Send + 'static>(
    app: AppHandle,
    stream: &'static str,
    reader: R,
) {
    thread::spawn(move || {
        let mut reader = BufReader::new(reader);
        let mut buffer = Vec::new();
        loop {
            buffer.clear();
            match reader.read_until(b'\n', &mut buffer) {
                Ok(0) => break,
                Ok(_) => {
                    let line = String::from_utf8_lossy(&buffer)
                        .trim_end_matches(['\r', '\n'])
                        .to_owned();
                    let _ = app.emit("studio-pipeline-log", PipelineLogEvent { stream, line });
                }
                Err(error) => {
                    let _ = app.emit(
                        "studio-pipeline-log",
                        PipelineLogEvent {
                            stream: "stderr",
                            line: format!("Unable to read pipeline output: {error}"),
                        },
                    );
                    break;
                }
            }
        }
    });
}

#[tauri::command]
fn studio_status(
    app: AppHandle,
    state: State<'_, PipelineState>,
    rotation_state: State<'_, VaultRotationState>,
) -> StudioStatus {
    let pipeline = resolve_runtime();
    let (
        pipeline_available,
        pipeline_root,
        runtime_error,
        runtime_integrity,
        runtime_key_id,
        runtime_manifest_sha256,
        runtime_file_count,
    ) = match pipeline {
        Ok(runtime) => (
            true,
            Some(runtime.root),
            None,
            Some(runtime.integrity),
            runtime.key_id,
            runtime.manifest_sha256,
            runtime.file_count,
        ),
        Err(error) => (false, None, Some(error), None, None, None, None),
    };
    let rotation_active = rotation_state.active.lock().is_ok_and(|active| *active);
    let rotation_state_directory = app.path().app_data_dir().map(|path| path.join("security"));
    let (vault_rotation_status, vault_rotation_error) = match rotation_state_directory {
        Ok(path) => match library_master_rotation_status(&path) {
            Ok(status) => (status, None),
            Err(error) => (None, Some(error.to_string())),
        },
        Err(error) => (None, Some(error.to_string())),
    };
    StudioStatus {
        version: env!("CARGO_PKG_VERSION"),
        pipeline_active: state.child.lock().is_ok_and(|child| child.is_some()),
        vault_initialized: load_vault_master().is_ok(),
        vault_rotation_active: rotation_active,
        vault_rotation_status,
        vault_rotation_error,
        pipeline_available,
        pipeline_root,
        runtime_error,
        runtime_integrity,
        runtime_key_id,
        runtime_manifest_sha256,
        runtime_file_count,
    }
}

#[tauri::command]
async fn workspace_volume_status(app: AppHandle) -> Result<WorkspaceVolumeStatus, String> {
    let data_root = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Unable to resolve Studio data directory: {error}"))?;
    tauri::async_runtime::spawn_blocking(move || inspect_workspace_volume(&data_root))
        .await
        .map_err(|error| format!("Workspace volume check failed: {error}"))
}

#[tauri::command]
fn start_pipeline(
    app: AppHandle,
    state: State<'_, PipelineState>,
    rotation_state: State<'_, VaultRotationState>,
    request: PipelineRequest,
) -> Result<u32, String> {
    let rotation_active = rotation_state
        .active
        .lock()
        .map_err(|_| "Vault rotation state lock is poisoned".to_string())?;
    if *rotation_active {
        return Err("Library master rotation is active; wait for it to finish".into());
    }
    let mut active = state
        .child
        .lock()
        .map_err(|_| "Pipeline state lock is poisoned".to_string())?;
    if active.is_some() {
        return Err("A LyricRail production job is already running".into());
    }
    if request.start_seconds.is_some_and(|value| value < 0.0)
        || request.end_seconds.is_some_and(|value| value <= 0.0)
        || matches!((request.start_seconds, request.end_seconds), (Some(start), Some(end)) if end <= start)
    {
        return Err("The requested media range is invalid".into());
    }

    let media = canonical_file(&request.media_path, "media")?;
    let lyrics = canonical_file(&request.lyrics_path, "lyrics")?;
    let title = clean_optional_text(request.title, "Title")?;
    let artist = clean_optional_text(request.artist, "Artist")?;
    // Re-verify at the execution boundary instead of trusting an earlier UI
    // status check. This intentionally detects runtime changes made while the
    // Studio window was open.
    let runtime = resolve_runtime()?;
    let root = runtime.root;
    let python = runtime.python;
    let data_root = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Unable to resolve Studio data directory: {error}"))?;
    let workspace_volume = inspect_workspace_volume(&data_root);
    require_protected_workspace(&workspace_volume)?;
    for directory in ["output", "cache", "logs", "input", "credentials"] {
        fs::create_dir_all(data_root.join(directory)).map_err(|error| {
            format!(
                "Unable to create Studio data directory {}: {error}",
                data_root.join(directory).display()
            )
        })?;
    }

    let mut command = Command::new(python);
    command
        .current_dir(&root)
        .env("LYRICRAIL_HOME", &root)
        .env("LYRICRAIL_DATA_HOME", &data_root)
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONUNBUFFERED", "1")
        .arg(if runtime.integrity == "signed-verified" {
            "-I"
        } else {
            "-s"
        });
    if runtime.integrity != "signed-verified" {
        command.env("PYTHONPATH", root.join("src"));
    }
    if let Some(path) = runtime.ffmpeg {
        command.env("LYRICRAIL_FFMPEG", path);
    }
    if let Some(path) = runtime.ffprobe {
        command.env("LYRICRAIL_FFPROBE", path);
    }
    if let Some(path) = runtime.lrail {
        command.env("LYRICRAIL_LRAIL", path);
    }
    command
        .arg("-m")
        .arg("lyricrail")
        .arg("run")
        .arg(&media)
        .arg("--lyrics")
        .arg(&lyrics)
        .arg("--no-upload")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(value) = title {
        command.arg("--title").arg(value);
    }
    if let Some(value) = artist {
        command.arg("--artist").arg(value);
    }
    if let Some(value) = request.start_seconds {
        command.arg("--start").arg(value.to_string());
    }
    if let Some(value) = request.end_seconds {
        command.arg("--end").arg(value.to_string());
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }

    let mut child = command
        .spawn()
        .map_err(|error| format!("Unable to start the LyricRail pipeline: {error}"))?;
    #[cfg(windows)]
    let job = match ProcessJob::new_and_assign(&child) {
        Ok(job) => job,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!(
                "Unable to contain the pipeline process tree in a Windows Job Object: {error}"
            ));
        }
    };
    let process_id = child.id();
    if let Some(stdout) = child.stdout.take() {
        forward_lines(app.clone(), "stdout", stdout);
    }
    if let Some(stderr) = child.stderr.take() {
        forward_lines(app.clone(), "stderr", stderr);
    }
    *active = Some(PipelineProcess {
        child,
        #[cfg(windows)]
        job,
    });
    drop(active);
    drop(rotation_active);

    thread::spawn(move || {
        let status = loop {
            let result = app
                .state::<PipelineState>()
                .child
                .lock()
                .map_err(|_| ())
                .and_then(|mut active| {
                    active
                        .as_mut()
                        .ok_or(())
                        .and_then(|process| process.child.try_wait().map_err(|_| ()))
                });
            match result {
                Ok(Some(status)) => {
                    if let Ok(mut active) = app.state::<PipelineState>().child.lock() {
                        active.take();
                    }
                    break Some(status);
                }
                Ok(None) => thread::sleep(std::time::Duration::from_millis(250)),
                Err(()) => break None,
            }
        };
        let _ = app.emit(
            "studio-pipeline-completed",
            PipelineCompletedEvent {
                success: status.as_ref().is_some_and(|status| status.success()),
                exit_code: status.and_then(|status| status.code()),
            },
        );
    });
    Ok(process_id)
}

#[tauri::command]
fn cancel_pipeline(state: State<'_, PipelineState>) -> Result<bool, String> {
    let mut active = state
        .child
        .lock()
        .map_err(|_| "Pipeline state lock is poisoned".to_string())?;
    let Some(process) = active.as_mut() else {
        return Ok(false);
    };
    #[cfg(windows)]
    process
        .job
        .terminate()
        .map_err(|error| format!("Unable to stop the pipeline process tree: {error}"))?;
    #[cfg(not(windows))]
    process
        .child
        .kill()
        .map_err(|error| format!("Unable to stop the pipeline: {error}"))?;
    Ok(true)
}

#[tauri::command]
fn pack_request(
    rotation_state: State<'_, VaultRotationState>,
    request_path: PathBuf,
    output_path: PathBuf,
) -> Result<serde_json::Value, String> {
    if rotation_state
        .active
        .lock()
        .map_err(|_| "Vault rotation state lock is poisoned".to_string())?
        .to_owned()
    {
        return Err("Library master rotation is active; wait for it to finish".into());
    }
    if output_path.extension().and_then(|value| value.to_str()) != Some("lrail") {
        return Err("The package output must use the .lrail extension".into());
    }
    let request_path = canonical_file(&request_path, "package request")?;
    let request_bytes = fs::read(&request_path)
        .map_err(|error| format!("Unable to read {}: {error}", request_path.display()))?;
    if request_bytes.len() > 32 * 1024 * 1024 {
        return Err("Package request exceeds the 32 MiB limit".into());
    }
    let request: PackageRequest = serde_json::from_slice(&request_bytes)
        .map_err(|error| format!("Invalid package request: {error}"))?;
    let packaged =
        pack_for_device_vault(&request, &output_path, None).map_err(|error| error.to_string())?;
    let vault_master = load_vault_master().map_err(|error| error.to_string())?;
    let verification = verify_package_with_vault(&output_path, &vault_master)
        .map_err(|error| error.to_string())?;
    Ok(serde_json::json!({
        "output": output_path,
        "assets": packaged.len(),
        "verification": verification,
    }))
}

#[tauri::command]
async fn rotate_library_master(
    app: AppHandle,
    pipeline_state: State<'_, PipelineState>,
    rotation_state: State<'_, VaultRotationState>,
    library_root: PathBuf,
) -> Result<RotationReport, String> {
    let state_directory = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Unable to resolve Studio data directory: {error}"))?
        .join("security");
    {
        let mut rotation_active = rotation_state
            .active
            .lock()
            .map_err(|_| "Vault rotation state lock is poisoned".to_string())?;
        if *rotation_active {
            return Err("Library master rotation is already active".into());
        }
        let pipeline = pipeline_state
            .child
            .lock()
            .map_err(|_| "Pipeline state lock is poisoned".to_string())?;
        if pipeline.is_some() {
            return Err("Stop the active production job before rotating the library key".into());
        }
        *rotation_active = true;
    }

    let result = tauri::async_runtime::spawn_blocking(move || {
        rotate_library_master_core(&library_root, &state_directory)
    })
    .await
    .map_err(|error| format!("Library master rotation task failed: {error}"));
    if let Ok(mut active) = app.state::<VaultRotationState>().active.lock() {
        *active = false;
    }
    result
        .map_err(|error| error.to_string())?
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn inspect_lrail(path: PathBuf) -> Result<serde_json::Value, String> {
    let path = canonical_file(&path, "LyricRail package")?;
    let inspection = inspect_package(&path).map_err(|error| error.to_string())?;
    serde_json::to_value(inspection).map_err(|error| error.to_string())
}

fn path_argument(path: &Path, label: &str) -> Result<String, String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("{label} path is not valid UTF-8"))
}

#[tauri::command]
fn launch_recovery_export(output_path: PathBuf) -> Result<RecoveryToolLaunch, String> {
    let output = canonical_new_file(&output_path, "lrail-recovery", "recovery bundle")?;
    spawn_recovery_tool(
        "export",
        [
            "recovery-export".to_owned(),
            "--output".to_owned(),
            path_argument(&output, "Recovery bundle")?,
        ],
    )
}

#[tauri::command]
fn launch_recovery_verify(input_path: PathBuf) -> Result<RecoveryToolLaunch, String> {
    let input = canonical_file(&input_path, "recovery bundle")?;
    spawn_recovery_tool(
        "verify",
        [
            "recovery-verify".to_owned(),
            path_argument(&input, "Recovery bundle")?,
        ],
    )
}

#[tauri::command]
fn launch_recovery_restore(
    input_path: PathBuf,
    library_root: PathBuf,
) -> Result<RecoveryToolLaunch, String> {
    let input = canonical_file(&input_path, "recovery bundle")?;
    let library = library_root
        .canonicalize()
        .map_err(|error| format!("Unable to open package library: {error}"))?;
    if !library.is_dir() {
        return Err("Package library is not a directory".into());
    }
    spawn_recovery_tool(
        "restore",
        [
            "recovery-restore".to_owned(),
            path_argument(&input, "Recovery bundle")?,
            "--library".to_owned(),
            path_argument(&library, "Package library")?,
        ],
    )
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(PipelineState::default())
        .manage(VaultRotationState::default())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            studio_status,
            workspace_volume_status,
            start_pipeline,
            cancel_pipeline,
            pack_request,
            inspect_lrail,
            rotate_library_master,
            launch_recovery_export,
            launch_recovery_verify,
            launch_recovery_restore,
        ])
        .run(tauri::generate_context!())
        .expect("error while running LyricRail Studio");
}
