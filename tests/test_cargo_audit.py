from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_cargo_audit_module():
    path = ROOT / "scripts" / "run_cargo_audit.py"
    spec = importlib.util.spec_from_file_location("run_cargo_audit", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def accepted_report(module) -> dict[str, object]:
    return {
        "vulnerabilities": {"count": 0, "list": []},
        "warnings": {
            category: [
                {"advisory": {"id": identifier}}
                for identifier in sorted(identifiers)
            ]
            for category, identifiers in module.EXPECTED_INFORMATIONAL_ADVISORIES.items()
        },
    }


def test_cargo_audit_policy_accepts_only_the_reviewed_advisory_set() -> None:
    module = load_cargo_audit_module()
    report = accepted_report(module)
    assert module.report_is_accepted(report)

    report["warnings"]["unmaintained"].append(
        {"advisory": {"id": "RUSTSEC-2099-0001"}}
    )
    assert not module.report_is_accepted(report)


def test_cargo_audit_policy_rejects_empty_unknown_categories_and_duplicates() -> None:
    module = load_cargo_audit_module()

    unknown_empty_list = accepted_report(module)
    unknown_empty_list["warnings"]["unexpected"] = []
    assert not module.report_is_accepted(unknown_empty_list)

    unknown_empty_object = accepted_report(module)
    unknown_empty_object["warnings"]["unexpected"] = {}
    assert not module.report_is_accepted(unknown_empty_object)

    duplicate = accepted_report(module)
    duplicate["warnings"]["unmaintained"].append(
        duplicate["warnings"]["unmaintained"][0]
    )
    assert not module.report_is_accepted(duplicate)

    empty_known_category = accepted_report(module)
    empty_known_category["warnings"]["unsound"] = []
    assert not module.report_is_accepted(empty_known_category)


def test_cargo_audit_policy_rejects_vulnerabilities_and_malformed_reports() -> None:
    module = load_cargo_audit_module()
    report = accepted_report(module)
    report["vulnerabilities"] = {
        "count": 1,
        "list": [{"advisory": {"id": "RUSTSEC-2099-0002"}}],
    }
    assert not module.report_is_accepted(report)
    assert not module.report_is_accepted({})
