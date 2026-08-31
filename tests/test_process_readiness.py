from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read_json(path: str) -> dict[str, object]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


EXPECTED_EVIDENCE = {
    "application-correctness": ["frontend", "python", "rust"],
    "authoritative-input-integrity": ["python"],
    "cross-platform-portability": ["frontend", "python", "rust"],
    "dependency-audit": ["frontend", "security"],
    "media-pipeline-integrity": ["python"],
    "package-security": ["rust", "security"],
    "recovery-mechanism-integrity": ["python", "rust"],
}
EXPECTED_GAPS = {
    "dependency-security": "Linux GTK/glib advisories and audit coverage exclusions remain stable-release blockers.",
    "incident-recovery": "Signing-key compromise and destructive recovery drills remain open.",
    "independent-security-review": "The format, key lifecycle, parser, player, runtime, broker, and update chain still require independent assessment.",
    "key-custody": "The release signing seed still needs documented offline or hardware-backed custody.",
    "linux-release-security": "The Tauri GTK3/glib unsoundness and unmaintained dependency chain remains unresolved.",
    "recovery-integrity": "Recovery must be verified before the last clear master can be removed.",
    "release-integrity": "Signed installers and clean-host platform release evidence remain open.",
    "runtime-delivery-integrity": "Runtime delivery and model/checkpoint redistribution licensing remain unresolved.",
    "update-integrity": "A signed updater and rollback policy remain open.",
    "workspace-security": "Credential storage and encrypted-workspace adapters still need real-host evidence.",
}
EXPECTED_CHECKS = {
    "frontend": ["frontend-build", "frontend-tests", "frontend-dependency-audit"],
    "python": [
        "python-compile",
        "python-dependency-consistency",
        "python-tests",
        "python-media-integration",
    ],
    "rust": ["rust-format", "rust-tests", "rust-clippy"],
    "security": [
        "python-dependency-audit",
        "rust-dependency-audit",
        "package-fuzz-smoke",
    ],
}


def test_desktop_media_readiness_is_building_and_version_pinned() -> None:
    project = read_json(".process/project.json")
    readiness = read_json(".process/readiness.json")
    assert "readiness" not in project
    assert readiness["target"] == "production"
    assert readiness["stage"] == "building"
    assert readiness["packs"] == [{"id": "desktop-media", "version": 1}]
    capabilities = readiness["capabilities"]
    assert len({item["id"] for item in capabilities}) == len(capabilities)
    assert {
        item["id"]: item["evidenceProfiles"]
        for item in capabilities
        if item["state"] == "enforced"
    } == EXPECTED_EVIDENCE
    planned = {item["id"]: item["gap"] for item in capabilities if item["state"] == "planned"}
    assert planned == EXPECTED_GAPS
    assert all(gap.strip() for gap in planned.values())


def test_readiness_evidence_resolves_without_making_security_global() -> None:
    project = read_json(".process/project.json")
    required = project["lifecycle"]["requiredProfiles"]
    assert required == ["frontend", "python", "rust"]
    checks = {
        profile: [check["id"] for check in entries]
        for profile, entries in project["profiles"].items()
    }
    assert checks == EXPECTED_CHECKS
    for profiles in EXPECTED_EVIDENCE.values():
        assert set(profiles) <= set(checks)
        assert set(profiles) & set(required)
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "security-sensitive" in agents
    assert "include the `security` profile" in agents


def test_readiness_keeps_existing_release_claims_honest() -> None:
    release_status = (ROOT / "docs/RELEASE_STATUS.md").read_text(encoding="utf-8")
    security_acceptance = (ROOT / "docs/SECURITY_ACCEPTANCE.md").read_text(encoding="utf-8")
    assert "not a stable\nproduction-security release" in release_status
    assert "must not be described as production-grade security" in security_acceptance
