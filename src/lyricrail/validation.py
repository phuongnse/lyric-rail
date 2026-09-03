from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import load_project_config, resolve_data_root
from .model_provenance import verify_model_provenance


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    location: str
    message: str
    hint: str = ""


def _issue(
    issues: list[ValidationIssue],
    severity: str,
    code: str,
    location: str,
    message: str,
    hint: str = "",
) -> None:
    issues.append(ValidationIssue(severity, code, location, message, hint))


def _require_type(
    issues: list[ValidationIssue],
    value: Any,
    expected: type,
    location: str,
) -> bool:
    if not isinstance(value, expected):
        _issue(
            issues,
            "error",
            "CONFIG_TYPE",
            location,
            f"Expected {expected.__name__}, received {type(value).__name__}.",
        )
        return False
    return True


def validate_project(root: Path) -> dict[str, Any]:
    issues: list[ValidationIssue] = []
    try:
        config = load_project_config(root)
    except ValueError as exc:
        _issue(issues, "error", "CONFIG_LOAD", "config", str(exc))
        return _report(root, issues)

    pipeline = config["pipeline"]

    if pipeline.get("pipelineVersion") != 1:
        _issue(
            issues,
            "error",
            "PIPELINE_VERSION",
            "config/pipeline.json:pipelineVersion",
            "Only pipelineVersion=1 is supported.",
        )

    runtime = pipeline.get("runtime")
    if _require_type(issues, runtime, dict, "config/pipeline.json:runtime"):
        accelerator = runtime.get("accelerator", "auto")
        if accelerator not in {"auto", "cuda", "metal", "cpu"}:
            _issue(
                issues,
                "error",
                "ACCELERATOR_VALUE",
                "config/pipeline.json:runtime.accelerator",
                "Valid values: auto, cuda, metal, cpu.",
            )

    quality = pipeline.get("quality")
    if _require_type(issues, quality, dict, "config/pipeline.json:quality"):
        if quality.get("mode") not in {"maximum", "balanced"}:
            _issue(
                issues,
                "error",
                "QUALITY_MODE",
                "config/pipeline.json:quality.mode",
                "Valid values: maximum or balanced.",
            )
        playback = quality.get("appPlayback", {})
        for field in (
            "layout",
            "videoContainer",
            "audioContainer",
            "videoPolicy",
            "fallbackVideoCodec",
            "audioCodec",
        ):
            if not str(playback.get(field, "")).strip():
                _issue(
                    issues,
                    "error",
                    "APP_PLAYBACK_FIELD",
                    f"config/pipeline.json:quality.appPlayback.{field}",
                    "This value must not be empty.",
                )
        if int(playback.get("fallbackCrf", 18)) < 0 or int(
            playback.get("fallbackCrf", 18)
        ) > 30:
            _issue(
                issues,
                "error",
                "APP_PLAYBACK_CRF",
                "config/pipeline.json:quality.appPlayback.fallbackCrf",
                "The source-preserving fallback CRF must be between 0 and 30.",
            )

    separation = pipeline.get("audioSeparation", {})
    if separation.get("allowModelFallback") is not False:
        _issue(
            issues,
            "error",
            "SEPARATION_FALLBACK_ENABLED",
            "config/pipeline.json:audioSeparation.allowModelFallback",
            "Production separation must not silently fall back to another model.",
        )

    render = pipeline.get("render", {})
    template_value = str(render.get("template", "")).strip()
    template_path = root / template_value if template_value else Path()
    if not template_value or not template_path.is_file():
        _issue(
            issues,
            "error",
            "TEMPLATE_MISSING",
            "config/pipeline.json:render.template",
            f"Template not found: {template_value or '(empty)'}",
        )
    fonts_directory_value = str(render.get("fontsDirectory", "")).strip()
    fonts_directory = root / fonts_directory_value if fonts_directory_value else Path()
    if not fonts_directory_value or not fonts_directory.is_dir():
        _issue(
            issues,
            "error",
            "FONTS_DIRECTORY_MISSING",
            "config/pipeline.json:render.fontsDirectory",
            f"Font directory not found: {fonts_directory_value or '(empty)'}",
        )
    lyrics = pipeline.get("lyrics", {})
    if lyrics.get("textSource") != "authoritative-input":
        _issue(
            issues,
            "error",
            "LYRIC_TEXT_SOURCE",
            "config/pipeline.json:lyrics.textSource",
            "Production lyrics must use authoritative-input text.",
        )
    if lyrics.get("textDetectionEnabled") is not False:
        _issue(
            issues,
            "error",
            "LYRIC_TEXT_DETECTION_ENABLED",
            "config/pipeline.json:lyrics.textDetectionEnabled",
            "Lyric text detection must remain disabled.",
        )
    if bool(lyrics.get("forcedAlignment", True)):
        if lyrics.get("forcedAlignmentEngine") != "vietnamese-song-ctc":
            _issue(
                issues,
                "error",
                "FORCED_ALIGNMENT_ENGINE",
                "config/pipeline.json:lyrics.forcedAlignmentEngine",
                "Production timing requires the vietnamese-song-ctc engine.",
            )
        vendor = root / "vendor" / "lyric-alignment"
        if not (vendor / "model_handling.py").is_file() or not (
            vendor / "LICENSE"
        ).is_file():
            _issue(
                issues,
                "error",
                "FORCED_ALIGNMENT_VENDOR_MISSING",
                "vendor/lyric-alignment",
                "Vietnamese song aligner source or license is missing.",
            )
    leakage_gate = lyrics.get("leakageQualityGate", {})
    if leakage_gate.get("lexicalResidualAuditPolicy") != "strict":
        _issue(
            issues,
            "error",
            "RESIDUAL_VOCAL_POLICY_NOT_STRICT",
            "config/pipeline.json:lyrics.leakageQualityGate.lexicalResidualAuditPolicy",
            "Production output must fail when the lexical residual-vocal audit fails.",
        )

    roles = pipeline.get("roles", {})
    required_role_gates = {
        "adaptiveSpeakerCount": "Speaker count must be inferred instead of forced to two.",
        "failOnAmbiguousSingerCount": "Ambiguous singer count must stop the job.",
        "failOnAmbiguousGender": "Ambiguous male/female evidence must stop the job.",
        "failOnInconsistentSemanticGroupRoles": "Unresolved role changes inside a semantic group must stop the job.",
        "failOnAmbiguousCoLeadSemanticTail": "Ambiguous duet boundaries must stop the job.",
    }
    for field, message in required_role_gates.items():
        if roles.get(field) is not True:
            _issue(
                issues,
                "error",
                "ROLE_GATE_NOT_STRICT",
                f"config/pipeline.json:roles.{field}",
                message,
            )

    try:
        provenance = verify_model_provenance(
            root, pipeline, require_files=False, verify_hashes=False
        )
        for message in provenance["errors"]:
            _issue(
                issues,
                "error",
                "MODEL_PROVENANCE",
                "config/model-manifest.json",
                message,
            )
    except ValueError as exc:
        _issue(
            issues,
            "error",
            "MODEL_MANIFEST_INVALID",
            "config/model-manifest.json",
            str(exc),
        )

    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
        colors = template.get("sung", {}).get("colors", {})
        for role in ("male", "female", "duet"):
            color = str(colors.get(role, ""))
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                _issue(
                    issues,
                    "error",
                    "ROLE_COLOR",
                    f"{template_value}:sung.colors.{role}",
                    "Color must use #RRGGBB format.",
                )
        layout = template.get("layout", {})
        line_break_policy = str(layout.get("lineBreakPolicy", "legacy-word-count"))
        if line_break_policy in {"semantic-width", "semantic-audio-width"}:
            maximum_width_percent = float(
                layout.get("maximumLineWidthPercent", 90.0)
            )
            if not 60.0 <= maximum_width_percent <= 95.0:
                _issue(
                    issues,
                    "error",
                    "LYRIC_WIDTH_LIMIT",
                    f"{template_value}:layout.maximumLineWidthPercent",
                    "Semantic layout width must be between 60% and 95%.",
                )
        else:
            target_words = int(layout.get("targetWordsPerLine", 5))
            maximum_line_words = int(layout.get("maximumWordsPerLine", 6))
            maximum_screen_words = int(layout.get("maximumWordsPerScreen", 10))
            if not (3 <= target_words <= maximum_line_words <= 6):
                _issue(
                    issues,
                    "error",
                    "LYRIC_LINE_LIMIT",
                    f"{template_value}:layout",
                    "Required: 3 <= targetWordsPerLine <= maximumWordsPerLine <= 6.",
                )
            if maximum_screen_words != 10:
                _issue(
                    issues,
                    "error",
                    "LYRIC_SCREEN_LIMIT",
                    f"{template_value}:layout.maximumWordsPerScreen",
                    "The legacy layout requires a maximum of 10 words across both lines.",
                )
        scheduling_values = {
            "minimumDisplayLeadSeconds": float(
                layout.get("minimumDisplayLeadSeconds", 0.45)
            ),
            "preferredPostHoldSeconds": float(
                layout.get("preferredPostHoldSeconds", 0.2)
            ),
            "slotTransitionGapSeconds": float(
                layout.get("slotTransitionGapSeconds", 0.08)
            ),
            "minimumVisualSweepSeconds": float(
                layout.get("minimumVisualSweepSeconds", 0.0)
            ),
            "maximumVisualBoundaryShiftSeconds": float(
                layout.get("maximumVisualBoundaryShiftSeconds", 0.08)
            ),
        }
        if any(value < 0 for value in scheduling_values.values()):
            _issue(
                issues,
                "error",
                "KARAOKE_SCHEDULING_RANGE",
                f"{template_value}:layout",
                "Karaoke scheduling durations must be non-negative.",
            )
        if scheduling_values["minimumDisplayLeadSeconds"] > float(
            layout.get("displayLeadSeconds", 1.6)
        ):
            _issue(
                issues,
                "error",
                "DISPLAY_LEAD_RANGE",
                f"{template_value}:layout.minimumDisplayLeadSeconds",
                "minimumDisplayLeadSeconds must not exceed displayLeadSeconds.",
            )
        if scheduling_values["preferredPostHoldSeconds"] > float(
            layout.get("maximumPostHoldSeconds", 2.5)
        ):
            _issue(
                issues,
                "error",
                "POST_HOLD_RANGE",
                f"{template_value}:layout.preferredPostHoldSeconds",
                "preferredPostHoldSeconds must not exceed maximumPostHoldSeconds.",
            )
        if scheduling_values["maximumVisualBoundaryShiftSeconds"] > 0.12:
            _issue(
                issues,
                "error",
                "VISUAL_TIMING_SHIFT_LIMIT",
                f"{template_value}:layout.maximumVisualBoundaryShiftSeconds",
                "Visual timing boundary shifts must not exceed 120 ms.",
            )
        font_file = str(template.get("font", {}).get("file", "")).strip()
        if not font_file or not (fonts_directory / font_file).is_file():
            _issue(
                issues,
                "error",
                "FONT_FILE_MISSING",
                f"{template_value}:font.file",
                f"Bundled font not found: {font_file or '(empty)'}",
            )
        cue = template.get("roleChangeCue", {})
        if bool(cue.get("enabled", False)) and int(cue.get("dotCount", 0)) not in {
            3,
            4,
        }:
            _issue(
                issues,
                "error",
                "ROLE_CUE_DOT_COUNT",
                f"{template_value}:roleChangeCue.dotCount",
                "Role-change cues support only 3 or 4 dots.",
            )
        if float(cue.get("minimumDurationSeconds", 0.1)) > float(
            cue.get("durationSeconds", 2.0)
        ):
            _issue(
                issues,
                "error",
                "ROLE_CUE_DURATION_RANGE",
                f"{template_value}:roleChangeCue.minimumDurationSeconds",
                "minimumDurationSeconds must not exceed durationSeconds.",
            )
        if float(cue.get("showAfterPauseSeconds", 0.0)) < 0.0:
            _issue(
                issues,
                "error",
                "ROLE_CUE_PAUSE_RANGE",
                f"{template_value}:roleChangeCue.showAfterPauseSeconds",
                "showAfterPauseSeconds must be zero or positive.",
            )
    except (OSError, ValueError, AttributeError) as exc:
        _issue(issues, "error", "TEMPLATE_INVALID", template_value, str(exc))

    data_root = resolve_data_root(root)
    for directory, directory_root in (
        ("input", data_root),
        ("output", data_root),
        ("cache", data_root),
        ("logs", data_root),
        ("credentials", data_root),
        ("models", root),
    ):
        if not (directory_root / directory).is_dir():
            _issue(
                issues,
                "error",
                "DIRECTORY_MISSING",
                directory,
                f"Project directory is missing: {directory_root / directory}",
            )
    return _report(root, issues)


def _report(root: Path, issues: list[ValidationIssue]) -> dict[str, Any]:
    errors = sum(issue.severity == "error" for issue in issues)
    warnings = sum(issue.severity == "warning" for issue in issues)
    return {
        "kind": "lyricrail.validation",
        "projectRoot": str(root),
        "valid": errors == 0,
        "summary": {"errors": errors, "warnings": warnings},
        "issues": [asdict(issue) for issue in issues],
    }
