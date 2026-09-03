mod catalog;
#[cfg(target_os = "macos")]
mod desktop_menu;
mod google_drive;
mod issues;
mod local_clip;
mod local_source;
mod lyric_revision;
mod model_installer;
mod processing;
mod range_cache;
mod recovery_ui;
mod runtime;
mod scheduler;
mod tasks;

use std::{
    collections::{HashMap, HashSet},
    ffi::OsString,
    fs,
    io::Write,
    path::PathBuf,
    sync::{Arc, Mutex},
};

use base64::{Engine, engine::general_purpose::STANDARD};
use catalog::{
    Catalog, CatalogItem, CatalogSnapshot, DriveRoot, ItemLocation, ItemStatus, SearchResult,
};
use google_drive::{
    DriveFile, GoogleConfig, GoogleDriveTransport, GoogleTokenProvider, authorize_and_pick,
    expand_drive_root, resolve_selection_roots,
};
use local_source::{read_authoritative_lyrics, scan_files, scan_root};
use lrail_format::{LockedSecret, PackageReader, load_vault_master};
use processing::{ProcessingState, enqueue_item};
use range_cache::{CachedRandomAccessSource, RangeCache, RangeTransport, RemoteObject};
use scheduler::{IoPriority, PriorityScheduler};
use semver::Version;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tauri::{
    Emitter, Manager,
    http::{Method, Request, Response, StatusCode, header},
};
use uuid::Uuid;

const MAX_PROTOCOL_RANGE: u64 = 2 * 1024 * 1024;
const MAX_JSON_ASSET: u64 = 16 * 1024 * 1024;
const MAX_INDEXED_LYRIC_BYTES: u64 = 512 * 1024;
const MAX_THUMBNAIL_BYTES: u64 = 1024 * 1024;
const MAX_PASTED_LYRIC_BYTES: usize = 1_000_000;

pub struct CatalogState(pub Mutex<Catalog>);

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

type RemoteDownload = (Arc<RangeCache>, RemoteObject);
type ItemReader = (PackageReader, Option<RemoteDownload>);

#[derive(Default)]
struct PlayerState {
    loaded: Mutex<Option<LoadedPackage>>,
}

struct CloudState {
    tokens: Mutex<Option<Arc<GoogleTokenProvider>>>,
    cache: Mutex<Option<Arc<RangeCache>>>,
    scheduler: Arc<PriorityScheduler>,
}

impl Default for CloudState {
    fn default() -> Self {
        Self {
            tokens: Mutex::new(None),
            cache: Mutex::new(None),
            scheduler: Arc::new(PriorityScheduler::default()),
        }
    }
}

#[derive(Default)]
struct StartupPackage(Mutex<Option<PathBuf>>);

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct OpenPackageResult {
    package_id: String,
    minimum_player_version: String,
    metadata: Value,
    lyrics: Value,
    render_plan: Value,
    presentation: KaraokePresentation,
    media: PlaybackMediaResult,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
struct KaraokePresentation {
    reference_resolution: [u32; 2],
    layout: KaraokeLayout,
    font: KaraokeFont,
    role_change_cue: KaraokeCue,
    unsung: KaraokeUnsung,
    sung: KaraokeSung,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
struct KaraokeLayout {
    line_mode: String,
    alignment: String,
    bottom_margin: f32,
    line_gap: f32,
    safe_area_percent: f32,
    maximum_line_width_percent: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
struct KaraokeFont {
    family: String,
    bold: bool,
    size_at_1080p: f32,
    scale_x: f32,
    scale_y: f32,
    letter_spacing: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
struct KaraokeCue {
    enabled: bool,
    dot_count: u8,
    dot_font_size_at_1080p: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
struct KaraokeUnsung {
    fill: String,
    outer_outline: String,
    outer_outline_width: f32,
    shadow: String,
    shadow_offset: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
struct KaraokeSung {
    direction: String,
    timing: String,
    inner_outline: String,
    inner_outline_width: f32,
    colors: KaraokeRoleColors,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct KaraokeRoleColors {
    male: String,
    female: String,
    duet: String,
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
    platform: &'static str,
    package_open: bool,
    vault_available: bool,
    processing: processing::ProcessingStatus,
}

fn error_response(status: StatusCode, message: &str) -> Response<Vec<u8>> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "text/plain; charset=utf-8")
        .header(header::CACHE_CONTROL, "no-store")
        .body(message.as_bytes().to_vec())
        .expect("static protocol error response")
}

pub(crate) fn parse_single_range(value: &str, length: u64) -> Result<(u64, u64), ()> {
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
        Err(()) => {
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

fn bounded_presentation_number(
    label: &str,
    value: f32,
    minimum: f32,
    maximum: f32,
) -> Result<(), String> {
    if value.is_finite() && (minimum..=maximum).contains(&value) {
        Ok(())
    } else {
        Err(format!(
            "Presentation field {label} must be between {minimum} and {maximum}"
        ))
    }
}

fn normalize_presentation_color(label: &str, value: &mut str) -> Result<(), String> {
    if value.len() != 7
        || !value.starts_with('#')
        || !value[1..].bytes().all(|byte| byte.is_ascii_hexdigit())
    {
        return Err(format!(
            "Presentation field {label} must be a six-digit hex color"
        ));
    }
    value.make_ascii_uppercase();
    Ok(())
}

fn parse_karaoke_presentation(value: Value) -> Result<KaraokePresentation, String> {
    let mut presentation: KaraokePresentation = serde_json::from_value(value)
        .map_err(|error| format!("Invalid presentation/template.json: {error}"))?;
    let [width, height] = presentation.reference_resolution;
    if !(320..=7680).contains(&width) || !(240..=4320).contains(&height) {
        return Err("Presentation reference resolution is outside supported bounds".into());
    }
    if presentation.layout.line_mode != "alternating-two-lines"
        || presentation.layout.alignment != "top-left-bottom-right"
        || presentation.sung.direction != "left-to-right"
        || presentation.sung.timing != "syllable"
    {
        return Err("Presentation uses an unsupported lyric layout or sweep mode".into());
    }
    if presentation.font.family.is_empty()
        || presentation.font.family.chars().count() > 80
        || presentation
            .font
            .family
            .chars()
            .any(|character| !character.is_alphanumeric() && character != ' ' && character != '-')
    {
        return Err("Presentation font family is invalid".into());
    }
    for (label, value, minimum, maximum) in [
        (
            "layout.bottomMargin",
            presentation.layout.bottom_margin,
            0.0,
            300.0,
        ),
        ("layout.lineGap", presentation.layout.line_gap, 0.0, 120.0),
        (
            "layout.safeAreaPercent",
            presentation.layout.safe_area_percent,
            0.0,
            15.0,
        ),
        (
            "layout.maximumLineWidthPercent",
            presentation.layout.maximum_line_width_percent,
            40.0,
            100.0,
        ),
        (
            "font.sizeAt1080p",
            presentation.font.size_at_1080p,
            24.0,
            240.0,
        ),
        ("font.scaleX", presentation.font.scale_x, 50.0, 150.0),
        ("font.scaleY", presentation.font.scale_y, 50.0, 150.0),
        (
            "font.letterSpacing",
            presentation.font.letter_spacing,
            -10.0,
            20.0,
        ),
        (
            "roleChangeCue.dotFontSizeAt1080p",
            presentation.role_change_cue.dot_font_size_at_1080p,
            12.0,
            180.0,
        ),
        (
            "unsung.outerOutlineWidth",
            presentation.unsung.outer_outline_width,
            0.0,
            16.0,
        ),
        (
            "unsung.shadowOffset",
            presentation.unsung.shadow_offset,
            0.0,
            16.0,
        ),
        (
            "sung.innerOutlineWidth",
            presentation.sung.inner_outline_width,
            0.0,
            16.0,
        ),
    ] {
        bounded_presentation_number(label, value, minimum, maximum)?;
    }
    if presentation.role_change_cue.dot_count > 6
        || (presentation.role_change_cue.enabled && presentation.role_change_cue.dot_count == 0)
    {
        return Err("Presentation role cue dot count is outside supported bounds".into());
    }
    for (label, color) in [
        ("unsung.fill", &mut presentation.unsung.fill),
        (
            "unsung.outerOutline",
            &mut presentation.unsung.outer_outline,
        ),
        ("unsung.shadow", &mut presentation.unsung.shadow),
        ("sung.innerOutline", &mut presentation.sung.inner_outline),
        ("sung.colors.male", &mut presentation.sung.colors.male),
        ("sung.colors.female", &mut presentation.sung.colors.female),
        ("sung.colors.duet", &mut presentation.sung.colors.duet),
    ] {
        normalize_presentation_color(label, color)?;
    }
    Ok(presentation)
}

fn validate_presentation_asset_contract(kind: &str, media_type: &str) -> Result<(), String> {
    if kind == "presentation-template" && media_type == "application/json" {
        Ok(())
    } else {
        Err("Package presentation asset has an invalid kind or media type".into())
    }
}

fn presentation_asset(reader: &mut PackageReader) -> Result<KaraokePresentation, String> {
    let asset = reader
        .manifest
        .assets
        .iter()
        .find(|asset| asset.logical_name == "presentation/template.json")
        .ok_or_else(|| "Package is missing presentation/template.json".to_string())?;
    validate_presentation_asset_contract(&asset.kind, &asset.media_type)?;
    parse_karaoke_presentation(json_asset(reader, "presentation/template.json")?)
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

fn catalog_item_from_reader(
    reader: &mut PackageReader,
    location: ItemLocation,
    fallback_title: String,
) -> CatalogItem {
    let package_id = reader.manifest.package_id.to_string();
    let title = metadata_string(&reader.manifest.metadata, "title").unwrap_or(fallback_title);
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
        .is_some_and(|asset| asset.plaintext_length <= MAX_PASTED_LYRIC_BYTES as u64);
    let lyric_is_bounded = reader
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
        lyric_is_bounded
            .then(|| json_asset(reader, "lyrics/timing.json").ok())
            .flatten()
            .map(|value| lyric_text(&value))
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
        locations: vec![location],
    }
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

fn prepare_open(mut reader: PackageReader) -> Result<(LoadedPackage, OpenPackageResult), String> {
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
    let mut originals = reader.manifest.assets.iter().filter(|asset| {
        is_supported_original_reference(
            &asset.logical_name,
            &asset.media_type,
            &asset.kind,
            asset.default,
        )
    });
    let original = originals
        .next()
        .ok_or_else(|| "Package is missing Original Reference audio".to_string())?;
    if originals.next().is_some() {
        return Err("Package contains ambiguous Original Reference audio".into());
    }
    routes.insert(
        "/audio/original-reference",
        PlaybackAsset {
            logical_name: original.logical_name.clone(),
            length: original.plaintext_length,
            media_type: original.media_type.clone(),
        },
    );
    let lyrics = json_asset(&mut reader, "lyrics/timing.json")?;
    let render_plan = json_asset(&mut reader, "lyrics/render-plan.json")?;
    let presentation = presentation_asset(&mut reader)?;
    let result = OpenPackageResult {
        package_id: reader.manifest.package_id.to_string(),
        minimum_player_version: reader.manifest.minimum_player_version.clone(),
        metadata: reader.manifest.metadata.clone(),
        lyrics,
        render_plan,
        presentation,
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
                    name: "Original",
                    url: media_url("/audio/original-reference"),
                    default: false,
                },
            ],
        },
    };
    Ok((LoadedPackage { reader, routes }, result))
}

fn drive_provider(app: &tauri::AppHandle) -> Result<Arc<GoogleTokenProvider>, String> {
    let state = app.state::<CloudState>();
    if let Some(provider) = state
        .tokens
        .lock()
        .map_err(|_| "Drive token state is poisoned".to_string())?
        .as_ref()
        .cloned()
    {
        return Ok(provider);
    }
    let provider = GoogleTokenProvider::from_saved(GoogleConfig::from_environment()?)?;
    *state
        .tokens
        .lock()
        .map_err(|_| "Drive token state is poisoned".to_string())? = Some(provider.clone());
    Ok(provider)
}

fn drive_cache(
    app: &tauri::AppHandle,
    provider: Arc<GoogleTokenProvider>,
) -> Result<Arc<RangeCache>, String> {
    let cloud = app.state::<CloudState>();
    if let Some(cache) = cloud
        .cache
        .lock()
        .map_err(|_| "Drive cache state is poisoned".to_string())?
        .as_ref()
        .cloned()
    {
        return Ok(cache);
    }
    let root = drive_cache_root(app)?;
    let scheduler = cloud.scheduler.clone();
    let cache = Arc::new(RangeCache::new(
        root,
        Arc::new(GoogleDriveTransport::new(provider)?),
        scheduler,
    )?);
    *cloud
        .cache
        .lock()
        .map_err(|_| "Drive cache state is poisoned".to_string())? = Some(cache.clone());
    Ok(cache)
}

fn drive_cache_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let root = app
        .path()
        .app_cache_dir()
        .map_err(|error| error.to_string())?
        .join("drive-ciphertext");
    Ok(root)
}

struct OfflineRangeTransport;

impl RangeTransport for OfflineRangeTransport {
    fn fetch_range(
        &self,
        _object: &RemoteObject,
        _start: u64,
        _end_inclusive: u64,
    ) -> Result<Vec<u8>, String> {
        Err("Google Drive is offline and this ciphertext block is not cached".into())
    }
}

fn offline_drive_cache(app: &tauri::AppHandle) -> Result<Arc<RangeCache>, String> {
    let root = drive_cache_root(app)?;
    let scheduler = app.state::<CloudState>().scheduler.clone();
    RangeCache::new(root, Arc::new(OfflineRangeTransport), scheduler).map(Arc::new)
}

fn remote_reader(
    app: &tauri::AppHandle,
    file_id: &str,
    size: u64,
    version: &str,
    priority: IoPriority,
) -> Result<(PackageReader, Arc<RangeCache>, RemoteObject), String> {
    let object = RemoteObject {
        cache_key: format!("google-drive:{file_id}"),
        length: size,
        version: version.to_owned(),
    };
    let (cache, offline) = match drive_provider(app).and_then(|provider| drive_cache(app, provider))
    {
        Ok(cache) => (cache, false),
        Err(_) => (offline_drive_cache(app)?, true),
    };
    if offline && !cache.is_complete(&object) {
        return Err("Google Drive is offline and this package is not completely cached".into());
    }
    let source = CachedRandomAccessSource::new(
        format!("gdrive://{file_id}"),
        object.clone(),
        cache.clone(),
        priority,
    );
    let master = load_vault_master().map_err(|error| error.to_string())?;
    let reader = PackageReader::open_source_with_vault(Box::new(source), &master)
        .map_err(|error| error.to_string())?;
    Ok((reader, cache, object))
}

fn reader_for_item(
    app: &tauri::AppHandle,
    item: &CatalogItem,
    priority: IoPriority,
) -> Result<ItemReader, String> {
    let mut errors = Vec::new();
    let mut locations = item
        .locations
        .iter()
        .filter(|location| !matches!(location, ItemLocation::LocalMedia { .. }))
        .collect::<Vec<_>>();
    locations.sort_by_key(|location| (!location.is_local_package(), !location.is_available()));
    for location in locations {
        let result = match location {
            ItemLocation::LocalPackage { path, .. } => path
                .canonicalize()
                .map_err(|error| format!("Unable to open {}: {error}", path.display()))
                .and_then(|path| {
                    let master = load_vault_master().map_err(|error| error.to_string())?;
                    PackageReader::open_with_vault(&path, &master)
                        .map(|reader| (reader, None))
                        .map_err(|error| error.to_string())
                }),
            ItemLocation::GoogleDrive {
                file_id,
                size,
                version,
                ..
            } => remote_reader(app, file_id, *size, version, priority)
                .map(|(reader, cache, object)| (reader, Some((cache, object)))),
            ItemLocation::LocalMedia { .. } => unreachable!(),
        };
        match result {
            Ok(reader) => return Ok(reader),
            Err(error) => errors.push(error),
        }
    }
    Err(if errors.is_empty() {
        "Library item has no package source".into()
    } else {
        format!("No package source is available: {}", errors.join("; "))
    })
}

fn save_and_emit(app: &tauri::AppHandle) -> Result<CatalogSnapshot, String> {
    let catalog = app.state::<CatalogState>();
    let catalog = catalog
        .0
        .lock()
        .map_err(|_| "Catalog lock is poisoned".to_string())?;
    catalog.save()?;
    let snapshot = catalog.snapshot();
    let _ = app.emit("library-changed", snapshot.clone());
    Ok(snapshot)
}

fn enqueue_ready(app: &tauri::AppHandle, items: Vec<CatalogItem>) {
    for item in items
        .into_iter()
        .filter(|item| item.status == ItemStatus::Queued)
    {
        if let Err(error) = enqueue_item(app, item.clone(), None) {
            if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
                catalog.set_progress(
                    &item.id,
                    ItemStatus::SetupRequired,
                    0.0,
                    Some("Processing setup requires attention; open Issues".into()),
                );
            }
            issues::report(app, issues::runtime_repair_issue(&error));
        }
    }
    let _ = save_and_emit(app);
}

#[tauri::command]
fn player_status(
    app: tauri::AppHandle,
    player: tauri::State<'_, PlayerState>,
    processing: tauri::State<'_, ProcessingState>,
) -> PlayerStatus {
    let mut processing_status = processing::status(&processing);
    if processing_status.runtime_available
        && let Err(error) = runtime::model_files_present_hint()
    {
        processing_status.runtime_available = false;
        processing_status.runtime_error = Some(error.clone());
        issues::ensure(
            &app,
            issues::models_missing_issue(&error, model_installer::install_is_allowed()),
        );
    } else if !processing_status.runtime_available
        && let Some(error) = processing_status.runtime_error.clone()
    {
        issues::ensure(&app, issues::runtime_repair_issue(&error));
    }
    PlayerStatus {
        version: env!("CARGO_PKG_VERSION"),
        platform: std::env::consts::OS,
        package_open: player.loaded.lock().is_ok_and(|loaded| loaded.is_some()),
        vault_available: load_vault_master().is_ok(),
        processing: processing_status,
    }
}

#[tauri::command]
fn catalog_snapshot(state: tauri::State<'_, CatalogState>) -> Result<CatalogSnapshot, String> {
    state
        .0
        .lock()
        .map_err(|_| "Catalog lock is poisoned".to_string())
        .map(|catalog| catalog.snapshot())
}

#[tauri::command]
fn search_library(
    state: tauri::State<'_, CatalogState>,
    query: String,
) -> Result<Vec<SearchResult>, String> {
    if query.len() > 512 {
        return Err("Search query is too long".into());
    }
    state
        .0
        .lock()
        .map_err(|_| "Catalog lock is poisoned".to_string())
        .map(|catalog| catalog.search(&query))
}

#[tauri::command]
fn item_lyrics(state: tauri::State<'_, CatalogState>, item_id: String) -> Result<String, String> {
    state
        .0
        .lock()
        .map_err(|_| "Catalog lock is poisoned".to_string())?
        .item(&item_id)
        .map(|item| item.lyric_text.clone())
        .ok_or_else(|| "Library item no longer exists".to_string())
}

#[tauri::command]
fn system_issues(app: tauri::AppHandle) -> Vec<issues::SystemIssue> {
    issues::snapshot(&app)
}

#[tauri::command]
fn task_runtime_snapshot(app: tauri::AppHandle) -> tasks::TaskSnapshot {
    tasks::snapshot(&app)
}

#[tauri::command]
fn task_output_snapshot(
    app: tauri::AppHandle,
    task_id: String,
    after_sequence: u64,
) -> tasks::TaskOutputSnapshot {
    tasks::output_snapshot(&app, &task_id, after_sequence)
}

#[tauri::command]
fn task_record(app: tauri::AppHandle, task_id: String) -> Option<tasks::TaskRecord> {
    tasks::task(&app, &task_id)
}

#[tauri::command]
fn cancel_task(app: tauri::AppHandle, task_id: String) -> Result<bool, String> {
    let task = tasks::task(&app, &task_id).ok_or_else(|| "Task no longer exists".to_string())?;
    if !task.cancellable {
        return Err("This task cannot be cancelled".into());
    }
    match task.kind {
        tasks::TaskKind::Processing => {
            let item_id = task.related_item_id.as_deref().unwrap_or(&task.id);
            processing::cancel_item(&app, item_id)?;
            Ok(true)
        }
        tasks::TaskKind::ModelInstall => model_installer::cancel_active(&app),
        _ => Err("This task cannot be cancelled".into()),
    }
}

fn start_runtime_task(
    app: &tauri::AppHandle,
    prefix: &str,
    kind: tasks::TaskKind,
    title: &str,
    mode: tasks::ProgressMode,
) -> Result<String, String> {
    let id = format!("{prefix}-{}", Uuid::new_v4());
    tasks::start(
        app,
        tasks::TaskSpec {
            id: id.clone(),
            kind,
            title: title.into(),
            status: tasks::TaskStatus::Running,
            progress_mode: mode,
            cancellable: false,
            related_item_id: None,
        },
    )?;
    Ok(id)
}

fn finish_runtime_task<T>(
    app: &tauri::AppHandle,
    task_id: &str,
    result: &Result<T, String>,
    success_message: &str,
) {
    match result {
        Ok(_) => tasks::finish(
            app,
            task_id,
            tasks::TaskStatus::Succeeded,
            Some(success_message.into()),
        ),
        Err(error) => tasks::finish(app, task_id, tasks::TaskStatus::Failed, Some(error.clone())),
    }
}

#[tauri::command]
fn dismiss_system_issue(app: tauri::AppHandle, issue_id: String) -> bool {
    issues::dismiss(&app, &issue_id)
}

#[tauri::command]
async fn install_processing_models(
    app: tauri::AppHandle,
    issue_id: String,
    license_confirmed: bool,
) -> Result<model_installer::ModelInstallResult, String> {
    model_installer::install(app, issue_id, license_confirmed).await
}

#[tauri::command]
fn cancel_model_install(app: tauri::AppHandle, issue_id: String) -> Result<bool, String> {
    model_installer::cancel(&app, &issue_id)
}

#[tauri::command]
async fn add_local_files(
    app: tauri::AppHandle,
    paths: Vec<PathBuf>,
) -> Result<CatalogSnapshot, String> {
    let selected_count = paths.len();
    let task_id = start_runtime_task(
        &app,
        "local-scan",
        tasks::TaskKind::LocalScan,
        "Scan selected local files",
        tasks::ProgressMode::Indeterminate,
    )?;
    tasks::append_output(
        &app,
        &task_id,
        tasks::OutputStream::System,
        Some("scan"),
        &format!("Scanning {selected_count} selected entries"),
    );
    tasks::progress(
        &app,
        &task_id,
        tasks::TaskProgress {
            stage_key: Some("scan".into()),
            stage_title: Some("Scan selected local files".into()),
            stage_progress_percent: Some(0.0),
            completed_units: Some(0),
            total_units: Some(selected_count as u64),
            unit_label: Some("entries".into()),
            message: Some("Reading selected local file metadata".into()),
            ..Default::default()
        },
    );
    let result: Result<CatalogSnapshot, String> = async {
        let items = tauri::async_runtime::spawn_blocking(move || scan_files(paths))
            .await
            .map_err(|error| format!("Local scan task failed: {error}"))??;
        tasks::progress(
            &app,
            &task_id,
            tasks::TaskProgress {
                stage_key: Some("scan".into()),
                stage_title: Some("Scan selected local files".into()),
                stage_progress_percent: Some(100.0),
                completed_units: Some(selected_count as u64),
                total_units: Some(selected_count as u64),
                unit_label: Some("entries".into()),
                message: Some(format!("Scanned {selected_count} selected entries")),
                ..Default::default()
            },
        );
        tasks::progress(
            &app,
            &task_id,
            tasks::TaskProgress {
                stage_key: Some("catalog".into()),
                stage_title: Some("Update Library catalog".into()),
                stage_progress_percent: Some(100.0),
                completed_units: Some(selected_count as u64),
                total_units: Some(selected_count as u64),
                unit_label: Some("entries".into()),
                message: Some(format!("Found {} supported items", items.len())),
                ..Default::default()
            },
        );
        let queued_items = {
            let state = app.state::<CatalogState>();
            let mut catalog = state
                .0
                .lock()
                .map_err(|_| "Catalog lock is poisoned".to_string())?;
            let ids = catalog.upsert_many(items)?;
            ids.into_iter()
                .filter_map(|id| catalog.item(&id).cloned())
                .collect::<Vec<_>>()
        };
        let snapshot = save_and_emit(&app)?;
        enqueue_ready(&app, queued_items);
        Ok(snapshot)
    }
    .await;
    finish_runtime_task(&app, &task_id, &result, "Selected files added to Library");
    result
}

#[tauri::command]
async fn add_local_folder(app: tauri::AppHandle, path: PathBuf) -> Result<CatalogSnapshot, String> {
    let task_id = start_runtime_task(
        &app,
        "folder-scan",
        tasks::TaskKind::LocalScan,
        "Scan local folder",
        tasks::ProgressMode::Indeterminate,
    )?;
    tasks::append_output(
        &app,
        &task_id,
        tasks::OutputStream::System,
        Some("scan"),
        "Scanning a selected folder with bounded depth and entry limits",
    );
    tasks::progress(
        &app,
        &task_id,
        tasks::TaskProgress {
            stage_key: Some("scan".into()),
            stage_title: Some("Scan local folder".into()),
            message: Some("Enumerating a bounded local folder tree".into()),
            ..Default::default()
        },
    );
    let result: Result<CatalogSnapshot, String> = async {
        let scan = tauri::async_runtime::spawn_blocking(move || scan_root(&path))
            .await
            .map_err(|error| format!("Local scan task failed: {error}"))??;
        tasks::progress(
            &app,
            &task_id,
            tasks::TaskProgress {
                stage_key: Some("catalog".into()),
                stage_title: Some("Update Library catalog".into()),
                stage_progress_percent: Some(100.0),
                completed_units: Some(scan.entries_seen as u64),
                total_units: Some(scan.entries_seen as u64),
                unit_label: Some("entries".into()),
                message: Some(format!("Scanned {} entries", scan.entries_seen)),
                ..Default::default()
            },
        );
        let mut items = scan.items;
        let live_paths = items
            .iter()
            .flat_map(|item| item.locations.iter())
            .filter_map(|location| match location {
                ItemLocation::LocalPackage { path, .. } | ItemLocation::LocalMedia { path, .. } => {
                    Some(path.clone())
                }
                ItemLocation::GoogleDrive { .. } => None,
            })
            .collect::<std::collections::HashSet<_>>();
        let queued_items = {
            let state = app.state::<CatalogState>();
            let mut catalog = state
                .0
                .lock()
                .map_err(|_| "Catalog lock is poisoned".to_string())?;
            catalog.validate_upserts(items.iter())?;
            let source_id = catalog.add_local_source(scan.root);
            if !scan.truncated {
                catalog.reconcile_local_source(&source_id, &live_paths);
            }
            let mut ids = Vec::new();
            for item in &mut items {
                for location in &mut item.locations {
                    match location {
                        ItemLocation::LocalPackage {
                            source_id: current,
                            available,
                            ..
                        }
                        | ItemLocation::LocalMedia {
                            source_id: current,
                            available,
                            ..
                        } => {
                            *current = Some(source_id.clone());
                            *available = true;
                        }
                        ItemLocation::GoogleDrive { .. } => {}
                    }
                }
                ids.push(catalog.upsert(item.clone())?);
            }
            ids.into_iter()
                .filter_map(|id| catalog.item(&id).cloned())
                .collect::<Vec<_>>()
        };
        let snapshot = save_and_emit(&app)?;
        enqueue_ready(&app, queued_items);
        Ok(snapshot)
    }
    .await;
    finish_runtime_task(&app, &task_id, &result, "Folder scan completed");
    result
}

#[tauri::command]
async fn prepare_local_clip(
    app: tauri::AppHandle,
    path: PathBuf,
) -> Result<local_clip::LocalClipPreview, String> {
    let scheduler = app.state::<CloudState>().scheduler.clone();
    local_clip::prepare(app, scheduler, path).await
}

#[tauri::command]
fn cancel_local_clip(app: tauri::AppHandle, clip_id: String) -> Result<bool, String> {
    local_clip::cancel(&app, &clip_id)
}

#[tauri::command]
fn commit_local_clip(
    app: tauri::AppHandle,
    clip_id: String,
    start_millis: u64,
    end_millis: u64,
    title: String,
) -> Result<CatalogSnapshot, String> {
    let item = local_clip::commit(&app, &clip_id, start_millis, end_millis, &title)?;
    let queued_item = {
        let state = app.state::<CatalogState>();
        let mut catalog = state
            .0
            .lock()
            .map_err(|_| "Catalog lock is poisoned".to_string())?;
        let id = catalog.upsert(item)?;
        catalog
            .item(&id)
            .cloned()
            .ok_or_else(|| "Local clip was not added to the catalog".to_string())?
    };
    let snapshot = save_and_emit(&app)?;
    let _ = local_clip::cancel(&app, &clip_id);
    enqueue_ready(&app, vec![queued_item]);
    Ok(snapshot)
}

#[tauri::command]
async fn rescan_local_sources(app: tauri::AppHandle) -> Result<CatalogSnapshot, String> {
    let sources = app
        .state::<CatalogState>()
        .0
        .lock()
        .map_err(|_| "Catalog lock is poisoned".to_string())?
        .local_sources()
        .to_vec();
    for source in sources {
        if add_local_folder(app.clone(), source.path).await.is_err() {
            let state = app.state::<CatalogState>();
            let mut catalog = state
                .0
                .lock()
                .map_err(|_| "Catalog lock is poisoned".to_string())?;
            catalog.reconcile_local_source(&source.id, &HashSet::new());
        }
    }
    save_and_emit(&app)
}

#[tauri::command]
fn remove_library_source(
    app: tauri::AppHandle,
    source_id: String,
) -> Result<CatalogSnapshot, String> {
    {
        let state = app.state::<CatalogState>();
        let mut catalog = state
            .0
            .lock()
            .map_err(|_| "Catalog lock is poisoned".to_string())?;
        catalog.remove_source(&source_id);
    }
    save_and_emit(&app)
}

#[tauri::command]
fn provide_lyrics_file(
    app: tauri::AppHandle,
    item_id: String,
    path: PathBuf,
) -> Result<CatalogSnapshot, String> {
    let path = path
        .canonicalize()
        .map_err(|error| format!("Unable to open lyrics: {error}"))?;
    let text = read_authoritative_lyrics(&path)?;
    let item = {
        let state = app.state::<CatalogState>();
        let mut catalog = state
            .0
            .lock()
            .map_err(|_| "Catalog lock is poisoned".to_string())?;
        catalog.provide_lyrics(&item_id, path, text)?;
        catalog
            .item(&item_id)
            .cloned()
            .ok_or_else(|| "Library item no longer exists".to_string())?
    };
    enqueue_item(&app, item, None)?;
    save_and_emit(&app)
}

fn write_pasted_lyrics(
    app: &tauri::AppHandle,
    item_id: &str,
    text: &str,
) -> Result<PathBuf, String> {
    if text.len() > MAX_PASTED_LYRIC_BYTES
        || text.lines().all(|line| line.trim().is_empty())
        || text.contains('\0')
    {
        return Err(
            "Pasted lyrics must be non-empty UTF-8 text no larger than 1,000,000 bytes".into(),
        );
    }
    let root = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?
        .join("input")
        .join("lyrics");
    fs::create_dir_all(&root)
        .map_err(|error| format!("Unable to create lyric input directory: {error}"))?;
    let mut digest = Sha256::new();
    digest.update(item_id.as_bytes());
    digest.update([0]);
    digest.update(text.as_bytes());
    let path = root.join(format!("{}.txt", hex::encode(digest.finalize())));
    if !path.exists() {
        let temporary = path.with_extension("txt.partial");
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|error| format!("Unable to create lyric snapshot: {error}"))?;
        file.write_all(text.as_bytes())
            .and_then(|()| file.sync_all())
            .map_err(|error| format!("Unable to write lyric snapshot: {error}"))?;
        fs::rename(&temporary, &path)
            .map_err(|error| format!("Unable to publish lyric snapshot: {error}"))?;
    }
    Ok(path)
}

#[tauri::command]
fn provide_lyrics_text(
    app: tauri::AppHandle,
    item_id: String,
    text: String,
) -> Result<CatalogSnapshot, String> {
    let path = write_pasted_lyrics(&app, &item_id, &text)?;
    let item = {
        let state = app.state::<CatalogState>();
        let mut catalog = state
            .0
            .lock()
            .map_err(|_| "Catalog lock is poisoned".to_string())?;
        catalog.provide_lyrics(&item_id, path.clone(), text)?;
        catalog
            .item(&item_id)
            .cloned()
            .ok_or_else(|| "Library item no longer exists".to_string())?
    };
    enqueue_item(&app, item, Some(path))?;
    save_and_emit(&app)
}

fn bind_retry_lyrics_path(
    mut item: CatalogItem,
    lyrics_path: PathBuf,
) -> Result<CatalogItem, String> {
    let location = item
        .locations
        .iter_mut()
        .find_map(|location| match location {
            ItemLocation::LocalMedia { lyrics_path, .. } => Some(lyrics_path),
            _ => None,
        })
        .ok_or_else(|| "Only local media can be retried for processing".to_string())?;
    *location = Some(lyrics_path);
    Ok(item)
}

#[tauri::command]
fn retry_processing_item(
    app: tauri::AppHandle,
    item_id: String,
) -> Result<CatalogSnapshot, String> {
    let item = app
        .state::<CatalogState>()
        .0
        .lock()
        .map_err(|_| "Catalog lock is poisoned".to_string())?
        .item(&item_id)
        .cloned()
        .ok_or_else(|| "Library item no longer exists".to_string())?;
    let existing_lyrics = item.locations.iter().find_map(|location| match location {
        ItemLocation::LocalMedia { lyrics_path, .. } => lyrics_path.clone(),
        _ => None,
    });
    let (lyrics_path, transient) = if let Some(path) = existing_lyrics.filter(|path| path.is_file())
    {
        (path, None)
    } else {
        let path = write_pasted_lyrics(&app, &item_id, &item.lyric_text)?;
        (path.clone(), Some(path))
    };
    let queued = bind_retry_lyrics_path(item, lyrics_path)?;
    enqueue_item(&app, queued, transient)?;
    issues::resolve_processing_failure(&app, &item_id);
    save_and_emit(&app)
}

#[tauri::command]
fn cancel_processing_item(
    app: tauri::AppHandle,
    item_id: String,
) -> Result<CatalogSnapshot, String> {
    processing::cancel_item(&app, &item_id)?;
    catalog_snapshot(app.state::<CatalogState>())
}

fn drive_catalog_item(
    cache: &Arc<RangeCache>,
    master: Option<&LockedSecret<32>>,
    source_id: &str,
    root_id: &str,
    file: DriveFile,
) -> CatalogItem {
    let mut location = ItemLocation::GoogleDrive {
        source_id: source_id.to_owned(),
        root_id: root_id.to_owned(),
        file_id: file.id.clone(),
        name: file.name.clone(),
        size: file.size,
        version: file.version.clone(),
        modified_time: file.modified_time.clone(),
        md5_checksum: file.md5_checksum.clone(),
        available: true,
    };
    let opened = master
        .ok_or_else(|| "The library master must be restored on this device".to_string())
        .and_then(|master| {
            let source = CachedRandomAccessSource::new(
                format!("gdrive://{}", file.id),
                file.remote_object(),
                cache.clone(),
                IoPriority::Background,
            );
            PackageReader::open_source_with_vault(Box::new(source), master)
                .map_err(|error| error.to_string())
        });
    match opened {
        Ok(mut reader) => catalog_item_from_reader(&mut reader, location, file.name),
        Err(error) => {
            if master.is_some()
                && let ItemLocation::GoogleDrive { available, .. } = &mut location
            {
                *available = false;
            }
            CatalogItem {
                id: format!("drive-{}", file.id),
                package_id: None,
                title: file.name,
                artist: None,
                composer: None,
                first_lyric_line: None,
                lyric_text: String::new(),
                status: ItemStatus::Failed,
                progress_percent: 0.0,
                status_message: Some(format!("Recovery or package validation required: {error}")),
                processing_job_id: None,
                processing_task_evidence: None,
                has_thumbnail: false,
                locations: vec![location],
            }
        }
    }
}

fn drive_download_task_id(object: &RemoteObject) -> String {
    let mut digest = Sha256::new();
    digest.update(object.cache_key.as_bytes());
    digest.update([0]);
    digest.update(object.version.as_bytes());
    digest.update([0]);
    digest.update(object.length.to_le_bytes());
    format!("drive-download-{}", hex::encode(digest.finalize()))
}

fn drive_location_is_cached(root: &std::path::Path, location: &ItemLocation) -> bool {
    let ItemLocation::GoogleDrive {
        file_id,
        size,
        version,
        ..
    } = location
    else {
        return false;
    };
    RangeCache::is_complete_at(
        root,
        &RemoteObject {
            cache_key: format!("google-drive:{file_id}"),
            length: *size,
            version: version.clone(),
        },
    )
}

fn legacy_drive_roots(catalog: &Catalog, source_id: &str) -> Vec<DriveRoot> {
    let mut roots = catalog
        .items()
        .iter()
        .flat_map(|item| item.locations.iter())
        .filter_map(|location| match location {
            ItemLocation::GoogleDrive {
                source_id: current,
                root_id,
                file_id,
                name,
                ..
            } if current == source_id => Some(DriveRoot {
                file_id: if root_id.is_empty() {
                    file_id.clone()
                } else {
                    root_id.clone()
                },
                name: name.clone(),
                is_folder: false,
            }),
            _ => None,
        })
        .collect::<Vec<_>>();
    roots.sort_by(|left, right| left.file_id.cmp(&right.file_id));
    roots.dedup_by(|left, right| left.file_id == right.file_id);
    roots
}

#[tauri::command]
async fn connect_google_drive(app: tauri::AppHandle) -> Result<CatalogSnapshot, String> {
    let task_id = start_runtime_task(
        &app,
        "drive-connect",
        tasks::TaskKind::DriveScan,
        "Connect and scan Google Drive",
        tasks::ProgressMode::Indeterminate,
    )?;
    let task_app = app.clone();
    let task_key = task_id.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        tasks::progress(
            &task_app,
            &task_key,
            tasks::TaskProgress {
                stage_key: Some("authorization".into()),
                stage_title: Some("Authorize and choose Drive sources".into()),
                message: Some("Waiting for Drive authorization and selection".into()),
                ..Default::default()
            },
        );
        let (provider, selected) = authorize_and_pick(&task_app)?;
        let roots = resolve_selection_roots(&provider, selected)?;
        *task_app
            .state::<CloudState>()
            .tokens
            .lock()
            .map_err(|_| "Drive token state is poisoned".to_string())? = Some(provider.clone());
        task_app
            .state::<CloudState>()
            .cache
            .lock()
            .map_err(|_| "Drive cache state is poisoned".to_string())?
            .take();
        let source_id = Uuid::new_v4().to_string();
        let cache = drive_cache(&task_app, provider.clone())?;
        let master = load_vault_master();
        let mut items = Vec::new();
        let root_count = roots.len();
        if root_count == 0 {
            tasks::progress(
                &task_app,
                &task_key,
                tasks::TaskProgress {
                    stage_key: Some("discovery".into()),
                    stage_title: Some("Discover selected Drive packages".into()),
                    stage_progress_percent: Some(100.0),
                    completed_units: Some(0),
                    total_units: Some(0),
                    unit_label: Some("roots".into()),
                    message: Some("No Drive roots were selected".into()),
                    ..Default::default()
                },
            );
        }
        for (index, root) in roots.iter().enumerate() {
            tasks::progress(
                &task_app,
                &task_key,
                tasks::TaskProgress {
                    stage_key: Some("discovery".into()),
                    stage_title: Some("Discover selected Drive packages".into()),
                    stage_progress_percent: Some(if root_count == 0 {
                        100.0
                    } else {
                        index as f32 / root_count as f32 * 100.0
                    }),
                    completed_units: Some(index as u64),
                    total_units: Some(root_count as u64),
                    unit_label: Some("roots".into()),
                    message: Some(format!("Scanning Drive root {} of {root_count}", index + 1)),
                    ..Default::default()
                },
            );
            match expand_drive_root(&provider, &root.file_id) {
                Ok(files) => items.extend(files.into_iter().map(|file| {
                    drive_catalog_item(
                        &cache,
                        master.as_ref().ok(),
                        &source_id,
                        &root.file_id,
                        file,
                    )
                })),
                Err(error) => tasks::append_output(
                    &task_app,
                    &task_key,
                    tasks::OutputStream::Stderr,
                    Some("discovery"),
                    &error,
                ),
            }
            let completed = index + 1;
            tasks::progress(
                &task_app,
                &task_key,
                tasks::TaskProgress {
                    stage_key: Some("discovery".into()),
                    stage_title: Some("Discover selected Drive packages".into()),
                    stage_progress_percent: Some(if root_count == 0 {
                        100.0
                    } else {
                        completed as f32 / root_count as f32 * 100.0
                    }),
                    completed_units: Some(completed as u64),
                    total_units: Some(root_count as u64),
                    unit_label: Some("roots".into()),
                    message: Some(format!("Completed Drive root {completed} of {root_count}")),
                    ..Default::default()
                },
            );
        }
        {
            let state = task_app.state::<CatalogState>();
            let mut catalog = state
                .0
                .lock()
                .map_err(|_| "Catalog lock is poisoned".to_string())?;
            catalog.validate_upserts(items.iter())?;
            catalog.add_drive_source(source_id, "Google Drive".into(), roots);
            catalog.upsert_many(items)?;
        }
        save_and_emit(&task_app)
    })
    .await
    .map_err(|error| format!("Google Drive task failed: {error}"))?;
    finish_runtime_task(
        &app,
        &task_id,
        &result,
        "Google Drive connected and scanned",
    );
    result
}

#[tauri::command]
async fn rescan_google_drive(app: tauri::AppHandle) -> Result<CatalogSnapshot, String> {
    let sources = {
        let state = app.state::<CatalogState>();
        let catalog = state
            .0
            .lock()
            .map_err(|_| "Catalog lock is poisoned".to_string())?;
        let mut sources = catalog.drive_sources().to_vec();
        for source in &mut sources {
            if source.roots.is_empty() {
                source.roots = legacy_drive_roots(&catalog, &source.id);
            }
        }
        sources
    };
    if sources.is_empty() {
        return catalog_snapshot(app.state::<CatalogState>());
    }
    let task_id = start_runtime_task(
        &app,
        "drive-rescan",
        tasks::TaskKind::DriveScan,
        "Rescan Google Drive sources",
        tasks::ProgressMode::Indeterminate,
    )?;
    let task_app = app.clone();
    let task_key = task_id.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        tasks::progress(
            &task_app,
            &task_key,
            tasks::TaskProgress {
                stage_key: Some("availability".into()),
                stage_title: Some("Check Drive source availability".into()),
                message: Some("Checking connected Drive sources and cached packages".into()),
                ..Default::default()
            },
        );
        let cache_root = drive_cache_root(&task_app)?;
        let provider = match drive_provider(&task_app) {
            Ok(provider) => provider,
            Err(_) => {
                let state = task_app.state::<CatalogState>();
                let mut catalog = state
                    .0
                    .lock()
                    .map_err(|_| "Catalog lock is poisoned".to_string())?;
                for source in &sources {
                    catalog.set_drive_source_availability(&source.id, |location| {
                        drive_location_is_cached(&cache_root, location)
                    });
                }
                drop(catalog);
                return save_and_emit(&task_app);
            }
        };
        let cache = drive_cache(&task_app, provider.clone())?;
        let master = load_vault_master();
        let roots = sources
            .iter()
            .flat_map(|source| {
                source
                    .roots
                    .iter()
                    .map(|root| (source.id.clone(), root.clone()))
            })
            .collect::<Vec<_>>();
        let scan_count = roots.len();
        if scan_count == 0 {
            tasks::progress(
                &task_app,
                &task_key,
                tasks::TaskProgress {
                    stage_key: Some("discovery".into()),
                    stage_title: Some("Refresh Drive package metadata".into()),
                    stage_progress_percent: Some(100.0),
                    completed_units: Some(0),
                    total_units: Some(0),
                    unit_label: Some("roots".into()),
                    message: Some("No Drive roots require scanning".into()),
                    ..Default::default()
                },
            );
        }
        let mut scans = Vec::with_capacity(scan_count);
        for (index, (source_id, root)) in roots.into_iter().enumerate() {
            tasks::progress(
                &task_app,
                &task_key,
                tasks::TaskProgress {
                    stage_key: Some("discovery".into()),
                    stage_title: Some("Refresh Drive package metadata".into()),
                    stage_progress_percent: Some(if scan_count == 0 {
                        100.0
                    } else {
                        index as f32 / scan_count as f32 * 100.0
                    }),
                    completed_units: Some(index as u64),
                    total_units: Some(scan_count as u64),
                    unit_label: Some("roots".into()),
                    message: Some(format!("Scanning Drive root {} of {scan_count}", index + 1)),
                    ..Default::default()
                },
            );
            let result = expand_drive_root(&provider, &root.file_id);
            if let Err(error) = &result {
                tasks::append_output(
                    &task_app,
                    &task_key,
                    tasks::OutputStream::Stderr,
                    Some("discovery"),
                    error,
                );
            }
            let completed = index + 1;
            tasks::progress(
                &task_app,
                &task_key,
                tasks::TaskProgress {
                    stage_key: Some("discovery".into()),
                    stage_title: Some("Refresh Drive package metadata".into()),
                    stage_progress_percent: Some(if scan_count == 0 {
                        100.0
                    } else {
                        completed as f32 / scan_count as f32 * 100.0
                    }),
                    completed_units: Some(completed as u64),
                    total_units: Some(scan_count as u64),
                    unit_label: Some("roots".into()),
                    message: Some(format!("Completed Drive root {completed} of {scan_count}")),
                    ..Default::default()
                },
            );
            scans.push((source_id, root, result));
        }
        let prepared = scans
            .into_iter()
            .map(|(source_id, root, result)| {
                let result = result.map(|files| {
                    let live_ids = files
                        .iter()
                        .map(|file| file.id.clone())
                        .collect::<HashSet<_>>();
                    let items = files
                        .into_iter()
                        .map(|file| {
                            drive_catalog_item(
                                &cache,
                                master.as_ref().ok(),
                                &source_id,
                                &root.file_id,
                                file,
                            )
                        })
                        .collect::<Vec<_>>();
                    (live_ids, items)
                });
                (source_id, root, result)
            })
            .collect::<Vec<_>>();
        {
            let state = task_app.state::<CatalogState>();
            let mut catalog = state
                .0
                .lock()
                .map_err(|_| "Catalog lock is poisoned".to_string())?;
            catalog.validate_upserts(
                prepared
                    .iter()
                    .filter_map(|(_, _, result)| result.as_ref().ok())
                    .flat_map(|(_, items)| items.iter()),
            )?;
            for (source_id, root, result) in prepared {
                match result {
                    Ok((live_ids, items)) => {
                        catalog.reconcile_drive_root(&source_id, &root.file_id, &live_ids);
                        catalog.upsert_many(items)?;
                    }
                    Err(_) => {
                        catalog.set_drive_root_availability(&source_id, &root.file_id, |location| {
                            drive_location_is_cached(&cache_root, location)
                        })
                    }
                }
            }
        }
        save_and_emit(&task_app)
    })
    .await
    .map_err(|error| format!("Google Drive rescan failed: {error}"))?;
    finish_runtime_task(&app, &task_id, &result, "Google Drive rescan completed");
    result
}

#[tauri::command]
fn disconnect_google_drive(app: tauri::AppHandle) -> Result<CatalogSnapshot, String> {
    if let Ok(provider) = drive_provider(&app) {
        provider.disconnect()?;
    }
    if let Ok(mut tokens) = app.state::<CloudState>().tokens.lock() {
        tokens.take();
    }
    if let Ok(mut cache) = app.state::<CloudState>().cache.lock() {
        cache.take();
    }
    {
        let state = app.state::<CatalogState>();
        let mut catalog = state
            .0
            .lock()
            .map_err(|_| "Catalog lock is poisoned".to_string())?;
        let source_ids = catalog
            .snapshot()
            .drive_sources
            .into_iter()
            .map(|source| source.id)
            .collect::<Vec<_>>();
        for source_id in source_ids {
            catalog.remove_source(&source_id);
        }
    }
    save_and_emit(&app)
}

#[tauri::command]
fn open_library_item(app: tauri::AppHandle, item_id: String) -> Result<OpenPackageResult, String> {
    let item = app
        .state::<CatalogState>()
        .0
        .lock()
        .map_err(|_| "Catalog lock is poisoned".to_string())?
        .item(&item_id)
        .cloned()
        .ok_or_else(|| "Library item no longer exists".to_string())?;
    if !matches!(item.status, ItemStatus::Ready | ItemStatus::Offline) {
        return Err("Only a ready or fully cached offline item can be played".into());
    }
    let (reader, remote) = match reader_for_item(&app, &item, IoPriority::Playback) {
        Ok(reader) => reader,
        Err(error) => {
            if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
                catalog.set_progress(
                    &item.id,
                    ItemStatus::Offline,
                    0.0,
                    Some("No local, cloud, or complete cached copy is available".into()),
                );
            }
            let _ = save_and_emit(&app);
            return Err(error);
        }
    };
    let (loaded, result) = prepare_open(reader)?;
    *app.state::<PlayerState>()
        .loaded
        .lock()
        .map_err(|_| "Player state lock is poisoned".to_string())? = Some(loaded);
    if let Some((cache, object)) = remote {
        if !cache.is_complete(&object) {
            let task_id = drive_download_task_id(&object);
            let already_running = tasks::task(&app, &task_id).is_some_and(|task| {
                matches!(
                    task.status,
                    tasks::TaskStatus::Queued | tasks::TaskStatus::Running
                )
            });
            if !already_running {
                tasks::start(
                    &app,
                    tasks::TaskSpec {
                        id: task_id.clone(),
                        kind: tasks::TaskKind::DriveDownload,
                        title: format!("Cache Drive package: {}", item.title),
                        status: tasks::TaskStatus::Running,
                        progress_mode: tasks::ProgressMode::Determinate,
                        cancellable: false,
                        related_item_id: Some(item.id.clone()),
                    },
                )?;
            }
            let task_app = app.clone();
            let callback_id = task_id.clone();
            let started = cache.download_in_background_with_progress(
                object,
                Arc::new(move |completed, total, error| {
                    if let Some(error) = error {
                        tasks::finish(
                            &task_app,
                            &callback_id,
                            tasks::TaskStatus::Failed,
                            Some(error),
                        );
                    } else {
                        let percent = if total == 0 {
                            100.0
                        } else {
                            completed as f32 / total as f32 * 100.0
                        };
                        tasks::progress(
                            &task_app,
                            &callback_id,
                            tasks::TaskProgress {
                                stage_key: Some("ciphertext-cache".into()),
                                stage_title: Some("Cache encrypted Drive package".into()),
                                progress_percent: Some(percent),
                                completed_units: Some(completed),
                                total_units: Some(total),
                                unit_label: Some("bytes".into()),
                                message: Some(format!("Cached {completed} of {total} bytes")),
                                ..Default::default()
                            },
                        );
                        if completed >= total {
                            tasks::finish(
                                &task_app,
                                &callback_id,
                                tasks::TaskStatus::Succeeded,
                                Some("Encrypted Drive package cached for offline playback".into()),
                            );
                        }
                    }
                }),
            );
            if let Err(error) = started {
                tasks::finish(&app, &task_id, tasks::TaskStatus::Failed, Some(error));
            }
        }
    }
    if item.status == ItemStatus::Offline {
        if let Ok(mut catalog) = app.state::<CatalogState>().0.lock() {
            catalog.set_progress(
                &item.id,
                ItemStatus::Ready,
                100.0,
                Some("Playing from the complete offline cache".into()),
            );
        }
        let _ = save_and_emit(&app);
    }
    Ok(result)
}

#[tauri::command]
fn load_item_thumbnail(app: tauri::AppHandle, item_id: String) -> Result<Option<String>, String> {
    let item = app
        .state::<CatalogState>()
        .0
        .lock()
        .map_err(|_| "Catalog lock is poisoned".to_string())?
        .item(&item_id)
        .cloned()
        .ok_or_else(|| "Library item no longer exists".to_string())?;
    if !item.has_thumbnail {
        return Ok(None);
    }
    let (mut reader, _) = reader_for_item(&app, &item, IoPriority::AlternateTrack)?;
    let asset = reader
        .manifest
        .assets
        .iter()
        .find(|asset| asset.logical_name == "artwork/thumbnail.webp")
        .ok_or_else(|| "Package thumbnail is missing".to_string())?;
    if asset.plaintext_length == 0 || asset.plaintext_length > MAX_THUMBNAIL_BYTES {
        return Err("Package thumbnail exceeds the display bound".into());
    }
    let bytes = reader
        .read_asset("artwork/thumbnail.webp")
        .map_err(|error| error.to_string())?;
    Ok(Some(format!(
        "data:image/webp;base64,{}",
        STANDARD.encode(bytes.as_slice())
    )))
}

#[tauri::command]
fn close_package(state: tauri::State<'_, PlayerState>) -> Result<(), String> {
    *state
        .loaded
        .lock()
        .map_err(|_| "Player state lock is poisoned".to_string())? = None;
    Ok(())
}

#[tauri::command]
async fn revise_item_lyrics(
    app: tauri::AppHandle,
    item_id: String,
    text: String,
) -> Result<CatalogSnapshot, String> {
    let task_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || lyric_revision::revise(task_app, item_id, text))
        .await
        .map_err(|error| format!("Lyric revision task failed: {error}"))??;
    catalog_snapshot(app.state::<CatalogState>())
}

#[tauri::command]
fn set_playback_active(app: tauri::AppHandle, playing: bool) -> Result<(), String> {
    processing::set_playback_state(&app, playing)
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
fn launch_recovery_export(
    app: tauri::AppHandle,
    output: PathBuf,
) -> Result<recovery_ui::RecoveryToolLaunch, String> {
    recovery_ui::export(app, output)
}

#[tauri::command]
fn launch_recovery_restore_local(
    app: tauri::AppHandle,
    bundle: PathBuf,
    library: PathBuf,
) -> Result<recovery_ui::RecoveryToolLaunch, String> {
    recovery_ui::restore_local(app, bundle, library)
}

#[tauri::command]
async fn launch_recovery_restore_cloud(
    app: tauri::AppHandle,
    bundle: PathBuf,
    item_id: String,
) -> Result<recovery_ui::RecoveryToolLaunch, String> {
    tauri::async_runtime::spawn_blocking(move || recovery_ui::restore_cloud(app, bundle, item_id))
        .await
        .map_err(|error| format!("Cloud recovery preparation failed: {error}"))?
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let startup_package = package_argument(std::env::args_os());
    let mut builder = tauri::Builder::default();
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(
            |app, arguments, _working_directory| {
                if let Some(path) = package_argument(arguments) {
                    let _ = app.emit("library-import-package", path);
                }
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            },
        ));
    }
    #[cfg(target_os = "macos")]
    {
        builder = builder.menu(desktop_menu::build);
    }
    builder
        .manage(PlayerState::default())
        .manage(CloudState::default())
        .manage(ProcessingState::default())
        .manage(issues::IssueStateStore::default())
        .manage(model_installer::ModelInstallerState::default())
        .manage(tasks::TaskStateStore::default())
        .manage(local_clip::LocalClipState::default())
        .manage(StartupPackage(Mutex::new(startup_package)))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let data = app.path().app_data_dir()?;
            let mut catalog = Catalog::load(&data).map_err(std::io::Error::other)?;
            if runtime::model_files_present_hint().is_err()
                && catalog.migrate_legacy_runtime_failures_to_setup_required() > 0
            {
                catalog.save().map_err(std::io::Error::other)?;
            }
            let setup_required = !catalog.setup_required_items().is_empty();
            let durable_processing_items = catalog
                .items()
                .iter()
                .filter(|item| item.processing_task_evidence.is_some())
                .cloned()
                .collect::<Vec<_>>();
            app.manage(CatalogState(Mutex::new(catalog)));
            processing::restore_durable_tasks(app.handle(), &data, &durable_processing_items);
            if setup_required {
                issues::report(
                    app.handle(),
                    issues::models_missing_issue(
                        "One or more songs are waiting for verified processing models.",
                        model_installer::install_is_allowed(),
                    ),
                );
            }
            Ok(())
        })
        .register_uri_scheme_protocol("lrailmedia", media_protocol)
        .register_uri_scheme_protocol("clippreview", local_clip::preview_protocol)
        .invoke_handler(tauri::generate_handler![
            player_status,
            catalog_snapshot,
            search_library,
            item_lyrics,
            system_issues,
            task_runtime_snapshot,
            task_output_snapshot,
            task_record,
            cancel_task,
            dismiss_system_issue,
            install_processing_models,
            cancel_model_install,
            add_local_files,
            add_local_folder,
            prepare_local_clip,
            cancel_local_clip,
            commit_local_clip,
            rescan_local_sources,
            remove_library_source,
            provide_lyrics_file,
            provide_lyrics_text,
            retry_processing_item,
            cancel_processing_item,
            connect_google_drive,
            rescan_google_drive,
            disconnect_google_drive,
            open_library_item,
            load_item_thumbnail,
            revise_item_lyrics,
            close_package,
            set_playback_active,
            take_startup_package,
            launch_recovery_export,
            launch_recovery_restore_local,
            launch_recovery_restore_cloud,
        ])
        .run(tauri::generate_context!())
        .expect("error while running LyricRail");
}

#[cfg(test)]
mod tests {
    use super::{
        bind_retry_lyrics_path, drive_download_task_id, is_supported_original_reference,
        parse_karaoke_presentation, parse_single_range, validate_presentation_asset_contract,
    };
    use crate::catalog::{
        CatalogItem, ItemLocation, ItemStatus, MediaOrigin, ProcessingEvidenceStatus,
        ProcessingTaskEvidence,
    };
    use crate::range_cache::RemoteObject;
    use std::path::PathBuf;

    fn presentation_fixture() -> serde_json::Value {
        serde_json::json!({
            "referenceResolution": [1920, 1080],
            "layout": {
                "lineMode": "alternating-two-lines",
                "alignment": "top-left-bottom-right",
                "bottomMargin": 84,
                "lineGap": 28,
                "safeAreaPercent": 3.5,
                "maximumLineWidthPercent": 93
            },
            "font": {
                "family": "Be Vietnam Pro Bold",
                "bold": false,
                "sizeAt1080p": 134,
                "scaleX": 96,
                "scaleY": 100,
                "letterSpacing": 0
            },
            "roleChangeCue": {
                "enabled": true,
                "dotCount": 3,
                "dotFontSizeAt1080p": 82
            },
            "unsung": {
                "fill": "#ffffff",
                "outerOutline": "#000000",
                "outerOutlineWidth": 4.5,
                "shadow": "#000000",
                "shadowOffset": 2
            },
            "sung": {
                "direction": "left-to-right",
                "timing": "syllable",
                "innerOutline": "#ffffff",
                "innerOutlineWidth": 4.5,
                "colors": {
                    "male": "#153cff",
                    "female": "#f02a2a",
                    "duet": "#ff3d9d"
                }
            }
        })
    }

    #[test]
    fn package_presentation_is_typed_bounded_and_color_normalized() {
        assert!(
            validate_presentation_asset_contract("presentation-template", "application/json")
                .is_ok()
        );
        assert!(
            validate_presentation_asset_contract("lyrics-render-plan", "application/json").is_err()
        );
        assert!(
            validate_presentation_asset_contract("presentation-template", "text/plain").is_err()
        );
        let presentation = parse_karaoke_presentation(presentation_fixture()).unwrap();
        assert_eq!(presentation.reference_resolution, [1920, 1080]);
        assert_eq!(presentation.layout.alignment, "top-left-bottom-right");
        assert_eq!(presentation.font.size_at_1080p, 134.0);
        assert_eq!(presentation.role_change_cue.dot_count, 3);
        assert_eq!(presentation.sung.colors.male, "#153CFF");
        assert_eq!(presentation.sung.colors.female, "#F02A2A");
        assert_eq!(presentation.sung.colors.duet, "#FF3D9D");

        for (pointer, hostile) in [
            ("/layout/alignment", serde_json::json!("center")),
            ("/layout/bottomMargin", serde_json::json!(10000)),
            ("/font/sizeAt1080p", serde_json::json!(-1)),
            ("/font/family", serde_json::json!("unsafe; url")),
            ("/roleChangeCue/dotCount", serde_json::json!(0)),
            ("/sung/direction", serde_json::json!("right-to-left")),
            ("/sung/colors/female", serde_json::json!("url(unsafe)")),
        ] {
            let mut value = presentation_fixture();
            *value.pointer_mut(pointer).unwrap() = hostile;
            assert!(parse_karaoke_presentation(value).is_err(), "{pointer}");
        }
    }

    #[test]
    fn retry_rebinds_lyrics_without_clearing_authenticated_job_identity() {
        let item = CatalogItem {
            id: "local-item".into(),
            package_id: None,
            title: "Exact title".into(),
            artist: None,
            composer: None,
            first_lyric_line: Some("Exact lyric".into()),
            lyric_text: "Exact lyric\n".into(),
            status: ItemStatus::Failed,
            progress_percent: 58.06,
            status_message: Some("Independent stage failure".into()),
            processing_job_id: Some("authenticated-job-id".into()),
            processing_task_evidence: Some(ProcessingTaskEvidence {
                job_id: Some("authenticated-job-id".into()),
                status: ProcessingEvidenceStatus::Failed,
                progress_percent: 58.06,
                stage_key: Some("classify_roles".into()),
                stage_title: Some("Classify vocal roles".into()),
                stage_progress_percent: Some(35.0),
                started_at_millis: 1,
                updated_at_millis: 2,
                finished_at_millis: Some(3),
            }),
            has_thumbnail: false,
            locations: vec![ItemLocation::LocalMedia {
                source_id: Some("source".into()),
                path: PathBuf::from("source.mp4"),
                lyrics_path: Some(PathBuf::from("missing.txt")),
                origin: MediaOrigin::Disk,
                trim_start_millis: Some(185_000),
                trim_end_millis: Some(391_000),
                available: true,
            }],
        };

        let rebound = bind_retry_lyrics_path(item, PathBuf::from("retry.txt")).unwrap();
        assert_eq!(
            rebound.processing_job_id.as_deref(),
            Some("authenticated-job-id")
        );
        assert_eq!(
            rebound
                .processing_task_evidence
                .as_ref()
                .and_then(|evidence| evidence.job_id.as_deref()),
            Some("authenticated-job-id")
        );
        assert_eq!(rebound.lyric_text, "Exact lyric\n");
        assert!(matches!(
            &rebound.locations[0],
            ItemLocation::LocalMedia {
                path,
                lyrics_path: Some(lyrics),
                trim_start_millis: Some(185_000),
                trim_end_millis: Some(391_000),
                ..
            } if path == &PathBuf::from("source.mp4") && lyrics == &PathBuf::from("retry.txt")
        ));
    }

    #[test]
    fn media_ranges_are_single_and_capped() {
        assert_eq!(parse_single_range("bytes=10-19", 100), Ok((10, 19)));
        assert_eq!(parse_single_range("bytes=-10", 100), Ok((90, 99)));
        assert!(parse_single_range("bytes=10-19,30-39", 100).is_err());
        let (_, end) = parse_single_range("bytes=0-9999999", 10_000_000).unwrap();
        assert_eq!(end, 2 * 1024 * 1024 - 1);
    }

    #[test]
    fn drive_download_task_identity_is_stable_per_object_version() {
        let object = RemoteObject {
            cache_key: "drive-file".into(),
            length: 42,
            version: "v1".into(),
        };
        assert_eq!(
            drive_download_task_id(&object),
            drive_download_task_id(&object)
        );
        assert_ne!(
            drive_download_task_id(&object),
            drive_download_task_id(&RemoteObject {
                version: "v2".into(),
                ..object
            })
        );
    }

    #[test]
    fn original_track_contract_is_exact() {
        assert!(is_supported_original_reference(
            "audio/original-reference.m4a",
            "audio/mp4",
            "playback-audio",
            false,
        ));
        assert!(!is_supported_original_reference(
            "audio/original-reference.m4a",
            "audio/mp4",
            "playback-audio",
            true,
        ));
    }
}
