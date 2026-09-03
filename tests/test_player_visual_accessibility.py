from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "apps" / "player" / "src" / "App.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "apps" / "player" / "src" / "App.css").read_text(encoding="utf-8")
ICONS = (ROOT / "apps" / "player" / "src" / "Icon.tsx").read_text(encoding="utf-8")
LYRICS = (ROOT / "apps" / "player" / "src" / "LyricOverlay.tsx").read_text(
    encoding="utf-8"
)


def test_player_uses_repository_owned_svg_icons_without_placeholder_glyphs() -> None:
    package = json.loads(
        (ROOT / "apps" / "player" / "package.json").read_text(encoding="utf-8")
    )
    assert 'from "./Icon"' in APP
    assert "<svg" in ICONS
    assert not any(
        token in APP for token in ("⌕", "↻", "▶", "Ⅱ", "‹", "›", "⤨", "⛶", "✎", "＋")
    )
    assert not any("icon" in dependency.lower() for dependency in package["dependencies"])
    right_controls = APP.split('<div className="right-controls">', 1)[1].split(
        "</div>", 1
    )[0]
    assert 'icon={volume <= 0.001 ? "volume-muted" : "volume-high"}' in right_controls
    assert 'icon="search"' not in right_controls


def test_every_icon_only_button_gets_matching_aria_and_tooltip_help() -> None:
    tags = re.findall(r"<IconButton\b.*?\s/>", APP, flags=re.DOTALL)
    assert len(tags) >= 13
    for tag in tags:
        assert " icon=" in tag
        assert " label=" in tag
    assert "aria-label={label}" in ICONS
    assert "title={label}" not in ICONS
    assert "data-tooltip" not in ICONS
    assert "tooltipSide" not in ICONS
    assert "createPortal" in ICONS
    assert 'role="tooltip"' in ICONS
    assert "onMouseEnter" in ICONS and "onFocus" in ICONS
    assert "useLayoutEffect" in ICONS
    assert "tooltipElement.getBoundingClientRect().width" in ICONS
    assert 'visibility: tooltip.measured ? "visible" : "hidden"' in ICONS
    assert 'window.addEventListener("resize", prepareTooltip)' in ICONS
    assert 'window.addEventListener("scroll", updateTooltip, true)' in ICONS
    assert ".icon-tooltip { position: fixed" in CSS
    assert "width: max-content" in CSS
    assert "overflow-wrap: anywhere" in CSS
    assert "word-break: normal" in CSS
    assert "white-space: normal" in CSS
    assert ".icon-control::after" not in CSS
    assert "title=" not in APP
    assert "tooltipSide=" not in APP


def test_tooltip_placement_policy_is_global_and_edge_aware() -> None:
    assert 'const side = rect.top < 104 ? "bottom" : "top"' in ICONS
    assert "center - boundedTooltipWidth / 2 < 12" in ICONS
    assert "center + boundedTooltipWidth / 2 > viewportWidth - 12" in ICONS
    assert "label ===" not in ICONS
    for placement in (
        ".icon-tooltip.top.center",
        ".icon-tooltip.top.left",
        ".icon-tooltip.top.right",
        ".icon-tooltip.bottom.center",
        ".icon-tooltip.bottom.left",
        ".icon-tooltip.bottom.right",
    ):
        assert placement in CSS


def test_type_and_transport_scale_stays_above_the_compact_floor() -> None:
    assert "--font-caption: 12px" in CSS
    assert "--font-small: 13px" in CSS
    assert "--font-ui: 14px" in CSS
    assert "--font-label: 15px" in CSS
    explicit_sizes = [
        int(value) for value in re.findall(r"font-size:\s*(\d+)px", CSS)
    ]
    assert explicit_sizes and min(explicit_sizes) >= 14
    assert ".main-controls .transport-play { width: 56px; height: 56px" in CSS
    assert ".main-controls .transport-skip { width: 44px; height: 44px" in CSS
    assert 'iconSize={24}' in APP
    assert 'iconSize={21}' in APP
    assert 'aria-label="Volume"' in APP


def test_fullscreen_icon_label_and_action_follow_live_state() -> None:
    playback = (ROOT / "apps" / "player" / "src" / "playback.ts").read_text(
        encoding="utf-8"
    )
    assert 'icon={fullscreen ? "fullscreen-exit" : "fullscreen"}' in APP
    assert 'label={fullscreen ? "Exit fullscreen" : "Enter fullscreen"}' in APP
    assert 'document.addEventListener("fullscreenchange", update)' in APP
    assert "toggleDocumentFullscreen" in APP
    assert "fullscreenDocument.exitFullscreen()" in playback
    assert "target.requestFullscreen()" in playback


def test_player_honors_authenticated_karaoke_presentation_and_cues() -> None:
    native = (ROOT / "apps/player/src-tauri/src/lib.rs").read_text(encoding="utf-8")
    assert 'from "./LyricOverlay"' in APP
    assert "presentation={opened.presentation}" in APP
    assert '"presentation/template.json"' in native
    assert "validate_presentation_asset_contract(&asset.kind, &asset.media_type)" in native
    assert "parse_karaoke_presentation" in native
    for token in (
        "presentation.sung.colors.male",
        "presentation.sung.colors.female",
        "presentation.sung.colors.duet",
        "presentation.unsung.fill",
        "presentation.font.sizeAt1080p",
        "presentation.layout.bottomMargin",
        "presentation.layout.lineGap",
        "presentation.layout.safeAreaPercent",
        "event.slot === \"top\"",
        "event.showRoleCue",
        "event.roleCueReason",
        "cueDotFill(event, time, dotIndex, cueCount)",
        'text="●"',
    ):
        assert token in LYRICS
    for selector in (
        ".lyric-line.top",
        ".lyric-line.bottom",
        ".lyric-token-outline",
        ".lyric-cue {",
    ):
        assert selector in CSS
    assert "justify-content: flex-start; text-align: left" in CSS
    assert "justify-content: flex-end; text-align: right" in CSS
    assert "container-type: size" in CSS
    assert "var(--lyric-line-font-size" in CSS
    assert "var(--lyric-inner-width) var(--lyric-inner)" in CSS
    assert "var(--lyric-outer-width) var(--lyric-outer)" in CSS
    assert "#6cb9ff" not in CSS
    assert "#ff83c5" not in CSS
