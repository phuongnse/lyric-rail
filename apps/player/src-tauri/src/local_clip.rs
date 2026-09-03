use std::{
    env, fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Child, Command, ExitStatus, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};
use tauri::{
    AppHandle, Manager, UriSchemeContext,
    http::{Method, Request, Response, StatusCode, header},
};
use uuid::Uuid;

use crate::{
    catalog::CatalogItem,
    local_source::{clipped_local_media_item_from_verified_path, is_media},
    parse_single_range,
    runtime::resolve_runtime,
    scheduler::{IoPriority, PriorityScheduler},
    tasks::{self, OutputStream, ProgressMode, TaskKind, TaskProgress, TaskSpec, TaskStatus},
};

const MAX_MEDIA_BYTES: u64 = 8 * 1024 * 1024 * 1024;
const MAX_PREVIEW_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const MAX_SOURCE_DURATION_MILLIS: u64 = 24 * 60 * 60 * 1000;
const MAX_PROBE_BYTES: usize = 1024 * 1024;
const MAX_TITLE_CHARS: usize = 200;
const SAFE_INPUT_FORMATS: &str = "mov,matroska,webm,mp3,aac,wav,ogg,flac,avi,asf";
const SAFE_INPUT_PROTOCOLS: &str = "file";
const CLIP_TASK_ID: &str = "clip-preparation";
type ClipProgressReporter = Arc<dyn Fn(u64, u64) + Send + Sync>;

#[derive(Default)]
struct LocalClipInner {
    preparing: bool,
    preview: Option<LocalClipSession>,
}

#[derive(Clone)]
struct LocalClipSession {
    clip_id: String,
    path: PathBuf,
    source_file: Arc<fs::File>,
    source_identity: SourceIdentity,
    preview_file: Arc<fs::File>,
    preview_size_bytes: u64,
    duration_millis: u64,
}

#[derive(Default)]
pub struct LocalClipState {
    inner: Mutex<LocalClipInner>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalClipPreview {
    clip_id: String,
    suggested_title: String,
    size_bytes: u64,
    duration_millis: u64,
    frame_duration_millis: Option<f64>,
    preview_url: String,
}

struct PreparedLocalClip {
    path: PathBuf,
    source_file: Arc<fs::File>,
    source_identity: SourceIdentity,
    preview_file: Arc<fs::File>,
    preview_size_bytes: u64,
    suggested_title: String,
    size_bytes: u64,
    duration_millis: u64,
    frame_duration_millis: Option<f64>,
}

#[cfg(unix)]
#[derive(Clone, Debug, PartialEq, Eq)]
struct SourceIdentity {
    size_bytes: u64,
    device: u64,
    inode: u64,
    modified_seconds: i64,
    modified_nanoseconds: i64,
    changed_seconds: i64,
    changed_nanoseconds: i64,
}

#[cfg(windows)]
#[derive(Clone, Debug, PartialEq, Eq)]
struct SourceIdentity {
    size_bytes: u64,
    volume_serial: u64,
    file_id: [u8; 16],
    created_ticks: i64,
    modified_ticks: i64,
    changed_ticks: i64,
}

#[cfg(not(any(unix, windows)))]
#[derive(Clone, Debug, PartialEq, Eq)]
struct SourceIdentity {
    size_bytes: u64,
    modified: Option<std::time::SystemTime>,
}

#[derive(Deserialize)]
struct ProbeOutput {
    #[serde(default)]
    streams: Vec<ProbeStream>,
    #[serde(default)]
    format: ProbeFormat,
}

#[derive(Default, Deserialize)]
struct ProbeFormat {
    duration: Option<String>,
}

#[derive(Deserialize)]
struct ProbeStream {
    codec_type: Option<String>,
    avg_frame_rate: Option<String>,
}

fn error_response(status: StatusCode, message: &str) -> Response<Vec<u8>> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "text/plain; charset=utf-8")
        .header(header::CACHE_CONTROL, "no-store")
        .body(message.as_bytes().to_vec())
        .expect("static local clip preview response")
}

fn validate_clip_id(value: &str) -> Result<(), String> {
    let parsed = Uuid::parse_str(value).map_err(|_| "Clip ID is invalid")?;
    if parsed.to_string() != value {
        return Err("Clip ID is invalid".into());
    }
    Ok(())
}

fn validate_title(value: &str) -> Result<String, String> {
    let title = value.trim();
    if title.is_empty()
        || title.chars().count() > MAX_TITLE_CHARS
        || title.chars().any(char::is_control)
    {
        return Err("Title must be 1 to 200 visible characters".into());
    }
    Ok(title.to_owned())
}

fn validate_local_source(path: &Path) -> Result<(PathBuf, u64), String> {
    let original = fs::symlink_metadata(path)
        .map_err(|error| format!("Unable to inspect selected media: {error}"))?;
    if original.file_type().is_symlink() || !original.is_file() {
        return Err("Select a supported regular local media file, not a link".into());
    }
    let path = path
        .canonicalize()
        .map_err(|error| format!("Unable to open selected media: {error}"))?;
    let metadata = fs::symlink_metadata(&path)
        .map_err(|error| format!("Unable to inspect selected media: {error}"))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() || !is_media(&path) {
        return Err("Selected file is not supported regular local media".into());
    }
    let size_bytes = metadata.len();
    if size_bytes == 0 || size_bytes > MAX_MEDIA_BYTES {
        return Err("Selected media must be between 1 byte and 8 GiB".into());
    }
    Ok((path, size_bytes))
}

#[cfg(unix)]
fn open_source_guard(path: &Path) -> Result<fs::File, String> {
    use std::os::fd::AsRawFd;

    let file = fs::File::open(path).map_err(|_| "Unable to pin selected media for preview")?;
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_SH | libc::LOCK_NB) } != 0 {
        return Err("Selected media is already locked for modification".into());
    }
    Ok(file)
}

#[cfg(windows)]
fn open_source_guard(path: &Path) -> Result<fs::File, String> {
    use std::os::windows::fs::OpenOptionsExt;
    use windows_sys::Win32::Storage::FileSystem::FILE_SHARE_READ;

    fs::OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .open(path)
        .map_err(|_| "Unable to lock selected media against changes while editing the clip".into())
}

#[cfg(not(any(unix, windows)))]
fn open_source_guard(path: &Path) -> Result<fs::File, String> {
    fs::File::open(path).map_err(|_| "Unable to pin selected media for preview".into())
}

#[cfg(unix)]
fn source_identity(file: &fs::File) -> Result<SourceIdentity, String> {
    use std::os::unix::fs::MetadataExt;

    let metadata = file
        .metadata()
        .map_err(|_| "Unable to inspect selected media identity")?;
    Ok(SourceIdentity {
        size_bytes: metadata.len(),
        device: metadata.dev(),
        inode: metadata.ino(),
        modified_seconds: metadata.mtime(),
        modified_nanoseconds: metadata.mtime_nsec(),
        changed_seconds: metadata.ctime(),
        changed_nanoseconds: metadata.ctime_nsec(),
    })
}

#[cfg(windows)]
fn source_identity(file: &fs::File) -> Result<SourceIdentity, String> {
    use std::{mem::MaybeUninit, os::windows::io::AsRawHandle};
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_BASIC_INFO, FILE_ID_INFO, FileBasicInfo, FileIdInfo, GetFileInformationByHandleEx,
    };

    let handle = file.as_raw_handle();
    let mut identifier = MaybeUninit::<FILE_ID_INFO>::zeroed();
    if unsafe {
        GetFileInformationByHandleEx(
            handle,
            FileIdInfo,
            identifier.as_mut_ptr().cast(),
            std::mem::size_of::<FILE_ID_INFO>() as u32,
        )
    } == 0
    {
        return Err("Unable to inspect selected media identity".into());
    }
    let mut basic = MaybeUninit::<FILE_BASIC_INFO>::zeroed();
    if unsafe {
        GetFileInformationByHandleEx(
            handle,
            FileBasicInfo,
            basic.as_mut_ptr().cast(),
            std::mem::size_of::<FILE_BASIC_INFO>() as u32,
        )
    } == 0
    {
        return Err("Unable to inspect selected media change metadata".into());
    }
    let identifier = unsafe { identifier.assume_init() };
    let basic = unsafe { basic.assume_init() };
    let metadata = file
        .metadata()
        .map_err(|_| "Unable to inspect selected media size")?;
    Ok(SourceIdentity {
        size_bytes: metadata.len(),
        volume_serial: identifier.VolumeSerialNumber,
        file_id: identifier.FileId.Identifier,
        created_ticks: basic.CreationTime,
        modified_ticks: basic.LastWriteTime,
        changed_ticks: basic.ChangeTime,
    })
}

#[cfg(not(any(unix, windows)))]
fn source_identity(file: &fs::File) -> Result<SourceIdentity, String> {
    let metadata = file
        .metadata()
        .map_err(|_| "Unable to inspect selected media identity")?;
    Ok(SourceIdentity {
        size_bytes: metadata.len(),
        modified: metadata.modified().ok(),
    })
}

fn verify_source_unchanged(
    pinned: &fs::File,
    path: &Path,
    expected: &SourceIdentity,
) -> Result<(), String> {
    if source_identity(pinned)? != *expected {
        return Err("Selected media changed after its preview was prepared".into());
    }
    let metadata =
        fs::symlink_metadata(path).map_err(|_| "Selected media is no longer available")?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("Selected media path changed after its preview was prepared".into());
    }
    let current = fs::File::open(path).map_err(|_| "Selected media is no longer available")?;
    if source_identity(&current)? != *expected {
        return Err("Selected media was replaced or modified after preview".into());
    }
    Ok(())
}

#[cfg(unix)]
fn platform_read_at(file: &fs::File, buffer: &mut [u8], offset: u64) -> std::io::Result<usize> {
    use std::os::unix::fs::FileExt;

    file.read_at(buffer, offset)
}

#[cfg(windows)]
fn platform_read_at(file: &fs::File, buffer: &mut [u8], offset: u64) -> std::io::Result<usize> {
    use std::os::windows::fs::FileExt;

    file.seek_read(buffer, offset)
}

#[cfg(not(any(unix, windows)))]
fn platform_read_at(file: &fs::File, buffer: &mut [u8], offset: u64) -> std::io::Result<usize> {
    use std::io::{Seek, SeekFrom};

    let mut file = file.try_clone()?;
    file.seek(SeekFrom::Start(offset))?;
    file.read(buffer)
}

fn read_exact_at(file: &fs::File, mut buffer: &mut [u8], mut offset: u64) -> Result<(), ()> {
    while !buffer.is_empty() {
        let read = platform_read_at(file, buffer, offset).map_err(|_| ())?;
        if read == 0 {
            return Err(());
        }
        offset = offset.checked_add(read as u64).ok_or(())?;
        buffer = &mut buffer[read..];
    }
    Ok(())
}

fn resolve_media_tools() -> Result<(PathBuf, PathBuf), String> {
    let runtime = resolve_runtime()?;
    let ffprobe = runtime
        .ffprobe
        .or_else(|| env::var_os("LYRICRAIL_FFPROBE").map(PathBuf::from))
        .ok_or_else(|| "Clip preview requires the verified ffprobe tool".to_string())?;
    let ffmpeg = runtime
        .ffmpeg
        .or_else(|| env::var_os("LYRICRAIL_FFMPEG").map(PathBuf::from))
        .ok_or_else(|| "Clip preview requires the verified ffmpeg tool".to_string())?;
    let ffprobe = ffprobe
        .canonicalize()
        .map_err(|_| "Clip preview ffprobe tool is unavailable")?;
    let ffmpeg = ffmpeg
        .canonicalize()
        .map_err(|_| "Clip preview ffmpeg tool is unavailable")?;
    if !ffprobe.is_file() || !ffmpeg.is_file() {
        return Err("Clip preview media tools are unavailable".into());
    }
    Ok((ffprobe, ffmpeg))
}

fn parse_frame_duration(rate: Option<&str>) -> Option<f64> {
    let (numerator, denominator) = rate?.split_once('/')?;
    let numerator = numerator.parse::<f64>().ok()?;
    let denominator = denominator.parse::<f64>().ok()?;
    (numerator.is_finite() && denominator.is_finite() && numerator > 0.0 && denominator > 0.0)
        .then_some((1000.0 * denominator / numerator).clamp(1.0, 1000.0))
}

fn wait_bounded_child(
    child: &mut Child,
    timeout: Duration,
    timeout_message: &str,
    aborted: Option<&AtomicBool>,
) -> Result<ExitStatus, String> {
    let deadline = Instant::now() + timeout;
    loop {
        if aborted.is_some_and(|aborted| aborted.load(Ordering::Acquire)) {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Portable clip preview exceeded its output bound".into());
        }
        match child.try_wait() {
            Ok(Some(status)) => return Ok(status),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(timeout_message.into());
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("Native media probe failed".into());
            }
        }
    }
}

fn bounded_command_output(mut command: Command) -> Result<Vec<u8>, String> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let mut child = command
        .spawn()
        .map_err(|_| "Unable to inspect selected media")?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "Local media probe has no output".to_string())?;
    let reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout
            .take((MAX_PROBE_BYTES + 1) as u64)
            .read_to_end(&mut bytes)
            .map(|_| bytes)
    });
    let status = wait_bounded_child(
        &mut child,
        Duration::from_secs(20),
        "Local media probe exceeded its time limit",
        None,
    )?;
    let bytes = reader
        .join()
        .map_err(|_| "Local media probe output failed".to_string())?
        .map_err(|_| "Local media probe output failed".to_string())?;
    if !status.success() || bytes.len() > MAX_PROBE_BYTES {
        return Err("Selected file is not valid bounded audio/video media".into());
    }
    Ok(bytes)
}

fn report_command(command: &Command, report: &impl Fn(&str, &[String])) {
    let program = command.get_program().to_string_lossy().into_owned();
    let arguments = command
        .get_args()
        .map(|argument| argument.to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    report(&program, &arguments);
}

fn probe_media_with_report(
    ffprobe: &Path,
    path: &Path,
    report: &impl Fn(&str, &[String]),
) -> Result<(u64, Option<f64>), String> {
    let mut command = Command::new(ffprobe);
    command.args([
        "-v",
        "error",
        "-protocol_whitelist",
        SAFE_INPUT_PROTOCOLS,
        "-format_whitelist",
        SAFE_INPUT_FORMATS,
        "-i",
    ]);
    command.arg(path).args([
        "-show_entries",
        "format=duration:stream=codec_type,avg_frame_rate",
        "-of",
        "json",
    ]);
    report_command(&command, report);
    let output = bounded_command_output(command)?;
    let probe: ProbeOutput =
        serde_json::from_slice(&output).map_err(|_| "Local media probe is invalid")?;
    let duration = probe
        .format
        .duration
        .as_deref()
        .and_then(|value| value.parse::<f64>().ok())
        .filter(|value| value.is_finite() && *value > 0.0)
        .ok_or_else(|| "Selected media has no valid duration".to_string())?;
    let duration_millis = (duration * 1000.0).round() as u64;
    if duration_millis == 0 || duration_millis > MAX_SOURCE_DURATION_MILLIS {
        return Err("Selected media duration is outside the 24-hour bound".into());
    }
    let video = probe
        .streams
        .iter()
        .find(|stream| stream.codec_type.as_deref() == Some("video"));
    let has_audio = probe
        .streams
        .iter()
        .any(|stream| stream.codec_type.as_deref() == Some("audio"));
    if !has_audio {
        return Err("Selected media contains no audio stream for karaoke preview".into());
    }
    Ok((
        duration_millis,
        video.and_then(|stream| parse_frame_duration(stream.avg_frame_rate.as_deref())),
    ))
}

#[cfg_attr(not(test), allow(dead_code))]
fn probe_media_with_tool(ffprobe: &Path, path: &Path) -> Result<(u64, Option<f64>), String> {
    probe_media_with_report(ffprobe, path, &|_, _| {})
}

fn wav_header(data_bytes: u32) -> [u8; 44] {
    let mut header = [0_u8; 44];
    header[0..4].copy_from_slice(b"RIFF");
    header[4..8].copy_from_slice(&(36_u32 + data_bytes).to_le_bytes());
    header[8..12].copy_from_slice(b"WAVE");
    header[12..16].copy_from_slice(b"fmt ");
    header[16..20].copy_from_slice(&16_u32.to_le_bytes());
    header[20..22].copy_from_slice(&1_u16.to_le_bytes());
    header[22..24].copy_from_slice(&1_u16.to_le_bytes());
    header[24..28].copy_from_slice(&16_000_u32.to_le_bytes());
    header[28..32].copy_from_slice(&16_000_u32.to_le_bytes());
    header[32..34].copy_from_slice(&1_u16.to_le_bytes());
    header[34..36].copy_from_slice(&8_u16.to_le_bytes());
    header[36..40].copy_from_slice(b"data");
    header[40..44].copy_from_slice(&data_bytes.to_le_bytes());
    header
}

#[cfg_attr(not(test), allow(dead_code))]
fn portable_preview_with_tool(
    ffmpeg: &Path,
    source: &Path,
    preview_root: &Path,
    duration_millis: u64,
) -> Result<(Arc<fs::File>, u64), String> {
    portable_preview_with_report(
        ffmpeg,
        source,
        preview_root,
        duration_millis,
        &|_, _| {},
        Arc::new(|_, _| {}),
    )
}

fn portable_preview_with_report(
    ffmpeg: &Path,
    source: &Path,
    preview_root: &Path,
    duration_millis: u64,
    report: &impl Fn(&str, &[String]),
    progress: ClipProgressReporter,
) -> Result<(Arc<fs::File>, u64), String> {
    use std::io::{Seek, SeekFrom};

    let expected_data_bytes = duration_millis
        .checked_mul(16)
        .filter(|bytes| *bytes <= MAX_PREVIEW_BYTES - 44)
        .ok_or_else(|| "Portable local preview duration exceeds its output bound".to_string())?;
    let duration_seconds = format!("{:.3}", duration_millis as f64 / 1000.0);
    let timeline_filter = format!(
        "aresample=16000:async=1:first_pts=0,apad,atrim=end={duration_seconds},asetpts=N/SR/TB"
    );
    fs::create_dir_all(preview_root)
        .map_err(|_| "Unable to create the local clip preview directory")?;
    let mut preview = tempfile::tempfile_in(preview_root)
        .map_err(|_| "Unable to create an anonymous local clip preview")?;
    preview
        .write_all(&wav_header(0))
        .map_err(|_| "Unable to initialize the local clip preview")?;
    let mut output = preview
        .try_clone()
        .map_err(|_| "Unable to initialize the local clip preview writer")?;

    let mut command = Command::new(ffmpeg);
    command
        .args([
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            SAFE_INPUT_PROTOCOLS,
            "-format_whitelist",
            SAFE_INPUT_FORMATS,
            "-i",
        ])
        .arg(source)
        .args([
            "-map",
            "0:a:0",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-vn",
            "-sn",
            "-dn",
            "-af",
            &timeline_filter,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_u8",
            "-threads",
            "1",
            "-t",
            &duration_seconds,
            "-fs",
            &(MAX_PREVIEW_BYTES - 44).to_string(),
            "-f",
            "u8",
            "pipe:1",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    report_command(&command, report);
    let mut child = command
        .spawn()
        .map_err(|_| "Unable to create a portable local audio preview")?;
    let mut stdout = child
        .stdout
        .take()
        .ok_or_else(|| "Portable local preview has no media output".to_string())?;
    let aborted = Arc::new(AtomicBool::new(false));
    let writer_aborted = aborted.clone();
    let writer_progress = progress.clone();
    let writer = thread::spawn(move || {
        let result: Result<u64, String> = (|| {
            let mut total = 0_u64;
            let mut last_report = Instant::now() - Duration::from_millis(100);
            let mut block = [0_u8; 64 * 1024];
            loop {
                let count = stdout
                    .read(&mut block)
                    .map_err(|_| "Unable to read portable local preview output".to_string())?;
                if count == 0 {
                    break;
                }
                total = total
                    .checked_add(count as u64)
                    .filter(|total| *total <= MAX_PREVIEW_BYTES - 44)
                    .ok_or_else(|| {
                        "Portable local preview exceeded its output bound".to_string()
                    })?;
                output
                    .write_all(&block[..count])
                    .map_err(|_| "Unable to store portable local preview output".to_string())?;
                let now = Instant::now();
                if now.duration_since(last_report) >= Duration::from_millis(100)
                    || total >= expected_data_bytes
                {
                    writer_progress(total.min(expected_data_bytes), expected_data_bytes);
                    last_report = now;
                }
            }
            output
                .flush()
                .map_err(|_| "Unable to flush portable local preview output".to_string())?;
            Ok(total)
        })();
        if result.is_err() {
            writer_aborted.store(true, Ordering::Release);
        }
        result
    });
    let status = wait_bounded_child(
        &mut child,
        Duration::from_secs(5 * 60),
        "Portable local preview exceeded its time limit",
        Some(&aborted),
    );
    let produced_data_bytes = writer
        .join()
        .map_err(|_| "Portable local preview writer failed".to_string())??;
    let status = status?;
    if !status.success() || produced_data_bytes == 0 {
        return Err("Unable to create a portable local audio preview".into());
    }
    if produced_data_bytes > expected_data_bytes {
        preview
            .set_len(expected_data_bytes + 44)
            .map_err(|_| "Unable to trim portable preview to the source timeline")?;
    } else if produced_data_bytes < expected_data_bytes {
        preview
            .seek(SeekFrom::End(0))
            .map_err(|_| "Unable to extend portable preview to the source timeline")?;
        let silence = [128_u8; 64 * 1024];
        let mut remaining = expected_data_bytes - produced_data_bytes;
        while remaining > 0 {
            let count = usize::try_from(remaining.min(silence.len() as u64))
                .expect("bounded preview block fits usize");
            preview
                .write_all(&silence[..count])
                .map_err(|_| "Unable to pad portable preview to the source timeline")?;
            remaining -= count as u64;
        }
    }
    let data_bytes = u32::try_from(expected_data_bytes)
        .map_err(|_| "Portable local preview exceeded the WAV format bound")?;
    preview
        .seek(SeekFrom::Start(0))
        .and_then(|_| preview.write_all(&wav_header(data_bytes)))
        .and_then(|_| preview.sync_data())
        .map_err(|_| "Unable to finalize the portable local audio preview")?;
    progress(expected_data_bytes, expected_data_bytes);
    Ok((Arc::new(preview), u64::from(data_bytes) + 44))
}

fn suggested_title(path: &Path) -> String {
    let title = path
        .file_stem()
        .map(|value| value.to_string_lossy().trim().to_owned())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "Local clip".into());
    title.chars().take(MAX_TITLE_CHARS).collect()
}

fn prepare_source(
    path: PathBuf,
    preview_root: PathBuf,
    report: impl Fn(&str, &str),
    report_command: impl Fn(&str, &[String]),
    report_units: impl Fn(u64, u64) + Send + Sync + 'static,
) -> Result<PreparedLocalClip, String> {
    report("validate", "Validate selected local media");
    let (path, size_bytes) = validate_local_source(&path)?;
    let source_file = Arc::new(open_source_guard(&path)?);
    let source_identity = source_identity(&source_file)?;
    if source_identity.size_bytes != size_bytes {
        return Err("Selected media changed while its preview was prepared".into());
    }
    report("probe", "Probe duration and streams");
    let (ffprobe, ffmpeg) = resolve_media_tools()?;
    let (duration_millis, frame_duration_millis) =
        probe_media_with_report(&ffprobe, &path, &report_command)?;
    verify_source_unchanged(&source_file, &path, &source_identity)?;
    report("portable-preview", "Build portable realtime preview");
    let (preview_file, preview_size_bytes) = portable_preview_with_report(
        &ffmpeg,
        &path,
        &preview_root,
        duration_millis,
        &report_command,
        Arc::new(report_units),
    )?;
    verify_source_unchanged(&source_file, &path, &source_identity)?;
    Ok(PreparedLocalClip {
        suggested_title: suggested_title(&path),
        path,
        source_file,
        source_identity,
        preview_file,
        preview_size_bytes,
        size_bytes,
        duration_millis,
        frame_duration_millis,
    })
}

fn preview_url(clip_id: &str) -> String {
    if cfg!(any(target_os = "windows", target_os = "android")) {
        format!("http://clippreview.localhost/{clip_id}")
    } else {
        format!("clippreview://localhost/{clip_id}")
    }
}

pub async fn prepare(
    app: AppHandle,
    scheduler: Arc<PriorityScheduler>,
    path: PathBuf,
) -> Result<LocalClipPreview, String> {
    let preview_root = app
        .path()
        .app_cache_dir()
        .map_err(|_| "Unable to resolve the local clip preview directory")?
        .join("clip-preview");
    {
        let state = app.state::<LocalClipState>();
        let mut inner = state
            .inner
            .lock()
            .map_err(|_| "Local clip state lock is poisoned".to_string())?;
        if inner.preparing || inner.preview.is_some() {
            return Err("Finish or cancel the current clip first".into());
        }
        inner.preparing = true;
    }
    if let Err(error) = tasks::start(
        &app,
        TaskSpec {
            id: CLIP_TASK_ID.into(),
            kind: TaskKind::ClipPreparation,
            title: "Prepare local clip".into(),
            status: TaskStatus::Running,
            progress_mode: ProgressMode::Indeterminate,
            cancellable: false,
            related_item_id: None,
        },
    ) {
        if let Ok(mut inner) = app.state::<LocalClipState>().inner.lock() {
            inner.preparing = false;
        }
        return Err(error);
    }
    tasks::append_output(
        &app,
        CLIP_TASK_ID,
        OutputStream::System,
        None,
        "Local clip preparation started",
    );
    let task_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let _permit = scheduler.acquire(IoPriority::AlternateTrack)?;
        let stage_app = task_app.clone();
        let command_app = task_app.clone();
        let unit_app = task_app;
        prepare_source(
            path,
            preview_root,
            |stage, title| {
                tasks::progress(
                    &stage_app,
                    CLIP_TASK_ID,
                    TaskProgress {
                        stage_key: Some(stage.into()),
                        stage_title: Some(title.into()),
                        message: Some(title.into()),
                        ..Default::default()
                    },
                );
            },
            |program, arguments| {
                tasks::append_command(&command_app, CLIP_TASK_ID, program, arguments);
            },
            move |completed, total| {
                let percent = if total == 0 {
                    100.0
                } else {
                    completed as f32 / total as f32 * 100.0
                };
                tasks::progress(
                    &unit_app,
                    CLIP_TASK_ID,
                    TaskProgress {
                        stage_key: Some("portable-preview".into()),
                        stage_title: Some("Build portable realtime preview".into()),
                        stage_progress_percent: Some(percent),
                        progress_percent: None,
                        completed_units: Some(completed),
                        total_units: Some(total),
                        unit_label: Some("bytes".into()),
                        message: Some(format!("Prepared {completed} of {total} preview bytes")),
                    },
                );
            },
        )
    })
    .await
    .map_err(|error| format!("Local clip task failed: {error}"));
    let state = app.state::<LocalClipState>();
    let mut inner = state
        .inner
        .lock()
        .map_err(|_| "Local clip state lock is poisoned".to_string())?;
    inner.preparing = false;
    let prepared = match result.and_then(|result| result) {
        Ok(prepared) => prepared,
        Err(error) => {
            tasks::finish(&app, CLIP_TASK_ID, TaskStatus::Failed, Some(error.clone()));
            return Err(error);
        }
    };
    let clip_id = Uuid::new_v4().to_string();
    let preview = LocalClipPreview {
        clip_id: clip_id.clone(),
        suggested_title: prepared.suggested_title,
        size_bytes: prepared.size_bytes,
        duration_millis: prepared.duration_millis,
        frame_duration_millis: prepared.frame_duration_millis,
        preview_url: preview_url(&clip_id),
    };
    inner.preview = Some(LocalClipSession {
        clip_id,
        path: prepared.path,
        source_file: prepared.source_file,
        source_identity: prepared.source_identity,
        preview_file: prepared.preview_file,
        preview_size_bytes: prepared.preview_size_bytes,
        duration_millis: prepared.duration_millis,
    });
    tasks::finish(
        &app,
        CLIP_TASK_ID,
        TaskStatus::Succeeded,
        Some("Local clip preview ready".into()),
    );
    Ok(preview)
}

pub fn cancel(app: &AppHandle, clip_id: &str) -> Result<bool, String> {
    validate_clip_id(clip_id)?;
    let state = app.state::<LocalClipState>();
    let mut inner = state
        .inner
        .lock()
        .map_err(|_| "Local clip state lock is poisoned".to_string())?;
    Ok(cancel_inner(&mut inner, clip_id))
}

fn cancel_inner(inner: &mut LocalClipInner, clip_id: &str) -> bool {
    if inner
        .preview
        .as_ref()
        .is_some_and(|preview| preview.clip_id == clip_id)
    {
        inner.preview = None;
        true
    } else {
        false
    }
}

pub fn commit(
    app: &AppHandle,
    clip_id: &str,
    start_millis: u64,
    end_millis: u64,
    title: &str,
) -> Result<CatalogItem, String> {
    validate_clip_id(clip_id)?;
    let title = validate_title(title)?;
    let state = app.state::<LocalClipState>();
    let (path, source_file, source_identity, duration_millis) = {
        let inner = state
            .inner
            .lock()
            .map_err(|_| "Local clip state lock is poisoned".to_string())?;
        let session = inner
            .preview
            .as_ref()
            .filter(|preview| preview.clip_id == clip_id)
            .ok_or_else(|| "Clip preview is no longer available".to_string())?;
        (
            session.path.clone(),
            session.source_file.clone(),
            session.source_identity.clone(),
            session.duration_millis,
        )
    };
    if end_millis <= start_millis || end_millis > duration_millis {
        return Err("Clip timestamps are outside the selected media duration".into());
    }
    verify_source_unchanged(&source_file, &path, &source_identity)?;
    clipped_local_media_item_from_verified_path(path, title, start_millis, end_millis)
}

fn preview_file_response(
    source: &fs::File,
    length: u64,
    method: &Method,
    range: Option<&str>,
) -> Response<Vec<u8>> {
    if method == Method::HEAD {
        return Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, "audio/wav")
            .header(header::CONTENT_LENGTH, length)
            .header(header::ACCEPT_RANGES, "bytes")
            .header(header::CACHE_CONTROL, "no-store")
            .header(header::ACCESS_CONTROL_ALLOW_ORIGIN, "*")
            .body(Vec::new())
            .expect("valid local clip preview HEAD response");
    }
    let (start, end) = match range {
        Some(value) => match parse_single_range(value, length) {
            Ok(range) => range,
            Err(()) => return error_response(StatusCode::RANGE_NOT_SATISFIABLE, "Invalid range"),
        },
        None if length > 0 => (0, (length - 1).min(2 * 1024 * 1024 - 1)),
        None => return error_response(StatusCode::NOT_FOUND, "Not found"),
    };
    let count = (end - start + 1) as usize;
    let mut bytes = vec![0_u8; count];
    if read_exact_at(source, &mut bytes, start).is_err() {
        return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Preview unavailable");
    }
    let partial = start != 0 || end + 1 != length;
    let mut builder = Response::builder()
        .status(if partial {
            StatusCode::PARTIAL_CONTENT
        } else {
            StatusCode::OK
        })
        .header(header::CONTENT_TYPE, "audio/wav")
        .header(header::CONTENT_LENGTH, bytes.len())
        .header(header::ACCEPT_RANGES, "bytes")
        .header(header::CACHE_CONTROL, "no-store")
        .header(header::ACCESS_CONTROL_ALLOW_ORIGIN, "*")
        .header(header::ACCESS_CONTROL_EXPOSE_HEADERS, "content-range");
    if partial {
        builder = builder.header(
            header::CONTENT_RANGE,
            format!("bytes {start}-{end}/{length}"),
        );
    }
    builder
        .body(bytes)
        .expect("valid local clip preview response")
}

pub fn preview_protocol(
    context: UriSchemeContext<'_, tauri::Wry>,
    request: Request<Vec<u8>>,
) -> Response<Vec<u8>> {
    if context.webview_label() != "main" {
        return error_response(StatusCode::NOT_FOUND, "Not found");
    }
    if request.method() != Method::GET && request.method() != Method::HEAD {
        return error_response(StatusCode::METHOD_NOT_ALLOWED, "Method not allowed");
    }
    let clip_id = request.uri().path().trim_start_matches('/');
    if validate_clip_id(clip_id).is_err() {
        return error_response(StatusCode::NOT_FOUND, "Not found");
    }
    let descriptor = context
        .app_handle()
        .state::<LocalClipState>()
        .inner
        .lock()
        .ok()
        .and_then(|inner| {
            inner
                .preview
                .as_ref()
                .filter(|preview| preview.clip_id == clip_id)
                .map(|preview| (preview.preview_file.clone(), preview.preview_size_bytes))
        });
    let Some((file, length)) = descriptor else {
        return error_response(StatusCode::NOT_FOUND, "Not found");
    };
    let range = request
        .headers()
        .get(header::RANGE)
        .and_then(|value| value.to_str().ok());
    preview_file_response(&file, length, request.method(), range)
}

#[cfg(test)]
mod tests {
    use super::{
        LocalClipInner, LocalClipSession, cancel_inner, open_source_guard, parse_frame_duration,
        portable_preview_with_tool, preview_file_response, read_exact_at, source_identity,
        validate_local_source, verify_source_unchanged,
    };
    use std::{
        env, fs,
        path::PathBuf,
        process::Command,
        sync::{Arc, Barrier},
        thread,
        time::Duration,
    };
    use tauri::http::{Method, StatusCode, header};

    fn available_tool(variable: &str, name: &str) -> Option<PathBuf> {
        if let Some(path) = env::var_os(variable).map(PathBuf::from)
            && path.is_file()
        {
            return Some(path);
        }
        Command::new(name)
            .arg("-version")
            .output()
            .ok()
            .filter(|output| output.status.success())
            .map(|_| PathBuf::from(name))
    }

    fn write_test_wav(path: &std::path::Path) {
        let sample_rate = 8_000_u32;
        let samples = vec![0_i16; sample_rate as usize];
        let data_length = (samples.len() * 2) as u32;
        let mut bytes = Vec::with_capacity(44 + data_length as usize);
        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(&(36 + data_length).to_le_bytes());
        bytes.extend_from_slice(b"WAVEfmt ");
        bytes.extend_from_slice(&16_u32.to_le_bytes());
        bytes.extend_from_slice(&1_u16.to_le_bytes());
        bytes.extend_from_slice(&1_u16.to_le_bytes());
        bytes.extend_from_slice(&sample_rate.to_le_bytes());
        bytes.extend_from_slice(&(sample_rate * 2).to_le_bytes());
        bytes.extend_from_slice(&2_u16.to_le_bytes());
        bytes.extend_from_slice(&16_u16.to_le_bytes());
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&data_length.to_le_bytes());
        for sample in samples {
            bytes.extend_from_slice(&sample.to_le_bytes());
        }
        fs::write(path, bytes).unwrap();
    }

    #[test]
    fn frame_duration_is_bounded_and_exact_enough_for_nudging() {
        assert_eq!(
            parse_frame_duration(Some("30000/1001")),
            Some(1001.0 / 30.0)
        );
        assert_eq!(parse_frame_duration(Some("0/1")), None);
        assert_eq!(parse_frame_duration(None), None);
    }

    #[test]
    fn preview_serves_only_the_requested_bounded_range() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source.mp4");
        fs::write(&path, (0_u8..100).collect::<Vec<_>>()).unwrap();
        let file = fs::File::open(&path).unwrap();
        let response = preview_file_response(&file, 100, &Method::GET, Some("bytes=10-19"));
        assert_eq!(response.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(response.headers()[header::CONTENT_RANGE], "bytes 10-19/100");
        assert_eq!(response.headers()[header::CONTENT_TYPE], "audio/wav");
        assert_eq!(response.body(), &(10_u8..20).collect::<Vec<_>>());
    }

    #[test]
    fn cancelling_a_preview_never_changes_the_selected_file() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source.mp4");
        let bytes = b"original media bytes";
        fs::write(&path, bytes).unwrap();
        let canonical = path.canonicalize().unwrap();
        let source_file = Arc::new(fs::File::open(&path).unwrap());
        let source_identity = source_identity(&source_file).unwrap();
        let preview_file = Arc::new(tempfile::tempfile().unwrap());
        let mut inner = LocalClipInner {
            preparing: false,
            preview: Some(LocalClipSession {
                clip_id: "4ca99e8b-ce8f-4d68-b6ab-c7566025cd7b".into(),
                path: canonical,
                source_file,
                source_identity,
                preview_file,
                preview_size_bytes: 44,
                duration_millis: 1_000,
            }),
        };
        assert!(cancel_inner(
            &mut inner,
            "4ca99e8b-ce8f-4d68-b6ab-c7566025cd7b"
        ));
        assert_eq!(fs::read(path).unwrap(), bytes);
    }

    #[test]
    fn local_source_validation_is_canonical_and_read_only() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source.mp4");
        let bytes = b"unchanged";
        fs::write(&path, bytes).unwrap();
        let (validated, size) = validate_local_source(&path).unwrap();
        assert_eq!(validated, path.canonicalize().unwrap());
        assert_eq!(size, bytes.len() as u64);
        assert_eq!(fs::read(path).unwrap(), bytes);
    }

    #[cfg(unix)]
    #[test]
    fn local_source_validation_rejects_symlinks() {
        use std::os::unix::fs::symlink;

        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("source.mp4");
        let link = directory.path().join("link.mp4");
        fs::write(&source, b"media").unwrap();
        symlink(&source, &link).unwrap();
        assert!(validate_local_source(&link).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn local_source_validation_rejects_symlinks_when_supported() {
        use std::os::windows::fs::symlink_file;

        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("source.mp4");
        let link = directory.path().join("link.mp4");
        fs::write(&source, b"media").unwrap();
        if symlink_file(&source, &link).is_ok() {
            assert!(validate_local_source(&link).is_err());
        }
    }

    #[test]
    fn unsupported_extensions_are_rejected_before_media_tools_run() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source.txt");
        fs::write(&path, b"not media").unwrap();
        assert!(validate_local_source(&path).is_err());
    }

    #[test]
    fn bounded_probe_accepts_a_regular_local_audio_file() {
        let Some(ffprobe) = available_tool("LYRICRAIL_FFPROBE", "ffprobe") else {
            return;
        };
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source.wav");
        write_test_wav(&path);
        let (duration, frame_duration) = super::probe_media_with_tool(&ffprobe, &path).unwrap();
        assert_eq!(duration, 1_000);
        assert_eq!(frame_duration, None);
    }

    #[test]
    fn bounded_probe_rejects_a_playlist_disguised_as_local_media() {
        let Some(ffprobe) = available_tool("LYRICRAIL_FFPROBE", "ffprobe") else {
            return;
        };
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("playlist.mp4");
        fs::write(
            &path,
            b"#EXTM3U\n#EXTINF:1,\nhttps://example.invalid/private-segment.ts\n",
        )
        .unwrap();
        assert!(super::probe_media_with_tool(&ffprobe, &path).is_err());
    }

    #[test]
    fn concurrent_preview_ranges_do_not_share_a_cursor() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("preview.wav");
        let bytes = (0_u32..64 * 1024)
            .map(|value| (value % 251) as u8)
            .collect::<Vec<_>>();
        fs::write(&path, &bytes).unwrap();
        let file = Arc::new(fs::File::open(path).unwrap());
        let barrier = Arc::new(Barrier::new(8));
        let workers = (0..8)
            .map(|worker| {
                let file = file.clone();
                let barrier = barrier.clone();
                let expected = bytes.clone();
                thread::spawn(move || {
                    let start = worker * 4_096;
                    let end = start + 2_047;
                    barrier.wait();
                    for _ in 0..40 {
                        let response = preview_file_response(
                            &file,
                            expected.len() as u64,
                            &Method::GET,
                            Some(&format!("bytes={start}-{end}")),
                        );
                        assert_eq!(response.body(), &expected[start..=end]);
                    }
                })
            })
            .collect::<Vec<_>>();
        for worker in workers {
            worker.join().unwrap();
        }
    }

    #[test]
    fn preview_identity_rejects_same_size_in_place_changes() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source.mp4");
        fs::write(&path, b"first version").unwrap();
        let pinned = fs::File::open(&path).unwrap();
        let identity = source_identity(&pinned).unwrap();
        thread::sleep(Duration::from_millis(15));
        fs::write(&path, b"other version").unwrap();
        assert_eq!(fs::metadata(&path).unwrap().len(), identity.size_bytes);
        assert!(verify_source_unchanged(&pinned, &path, &identity).is_err());
    }

    #[test]
    fn preview_identity_rejects_path_replacement() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source.mp4");
        let moved = directory.path().join("original.mp4");
        fs::write(&path, b"original bytes").unwrap();
        let pinned = fs::File::open(&path).unwrap();
        let identity = source_identity(&pinned).unwrap();
        fs::rename(&path, &moved).unwrap();
        fs::write(&path, b"replaced bytes").unwrap();
        assert!(verify_source_unchanged(&pinned, &path, &identity).is_err());
    }

    #[test]
    fn wma_source_gets_an_anonymous_pcm_wav_preview() {
        let (Some(ffmpeg), Some(ffprobe)) = (
            available_tool("LYRICRAIL_FFMPEG", "ffmpeg"),
            available_tool("LYRICRAIL_FFPROBE", "ffprobe"),
        ) else {
            return;
        };
        let directory = tempfile::tempdir().unwrap();
        let wav = directory.path().join("source.wav");
        let wma = directory.path().join("source.wma");
        write_test_wav(&wav);
        let status = Command::new(&ffmpeg)
            .args(["-y", "-v", "error", "-i"])
            .arg(&wav)
            .args(["-c:a", "wmav2"])
            .arg(&wma)
            .status()
            .unwrap();
        assert!(status.success());
        let (duration, _) = super::probe_media_with_tool(&ffprobe, &wma).unwrap();
        let source_before = fs::read(&wma).unwrap();
        let source_file = open_source_guard(&wma).unwrap();
        let identity = source_identity(&source_file).unwrap();
        let (preview, preview_size) =
            portable_preview_with_tool(&ffmpeg, &wma, directory.path(), duration).unwrap();
        verify_source_unchanged(&source_file, &wma, &identity).unwrap();
        let mut header = [0_u8; 44];
        read_exact_at(&preview, &mut header, 0).unwrap();
        assert_eq!(&header[0..4], b"RIFF");
        assert_eq!(&header[8..12], b"WAVE");
        assert_eq!(&header[36..40], b"data");
        assert_eq!(
            preview_size,
            u64::from(u32::from_le_bytes(header[40..44].try_into().unwrap())) + 44
        );
        assert_eq!(fs::read(wma).unwrap(), source_before);
    }

    #[test]
    fn portable_preview_preserves_delayed_and_short_audio_on_the_source_timeline() {
        let (Some(ffmpeg), Some(ffprobe)) = (
            available_tool("LYRICRAIL_FFMPEG", "ffmpeg"),
            available_tool("LYRICRAIL_FFPROBE", "ffprobe"),
        ) else {
            return;
        };
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("delayed-audio.mp4");
        let status = Command::new(&ffmpeg)
            .args([
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=64x64:r=25:d=5",
                "-itsoffset",
                "2",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:sample_rate=8000:duration=1",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "mpeg4",
                "-q:v",
                "10",
                "-c:a",
                "aac",
                "-t",
                "5",
            ])
            .arg(&source)
            .status()
            .unwrap();
        assert!(status.success());
        let (duration, _) = super::probe_media_with_tool(&ffprobe, &source).unwrap();
        assert_eq!(duration, 5_000);

        let (preview, preview_size) =
            portable_preview_with_tool(&ffmpeg, &source, directory.path(), duration).unwrap();
        assert_eq!(preview_size, 44 + 5 * 16_000);
        let mut pcm = vec![0_u8; 5 * 16_000];
        read_exact_at(&preview, &mut pcm, 44).unwrap();
        assert!(pcm[..24_000].iter().all(|sample| *sample == 128));
        assert!(
            pcm[30_000..50_000]
                .iter()
                .any(|sample| sample.abs_diff(128) > 4)
        );
        assert!(pcm[56_000..].iter().all(|sample| *sample == 128));
    }
}
