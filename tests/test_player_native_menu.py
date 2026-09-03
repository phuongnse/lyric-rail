from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUST = (ROOT / "apps/player/src-tauri/src/desktop_menu.rs").read_text(encoding="utf-8")
LIB = (ROOT / "apps/player/src-tauri/src/lib.rs").read_text(encoding="utf-8")
APP = (ROOT / "apps/player/src/App.tsx").read_text(encoding="utf-8")
COMMANDS = (ROOT / "apps/player/src/commands.ts").read_text(encoding="utf-8")


def test_visible_native_menu_is_mac_application_convention_only() -> None:
    assert '#[cfg(target_os = "macos")]\nmod desktop_menu;' in LIB
    assert '#[cfg(target_os = "macos")]\n    {' in LIB
    assert "builder = builder.menu(desktop_menu::build);" in LIB
    assert "#[cfg(desktop)]\nmod desktop_menu;" not in LIB
    assert "on_menu_event" not in LIB
    assert "PLAYER_MENU_EVENT" not in LIB
    assert 'SubmenuBuilder::new(app, "LyricRail")' in RUST
    for native_convention in ("about_with_text", ".services()", ".hide()", ".quit()"):
        assert native_convention in RUST
    for duplicated_group in ('"&File"', '"&Library"', '"&Playback"', '"&View"'):
        assert duplicated_group not in RUST


def test_each_visible_action_has_one_contextual_home() -> None:
    assert APP.count(">Files</button>") == 1
    assert APP.count(">Folder</button>") == 1
    assert APP.count(">Google Drive</button>") == 1
    assert APP.count(">Local</button>") == 1
    assert APP.count(">Cloud</button>") == 1
    assert APP.count(">Drive</button>") == 0
    assert APP.count("Activity {") == 1
    assert APP.count(">About LyricRail</button>") == 1
    assert 'status?.platform === "windows" || status?.platform === "linux"' in APP
    assert 'className="source-actions"' in APP
    assert 'className="track-toggle"' in APP
    assert 'className="main-controls"' in APP
    assert 'className="right-controls"' in APP
    assert "PLAYER_MENU_ACTIONS" not in APP


def test_shortcuts_use_a_non_rendering_shared_command_registry() -> None:
    for command in (
        "open-files",
        "open-folder",
        "toggle-library",
        "rescan-library",
        "play-pause",
        "toggle-fullscreen",
    ):
        assert f'"{command}"' in COMMANDS
    assert "dispatchCommand(event, handlers)" in APP
    assert "event.preventDefault()" in COMMANDS
    assert "isEditableTarget(event.target)" in COMMANDS
