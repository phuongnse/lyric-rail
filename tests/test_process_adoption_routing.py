import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
POLICY_REVISION = "1e3d0d333b62ec92c94ea5c355bbb0cd73024b78"
EXPECTED_POLICY_JOB = (
    "  policy-verification:\n"
    "    name: policy-verification\n"
    "    if: github.event_name == 'pull_request'\n"
    "    permissions:\n"
    "      contents: read\n"
    "      pull-requests: read\n"
    "    uses: phuongnse/renovate-ops/.github/workflows/"
    f"policy-verification.yml@{POLICY_REVISION}\n"
)


def extract_policy_job(workflow: str) -> str | None:
    marker = "  policy-verification:\n"
    next_job = "\n  python:\n"
    if marker not in workflow or next_job not in workflow:
        return None
    return marker + workflow.split(marker, maxsplit=1)[1].split(
        next_job, maxsplit=1
    )[0]


def test_process_adoption_is_materialized_by_the_managed_runner() -> None:
    renovate = json.loads(
        (ROOT / ".github" / "renovate.json").read_text(encoding="utf-8")
    )
    assert renovate["enabled"] is True
    assert renovate["automerge"] is False
    assert renovate["constraints"]["python"] == "==3.12"
    assert "postUpgradeTasks" not in renovate
    authority_rule = next(
        rule
        for rule in renovate["packageRules"]
        if "engineering-process" in rule.get("matchPackageNames", [])
    )
    assert authority_rule["enabled"] is True
    assert authority_rule["automerge"] is False
    assert authority_rule["postUpgradeTasks"]["commands"] == [
        "python .process/adopt-process.py --project-root . "
        "--requirements-lock requirements/process.txt"
    ]
    assert authority_rule["postUpgradeTasks"]["executionMode"] == "update"
    assert ".agents/skills/**" in authority_rule["postUpgradeTasks"]["fileFilters"]

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "automation/renovate/engineering-process" in workflow
    assert "processctl adoption check" in workflow
    assert extract_policy_job(workflow) == EXPECTED_POLICY_JOB

    assert workflow.count("Install published engineering-process authority") == 4
    assert workflow.count("--require-hashes") == 4
    assert "cargo install cargo-audit --version 0.22.2 --locked" in workflow
    assert "if: runner.os != 'Windows'" in workflow
    windows_gates = {
        "Validate Windows Rust environment (process 0.5.1 containment bridge)": (
            "processctl doctor --project-root . --profile rust",
            2,
        ),
        "Run Windows Rust formatting gate": ("cargo fmt --all --check", 1),
        "Run Windows Rust test gate": ("cargo test --workspace --locked", 1),
        "Run Windows Rust lint gate": (
            "cargo clippy --workspace --all-targets --locked -- -D warnings",
            1,
        ),
    }
    for name, (command, expected_count) in windows_gates.items():
        exact_step = (
            f"      - name: {name}\n"
            "        if: runner.os == 'Windows'\n"
            f"        run: {command}\n"
        )
        assert exact_step in workflow
        assert workflow.count(command) == expected_count

    assert (ROOT / ".process" / "adopt-process.py").is_file()

    project = json.loads((ROOT / ".process" / "project.json").read_text())
    fuzz_command = next(
        command
        for command in project["profiles"]["security"]
        if command["id"] == "package-fuzz-smoke"
    )
    audit_command = next(
        command
        for command in project["profiles"]["security"]
        if command["id"] == "rust-dependency-audit"
    )
    assert audit_command["run"] == ["python", "scripts/run_cargo_audit.py"]
    assert fuzz_command["run"] == ["python", "scripts/run_package_fuzz_smoke.py"]

def test_policy_job_rejects_trust_root_and_permission_mutations() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    mutations = {
        "owner": workflow.replace(
            "phuongnse/renovate-ops/", "attacker/renovate-ops/", 1
        ),
        "revision": workflow.replace(
            POLICY_REVISION,
            "1e3d0d333b62ec92c94ea5c355bbb0cd73024b79",
            1,
        ),
        "write": workflow.replace(
            "contents: read\n      pull-requests: read",
            "contents: write\n      pull-requests: write",
            1,
        ),
        "extra": workflow.replace(
            "pull-requests: read\n    uses:",
            "pull-requests: read\n      issues: write\n    uses:",
            1,
        ),
    }
    assert extract_policy_job(workflow) == EXPECTED_POLICY_JOB
    for mutation in mutations.values():
        assert extract_policy_job(mutation) != EXPECTED_POLICY_JOB
