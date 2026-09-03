use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

use lrail_format::{
    AssetRequest, ContentEncoding, PackageReader, PackageRevisionRequest, load_vault_master,
    revise_package_in_place_for_vault,
};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Manager};
use tempfile::tempdir;

use crate::{
    CatalogState, ItemLocation, PlayerState, local_source::scan_files, runtime::resolve_runtime,
    save_and_emit,
};

fn package_path(app: &AppHandle, item_id: &str) -> Result<PathBuf, String> {
    let item = app
        .state::<CatalogState>()
        .0
        .lock()
        .map_err(|_| "Catalog lock is poisoned".to_string())?
        .item(item_id)
        .cloned()
        .ok_or_else(|| "Library item no longer exists".to_string())?;
    item.locations
        .iter()
        .find_map(|location| match location {
            ItemLocation::LocalPackage { path, .. } => Some(path.clone()),
            _ => None,
        })
        .ok_or_else(|| "Copy a cloud package to local disk before editing lyrics".into())
}

fn read_json_asset(reader: &mut PackageReader, name: &str) -> Result<Value, String> {
    let asset = reader
        .manifest
        .assets
        .iter()
        .find(|asset| asset.logical_name == name)
        .ok_or_else(|| format!("Package is missing {name}"))?;
    if asset.plaintext_length > 16 * 1024 * 1024 {
        return Err(format!("{name} exceeds the revision limit"));
    }
    let bytes = reader.read_asset(name).map_err(|error| error.to_string())?;
    serde_json::from_slice(&bytes).map_err(|error| format!("Invalid {name}: {error}"))
}

fn semantic_lines(text: &str) -> Result<Vec<String>, String> {
    if text.len() > 1_000_000 || text.contains('\0') {
        return Err("Revised lyrics exceed the UTF-8 text bound".into());
    }
    let lines = text
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if lines.is_empty() {
        return Err("Revised lyrics contain no non-empty lines".into());
    }
    Ok(lines)
}

fn revise_render_plan(render_plan: &mut Value, timing: &Value) -> Result<(), String> {
    let lines = timing
        .get("lines")
        .and_then(Value::as_array)
        .ok_or_else(|| "Timing payload has no lines".to_string())?;
    let events = render_plan
        .get_mut("events")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| "Render plan has no events".to_string())?;
    for event in events {
        let index = event
            .get("lineIndex")
            .and_then(Value::as_u64)
            .and_then(|value| usize::try_from(value).ok())
            .and_then(|value| value.checked_sub(1))
            .ok_or_else(|| "Render plan line index is invalid".to_string())?;
        let aligned_line = lines
            .get(index)
            .ok_or_else(|| "Render plan references a missing lyric line".to_string())?;
        event
            .as_object_mut()
            .ok_or_else(|| "Render plan event is not an object".to_string())?
            .insert("line".into(), aligned_line.clone());
    }
    Ok(())
}

fn update_text_identical_timing(
    timing: &mut Value,
    lines: &[String],
    sha256: &str,
) -> Result<bool, String> {
    let object = timing
        .as_object_mut()
        .ok_or_else(|| "Timing payload is not an object".to_string())?;
    let timed_lines = object
        .get_mut("lines")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| "Timing payload has no lines".to_string())?;
    if timed_lines.len() != lines.len() {
        return Err(
            "This edit changes the sung line structure; reprocess the original local media".into(),
        );
    }
    let mut acoustic_change = false;
    for (line, exact_text) in timed_lines.iter_mut().zip(lines) {
        let object = line
            .as_object_mut()
            .ok_or_else(|| "Timing line is not an object".to_string())?;
        let current_words = object
            .get("syllables")
            .and_then(Value::as_array)
            .ok_or_else(|| "Timing line has no syllables".to_string())?
            .iter()
            .map(|word| word.get("text").and_then(Value::as_str).unwrap_or(""))
            .collect::<Vec<_>>();
        let revised_words = exact_text.split_whitespace().collect::<Vec<_>>();
        if current_words != revised_words {
            acoustic_change = true;
        } else {
            object.insert("text".into(), Value::String(exact_text.clone()));
        }
    }
    if acoustic_change {
        return Ok(true);
    }
    let word_count = lines
        .iter()
        .map(|line| line.split_whitespace().count())
        .sum::<usize>();
    object.insert("lineCount".into(), json!(lines.len()));
    if let Some(authoritative) = object
        .get_mut("authoritativeLyrics")
        .and_then(Value::as_object_mut)
    {
        authoritative.insert("sha256".into(), Value::String(sha256.into()));
        authoritative.insert("lineCount".into(), json!(lines.len()));
        authoritative.insert("wordCount".into(), json!(word_count));
    }
    if let Some(diagnostics) = object
        .get_mut("alignmentDiagnostics")
        .and_then(Value::as_object_mut)
    {
        diagnostics.insert("inputSha256".into(), Value::String(sha256.into()));
    }
    Ok(false)
}

fn ass_escape(text: &str) -> String {
    text.replace('\\', "\\\\")
        .replace('{', "\\{")
        .replace('}', "\\}")
}

fn filter_path(path: &Path) -> String {
    path.canonicalize()
        .unwrap_or_else(|_| path.to_path_buf())
        .to_string_lossy()
        .replace('\\', "/")
        .replace('\'', "\\'")
        .replace(':', "\\:")
}

fn replacement_thumbnail(
    directory: &Path,
    first_line: &str,
    base: Option<&Path>,
) -> Result<PathBuf, String> {
    if first_line
        .chars()
        .any(|character| character < ' ' && character != '\t')
    {
        return Err("First lyric line contains unsupported control characters".into());
    }
    let runtime = resolve_runtime()?;
    let ffmpeg = runtime.ffmpeg.unwrap_or_else(|| {
        PathBuf::from(if cfg!(windows) {
            "ffmpeg.exe"
        } else {
            "ffmpeg"
        })
    });
    let ass = directory.join("thumbnail.ass");
    fs::write(
        &ass,
        format!(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\nWrapStyle: 0\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Thumb,Be Vietnam Pro,34,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,38,38,30,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\nDialogue: 0,0:00:00.00,0:00:10.00,Thumb,,0,0,0,,{}\n",
            ass_escape(first_line)
        ),
    )
    .map_err(|error| format!("Unable to write thumbnail text: {error}"))?;
    let output = directory.join("thumbnail.webp");
    let filter = format!("ass='{}'", filter_path(&ass));
    let mut command = Command::new(ffmpeg);
    command.args(["-hide_banner", "-loglevel", "error", "-nostdin", "-y"]);
    if let Some(base) = base {
        command.arg("-i").arg(base);
    } else {
        command.args(["-f", "lavfi", "-i", "color=c=0x090d16:s=640x360:r=1"]);
    }
    let status = command
        .args([
            "-frames:v",
            "1",
            "-vf",
            &filter,
            "-c:v",
            "libwebp",
            "-quality",
            "82",
            "-compression_level",
            "6",
        ])
        .arg(&output)
        .status()
        .map_err(|error| format!("Unable to start thumbnail renderer: {error}"))?;
    let bytes = fs::metadata(&output)
        .map(|metadata| metadata.len())
        .unwrap_or(0);
    if !status.success() || bytes == 0 || bytes > 1024 * 1024 {
        return Err("Unable to render a bounded revision thumbnail".into());
    }
    Ok(output)
}

fn extract_authenticated_asset(
    reader: &mut PackageReader,
    logical_name: &str,
    output: &Path,
    maximum_bytes: u64,
) -> Result<(), String> {
    let asset = reader
        .manifest
        .assets
        .iter()
        .find(|asset| asset.logical_name == logical_name)
        .cloned()
        .ok_or_else(|| format!("Package is missing {logical_name}"))?;
    if asset.plaintext_length == 0 || asset.plaintext_length > maximum_bytes {
        return Err(format!(
            "{logical_name} exceeds the revision extraction bound"
        ));
    }
    let mut file = fs::File::create(output)
        .map_err(|error| format!("Unable to create revision input: {error}"))?;
    let mut digest = Sha256::new();
    let mut offset = 0_u64;
    while offset < asset.plaintext_length {
        let length = usize::try_from((asset.plaintext_length - offset).min(1024 * 1024))
            .map_err(|_| "Revision asset chunk exceeds this platform".to_string())?;
        let bytes = reader
            .read_asset_range(logical_name, offset, length)
            .map_err(|error| error.to_string())?;
        digest.update(bytes.as_slice());
        file.write_all(bytes.as_slice())
            .map_err(|error| format!("Unable to write revision input: {error}"))?;
        offset += length as u64;
    }
    file.sync_all()
        .map_err(|error| format!("Unable to flush revision input: {error}"))?;
    let observed: [u8; 32] = digest.finalize().into();
    if observed.as_slice() != asset.sha256.as_slice() {
        return Err(format!("{logical_name} failed full asset authentication"));
    }
    Ok(())
}

fn run_revision_alignment(
    audio: &Path,
    timing: &Path,
    lyrics: &Path,
    output: &Path,
) -> Result<(), String> {
    let runtime = resolve_runtime()?;
    let mut command = Command::new(&runtime.python);
    command
        .current_dir(&runtime.root)
        .env("LYRICRAIL_HOME", &runtime.root)
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
    command
        .args(["-m", "lyricrail", "revision-align", "--audio"])
        .arg(audio)
        .arg("--timing")
        .arg(timing)
        .arg("--lyrics")
        .arg(lyrics)
        .arg("--output")
        .arg(output)
        .arg("--root")
        .arg(&runtime.root)
        .arg("--json")
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("Unable to start affected-scope lyric alignment: {error}"))?;
    #[cfg(unix)]
    unsafe {
        libc::setpriority(libc::PRIO_PROCESS, child.id(), 10);
    }
    #[cfg(windows)]
    unsafe {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::Threading::{
            BELOW_NORMAL_PRIORITY_CLASS, SetPriorityClass,
        };
        SetPriorityClass(child.as_raw_handle().cast(), BELOW_NORMAL_PRIORITY_CLASS);
    }
    let status = child
        .wait()
        .map_err(|error| format!("Unable to wait for lyric alignment: {error}"))?;
    if !status.success() || !output.is_file() {
        return Err(
            "The changed lyric scope could not be aligned safely; keep the old package or reprocess the original media"
                .into(),
        );
    }
    Ok(())
}

fn update_release_metadata(
    release: &mut Value,
    sha256: &str,
    line_count: usize,
    word_count: usize,
) {
    if !release.is_object() {
        *release = Value::Object(Map::new());
    }
    let object = release.as_object_mut().expect("release metadata object");
    let lyrics = object
        .entry("lyrics")
        .or_insert_with(|| Value::Object(Map::new()));
    if let Some(lyrics) = lyrics.as_object_mut() {
        lyrics.insert("sha256".into(), Value::String(sha256.into()));
        lyrics.insert("lineCount".into(), json!(line_count));
        lyrics.insert("wordCount".into(), json!(word_count));
    }
}

pub fn revise(app: AppHandle, item_id: String, text: String) -> Result<(), String> {
    let path = package_path(&app, &item_id)?;
    let lines = semantic_lines(&text)?;
    let sha256 = hex::encode(Sha256::digest(text.as_bytes()));
    let master = load_vault_master().map_err(|error| error.to_string())?;
    let mut reader =
        PackageReader::open_with_vault(&path, &master).map_err(|error| error.to_string())?;
    let mut timing = read_json_asset(&mut reader, "lyrics/timing.json")?;
    let mut render_plan = read_json_asset(&mut reader, "lyrics/render-plan.json")?;
    let mut release = read_json_asset(&mut reader, "metadata/release.json")?;
    let needs_alignment = update_text_identical_timing(&mut timing, &lines, &sha256)?;
    let temporary = tempdir().map_err(|error| error.to_string())?;
    let timing_input = temporary.path().join("timing-input.json");
    let timing_path = temporary.path().join("timing.json");
    let lyrics_path = temporary.path().join("authoritative.txt");
    fs::write(
        &timing_input,
        serde_json::to_vec(&timing).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::write(&lyrics_path, text.as_bytes()).map_err(|error| error.to_string())?;
    if needs_alignment {
        let audio_name = reader
            .manifest
            .assets
            .iter()
            .find(|asset| asset.logical_name.starts_with("audio/original-reference."))
            .map(|asset| asset.logical_name.clone())
            .ok_or_else(|| {
                "Package has no Original Reference track for lyric alignment".to_string()
            })?;
        let suffix = Path::new(&audio_name)
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("m4a");
        let audio_path = temporary
            .path()
            .join(format!("original-reference.{suffix}"));
        extract_authenticated_asset(
            &mut reader,
            &audio_name,
            &audio_path,
            2 * 1024 * 1024 * 1024,
        )?;
        run_revision_alignment(&audio_path, &timing_input, &lyrics_path, &timing_path)?;
        let bytes = fs::read(&timing_path)
            .map_err(|error| format!("Unable to read revised timing: {error}"))?;
        timing = serde_json::from_slice(&bytes)
            .map_err(|error| format!("Revised timing is invalid: {error}"))?;
    } else {
        fs::copy(&timing_input, &timing_path)
            .map_err(|error| format!("Unable to preserve revised timing: {error}"))?;
    }
    revise_render_plan(&mut render_plan, &timing)?;
    let word_count = lines
        .iter()
        .map(|line| line.split_whitespace().count())
        .sum::<usize>();
    update_release_metadata(&mut release, &sha256, lines.len(), word_count);
    let mut metadata = reader.manifest.metadata.clone();
    update_release_metadata(&mut metadata, &sha256, lines.len(), word_count);
    let revision = metadata
        .get("revision")
        .and_then(Value::as_u64)
        .unwrap_or(1)
        .saturating_add(1);
    if let Some(object) = metadata.as_object_mut() {
        object.insert("revision".into(), json!(revision));
    }
    let render_path = temporary.path().join("render-plan.json");
    let release_path = temporary.path().join("release.json");
    fs::write(
        &render_path,
        serde_json::to_vec(&render_plan).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::write(
        &release_path,
        serde_json::to_vec(&release).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    let thumbnail_base = if reader
        .manifest
        .assets
        .iter()
        .any(|asset| asset.logical_name == "artwork/thumbnail-base.webp")
    {
        let base = temporary.path().join("thumbnail-base.webp");
        extract_authenticated_asset(
            &mut reader,
            "artwork/thumbnail-base.webp",
            &base,
            1024 * 1024,
        )?;
        Some(base)
    } else {
        None
    };
    drop(reader);
    let thumbnail = replacement_thumbnail(temporary.path(), &lines[0], thumbnail_base.as_deref())?;
    if let Ok(mut loaded) = app.state::<PlayerState>().loaded.lock() {
        loaded.take();
    }
    revise_package_in_place_for_vault(
        &path,
        &master,
        &PackageRevisionRequest {
            metadata: Some(metadata),
            producer: Some("LyricRail Player lyric revision".into()),
            assets: vec![
                AssetRequest {
                    logical_name: "lyrics/authoritative.txt".into(),
                    path: lyrics_path,
                    media_type: "text/plain; charset=utf-8".into(),
                    kind: "authoritative-lyrics".into(),
                    track_name: None,
                    language: Some("vi".into()),
                    default: false,
                    content_encoding: ContentEncoding::Identity,
                },
                AssetRequest {
                    logical_name: "lyrics/timing.json".into(),
                    path: timing_path,
                    media_type: "application/json".into(),
                    kind: "lyrics-timing".into(),
                    track_name: None,
                    language: Some("vi".into()),
                    default: false,
                    content_encoding: ContentEncoding::Identity,
                },
                AssetRequest {
                    logical_name: "lyrics/render-plan.json".into(),
                    path: render_path,
                    media_type: "application/json".into(),
                    kind: "lyrics-render-plan".into(),
                    track_name: None,
                    language: Some("vi".into()),
                    default: false,
                    content_encoding: ContentEncoding::Identity,
                },
                AssetRequest {
                    logical_name: "metadata/release.json".into(),
                    path: release_path,
                    media_type: "application/json".into(),
                    kind: "release-metadata".into(),
                    track_name: None,
                    language: None,
                    default: false,
                    content_encoding: ContentEncoding::Identity,
                },
                AssetRequest {
                    logical_name: "artwork/thumbnail.webp".into(),
                    path: thumbnail,
                    media_type: "image/webp".into(),
                    kind: "thumbnail".into(),
                    track_name: None,
                    language: None,
                    default: false,
                    content_encoding: ContentEncoding::Identity,
                },
            ],
        },
    )
    .map_err(|error| error.to_string())?;
    let package = scan_files(vec![path])?
        .into_iter()
        .next()
        .ok_or_else(|| "Revised package disappeared".to_string())?;
    app.state::<CatalogState>()
        .0
        .lock()
        .map_err(|_| "Catalog lock is poisoned".to_string())?
        .complete_processing(&item_id, package)?;
    save_and_emit(&app)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{revise_render_plan, semantic_lines, update_text_identical_timing};
    use serde_json::json;

    #[test]
    fn exact_text_is_preserved_and_acoustic_changes_require_alignment() {
        let mut timing = json!({
            "lines": [{
                "text": "Xin chao",
                "syllables": [
                    {"text": "Xin", "start": 1.0, "end": 1.2},
                    {"text": "chao", "start": 1.2, "end": 1.5}
                ]
            }]
        });
        assert!(update_text_identical_timing(&mut timing, &["Xin chào".into()], "hash").unwrap());

        let mut identical = timing.clone();
        assert!(
            !update_text_identical_timing(&mut identical, &["  Xin chao  ".into()], "hash")
                .unwrap()
        );
        assert_eq!(identical["lines"][0]["text"], "  Xin chao  ");
        assert!(
            update_text_identical_timing(&mut timing, &["Xin chào bạn".into()], "hash").unwrap()
        );

        let mut plan = json!({"events": [{"lineIndex": 1, "line": {}}]});
        revise_render_plan(&mut plan, &identical).unwrap();
        assert_eq!(plan["events"][0]["line"]["text"], "  Xin chao  ");
        assert_eq!(
            semantic_lines("\n Xin chào \n").unwrap(),
            vec![" Xin chào "]
        );
    }
}
