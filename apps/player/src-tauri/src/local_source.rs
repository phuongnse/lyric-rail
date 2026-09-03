use std::{
    collections::VecDeque,
    fs,
    path::{Path, PathBuf},
};

use lrail_format::{PackageReader, load_vault_master};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::catalog::{CatalogItem, ItemLocation, ItemStatus, MediaOrigin};

const MAX_SCAN_DEPTH: usize = 8;
const MAX_SCAN_ENTRIES: usize = 100_000;
const MAX_SCAN_ITEMS: usize = 20_000;
const MAX_LYRIC_BYTES: u64 = 1_000_000;
const MAX_INDEXED_LYRIC_BYTES: u64 = 512 * 1024;

const MEDIA_EXTENSIONS: [&str; 13] = [
    "aac", "flac", "m4a", "mkv", "mov", "mp3", "mp4", "ogg", "opus", "wav", "webm", "wma", "avi",
];

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScanResult {
    pub root: PathBuf,
    pub entries_seen: usize,
    pub items: Vec<CatalogItem>,
    pub truncated: bool,
}

fn extension(path: &Path) -> Option<String> {
    path.extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
}

pub fn is_lrail(path: &Path) -> bool {
    extension(path).as_deref() == Some("lrail")
}

pub fn is_media(path: &Path) -> bool {
    extension(path)
        .as_deref()
        .is_some_and(|value| MEDIA_EXTENSIONS.contains(&value))
}

fn path_item_id(path: &Path) -> String {
    let mut digest = Sha256::new();
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        digest.update(path.as_os_str().as_bytes());
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        for value in path.as_os_str().encode_wide() {
            digest.update(value.to_le_bytes());
        }
    }
    format!("local-{}", hex::encode(digest.finalize()))
}

fn metadata_string(metadata: &Value, key: &str) -> Option<String> {
    metadata
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn lyric_text(payload: &Value) -> String {
    payload
        .get("lines")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|line| {
            line.as_str()
                .or_else(|| line.get("text").and_then(Value::as_str))
        })
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

fn package_item(path: PathBuf) -> CatalogItem {
    let fallback_title = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("Unreadable package")
        .to_owned();
    let vault = match load_vault_master() {
        Ok(vault) => vault,
        Err(error) => {
            return CatalogItem {
                id: path_item_id(&path),
                package_id: None,
                title: fallback_title,
                artist: None,
                composer: None,
                first_lyric_line: None,
                lyric_text: String::new(),
                status: ItemStatus::Failed,
                progress_percent: 0.0,
                status_message: Some(error.to_string()),
                processing_job_id: None,
                processing_task_evidence: None,
                has_thumbnail: false,
                locations: vec![ItemLocation::LocalPackage {
                    source_id: None,
                    path,
                    available: false,
                }],
            };
        }
    };
    match PackageReader::open_with_vault(&path, &vault) {
        Ok(mut reader) => {
            let package_id = reader.manifest.package_id.to_string();
            let title =
                metadata_string(&reader.manifest.metadata, "title").unwrap_or(fallback_title);
            let artist = metadata_string(&reader.manifest.metadata, "referenceArtist")
                .or_else(|| metadata_string(&reader.manifest.metadata, "artist"));
            let composer = metadata_string(&reader.manifest.metadata, "composer");
            let has_thumbnail = reader
                .manifest
                .assets
                .iter()
                .any(|asset| asset.logical_name == "artwork/thumbnail.webp");
            let authoritative_is_bounded = reader
                .manifest
                .assets
                .iter()
                .find(|asset| asset.logical_name == "lyrics/authoritative.txt")
                .is_some_and(|asset| asset.plaintext_length <= MAX_LYRIC_BYTES);
            let lyrics_is_bounded = reader
                .manifest
                .assets
                .iter()
                .find(|asset| asset.logical_name == "lyrics/timing.json")
                .is_some_and(|asset| asset.plaintext_length <= MAX_INDEXED_LYRIC_BYTES);
            let lyrics = if authoritative_is_bounded {
                reader
                    .read_asset("lyrics/authoritative.txt")
                    .ok()
                    .and_then(|bytes| String::from_utf8(bytes.to_vec()).ok())
                    .unwrap_or_default()
            } else {
                lyrics_is_bounded
                    .then(|| reader.read_asset("lyrics/timing.json").ok())
                    .flatten()
                    .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
                    .map(|payload| lyric_text(&payload))
                    .unwrap_or_default()
            };
            CatalogItem {
                id: package_id.clone(),
                package_id: Some(package_id),
                title,
                artist,
                composer,
                first_lyric_line: lyrics
                    .lines()
                    .find(|line| !line.trim().is_empty())
                    .map(str::to_owned),
                lyric_text: lyrics,
                status: ItemStatus::Ready,
                progress_percent: 100.0,
                status_message: None,
                processing_job_id: None,
                processing_task_evidence: None,
                has_thumbnail,
                locations: vec![ItemLocation::LocalPackage {
                    source_id: None,
                    path,
                    available: true,
                }],
            }
        }
        Err(error) => CatalogItem {
            id: path_item_id(&path),
            package_id: None,
            title: fallback_title,
            artist: None,
            composer: None,
            first_lyric_line: None,
            lyric_text: String::new(),
            status: ItemStatus::Failed,
            progress_percent: 0.0,
            status_message: Some(error.to_string()),
            processing_job_id: None,
            processing_task_evidence: None,
            has_thumbnail: false,
            locations: vec![ItemLocation::LocalPackage {
                source_id: None,
                path,
                available: false,
            }],
        },
    }
}

fn exact_sidecar(path: &Path) -> Result<Option<PathBuf>, String> {
    let stem = path
        .file_stem()
        .ok_or_else(|| "Media file has no stem".to_string())?;
    let parent = path
        .parent()
        .ok_or_else(|| "Media file has no parent directory".to_string())?;
    let mut matches = fs::read_dir(parent)
        .map_err(|error| format!("Unable to inspect lyric sidecars: {error}"))?
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let candidate = entry.path();
            let file_type = entry.file_type().ok()?;
            if !file_type.is_file()
                || !candidate
                    .extension()
                    .and_then(|value| value.to_str())
                    .is_some_and(|value| value.eq_ignore_ascii_case("txt"))
                || candidate.file_stem()? != stem
            {
                return None;
            }
            Some(candidate)
        })
        .collect::<Vec<_>>();
    matches.sort();
    matches.dedup();
    match matches.len() {
        0 => Ok(None),
        1 => Ok(matches.pop()),
        count => Err(format!(
            "Found {count} exact-stem lyric files; choose one explicitly"
        )),
    }
}

pub fn read_authoritative_lyrics(path: &Path) -> Result<String, String> {
    let metadata = fs::metadata(path)
        .map_err(|error| format!("Unable to inspect lyrics {}: {error}", path.display()))?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > MAX_LYRIC_BYTES {
        return Err("Lyrics must be a non-empty UTF-8 file no larger than 1,000,000 bytes".into());
    }
    let text =
        fs::read_to_string(path).map_err(|error| format!("Lyrics must be valid UTF-8: {error}"))?;
    if text.lines().all(|line| line.trim().is_empty()) {
        return Err("Lyrics contain no non-empty lines".into());
    }
    Ok(text)
}

fn media_item(path: PathBuf) -> CatalogItem {
    let (lyrics_path, sidecar_error) = match exact_sidecar(&path) {
        Ok(path) => (path, None),
        Err(error) => (None, Some(error)),
    };
    let (lyric_text, status, message) = match lyrics_path.as_deref() {
        Some(lyrics) => match read_authoritative_lyrics(lyrics) {
            Ok(text) => (text, ItemStatus::Queued, None),
            Err(error) => (String::new(), ItemStatus::WaitingForLyrics, Some(error)),
        },
        None => (String::new(), ItemStatus::WaitingForLyrics, sidecar_error),
    };
    let title = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("Untitled media")
        .to_owned();
    CatalogItem {
        id: path_item_id(&path),
        package_id: None,
        title,
        artist: None,
        composer: None,
        first_lyric_line: lyric_text
            .lines()
            .find(|line| !line.trim().is_empty())
            .map(str::to_owned),
        lyric_text,
        status,
        progress_percent: 0.0,
        status_message: message,
        processing_job_id: None,
        processing_task_evidence: None,
        has_thumbnail: false,
        locations: vec![ItemLocation::LocalMedia {
            source_id: None,
            path,
            lyrics_path,
            origin: MediaOrigin::Disk,
            trim_start_millis: None,
            trim_end_millis: None,
            available: true,
        }],
    }
}

#[cfg(test)]
fn clipped_local_media_item(
    path: PathBuf,
    title: String,
    trim_start_millis: u64,
    trim_end_millis: u64,
) -> Result<CatalogItem, String> {
    let original = fs::symlink_metadata(&path)
        .map_err(|error| format!("Unable to inspect selected media: {error}"))?;
    if original.file_type().is_symlink() || !original.is_file() {
        return Err("Selected clip source is not a supported regular local file".into());
    }
    let path = path
        .canonicalize()
        .map_err(|error| format!("Unable to open selected media: {error}"))?;
    let metadata = fs::symlink_metadata(&path)
        .map_err(|error| format!("Unable to inspect selected media: {error}"))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() || !is_media(&path) {
        return Err("Selected clip source is not a supported regular local file".into());
    }
    clipped_local_media_item_from_verified_path(path, title, trim_start_millis, trim_end_millis)
}

pub fn clipped_local_media_item_from_verified_path(
    path: PathBuf,
    title: String,
    trim_start_millis: u64,
    trim_end_millis: u64,
) -> Result<CatalogItem, String> {
    if !path.is_absolute() || !is_media(&path) {
        return Err("Verified clip source path is invalid".into());
    }
    if trim_end_millis <= trim_start_millis {
        return Err("Clip End must be later than Start".into());
    }
    let mut item = media_item(path);
    item.title = title;
    if let Some(ItemLocation::LocalMedia {
        source_id,
        origin,
        trim_start_millis: start,
        trim_end_millis: end,
        ..
    }) = item.locations.first_mut()
    {
        *source_id = None;
        *origin = MediaOrigin::Disk;
        *start = Some(trim_start_millis);
        *end = Some(trim_end_millis);
    }
    Ok(item)
}

pub fn scan_files(paths: Vec<PathBuf>) -> Result<Vec<CatalogItem>, String> {
    if paths.len() > MAX_SCAN_ITEMS {
        return Err(format!("Select at most {MAX_SCAN_ITEMS} files at once"));
    }
    let mut items = Vec::new();
    for path in paths {
        let path = path
            .canonicalize()
            .map_err(|error| format!("Unable to open {}: {error}", path.display()))?;
        let metadata = fs::symlink_metadata(&path)
            .map_err(|error| format!("Unable to inspect {}: {error}", path.display()))?;
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            continue;
        }
        if is_lrail(&path) {
            items.push(package_item(path));
        } else if is_media(&path) {
            items.push(media_item(path));
        }
    }
    Ok(items)
}

pub fn scan_root(root: &Path) -> Result<ScanResult, String> {
    let root = root
        .canonicalize()
        .map_err(|error| format!("Unable to open {}: {error}", root.display()))?;
    if !root.is_dir() {
        return Err("Select a local folder".into());
    }
    let mut directories = VecDeque::from([(root.clone(), 0_usize)]);
    let mut candidates = Vec::new();
    let mut entries_seen = 0_usize;
    let mut truncated = false;
    while let Some((directory, depth)) = directories.pop_front() {
        let mut entries = fs::read_dir(&directory)
            .map_err(|error| format!("Unable to scan {}: {error}", directory.display()))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| {
                format!(
                    "Unable to read an entry in {}: {error}",
                    directory.display()
                )
            })?;
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            entries_seen += 1;
            if entries_seen > MAX_SCAN_ENTRIES || candidates.len() >= MAX_SCAN_ITEMS {
                truncated = true;
                break;
            }
            let file_type = entry.file_type().map_err(|error| {
                format!("Unable to inspect {}: {error}", entry.path().display())
            })?;
            if file_type.is_symlink() {
                continue;
            }
            if file_type.is_dir() && depth < MAX_SCAN_DEPTH {
                directories.push_back((entry.path(), depth + 1));
            } else if file_type.is_file() && (is_lrail(&entry.path()) || is_media(&entry.path())) {
                candidates.push(entry.path());
            }
        }
        if truncated {
            break;
        }
    }
    candidates.sort();
    let items = scan_files(candidates)?;
    Ok(ScanResult {
        root,
        entries_seen,
        items,
        truncated,
    })
}

#[cfg(test)]
mod tests {
    use super::{
        clipped_local_media_item, exact_sidecar, is_media, read_authoritative_lyrics, scan_root,
    };
    use std::fs;

    #[test]
    fn exact_utf8_sidecars_pair_without_fuzzy_guessing() {
        let directory = tempfile::tempdir().unwrap();
        let media = directory.path().join("Bài hát.mp4");
        let lyrics = directory.path().join("Bài hát.txt");
        fs::write(&media, b"media").unwrap();
        fs::write(&lyrics, "Câu hát đầu tiên\nCâu thứ hai\n").unwrap();
        fs::write(directory.path().join("Bai hat.txt"), "wrong candidate").unwrap();
        assert_eq!(exact_sidecar(&media).unwrap().unwrap(), lyrics);
        assert_eq!(
            read_authoritative_lyrics(&lyrics).unwrap(),
            "Câu hát đầu tiên\nCâu thứ hai\n"
        );
        assert!(is_media(&media));
    }

    #[test]
    fn folder_scan_skips_symlinks_and_exposes_missing_lyrics_inline() {
        let directory = tempfile::tempdir().unwrap();
        fs::write(directory.path().join("song.mp3"), b"media").unwrap();
        fs::write(directory.path().join("ignored.bin"), b"other").unwrap();
        let result = scan_root(directory.path()).unwrap();
        assert_eq!(result.items.len(), 1);
        assert_eq!(
            result.items[0].status,
            crate::catalog::ItemStatus::WaitingForLyrics
        );
    }

    #[test]
    fn selected_local_media_keeps_sidecar_and_trim_metadata() {
        let directory = tempfile::tempdir().unwrap();
        let media = directory.path().join("selected-source.mp4");
        let lyrics = directory.path().join("selected-source.txt");
        fs::write(&media, b"media").unwrap();
        fs::write(&lyrics, "Exact lyric\n").unwrap();
        let item = clipped_local_media_item(media, "Local Song".into(), 12_345, 67_890).unwrap();
        assert_eq!(item.status, crate::catalog::ItemStatus::Queued);
        assert_eq!(item.lyric_text, "Exact lyric\n");
        assert_eq!(item.source_labels(), vec!["Disk"]);
        assert!(matches!(
            &item.locations[0],
            crate::catalog::ItemLocation::LocalMedia {
                origin: crate::catalog::MediaOrigin::Disk,
                trim_start_millis: Some(12_345),
                trim_end_millis: Some(67_890),
                ..
            }
        ));
    }
}
