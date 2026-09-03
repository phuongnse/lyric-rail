from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_CLIP_PATH = ROOT / "apps" / "player" / "src-tauri" / "src" / "local_clip.rs"
LOCAL_CLIP = LOCAL_CLIP_PATH.read_text(encoding="utf-8")
LOCAL_CLIP_RUNTIME = LOCAL_CLIP.split("#[cfg(test)]", 1)[0]
CATALOG = (ROOT / "apps" / "player" / "src-tauri" / "src" / "catalog.rs").read_text(
    encoding="utf-8"
)
PROCESSING = (
    ROOT / "apps" / "player" / "src-tauri" / "src" / "processing.rs"
).read_text(encoding="utf-8")
LIB = (ROOT / "apps" / "player" / "src-tauri" / "src" / "lib.rs").read_text(
    encoding="utf-8"
)
APP = (ROOT / "apps" / "player" / "src" / "App.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "apps" / "player" / "src" / "App.css").read_text(encoding="utf-8")
SELECTION = (ROOT / "apps" / "player" / "src" / "clipSelection.ts").read_text(
    encoding="utf-8"
)
PYTHON_SOURCE = (ROOT / "src" / "lyricrail" / "source.py").read_text(
    encoding="utf-8"
)
PYTHON_WORKER = (ROOT / "src" / "lyricrail" / "__main__.py").read_text(
    encoding="utf-8"
)
TAURI = json.loads(
    (ROOT / "apps" / "player" / "src-tauri" / "tauri.conf.json").read_text(
        encoding="utf-8"
    )
)


def test_removed_remote_import_surface_cannot_be_reached() -> None:
    assert not (
        ROOT / "apps" / "player" / "src-tauri" / "src" / "url_import.rs"
    ).exists()
    assert not (ROOT / "apps" / "player" / "src" / "urlImport.ts").exists()
    for text in (APP, LIB):
        for removed in (
            "library.open-url",
            "prepare_url_import",
            "cancel_url_import",
            "commit_url_import",
            "url-import-progress",
            "urlpreview",
        ):
            assert removed not in text
    assert ">URL</button>" not in APP
    assert "Import a URL" not in APP


def test_single_local_media_opens_clip_editor_while_other_file_flows_stay_direct() -> None:
    for text in (
        "shouldOpenClipEditor(paths)",
        'invoke<LocalClipPreview>("prepare_local_clip"',
        'invoke("cancel_local_clip"',
        'invoke<CatalogSnapshot>("commit_local_clip"',
        "Set at playhead",
        "−1 frame",
        "+1 frame",
        "Loop selection",
        "Add whole file",
        "Add selected clip",
    ):
        assert text in APP
    assert "paths.length === 1" in SELECTION
    assert 'MEDIA_EXTENSIONS.has(extension(paths[0]))' in SELECTION
    assert '"lrail"' not in SELECTION.split("MEDIA_EXTENSIONS", 1)[1].split("]);", 1)[0]
    assert ".source-actions { display: grid; grid-template-columns: repeat(2, 1fr)" in CSS
    assert 'aria-label="Local sources"' in APP
    assert 'aria-label="Cloud providers"' in APP
    assert ".clip-dialog" in CSS and ".clip-range-grid" in CSS


def test_native_clip_probe_is_local_bounded_and_source_preserving() -> None:
    for text in (
        "fs::symlink_metadata(path)",
        "original.file_type().is_symlink()",
        ".canonicalize()",
        "const MAX_MEDIA_BYTES: u64 = 8 * 1024 * 1024 * 1024",
        'const SAFE_INPUT_PROTOCOLS: &str = "file"',
        "SAFE_INPUT_FORMATS",
        "MAX_PROBE_BYTES",
        "Duration::from_secs(20)",
        "resolve_media_tools()",
        "open_source_guard(&path)",
        "source_identity(&source_file)",
        "verify_source_unchanged(&source_file",
        "portable_preview_with_report(",
        '"pcm_u8"',
        '"aresample=16000:async=1:first_pts=0,apad,atrim=end=',
        "expected_data_bytes = duration_millis",
        "tempfile::tempfile_in(preview_root)",
        "clipped_local_media_item_from_verified_path(path",
    ):
        assert text in LOCAL_CLIP_RUNTIME
    for forbidden in (
        "reqwest",
        "HttpResponse",
        "NamedTempFile",
        "TempFileBuilder",
        "fs::write",
        "fs::copy",
        "fs::rename",
        "fs::remove_file",
        "File::create",
        "persist_noclobber",
    ):
        assert forbidden not in LOCAL_CLIP_RUNTIME
    assert "preview_identity_rejects_same_size_in_place_changes" in LOCAL_CLIP
    assert "preview_identity_rejects_path_replacement" in LOCAL_CLIP
    assert "wma_source_gets_an_anonymous_pcm_wav_preview" in LOCAL_CLIP
    assert (
        "portable_preview_preserves_delayed_and_short_audio_on_the_source_timeline"
        in LOCAL_CLIP
    )


def test_clip_preview_is_opaque_main_only_and_range_bounded() -> None:
    for text in (
        'context.webview_label() != "main"',
        "request.method() != Method::GET && request.method() != Method::HEAD",
        "validate_clip_id(clip_id)",
        "parse_single_range(value, length)",
        "read_exact_at(source, &mut bytes, start)",
        "2 * 1024 * 1024 - 1",
        "header::CONTENT_RANGE",
        'header::CACHE_CONTROL, "no-store"',
        '.register_uri_scheme_protocol("clippreview", local_clip::preview_protocol)',
    ):
        assert text in LOCAL_CLIP or text in LIB
    csp = TAURI["app"]["security"]["csp"]
    assert "clippreview:" in csp and "http://clippreview.localhost" in csp
    assert "https:" not in csp.split("media-src", 1)[1].split(";", 1)[0]
    assert "https:" not in csp.split("connect-src", 1)[1].split(";", 1)[0]
    assert "concurrent_preview_ranges_do_not_share_a_cursor" in LOCAL_CLIP
    assert "lightweight PCM audio preview" in APP


def test_local_clip_trim_reuses_the_existing_catalog_and_worker_contract() -> None:
    assert "const CATALOG_SCHEMA: u16 = 3" in CATALOG
    assert "trim_start_millis: Option<u64>" in CATALOG
    assert "trim_end_millis: Option<u64>" in CATALOG
    assert "is_trim_metadata_downgrade" in CATALOG
    assert "preserve_local_media_content" in CATALOG
    assert "disk_rescan_cannot_downgrade_local_clip_title_or_trim" in CATALOG
    assert "start_seconds: Option<f64>" in PROCESSING
    assert "end_seconds: Option<f64>" in PROCESSING
    assert "trim_start_millis.map(|value| value as f64 / 1000.0)" in PROCESSING
    assert 'start=request.get("startSeconds")' in PYTHON_WORKER
    assert 'end=request.get("endSeconds")' in PYTHON_WORKER
    assert 'if value and "://" in value:' in PYTHON_SOURCE
    assert 'raise ValueError("Karaoke processing accepts local disk media only.")' in PYTHON_SOURCE


def test_product_documents_describe_only_local_file_clipping() -> None:
    documents = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "README.md",
            "apps/player/README.md",
            "docs/CLI.md",
            "docs/PLATFORM_ARCHITECTURE.md",
            "docs/PRODUCTION_ACCEPTANCE.md",
            "docs/SECURITY_ACCEPTANCE.md",
            "docs/THREAT_MODEL.md",
        )
    )
    for phrase in (
        "Clip Editor",
        "regular local media",
        "Start/End",
        "opaque",
        "source",
        "unchanged",
    ):
        assert phrase.lower() in documents.lower()
    for removed in ("URL acquisition", "direct HTTPS", "provider scraper"):
        assert removed.lower() not in documents.lower()
