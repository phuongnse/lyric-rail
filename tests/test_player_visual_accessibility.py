from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "apps" / "player" / "src" / "App.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "apps" / "player" / "src" / "App.css").read_text(encoding="utf-8")
MEDIA_CONTROLS = (ROOT / "apps" / "player" / "src" / "mediaControls.css").read_text(
    encoding="utf-8"
)
ICONS = (ROOT / "apps" / "player" / "src" / "Icon.tsx").read_text(encoding="utf-8")
FOCUS = (ROOT / "apps" / "player" / "src" / "focus.ts").read_text(encoding="utf-8")
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
    assert 'className="topbar"' not in APP
    assert ".topbar" not in CSS
    assert "player-context" in APP
    assert 'icon="menu"' in APP
    assert 'id="player-application-menu"' in APP
    assert 'className="empty-stage"' in APP
    assert ".empty-stage" in CSS
    assert "Your karaoke, one click away." not in APP
    assert "Ready to sing" not in APP
    assert ".empty-stage button" in CSS
    assert ".player-context { position: absolute; z-index: 24; inset: 14px 14px auto; display: flex; width: calc(100% - 28px); align-items: center;" in CSS
    assert ".player-context.is-visible { opacity: 1; pointer-events: auto; transform: translateY(0); }" in CSS
    assert ".now-playing { display: inline-flex; align-items: center; height: 44px; box-sizing: border-box; width: fit-content;" in CSS
    assert ".player-menu-toggle { width: 44px; height: 44px;" in CSS
    assert ".video-stage { position: relative; min-height: 0; overflow: hidden; border: 0; border-radius: 0;" in CSS
    assert "box-shadow: none; background: #030407;" in CSS
    assert ".player-area {\n  display: grid;\n  min-height: 0;\n  grid-template-rows: minmax(0, 1fr);\n}" in CSS
    assert 'className="media-control-overlay player-controls"' in APP
    assert 'icon={volume <= 0.001 ? "volume-muted" : "volume-high"}' in APP
    assert 'icon="music"' in APP
    player_controls = APP.split('className="media-control-overlay player-controls"', 1)[1].split(
        "</section>", 1
    )[0]
    assert 'icon="search"' not in player_controls


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
    assert 'document.addEventListener("mousedown", dismissTooltip, true)' in ICONS
    assert 'document.addEventListener("click", dismissTooltip, true)' in ICONS
    assert 'document.addEventListener("keydown", dismissTooltip, true)' in ICONS
    assert "consumeFocusRestoration" in ICONS
    assert "const focusRestorationTargets = new WeakSet<HTMLElement>()" in FOCUS
    assert "markFocusRestoration(restore)" in FOCUS
    assert "focusRestorationTargets.delete(target)" in FOCUS
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
    assert ".media-control-overlay .media-control-primary {" in MEDIA_CONTROLS
    assert "opacity: 0" in MEDIA_CONTROLS
    assert "pointer-events: none" in MEDIA_CONTROLS
    assert ".media-player-frame:focus-within .media-control-overlay" in MEDIA_CONTROLS
    assert ".player-controls .media-control-row > .media-control-group:first-child { grid-column: 1 / -1; }" in MEDIA_CONTROLS
    assert ".player-controls .media-control-row > .media-control-group.end { grid-column: 2; justify-content: flex-end; }" in MEDIA_CONTROLS
    assert 'iconSize={22}' in APP
    assert 'iconSize={21}' in APP
    assert 'aria-label="Volume"' in APP
    assert ".player-menu { position: absolute" in CSS
    assert ".player-menu-action:focus-visible" in CSS
    assert "inset: 0" in CSS


def test_player_drawers_keep_exact_viewport_geometry_across_layout_modes() -> None:
    assert re.search(r"\.drawer-scrim \{[^}]*position: fixed;[^}]*inset: 0;", CSS)
    assert re.search(r"\.library-drawer \{[^}]*position: fixed;[^}]*top: 0;[^}]*bottom: 0;", CSS)
    assert re.search(r"\.issues-drawer \{[^}]*position: fixed;[^}]*top: 0;[^}]*bottom: 0;", CSS)
    assert "--drawer-width: clamp(360px, 42vw, 560px);" in CSS
    assert ".library-drawer { position: fixed;" in CSS and "width: var(--drawer-width);" in CSS
    assert ".issues-drawer { position: fixed;" in CSS and "width: var(--drawer-width);" in CSS
    assert ".library-drawer, .issues-drawer { top: auto; left: 0; width: 100%; height: min(80vh, 720px);" in CSS
    assert ".app-shell:fullscreen .library-drawer { top: 0; }" in CSS
    assert ".app-shell:fullscreen .drawer-scrim { inset: 0; }" in CSS
    assert ".app-shell:fullscreen .issues-drawer { top: 0; }" in CSS
    assert ".app-shell:fullscreen .issues-scrim { inset: 0; }" in CSS
    narrow = CSS.split("@media (max-width: 760px) {", 1)[1]
    assert ".library-drawer, .issues-drawer { top: auto;" in narrow
    assert ".drawer-scrim { inset: 0; }" in narrow
    assert ".issues-scrim { inset: 0; }" in narrow


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


def test_player_honors_authenticated_karaoke_layout_palette_and_cues() -> None:
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
        "presentation.layout.bottomMargin",
        "presentation.layout.lineGap",
        "presentation.layout.safeAreaPercent",
        "viewBox={`0 0 ${referenceWidth} ${referenceHeight}`}",
        'preserveAspectRatio="xMidYMid meet"',
        "<foreignObject",
        'className="lyric-canvas"',
        "event.slot === \"top\"",
        "event.showRoleCue",
        "event.roleCueReason",
        "cueDotInterval(event, dotIndex, cueCount)",
        "paginateLyricEvent(event, presentation, measureText)",
        "requestAnimationFrame(update)",
    ):
        assert token in LYRICS
    for selector in (
        ".lyric-line.top",
        ".lyric-line.bottom",
        ".lyric-stack",
        ".lyric-row",
        ".lyric-token-outline",
        ".lyric-token-shadow",
        ".lyric-cue-dot > .lyric-token-shadow",
        ".lyric-cue {",
    ):
        assert selector in CSS
    assert "justify-content: flex-start; text-align: left" in CSS
    assert "justify-content: flex-end; text-align: right" in CSS
    assert "container-type: size" not in CSS
    assert "cqh" not in LYRICS
    assert "144px" not in CSS
    assert "font-size: var(--lyric-base-font-size)" in CSS
    assert ".lyric-line.top { bottom: calc(var(--lyric-bottom-slot-height) + var(--lyric-bottom) + var(--lyric-outer-width) / 2)" in CSS
    assert ".lyric-line.bottom { bottom: 0" in CSS
    assert "width: var(--fill)" in CSS
    assert ".lyric-cue-fill::before" in CSS
    assert "opacity: var(--fill-ratio)" not in CSS
    assert "transform: scale(var(--lyric-scale-x)" not in CSS
    assert "shouldUpdateTransportClock(now, last)" in APP
    assert "now - last >= 33" not in APP
    assert "var(--lyric-inner-width) var(--lyric-inner)" in CSS
    assert "var(--lyric-outer-width) var(--lyric-outer)" in CSS
    assert "#6cb9ff" not in CSS
    assert "#ff83c5" not in CSS
