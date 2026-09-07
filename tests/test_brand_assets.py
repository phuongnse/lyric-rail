from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import tomllib
import xml.etree.ElementTree as ET

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "brand" / "lyricrail-mark.svg"
MANIFEST = ROOT / "assets" / "brand" / "generated-icons.json"
ICONS = ROOT / "apps" / "player" / "src-tauri" / "icons"

EXPECTED_PNG_DIMENSIONS = {
    "32x32.png": (32, 32),
    "64x64.png": (64, 64),
    "128x128.png": (128, 128),
    "128x128@2x.png": (256, 256),
    "icon.png": (512, 512),
    "Square30x30Logo.png": (30, 30),
    "Square44x44Logo.png": (44, 44),
    "Square71x71Logo.png": (71, 71),
    "Square89x89Logo.png": (89, 89),
    "Square107x107Logo.png": (107, 107),
    "Square142x142Logo.png": (142, 142),
    "Square150x150Logo.png": (150, 150),
    "Square284x284Logo.png": (284, 284),
    "Square310x310Logo.png": (310, 310),
    "StoreLogo.png": (50, 50),
}
EXPECTED_OUTPUTS = {
    *(f"apps/player/src-tauri/icons/{name}" for name in EXPECTED_PNG_DIMENSIONS),
    "apps/player/src-tauri/icons/icon.ico",
    "apps/player/src-tauri/icons/icon.icns",
}
DEFAULT_TAURI_HASHES = {
    "273cd669e07c455ad1c7c095890a37984652157cee73128a867300067dfb80e7",
    "1c6782dc65c8111c12cbc1882a0fea5e71ab8e51b18da2ce9580f5c88860ed02",
    "392206b573a809997f3ff16fe68f456a52e931c372107eade9572b329bbe3321",
    "3dc10493b7de48a61de58f768f8a5708d3a44a068c148cedf0502b9b9b71ba5d",
}


def file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_brand_master_is_flat_original_vector_geometry() -> None:
    root = ET.fromstring(SOURCE.read_text(encoding="utf-8"))
    assert root.attrib["viewBox"] == "0 0 64 64"
    assert root.attrib["width"] == root.attrib["height"] == "1024"
    tags = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}
    assert not tags.intersection(
        {"image", "text", "linearGradient", "radialGradient", "filter", "style"}
    )
    fills = {
        value.upper()
        for element in root.iter()
        if (value := element.attrib.get("fill", "")).startswith("#")
    }
    assert fills == {"#0B0E15", "#FFCC4D", "#5BD8D2"}
    assert not any(element.tag.endswith("rect") for element in root)
    assert sum(1 for element in root.iter() if element.tag.endswith("use")) == 4
    rows = {
        element.attrib["id"]: element
        for element in root.iter()
        if element.attrib.get("id") in {"top-row", "bottom-row"}
    }
    assert set(rows) == {"top-row", "bottom-row"}
    assert all(sum(1 for child in row if child.tag.endswith("rect")) == 3 for row in rows.values())
    rect_keys = ("x", "y", "width", "height", "rx")
    assert [
        {key: child.attrib[key] for key in rect_keys}
        for child in rows["top-row"]
    ] == [
        {"x": "9", "y": "16", "width": "16", "height": "12", "rx": "6"},
        {"x": "27", "y": "16", "width": "16", "height": "12", "rx": "6"},
        {"x": "45", "y": "16", "width": "10", "height": "12", "rx": "5"},
    ]
    assert [
        {key: child.attrib[key] for key in rect_keys}
        for child in rows["bottom-row"]
    ] == [
        {"x": "9", "y": "36", "width": "12", "height": "12", "rx": "6"},
        {"x": "23", "y": "36", "width": "20", "height": "12", "rx": "6"},
        {"x": "45", "y": "36", "width": "10", "height": "12", "rx": "5"},
    ]
    clips = {
        element.attrib["id"]: next(iter(element)).attrib["d"]
        for element in root.iter()
        if element.tag.endswith("clipPath")
    }
    assert clips == {
        "before-playhead": "M0 0H52L20 64H0Z",
        "after-playhead": "M52 0H64V64H20Z",
    }
    playhead = next(
        element for element in root.iter() if element.attrib.get("id") == "playhead-gap"
    )
    assert playhead.attrib == {
        "id": "playhead-gap",
        "d": "M44 10H50L28 54H22Z",
        "fill": "#0B0E15",
    }


def test_generated_icon_manifest_binds_every_desktop_output() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 1
    assert manifest["source"] == "assets/brand/lyricrail-mark.svg"
    assert manifest["sourceSha256"] == file_digest(SOURCE)
    assert manifest["generator"] == {
        "command": "npm run brand:icons",
        "package": "@tauri-apps/cli",
        "version": "2.11.4",
    }
    assert set(manifest["outputs"]) == EXPECTED_OUTPUTS
    for relative_path, expected_digest in manifest["outputs"].items():
        output = ROOT / relative_path
        assert expected_digest == file_digest(output)
        assert expected_digest not in DEFAULT_TAURI_HASHES


def test_generated_pngs_have_expected_dimensions_alpha_and_brand_colors() -> None:
    for name, dimensions in EXPECTED_PNG_DIMENSIONS.items():
        with Image.open(ICONS / name) as image:
            assert image.size == dimensions
            assert image.mode == "RGBA"
            assert image.getchannel("A").getextrema() == (0, 255)
    with Image.open(ICONS / "32x32.png") as small:
        opaque = [
            pixel[:3] for pixel in small.get_flattened_data() if pixel[3] > 220
        ]
    assert any(red > 220 and green > 150 and blue < 120 for red, green, blue in opaque)
    assert any(red < 130 and green > 170 and blue > 170 for red, green, blue in opaque)
    assert any(red < 35 and green < 35 and blue < 45 for red, green, blue in opaque)
    with Image.open(ICONS / "icon.png") as icon:
        assert icon.getpixel((32, 256))[3] == 0


def test_role_split_has_balanced_clean_color_regions_without_slivers() -> None:
    expected = {
        (17, 22): (255, 204, 77),
        (30, 22): (255, 204, 77),
        (37, 22): (255, 204, 77),
        (41, 22): (11, 14, 21),
        (50, 22): (91, 216, 210),
        (15, 42): (91, 216, 210),
        (27, 42): (91, 216, 210),
        (31, 42): (11, 14, 21),
        (37, 42): (255, 204, 77),
        (49, 42): (255, 204, 77),
    }
    with Image.open(ICONS / "icon.png") as image:
        for (unit_x, unit_y), color in expected.items():
            pixel = image.getpixel((round(unit_x * 8), round(unit_y * 8)))[:3]
            assert all(abs(actual - target) <= 2 for actual, target in zip(pixel, color))


def test_icon_containers_are_valid_and_icns_chunks_are_canonical() -> None:
    with Image.open(ICONS / "icon.ico") as icon:
        assert icon.format == "ICO"
        assert (256, 256) in icon.info["sizes"]
        rgba = icon.convert("RGBA")
        assert rgba.getpixel((16, 128))[3] == 0
    with Image.open(ICONS / "icon.icns") as icon:
        assert icon.format == "ICNS"
        assert icon.size == (1024, 1024)
        rgba = icon.convert("RGBA")
        assert rgba.getpixel((64, 512))[3] == 0
    data = (ICONS / "icon.icns").read_bytes()
    assert data[:4] == b"icns"
    assert int.from_bytes(data[4:8], "big") == len(data)
    chunk_types: list[bytes] = []
    offset = 8
    while offset < len(data):
        length = int.from_bytes(data[offset + 4 : offset + 8], "big")
        assert length >= 8
        chunk_types.append(data[offset : offset + 4])
        offset += length
    assert offset == len(data)
    assert chunk_types == sorted(chunk_types)


def test_player_and_bundle_use_only_the_canonical_brand_source() -> None:
    app = (ROOT / "apps" / "player" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "apps" / "player" / "src" / "App.css").read_text(encoding="utf-8")
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    tauri = json.loads(
        (ROOT / "apps" / "player" / "src-tauri" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    assert 'import lyricRailMark from "../../../assets/brand/lyricrail-mark.svg"' in app
    assert 'className="brand" aria-label="LyricRail"' in app
    assert app.count("src={lyricRailMark}") == 3
    assert 'className="empty-brand-lockup"' in app
    assert app.count("<strong>LyricRail</strong>") == 2
    assert ".brand-mark" in css and ".empty-brand-mark" in css
    assert ".brand strong { display: none; }" not in css
    assert "tauri.svg" not in app and "src-tauri/icons/icon.png" not in app
    assert package["scripts"]["brand:icons"] == "node scripts/generate_brand_icons.mjs"
    configured = {
        f"apps/player/src-tauri/{path}" for path in tauri["bundle"]["icon"]
    }
    assert configured.issubset(EXPECTED_OUTPUTS)
    build_script = (
        ROOT / "apps" / "player" / "src-tauri" / "build.rs"
    ).read_text(encoding="utf-8")
    inputs = re.search(
        r"const DESKTOP_ICON_INPUTS:.*?= &\[(.*?)\];",
        build_script,
        flags=re.DOTALL,
    )
    assert inputs is not None
    declared_inputs = set(re.findall(r'"([^"]+)"', inputs.group(1)))
    assert declared_inputs == set(tauri["bundle"]["icon"])
    assert 'println!("cargo:rerun-if-changed={icon}")' in build_script
    assert not (ICONS / "android").exists()
    assert not (ICONS / "ios").exists()


def test_clean_development_extras_declare_brand_test_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    optional = project["project"]["optional-dependencies"]
    for extra in ("dev", "bootstrap-common"):
        assert any(requirement.startswith("pillow>=") for requirement in optional[extra])
