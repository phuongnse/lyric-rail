import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
POLICY_REVISION = "1e3d0d333b62ec92c94ea5c355bbb0cd73024b78"
PROCESS_REVISION = "dd16e711d7c95a71173add8f31fac163cbc85e3f"
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


def test_process_adoption_is_owned_by_the_completed_lifecycle_host() -> None:
    renovate = json.loads(
        (ROOT / ".github" / "renovate.json").read_text(encoding="utf-8")
    )
    assert renovate["enabled"] is True
    assert renovate["automerge"] is False
    assert "postUpgradeTasks" not in renovate
    authority_rule = next(
        rule
        for rule in renovate["packageRules"]
        if "engineering-process" in rule.get("matchPackageNames", [])
    )
    assert authority_rule["enabled"] is False
    assert authority_rule["automerge"] is False

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "automation/process/engineering-process" in workflow
    assert "automation/renovate/engineering-process" not in workflow
    assert "processctl adoption check" in workflow
    assert extract_policy_job(workflow) == EXPECTED_POLICY_JOB

    process_action = f"phuongnse/engineering-process@{PROCESS_REVISION} # v0.5.1"
    assert workflow.count(process_action) == 4
    assert "# v0.5.0" not in workflow
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

    windows_helper = (
        ROOT / ".process" / "adopt-process-windows-job.py"
    ).read_text(encoding="utf-8")
    assert "NATURAL_DRAIN_GRACE_MILLISECONDS = 5_000" in windows_helper

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

    automation = json.loads(
        (ROOT / ".process" / "automation.json").read_text(encoding="utf-8")
    )
    assert automation == {
        "schemaVersion": 1,
        "kind": "engineering-process-standing-automation-policy",
        "enabled": True,
        "confirmationMode": "exceptions-only",
        "actions": [
            "adopt",
            "commit",
            "deploy",
            "ephemeral-cleanup",
            "merge",
            "publish",
            "push",
            "release",
            "review-object",
        ],
        "merge": {
            "method": "squash",
            "requireCompletedLifecycle": True,
            "requireCurrentBase": True,
            "requireExactHead": True,
            "requireIndependentReview": True,
            "requireRequiredChecks": True,
        },
        "escalationReasons": [
            "bounded-recovery-exhausted",
            "capability-unavailable",
            "decision-required",
        ],
    }


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
