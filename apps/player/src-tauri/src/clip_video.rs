//! Bounded anonymous video proxy; source presentation timestamps own frame stepping.
use super::local_clip::{
    SAFE_INPUT_FORMATS, SAFE_INPUT_PROTOCOLS, read_exact_at, wait_bounded_child,
};
use std::{
    fs,
    io::{Read, Write},
    path::Path,
    process::{Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::Duration,
};

const MAX_FRAMES: usize = 1_000_000;
const MAX_TIMELINE_BYTES: usize = 24 * 1024 * 1024;
const MAX_VIDEO_BYTES: u64 = 2 * 1024 * 1024 * 1024;

pub(super) fn parse_frames(bytes: &[u8], duration: u64) -> Result<Vec<f64>, String> {
    let text = std::str::from_utf8(bytes).map_err(|_| "Invalid video frame timeline")?;
    let mut frames = Vec::new();
    for line in text.lines().filter(|line| !line.trim().is_empty()) {
        // ffprobe CSV may append a side-data field after the timestamp.
        let value = line
            .split(',')
            .next()
            .unwrap_or("")
            .parse::<f64>()
            .map_err(|_| "Video frame timestamp unavailable")?
            * 1000.0;
        if !value.is_finite()
            || value < 0.0
            || value > duration as f64 + 1.0
            || frames.last().is_some_and(|last| value <= *last)
            || frames.len() >= MAX_FRAMES
        {
            return Err("Video frame timeline exceeds supported bounds".into());
        }
        if frames
            .last()
            .is_some_and(|last: &f64| value.floor() <= last.floor())
        {
            return Err("Video frames exceed millisecond selection precision".into());
        }
        frames.push(value);
    }
    if frames.is_empty() {
        return Err("Video has no preview frames".into());
    }
    Ok(frames)
}

pub(super) fn prepare(
    ffmpeg: &Path,
    ffprobe: &Path,
    source: &Path,
    root: &Path,
    duration: u64,
    offset_millis: f64,
) -> Result<(Arc<fs::File>, u64, Vec<f64>), String> {
    fs::create_dir_all(root).map_err(|_| "Unable to prepare video cache")?;
    let file =
        tempfile::tempfile_in(root).map_err(|_| "Unable to create anonymous video preview")?;
    let mut output = file
        .try_clone()
        .map_err(|_| "Unable to initialize video preview")?;
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
            "0:v:0",
            "-an",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-sn",
            "-dn",
            "-vf",
            "scale=640:360:force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "25",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "passthrough",
            "-enc_time_base:v",
            "1:1000000",
            "-video_track_timescale",
            "1000000",
            "-threads",
            "2",
            "-t",
        ])
        .arg(format!("{:.3}", duration as f64 / 1000.0))
        .args([
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
            "pipe:1",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command
        .spawn()
        .map_err(|_| "Unable to launch video preview encoder")?;
    let mut pipe = child
        .stdout
        .take()
        .ok_or("Video preview pipe unavailable")?;
    let aborted = Arc::new(AtomicBool::new(false));
    let writer_aborted = aborted.clone();
    let writer = thread::spawn(move || {
        let result = (|| {
            let mut total = 0u64;
            let mut buffer = [0u8; 65536];
            loop {
                let count = pipe
                    .read(&mut buffer)
                    .map_err(|_| "Video preview read failed")?;
                if count == 0 {
                    break;
                }
                total += count as u64;
                if total > MAX_VIDEO_BYTES {
                    return Err("Video preview exceeds 2 GiB");
                }
                output
                    .write_all(&buffer[..count])
                    .map_err(|_| "Video preview write failed")?;
            }
            output
                .sync_all()
                .map_err(|_| "Video preview flush failed")?;
            Ok(total)
        })();
        if result.is_err() {
            writer_aborted.store(true, Ordering::Release);
        }
        result
    });
    let status = wait_bounded_child(
        &mut child,
        Duration::from_secs(300),
        "Video preview exceeded five minutes",
        Some(&aborted),
    );
    let size = writer.join().map_err(|_| "Video preview writer failed")??;
    if !status?.success() || size == 0 {
        return Err("Video preview encoding failed".into());
    }

    // Probe the encoded frames through stdin: no named clear file or filesystem URL enters the UI.
    let mut probe = Command::new(ffprobe);
    probe
        .args([
            "-v",
            "error",
            "-protocol_whitelist",
            "pipe",
            "-format_whitelist",
            "mov",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "csv=p=0",
            "-i",
            "pipe:0",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        probe.creation_flags(0x08000000);
    }
    let mut child = probe
        .spawn()
        .map_err(|_| "Unable to inspect preview frames")?;
    let mut stdin = child.stdin.take().ok_or("Frame probe input unavailable")?;
    let stdout = child
        .stdout
        .take()
        .ok_or("Frame probe output unavailable")?;
    let input = file
        .try_clone()
        .map_err(|_| "Frame probe file unavailable")?;
    let feeder = thread::spawn(move || {
        let mut offset = 0;
        let mut buffer = [0u8; 65536];
        while offset < size {
            let count = (size - offset).min(buffer.len() as u64) as usize;
            read_exact_at(&input, &mut buffer[..count], offset)
                .map_err(|_| "Frame probe input failed")?;
            stdin
                .write_all(&buffer[..count])
                .map_err(|_| "Frame probe stopped reading")?;
            offset += count as u64;
        }
        Ok::<(), &str>(())
    });
    let reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout
            .take((MAX_TIMELINE_BYTES + 1) as u64)
            .read_to_end(&mut bytes)
            .map(|_| bytes)
    });
    let status = wait_bounded_child(
        &mut child,
        Duration::from_secs(120),
        "Frame inspection exceeded two minutes",
        None,
    );
    let fed = feeder.join().map_err(|_| "Frame probe input failed")?;
    let bytes = reader
        .join()
        .map_err(|_| "Frame probe output failed")?
        .map_err(|_| "Frame probe read failed")?;
    if !status?.success() || fed.is_err() || bytes.len() > MAX_TIMELINE_BYTES {
        return Err("Video frame inspection failed or exceeded bounds".into());
    }
    let mut frames = parse_frames(&bytes, duration)?;
    for frame in &mut frames {
        *frame += offset_millis;
    }
    if !offset_millis.is_finite()
        || offset_millis < 0.0
        || frames
            .last()
            .is_some_and(|frame| *frame > duration as f64 + 1.0)
    {
        return Err("Video frame offset exceeds source timeline".into());
    }
    Ok((Arc::new(file), size, frames))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_timeline_rejects_missing_reordered_or_out_of_bounds_evidence() {
        assert_eq!(
            parse_frames(b"0.000\n0.033367\n0.1001\n", 200).unwrap(),
            [0.0, 33.367, 100.1]
        );
        for invalid in [
            b"NaN\n".as_slice(),
            b"0\n0\n",
            b"2\n",
            b"-1\n",
            b"N/A\n",
            b"",
        ] {
            assert!(parse_frames(invalid, 200).is_err());
        }
    }

    #[test]
    fn video_proxy_preserves_variable_frame_timestamps_and_source_bytes() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("source.mp4");
        let ffmpeg = std::env::var_os("LYRICRAIL_FFMPEG")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| "ffmpeg".into());
        let ffprobe = std::env::var_os("LYRICRAIL_FFPROBE")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| "ffprobe".into());
        let status = Command::new(&ffmpeg)
            .args([
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=10:duration=1",
                "-vf",
                "select='eq(n,0)+eq(n,1)+eq(n,3)+eq(n,7)'",
                "-fps_mode",
                "vfr",
                "-c:v",
                "libx264",
                "-bf",
                "0",
                "-pix_fmt",
                "yuv420p",
            ])
            .arg(&source)
            .status()
            .unwrap();
        assert!(status.success());
        let original = fs::read(&source).unwrap();
        let cache = root.path().join("cache");
        let (file, size, frames) = prepare(&ffmpeg, &ffprobe, &source, &cache, 1000, 0.0).unwrap();
        assert!(size > 0);
        assert_eq!(frames.len(), 4);
        for (actual, expected) in frames.iter().zip([0.0_f64, 100.0, 300.0, 700.0]) {
            assert!((actual - expected).abs() < 0.01, "{frames:?}");
        }
        let mut header = [0; 12];
        read_exact_at(&file, &mut header, 0).unwrap();
        assert_eq!(&header[4..8], b"ftyp");
        drop(file);
        assert_eq!(fs::read(&source).unwrap(), original);
        assert_eq!(fs::read_dir(&cache).unwrap().count(), 0);
        let delayed = root.path().join("delayed.mp4");
        let status = Command::new(&ffmpeg)
            .args([
                "-v",
                "error",
                "-itsoffset",
                "0.3",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=10:duration=1",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=1.5",
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-fps_mode",
                "passthrough",
                "-c:v",
                "libx264",
                "-bf",
                "0",
                "-c:a",
                "aac",
            ])
            .arg(&delayed)
            .status()
            .unwrap();
        assert!(status.success());
        let (_, _, offset) =
            crate::local_clip::probe_media_with_report(&ffprobe, &delayed, &|_, _| {}).unwrap();
        assert!((offset.unwrap() - 300.0).abs() < 0.01);
        let (_, _, delayed_frames) =
            prepare(&ffmpeg, &ffprobe, &delayed, &cache, 1500, offset.unwrap()).unwrap();
        assert!(
            (delayed_frames[0] - 300.0).abs() < 0.01,
            "{delayed_frames:?}"
        );
    }
}
