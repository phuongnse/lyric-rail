from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_INFORMATIONAL_ADVISORIES = {
    "unmaintained": {
        "RUSTSEC-2024-0370",
        "RUSTSEC-2024-0411",
        "RUSTSEC-2024-0412",
        "RUSTSEC-2024-0413",
        "RUSTSEC-2024-0414",
        "RUSTSEC-2024-0415",
        "RUSTSEC-2024-0416",
        "RUSTSEC-2024-0417",
        "RUSTSEC-2024-0418",
        "RUSTSEC-2024-0419",
        "RUSTSEC-2024-0420",
        "RUSTSEC-2025-0075",
        "RUSTSEC-2025-0080",
        "RUSTSEC-2025-0081",
        "RUSTSEC-2025-0098",
        "RUSTSEC-2025-0100",
    },
    "unsound": {"RUSTSEC-2024-0429"},
}


def advisory_ids(entries: object) -> set[str] | None:
    if not isinstance(entries, list):
        return None
    identifiers: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        advisory = entry.get("advisory")
        if not isinstance(advisory, dict) or not isinstance(advisory.get("id"), str):
            return None
        identifiers.add(advisory["id"])
    return identifiers


def report_is_accepted(report: object) -> bool:
    if not isinstance(report, dict):
        return False
    vulnerabilities = report.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        return False
    if vulnerabilities.get("count") != 0 or vulnerabilities.get("list") != []:
        return False
    informational = report.get("warnings")
    if not isinstance(informational, dict):
        return False
    actual = {
        category: advisory_ids(entries)
        for category, entries in informational.items()
        if entries
    }
    return actual == EXPECTED_INFORMATIONAL_ADVISORIES


def main() -> int:
    completed = subprocess.run(
        ["cargo", "audit", "--json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        print("cargo-audit policy: failed; invalid structured report")
        return 1
    if completed.returncode != 0 or completed.stderr or not report_is_accepted(report):
        print("cargo-audit policy: failed; advisory set or command status changed")
        return 1
    accepted_count = sum(len(items) for items in EXPECTED_INFORMATIONAL_ADVISORIES.values())
    print(
        "cargo-audit policy: passed; "
        f"vulnerabilities=0; accepted_advisories={accepted_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
