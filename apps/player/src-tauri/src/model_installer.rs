use std::{
    fs,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::Duration,
};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

use crate::{
    issues, processing,
    runtime::{ResolvedRuntime, resolve_runtime},
    tasks::{self, OutputStream, ProgressMode, TaskKind, TaskProgress, TaskSpec, TaskStatus},
};

const MAX_INSTALL_OUTPUT_BYTES: usize = 1024 * 1024;
const MAX_INSTALL_LINE_BYTES: usize = 16 * 1024;
const MODEL_ISSUE_ID: &str = "processing.models-missing:processing:system";
pub const MODEL_TASK_ID: &str = "model-install";

#[derive(Default)]
struct InstallInner {
    active: Option<ActiveInstall>,
}

struct ActiveInstall {
    issue_id: String,
    cancelled: Arc<AtomicBool>,
}

#[derive(Default)]
pub struct ModelInstallerState {
    inner: Mutex<InstallInner>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelInstallResult {
    verified: bool,
    retried_items: usize,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct InstallerProgress {
    kind: String,
    #[serde(default)]
    progress_percent: f32,
    #[serde(default)]
    message: String,
}

struct OutputLine {
    stderr: bool,
    line: String,
}

#[cfg(windows)]
struct InstallProcessJob {
    _handle: std::os::windows::io::OwnedHandle,
}

#[cfg(windows)]
impl InstallProcessJob {
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
        Ok(Self { _handle: owned })
    }
}

#[cfg(unix)]
fn lower_priority(child: &Child) -> std::io::Result<()> {
    if unsafe { libc::setpriority(libc::PRIO_PROCESS, child.id(), 10) } == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

fn install_allowed(runtime: &ResolvedRuntime) -> bool {
    runtime.integrity == "development-unverified"
}

fn reserve_install(
    inner: &mut InstallInner,
    issue_id: String,
    cancelled: Arc<AtomicBool>,
) -> Result<(), String> {
    if inner.active.is_some() {
        return Err("A model installation is already running".into());
    }
    inner.active = Some(ActiveInstall {
        issue_id,
        cancelled,
    });
    Ok(())
}

fn canonical_installer(runtime: &ResolvedRuntime) -> Result<PathBuf, String> {
    if !install_allowed(runtime) {
        return Err(
            "A signed runtime cannot be modified. Reinstall a complete verified runtime pack."
                .into(),
        );
    }
    let script = runtime.root.join("scripts/install_models.py");
    let metadata = fs::symlink_metadata(&script)
        .map_err(|_| "The repository-owned model installer is unavailable")?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("The repository-owned model installer is invalid".into());
    }
    let script = script
        .canonicalize()
        .map_err(|_| "The repository-owned model installer is unavailable")?;
    if !script.starts_with(&runtime.root) {
        return Err("The model installer escaped the verified runtime root".into());
    }
    Ok(script)
}

fn installer_command(runtime: &ResolvedRuntime, script: &Path) -> Command {
    let mut command = Command::new(&runtime.python);
    command
        .current_dir(&runtime.root)
        .env("LYRICRAIL_HOME", &runtime.root)
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONUNBUFFERED", "1")
        .arg("-s")
        .arg(script)
        .arg("--json-lines")
        .stdin(Stdio::null())
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

fn stop_child(child: &mut Child) {
    #[cfg(unix)]
    if let Ok(process_id) = i32::try_from(child.id()) {
        unsafe {
            libc::kill(-process_id, libc::SIGTERM);
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn output_reader(
    mut stream: impl std::io::Read + Send + 'static,
    stderr: bool,
    sender: mpsc::SyncSender<OutputLine>,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        let mut block = [0_u8; 4 * 1024];
        let mut line = Vec::with_capacity(4 * 1024);
        let mut truncated = false;
        while let Ok(count) = stream.read(&mut block) {
            if count == 0 {
                break;
            }
            for byte in &block[..count] {
                if *byte == b'\n' || *byte == b'\r' {
                    if !line.is_empty() {
                        let output = OutputLine {
                            stderr,
                            line: String::from_utf8_lossy(&line).into_owned(),
                        };
                        if sender.send(output).is_err() {
                            return;
                        }
                    }
                    line.clear();
                    truncated = false;
                } else if !truncated {
                    if line.len() < MAX_INSTALL_LINE_BYTES {
                        line.push(*byte);
                    } else {
                        truncated = true;
                    }
                }
            }
        }
        if !line.is_empty() {
            let _ = sender.send(OutputLine {
                stderr,
                line: String::from_utf8_lossy(&line).into_owned(),
            });
        }
    })
}

fn installer_progress(output: &OutputLine) -> Option<InstallerProgress> {
    if output.stderr {
        return None;
    }
    serde_json::from_str::<InstallerProgress>(&output.line)
        .ok()
        .filter(|progress| progress.kind == "lyricrail.model-install.progress")
}

fn handle_line(app: &AppHandle, output: &OutputLine) {
    let progress = installer_progress(output);
    if let Some(progress) = progress.as_ref() {
        tasks::progress(
            app,
            MODEL_TASK_ID,
            TaskProgress {
                stage_key: Some("download-and-verify".into()),
                stage_title: Some("Download and verify pinned models".into()),
                stage_progress_percent: Some(progress.progress_percent),
                progress_percent: Some(progress.progress_percent),
                message: Some(progress.message.clone()),
                ..Default::default()
            },
        );
    }
    tasks::append_output(
        app,
        MODEL_TASK_ID,
        if output.stderr {
            OutputStream::Stderr
        } else if progress.is_some() {
            OutputStream::Progress
        } else {
            OutputStream::Stdout
        },
        Some("download-and-verify"),
        &output.line,
    );
}

fn safe_install_detail(value: &str, runtime_root: &Path) -> String {
    let mut detail = value.replace(&runtime_root.display().to_string(), "<runtime>");
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
        .take(MAX_INSTALL_OUTPUT_BYTES)
        .collect()
}

fn monitor_installer_child(
    child: &mut Child,
    cancelled: &AtomicBool,
    runtime_root: &Path,
    mut on_line: impl FnMut(&OutputLine),
) -> Result<(), String> {
    let stdout = match child.stdout.take() {
        Some(stdout) => stdout,
        None => {
            stop_child(child);
            return Err("Model installer has no progress output".into());
        }
    };
    let stderr = match child.stderr.take() {
        Some(stderr) => stderr,
        None => {
            stop_child(child);
            return Err("Model installer has no diagnostic output".into());
        }
    };
    let (sender, receiver) = mpsc::sync_channel(64);
    let stdout_reader = output_reader(stdout, false, sender.clone());
    let stderr_reader = output_reader(stderr, true, sender);
    let mut diagnostics = String::new();
    let status = loop {
        if cancelled.load(Ordering::Acquire) {
            stop_child(child);
            break Err(
                "Model installation was cancelled; verified existing files were preserved".into(),
            );
        }
        match receiver.recv_timeout(Duration::from_millis(100)) {
            Ok(output) => {
                on_line(&output);
                if diagnostics.len() < MAX_INSTALL_OUTPUT_BYTES {
                    let remaining = MAX_INSTALL_OUTPUT_BYTES - diagnostics.len();
                    diagnostics.push_str(&String::from_utf8_lossy(
                        &output.line.as_bytes()[..output.line.len().min(remaining)],
                    ));
                    diagnostics.push('\n');
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {}
        }
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) => {}
            Err(error) => {
                stop_child(child);
                break Err(format!("Unable to inspect model installer: {error}"));
            }
        }
    };
    drop(receiver);
    let _ = stdout_reader.join();
    let _ = stderr_reader.join();
    let status = status?;
    if !status.success() {
        let detail = safe_install_detail(diagnostics.trim(), runtime_root);
        return Err(if detail.is_empty() {
            format!("Pinned model installer exited with {status}")
        } else {
            detail
        });
    }
    Ok(())
}

fn run_installer(
    app: AppHandle,
    runtime: ResolvedRuntime,
    script: PathBuf,
    cancelled: Arc<AtomicBool>,
) -> Result<(), String> {
    tasks::append_command(
        &app,
        MODEL_TASK_ID,
        &runtime.python.display().to_string(),
        &[
            "-s".into(),
            script.display().to_string(),
            "--json-lines".into(),
        ],
    );
    let mut child = installer_command(&runtime, &script)
        .spawn()
        .map_err(|error| format!("Unable to start the pinned model installer: {error}"))?;
    #[cfg(windows)]
    let _job = InstallProcessJob::assign_and_lower_priority(&child).map_err(|error| {
        stop_child(&mut child);
        format!("Unable to contain the model installer: {error}")
    })?;
    #[cfg(unix)]
    lower_priority(&child).map_err(|error| {
        stop_child(&mut child);
        format!("Unable to lower model installer priority: {error}")
    })?;
    monitor_installer_child(&mut child, &cancelled, &runtime.root, |output| {
        handle_line(&app, output);
    })
}

pub async fn install(
    app: AppHandle,
    issue_id: String,
    license_confirmed: bool,
) -> Result<ModelInstallResult, String> {
    if issue_id != MODEL_ISSUE_ID || !license_confirmed {
        return Err("Confirm the model size and upstream license terms before installation".into());
    }
    let runtime = resolve_runtime()?;
    let script = canonical_installer(&runtime)?;
    let cancelled = Arc::new(AtomicBool::new(false));
    {
        let state = app.state::<ModelInstallerState>();
        let mut inner = state
            .inner
            .lock()
            .map_err(|_| "Model installer state lock is poisoned".to_string())?;
        reserve_install(&mut inner, issue_id.clone(), cancelled.clone())?;
    }
    tasks::start(
        &app,
        TaskSpec {
            id: MODEL_TASK_ID.into(),
            kind: TaskKind::ModelInstall,
            title: "Install processing models".into(),
            status: TaskStatus::Queued,
            progress_mode: ProgressMode::Indeterminate,
            cancellable: true,
            related_item_id: None,
        },
    )?;
    if let Err(error) = issues::set_resolving(
        &app,
        &issue_id,
        "Preparing pinned model installation",
        Some(MODEL_TASK_ID),
    ) {
        if let Ok(mut inner) = app.state::<ModelInstallerState>().inner.lock() {
            inner.active = None;
        }
        tasks::finish(&app, MODEL_TASK_ID, TaskStatus::Failed, Some(error.clone()));
        return Err(error);
    }
    let task_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        run_installer(task_app, runtime, script, cancelled)
    })
    .await
    .map_err(|error| format!("Model installer task failed: {error}"))
    .and_then(|result| result);
    if let Ok(mut inner) = app.state::<ModelInstallerState>().inner.lock() {
        inner.active = None;
    }
    if let Err(error) = result {
        issues::resolution_failed(&app, &issue_id, &error);
        tasks::finish(
            &app,
            MODEL_TASK_ID,
            if error.contains("cancelled") {
                TaskStatus::Cancelled
            } else {
                TaskStatus::Failed
            },
            Some(error.clone()),
        );
        return Err(error);
    }
    let retried_items = match processing::retry_setup_required(&app) {
        Ok(count) => count,
        Err(error) => {
            issues::resolution_failed(&app, &issue_id, &error);
            tasks::finish(&app, MODEL_TASK_ID, TaskStatus::Failed, Some(error.clone()));
            return Err(error);
        }
    };
    issues::resolve(&app, &issue_id);
    tasks::finish(
        &app,
        MODEL_TASK_ID,
        TaskStatus::Succeeded,
        Some(format!(
            "Pinned models verified; retried {retried_items} songs"
        )),
    );
    Ok(ModelInstallResult {
        verified: true,
        retried_items,
    })
}

pub fn cancel(app: &AppHandle, issue_id: &str) -> Result<bool, String> {
    if issue_id != MODEL_ISSUE_ID {
        return Err("Unknown model installation issue".into());
    }
    let state = app.state::<ModelInstallerState>();
    let inner = state
        .inner
        .lock()
        .map_err(|_| "Model installer state lock is poisoned".to_string())?;
    let Some(active) = &inner.active else {
        return Ok(false);
    };
    if active.issue_id != issue_id {
        return Ok(false);
    }
    active.cancelled.store(true, Ordering::Release);
    tasks::progress(
        app,
        MODEL_TASK_ID,
        TaskProgress {
            stage_key: Some("cancelling".into()),
            stage_title: Some("Cancelling model installation".into()),
            message: Some("Waiting for the contained installer process to stop".into()),
            ..Default::default()
        },
    );
    Ok(true)
}

pub fn cancel_active(app: &AppHandle) -> Result<bool, String> {
    cancel(app, MODEL_ISSUE_ID)
}

pub fn install_is_allowed() -> bool {
    resolve_runtime().is_ok_and(|runtime| install_allowed(&runtime))
}

#[cfg(test)]
mod tests {
    #[cfg(windows)]
    use super::InstallProcessJob;
    use super::{
        InstallInner, MAX_INSTALL_LINE_BYTES, OutputLine, install_allowed, installer_command,
        installer_progress, monitor_installer_child, reserve_install,
    };
    use crate::runtime::ResolvedRuntime;
    use std::{
        fs,
        path::{Path, PathBuf},
        process::{Command, Stdio},
        sync::{
            Arc, Mutex,
            atomic::{AtomicBool, Ordering},
        },
        thread,
        time::{Duration, Instant},
    };

    fn runtime(integrity: &'static str) -> ResolvedRuntime {
        ResolvedRuntime {
            root: PathBuf::from("runtime"),
            python: PathBuf::from("python"),
            ffmpeg: None,
            ffprobe: None,
            lrail: None,
            integrity,
        }
    }

    fn fixture_command(mode: &str) -> Command {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "model_installer::tests::installer_child_fixture",
                "--nocapture",
            ])
            .env("LYRICRAIL_INSTALLER_TEST_MODE", mode)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        command
    }

    fn wait_for_file(path: &Path) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !path.is_file() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert!(path.is_file());
    }

    #[test]
    fn installer_child_fixture() {
        let Ok(mode) = std::env::var("LYRICRAIL_INSTALLER_TEST_MODE") else {
            return;
        };
        match mode.as_str() {
            "success" => {
                println!(
                    "{{\"kind\":\"lyricrail.model-install.progress\",\"progressPercent\":55,\"message\":\"fixture\"}}"
                );
                println!("{}", "x".repeat(MAX_INSTALL_LINE_BYTES * 2));
            }
            "wait" | "descendant" => thread::sleep(Duration::from_secs(30)),
            "tree" => {
                let pid_path = PathBuf::from(std::env::var_os("LYRICRAIL_CHILD_PID_FILE").unwrap());
                let gate_path =
                    PathBuf::from(std::env::var_os("LYRICRAIL_TREE_GATE_FILE").unwrap());
                wait_for_file(&gate_path);
                let mut descendant = fixture_command("descendant");
                descendant.stdout(Stdio::null()).stderr(Stdio::null());
                let mut descendant = descendant.spawn().unwrap();
                fs::write(pid_path, descendant.id().to_string()).unwrap();
                thread::sleep(Duration::from_secs(30));
                let _ = descendant.wait();
            }
            _ => panic!("unknown installer fixture mode"),
        }
    }

    #[test]
    fn signed_runtime_model_mutation_is_never_allowed() {
        assert!(!install_allowed(&runtime("signed-verified")));
        assert!(install_allowed(&runtime("development-unverified")));
    }

    #[test]
    fn installer_command_is_a_fixed_argument_array_without_a_shell() {
        let command = installer_command(
            &runtime("development-unverified"),
            &PathBuf::from("runtime/scripts/install_models.py"),
        );
        let arguments = command
            .get_args()
            .map(|value| value.to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        assert_eq!(
            arguments,
            ["-s", "runtime/scripts/install_models.py", "--json-lines"]
        );
        assert_eq!(command.get_program(), "python");
    }

    #[test]
    fn one_active_install_is_enforced_before_process_launch() {
        let mut inner = InstallInner::default();
        reserve_install(&mut inner, "issue".into(), Arc::new(AtomicBool::new(false))).unwrap();
        assert!(
            reserve_install(&mut inner, "other".into(), Arc::new(AtomicBool::new(false)),)
                .unwrap_err()
                .contains("already running")
        );
    }

    #[test]
    fn controlled_child_proves_progress_line_bounds_and_cancellation() {
        let mut success = fixture_command("success").spawn().unwrap();
        let observed = Arc::new(Mutex::new(Vec::new()));
        let callback = observed.clone();
        monitor_installer_child(
            &mut success,
            &AtomicBool::new(false),
            Path::new("runtime"),
            move |line| {
                callback
                    .lock()
                    .unwrap()
                    .push((line.stderr, line.line.clone()))
            },
        )
        .unwrap();
        let observed = observed.lock().unwrap();
        assert!(
            observed
                .iter()
                .all(|(_, line)| line.len() <= MAX_INSTALL_LINE_BYTES)
        );
        assert!(observed.iter().any(|(stderr, line)| {
            installer_progress(&OutputLine {
                stderr: *stderr,
                line: line.clone(),
            })
            .is_some_and(|progress| progress.progress_percent == 55.0)
        }));
        drop(observed);

        let mut waiting = fixture_command("wait").spawn().unwrap();
        let cancelled = Arc::new(AtomicBool::new(false));
        let signal = cancelled.clone();
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(150));
            signal.store(true, Ordering::Release);
        });
        let started = Instant::now();
        let error = monitor_installer_child(&mut waiting, &cancelled, Path::new("runtime"), |_| {})
            .unwrap_err();
        assert!(error.contains("cancelled"));
        assert!(started.elapsed() < Duration::from_secs(5));
        assert!(waiting.try_wait().unwrap().is_some());
    }

    #[cfg(windows)]
    #[test]
    fn closing_the_job_terminates_the_installer_process_tree() {
        use windows_sys::Win32::{
            Foundation::{CloseHandle, STILL_ACTIVE},
            System::Threading::{
                GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
            },
        };

        let directory = tempfile::tempdir().unwrap();
        let pid_path = directory.path().join("descendant.pid");
        let gate_path = directory.path().join("tree.gate");
        let mut command = fixture_command("tree");
        command
            .env("LYRICRAIL_CHILD_PID_FILE", &pid_path)
            .env("LYRICRAIL_TREE_GATE_FILE", &gate_path);
        let mut parent = command.spawn().unwrap();
        let job = InstallProcessJob::assign_and_lower_priority(&parent).unwrap();
        fs::write(&gate_path, b"go").unwrap();
        wait_for_file(&pid_path);
        let descendant_id = fs::read_to_string(&pid_path)
            .unwrap()
            .parse::<u32>()
            .unwrap();
        drop(job);
        let deadline = Instant::now() + Duration::from_secs(5);
        while parent.try_wait().unwrap().is_none() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert!(parent.try_wait().unwrap().is_some());
        let descendant =
            unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, descendant_id) };
        if !descendant.is_null() {
            let mut exit_code = STILL_ACTIVE as u32;
            let queried = unsafe { GetExitCodeProcess(descendant, &mut exit_code) };
            unsafe { CloseHandle(descendant) };
            assert!(queried != 0 && exit_code != STILL_ACTIVE as u32);
        }
    }
}
