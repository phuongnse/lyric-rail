use std::{
    collections::{HashMap, VecDeque},
    ffi::OsString,
    fs,
    path::{Path, PathBuf},
    sync::Mutex,
};

use lrail_format::{PackageReader, load_vault_master};
use semver::Version;
use serde::Serialize;
use serde_json::Value;
use tauri::{
    Emitter, Manager,
    http::{Method, Request, Response, StatusCode, header},
};

const MAX_PROTOCOL_RANGE: u64 = 2 * 1024 * 1024;
const MAX_JSON_ASSET: u64 = 16 * 1024 * 1024;
const MAX_LIBRARY_DEPTH: usize = 4;
const MAX_LIBRARY_PACKAGES: usize = 1_000;
const MAX_LIBRARY_SCAN_ENTRIES: usize = 20_000;

#[derive(Clone)]
struct PlaybackAsset {
    logical_name: String,
    length: u64,
    media_type: String,
}

struct LoadedPackage {
    reader: PackageReader,
    routes: HashMap<&'static str, PlaybackAsset>,
}

#[derive(Default)]
struct PlayerState {
    loaded: Mutex<Option<LoadedPackage>>,
}

#[derive(Default)]
struct StartupPackage(Mutex<Option<PathBuf>>);

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct OpenPackageResult {
    package_id: String,
    minimum_player_version: String,
    metadata: Value,
    assets: Value,
    lyrics: Value,
    render_plan: Value,
    media: PlaybackMediaResult,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PlaybackMediaResult {
    video_url: String,
    audio_tracks: Vec<AudioTrackResult>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct AudioTrackResult {
    id: &'static str,
    name: &'static str,
    url: String,
    default: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PlayerStatus {
    version: &'static str,
    package_open: bool,
    vault_available: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct LibraryPackage {
    path: PathBuf,
    package_id: Option<String>,
    title: String,
    reference_artist: Option<String>,
    valid: bool,
    error: Option<String>,
}

fn error_response(status: StatusCode, message: &str) -> Response<Vec<u8>> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "text/plain; charset=utf-8")
        .header(header::CACHE_CONTROL, "no-store")
        .body(message.as_bytes().to_vec())
        .expect("static protocol error response")
}

fn parse_single_range(value: &str, length: u64) -> Result<(u64, u64), ()> {
    let spec = value.strip_prefix("bytes=").ok_or(())?;
    if spec.contains(',') || length == 0 {
        return Err(());
    }
    let (start, end) = spec.split_once('-').ok_or(())?;
    let (start, mut end) = if start.is_empty() {
        let suffix = end.parse::<u64>().map_err(|_| ())?.min(length);
        if suffix == 0 {
            return Err(());
        }
        (length - suffix, length - 1)
    } else {
        let start = start.parse::<u64>().map_err(|_| ())?;
        let end = if end.is_empty() {
            length - 1
        } else {
            end.parse::<u64>().map_err(|_| ())?
        };
        (start, end)
    };
    if start >= length || end < start || end >= length {
        return Err(());
    }
    end = end.min(start.saturating_add(MAX_PROTOCOL_RANGE - 1));
    Ok((start, end))
}

fn authenticated_asset_range(
    reader: &mut PackageReader,
    asset: &PlaybackAsset,
    start: u64,
    end: u64,
) -> Result<Vec<u8>, ()> {
    let count = end
        .checked_sub(start)
        .and_then(|length| length.checked_add(1))
        .and_then(|length| usize::try_from(length).ok())
        .ok_or(())?;
    reader
        .read_asset_range(&asset.logical_name, start, count)
        .map(|bytes| bytes.to_vec())
        .map_err(|_| ())
}

fn media_protocol(
    context: tauri::UriSchemeContext<'_, tauri::Wry>,
    request: Request<Vec<u8>>,
) -> Response<Vec<u8>> {
    if context.webview_label() != "main" {
        return error_response(StatusCode::NOT_FOUND, "Not found");
    }
    let state = context.app_handle().state::<PlayerState>();
    let mut guard = match state.loaded.lock() {
        Ok(guard) => guard,
        Err(_) => return error_response(StatusCode::INTERNAL_SERVER_ERROR, "Player unavailable"),
    };
    let Some(loaded) = guard.as_mut() else {
        return error_response(StatusCode::NOT_FOUND, "No package is open");
    };
    let Some(asset) = loaded.routes.get(request.uri().path()).cloned() else {
        return error_response(StatusCode::NOT_FOUND, "Not found");
    };
    if request.method() == Method::HEAD {
        return Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, asset.media_type.as_str())
            .header(header::CONTENT_LENGTH, asset.length)
            .header(header::ACCEPT_RANGES, "bytes")
            .header(header::CACHE_CONTROL, "no-store")
            .header(header::ACCESS_CONTROL_ALLOW_ORIGIN, "*")
            .body(Vec::new())
            .expect("valid media HEAD response");
    }
    if request.method() != Method::GET {
        return error_response(StatusCode::METHOD_NOT_ALLOWED, "Method not allowed");
    }

    let range_header = request
        .headers()
        .get(header::RANGE)
        .and_then(|value| value.to_str().ok());
    let (start, end) = match range_header {
        Some(value) => match parse_single_range(value, asset.length) {
            Ok(range) => range,
            Err(()) => {
                return Response::builder()
                    .status(StatusCode::RANGE_NOT_SATISFIABLE)
                    .header(header::CONTENT_RANGE, format!("bytes */{}", asset.length))
                    .body(Vec::new())
                    .expect("valid range error response");
            }
        },
        None => (0, (asset.length - 1).min(MAX_PROTOCOL_RANGE - 1)),
    };
    let bytes = match authenticated_asset_range(&mut loaded.reader, &asset, start, end) {
        Ok(bytes) => bytes,
        Err(_) => {
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Media authentication failed",
            );
        }
    };
    let partial = start != 0 || end + 1 != asset.length;
    let mut response = Response::builder()
        .status(if partial {
            StatusCode::PARTIAL_CONTENT
        } else {
            StatusCode::OK
        })
        .header(header::CONTENT_TYPE, asset.media_type.as_str())
        .header(header::CONTENT_LENGTH, bytes.len())
        .header(header::ACCEPT_RANGES, "bytes")
        .header(header::CACHE_CONTROL, "no-store")
        .header(header::ACCESS_CONTROL_ALLOW_ORIGIN, "*")
        .header(header::ACCESS_CONTROL_EXPOSE_HEADERS, "content-range");
    if partial {
        response = response.header(
            header::CONTENT_RANGE,
            format!("bytes {start}-{end}/{}", asset.length),
        );
    }
    response.body(bytes).expect("valid media response")
}

fn json_asset(reader: &mut PackageReader, logical_name: &str) -> Result<Value, String> {
    let asset = reader
        .manifest
        .assets
        .iter()
        .find(|asset| asset.logical_name == logical_name)
        .ok_or_else(|| format!("Package is missing {logical_name}"))?;
    if asset.plaintext_length > MAX_JSON_ASSET {
        return Err(format!("{logical_name} exceeds the JSON asset limit"));
    }
    let bytes = reader
        .read_asset(logical_name)
        .map_err(|error| error.to_string())?;
    serde_json::from_slice(&bytes).map_err(|error| format!("Invalid {logical_name}: {error}"))
}

fn media_url(path: &str) -> String {
    if cfg!(windows) {
        format!("http://lrailmedia.localhost{path}")
    } else {
        format!("lrailmedia://localhost{path}")
    }
}

fn is_supported_original_reference(
    logical_name: &str,
    media_type: &str,
    kind: &str,
    is_default: bool,
) -> bool {
    kind == "playback-audio"
        && !is_default
        && matches!(
            (logical_name, media_type),
            ("audio/original-reference.m4a", "audio/mp4")
                | ("audio/original-reference.mp3", "audio/mpeg")
        )
}

fn package_argument<T>(arguments: impl IntoIterator<Item = T>) -> Option<PathBuf>
where
    T: Into<OsString>,
{
    arguments.into_iter().skip(1).find_map(|argument| {
        let path = PathBuf::from(argument.into());
        if !path
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case("lrail"))
        {
            return None;
        }
        path.canonicalize().ok().filter(|path| path.is_file())
    })
}

fn is_lrail_file(path: &Path) -> bool {
    path.extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("lrail"))
}

fn library_package_paths(root: &Path) -> Result<Vec<PathBuf>, String> {
    let mut directories = VecDeque::from([(root.to_path_buf(), 0usize)]);
    let mut packages = Vec::new();
    let mut entries_seen = 0usize;

    while let Some((directory, depth)) = directories.pop_front() {
        let entries = fs::read_dir(&directory)
            .map_err(|error| format!("Unable to scan {}: {error}", directory.display()))?;
        for entry in entries {
            let entry = entry.map_err(|error| {
                format!(
                    "Unable to read an entry in {}: {error}",
                    directory.display()
                )
            })?;
            entries_seen += 1;
            if entries_seen > MAX_LIBRARY_SCAN_ENTRIES {
                return Err(format!(
                    "Library scan stopped after {MAX_LIBRARY_SCAN_ENTRIES} filesystem entries"
                ));
            }

            let file_type = entry.file_type().map_err(|error| {
                format!("Unable to inspect {}: {error}", entry.path().display())
            })?;
            if file_type.is_symlink() {
                continue;
            }
            if file_type.is_dir() && depth < MAX_LIBRARY_DEPTH {
                directories.push_back((entry.path(), depth + 1));
            } else if file_type.is_file() && is_lrail_file(&entry.path()) {
                packages.push(entry.path());
                if packages.len() >= MAX_LIBRARY_PACKAGES {
                    packages.sort();
                    return Ok(packages);
                }
            }
        }
    }
    packages.sort();
    Ok(packages)
}

fn metadata_string(metadata: &Value, key: &str) -> Option<String> {
    metadata
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
}

#[tauri::command]
fn player_status(state: tauri::State<'_, PlayerState>) -> PlayerStatus {
    PlayerStatus {
        version: env!("CARGO_PKG_VERSION"),
        package_open: state.loaded.lock().is_ok_and(|loaded| loaded.is_some()),
        vault_available: load_vault_master().is_ok(),
    }
}

#[tauri::command]
fn take_startup_package(
    state: tauri::State<'_, StartupPackage>,
) -> Result<Option<PathBuf>, String> {
    state
        .0
        .lock()
        .map_err(|_| "Startup package state lock is poisoned".to_string())
        .map(|mut path| path.take())
}

#[tauri::command]
fn scan_library(root: PathBuf) -> Result<Vec<LibraryPackage>, String> {
    let root = root
        .canonicalize()
        .map_err(|error| format!("Unable to open {}: {error}", root.display()))?;
    if !root.is_dir() {
        return Err("Select a library directory".into());
    }
    let vault_master = load_vault_master().map_err(|error| error.to_string())?;
    let paths = library_package_paths(&root)?;
    Ok(paths
        .into_iter()
        .map(
            |path| match PackageReader::open_with_vault(&path, &vault_master) {
                Ok(reader) => LibraryPackage {
                    path,
                    package_id: Some(reader.manifest.package_id.to_string()),
                    title: metadata_string(&reader.manifest.metadata, "title")
                        .unwrap_or_else(|| "Untitled karaoke".into()),
                    reference_artist: metadata_string(&reader.manifest.metadata, "referenceArtist"),
                    valid: true,
                    error: None,
                },
                Err(error) => LibraryPackage {
                    title: path
                        .file_stem()
                        .and_then(|value| value.to_str())
                        .unwrap_or("Unreadable package")
                        .to_owned(),
                    path,
                    package_id: None,
                    reference_artist: None,
                    valid: false,
                    error: Some(error.to_string()),
                },
            },
        )
        .collect())
}

#[tauri::command]
fn open_package(
    state: tauri::State<'_, PlayerState>,
    path: PathBuf,
) -> Result<OpenPackageResult, String> {
    let path = path
        .canonicalize()
        .map_err(|error| format!("Unable to open {}: {error}", path.display()))?;
    if !path
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("lrail"))
        || !path.is_file()
    {
        return Err("Select a regular .lrail package".into());
    }
    let vault_master = load_vault_master().map_err(|error| error.to_string())?;
    let mut reader =
        PackageReader::open_with_vault(&path, &vault_master).map_err(|error| error.to_string())?;
    let current = Version::parse(env!("CARGO_PKG_VERSION")).map_err(|error| error.to_string())?;
    let minimum = Version::parse(&reader.manifest.minimum_player_version)
        .map_err(|_| "Package declares an invalid minimum Player version".to_string())?;
    if minimum > current {
        return Err(format!(
            "This package requires LyricRail Player {minimum} or newer"
        ));
    }

    let fixed_assets = [
        ("/video", "media/video.mp4", "playback-video", "video/mp4"),
        (
            "/audio/karaoke",
            "audio/karaoke.m4a",
            "playback-audio",
            "audio/mp4",
        ),
    ];
    let mut routes = HashMap::new();
    for (route, logical_name, kind, media_type) in fixed_assets {
        let asset = reader
            .manifest
            .assets
            .iter()
            .find(|asset| {
                asset.logical_name == logical_name
                    && asset.kind == kind
                    && asset.media_type == media_type
            })
            .ok_or_else(|| format!("Package is missing required asset {logical_name}"))?;
        if asset.plaintext_length == 0 {
            return Err(format!("Package asset {logical_name} is empty"));
        }
        routes.insert(
            route,
            PlaybackAsset {
                logical_name: asset.logical_name.clone(),
                length: asset.plaintext_length,
                media_type: media_type.to_owned(),
            },
        );
    }
    let mut original_assets = reader.manifest.assets.iter().filter(|asset| {
        is_supported_original_reference(
            &asset.logical_name,
            &asset.media_type,
            &asset.kind,
            asset.default,
        )
    });
    let original = original_assets
        .next()
        .ok_or_else(|| "Package is missing a portable Original Reference asset".to_string())?;
    if original_assets.next().is_some() {
        return Err("Package contains ambiguous Original Reference assets".into());
    }
    if original.plaintext_length == 0 {
        return Err(format!("Package asset {} is empty", original.logical_name));
    }
    routes.insert(
        "/audio/original-reference",
        PlaybackAsset {
            logical_name: original.logical_name.clone(),
            length: original.plaintext_length,
            media_type: original.media_type.clone(),
        },
    );
    let assets =
        serde_json::to_value(&reader.manifest.assets).map_err(|error| error.to_string())?;
    let lyrics = json_asset(&mut reader, "lyrics/timing.json")?;
    let render_plan = json_asset(&mut reader, "lyrics/render-plan.json")?;
    let result = OpenPackageResult {
        package_id: reader.manifest.package_id.to_string(),
        minimum_player_version: reader.manifest.minimum_player_version.clone(),
        metadata: reader.manifest.metadata.clone(),
        assets,
        lyrics,
        render_plan,
        media: PlaybackMediaResult {
            video_url: media_url("/video"),
            audio_tracks: vec![
                AudioTrackResult {
                    id: "karaoke",
                    name: "Karaoke",
                    url: media_url("/audio/karaoke"),
                    default: true,
                },
                AudioTrackResult {
                    id: "original-reference",
                    name: "Original Reference",
                    url: media_url("/audio/original-reference"),
                    default: false,
                },
            ],
        },
    };
    *state
        .loaded
        .lock()
        .map_err(|_| "Player state lock is poisoned".to_string())? =
        Some(LoadedPackage { reader, routes });
    Ok(result)
}

#[tauri::command]
fn close_package(state: tauri::State<'_, PlayerState>) -> Result<(), String> {
    *state
        .loaded
        .lock()
        .map_err(|_| "Player state lock is poisoned".to_string())? = None;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let startup_package = package_argument(std::env::args_os());
    let mut builder = tauri::Builder::default();
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(
            |app, arguments, _working_directory| {
                if let Some(path) = package_argument(arguments) {
                    let _ = app.emit("player-open-package", path);
                }
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            },
        ));
    }
    builder
        .manage(PlayerState::default())
        .manage(StartupPackage(Mutex::new(startup_package)))
        .plugin(tauri_plugin_dialog::init())
        .register_uri_scheme_protocol("lrailmedia", media_protocol)
        .invoke_handler(tauri::generate_handler![
            player_status,
            take_startup_package,
            scan_library,
            open_package,
            close_package,
        ])
        .run(tauri::generate_context!())
        .expect("error while running LyricRail Player");
}

#[cfg(test)]
mod tests {
    use std::{
        collections::HashMap,
        fs::{self, File, OpenOptions},
        io::{Read, Seek, SeekFrom, Write},
    };

    use lrail_format::{
        AssetRequest, ContentEncoding, PackageReader, PackageRequest, pack_for_vault,
    };
    use serde_json::json;

    use super::{
        LoadedPackage, PlaybackAsset, authenticated_asset_range, is_supported_original_reference,
        library_package_paths, parse_single_range,
    };

    fn loaded_fixture() -> (tempfile::TempDir, std::path::PathBuf, LoadedPackage, u64) {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("media.bin");
        fs::write(&source, vec![0x6a; 32 * 1024]).unwrap();
        let output = directory.path().join("fixture.lrail");
        let vault_master = [0x42; 32];
        let request = PackageRequest {
            metadata: json!({"title": "Player mutation fixture"}),
            producer: "LyricRail Player tests".into(),
            minimum_player_version: "0.8.0".into(),
            assets: vec![AssetRequest {
                logical_name: "media/main.bin".into(),
                path: source,
                media_type: "application/octet-stream".into(),
                kind: "test".into(),
                track_name: None,
                language: None,
                default: true,
                content_encoding: ContentEncoding::Identity,
            }],
        };
        pack_for_vault(&request, &output, &vault_master, None).unwrap();
        let reader = PackageReader::open_with_vault(&output, &vault_master).unwrap();
        let manifest_asset = reader.manifest.assets[0].clone();
        let first_ciphertext_offset = manifest_asset.chunks[0].file_offset;
        let playback_asset = PlaybackAsset {
            logical_name: manifest_asset.logical_name,
            length: manifest_asset.plaintext_length,
            media_type: manifest_asset.media_type,
        };
        let routes = HashMap::from([("/fixture", playback_asset)]);
        (
            directory,
            output,
            LoadedPackage { reader, routes },
            first_ciphertext_offset,
        )
    }

    #[test]
    fn range_parser_bounds_and_limits_requests() {
        assert_eq!(parse_single_range("bytes=10-19", 100), Ok((10, 19)));
        assert_eq!(parse_single_range("bytes=-10", 100), Ok((90, 99)));
        assert!(parse_single_range("bytes=100-101", 100).is_err());
        let (_, end) = parse_single_range("bytes=0-9999999", 10_000_000).unwrap();
        assert_eq!(end, super::MAX_PROTOCOL_RANGE - 1);
    }

    #[test]
    fn library_scan_is_bounded_and_filters_extensions() {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("nested")).unwrap();
        File::create(root.path().join("b.LRAIL")).unwrap();
        File::create(root.path().join("nested").join("a.lrail")).unwrap();
        File::create(root.path().join("ignore.mp4")).unwrap();

        let packages = library_package_paths(root.path()).unwrap();
        assert_eq!(packages.len(), 2);
        assert!(packages[0].ends_with("b.LRAIL") || packages[0].ends_with("a.lrail"));
        assert!(packages.iter().all(|path| super::is_lrail_file(path)));
    }

    #[test]
    fn original_reference_accepts_only_exact_portable_name_mime_pairs() {
        assert!(is_supported_original_reference(
            "audio/original-reference.m4a",
            "audio/mp4",
            "playback-audio",
            false,
        ));
        assert!(is_supported_original_reference(
            "audio/original-reference.mp3",
            "audio/mpeg",
            "playback-audio",
            false,
        ));
        assert!(!is_supported_original_reference(
            "audio/original-reference.mp3",
            "audio/mp4",
            "playback-audio",
            false,
        ));
        assert!(!is_supported_original_reference(
            "audio/original-reference.mp3",
            "audio/mpeg",
            "playback-audio",
            true,
        ));
    }

    #[test]
    fn playback_survives_path_unavailability_and_fails_closed_on_corruption() {
        let (_directory, output, mut loaded, _) = loaded_fixture();
        let asset = loaded.routes["/fixture"].clone();
        assert_eq!(
            authenticated_asset_range(&mut loaded.reader, &asset, 0, 31).unwrap(),
            vec![0x6a; 32]
        );

        let moved = output.with_file_name("moved-while-open.lrail");
        fs::rename(&output, &moved).unwrap();
        assert_eq!(
            authenticated_asset_range(&mut loaded.reader, &asset, 32, 63).unwrap(),
            vec![0x6a; 32]
        );

        let (_directory, output, mut loaded, ciphertext_offset) = loaded_fixture();
        let asset = loaded.routes["/fixture"].clone();
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(output)
            .unwrap();
        file.seek(SeekFrom::Start(ciphertext_offset)).unwrap();
        let mut byte = [0_u8; 1];
        file.read_exact(&mut byte).unwrap();
        byte[0] ^= 0x80;
        file.seek(SeekFrom::Start(ciphertext_offset)).unwrap();
        file.write_all(&byte).unwrap();
        file.sync_all().unwrap();
        assert!(authenticated_asset_range(&mut loaded.reader, &asset, 0, 31).is_err());
    }
}
