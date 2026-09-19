import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
POLICY_REVISION = "38d952b8c94604df10fadc48b6c830a144ea1137"
EXPECTED_POLICY_JOB = {
    "name": "Policy verification",
    "if": "github.event_name == 'pull_request'",
    "permissions": {"contents": "read", "pull-requests": "read"},
    "uses": (
        "phuongnse/renovate-ops/.github/workflows/"
        f"policy-verification.yml@{POLICY_REVISION}"
    ),
}


def extract_policy_job(workflow: str) -> dict | None:
    document = yaml.safe_load(workflow)
    jobs = document.get("jobs", {})
    if not isinstance(jobs, dict):
        return None
    policy_job = jobs.get("policy-verification")
    return policy_job if isinstance(policy_job, dict) else None


def test_process_adoption_is_materialized_by_the_managed_runner() -> None:
    renovate = json.loads(
        (ROOT / ".github" / "renovate.json").read_text(encoding="utf-8")
    )
    assert renovate["enabled"] is True
    assert renovate["draftPR"] is True
    assert renovate["constraints"]["python"] == "==3.12"
    assert "postUpgradeTasks" not in renovate
    authority_rule = next(
        rule
        for rule in renovate["packageRules"]
        if "engineering-process" in rule.get("matchPackageNames", [])
    )
    assert authority_rule["enabled"] is True
    assert authority_rule["draftPR"] is True
    assert authority_rule["recreateWhen"] == "always"
    assert authority_rule["postUpgradeTasks"]["commands"] == [
        "python .process/adopt-process.py --project-root . "
        "--requirements-lock requirements/process.txt"
    ]
    assert authority_rule["postUpgradeTasks"]["executionMode"] == "update"
    assert ".agents/skills/**" in authority_rule["postUpgradeTasks"]["fileFilters"]

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    workflow_document = yaml.safe_load(workflow)
    jobs = workflow_document["jobs"]
    python_steps = jobs["python"]["steps"]
    adoption_steps = [
        step for step in python_steps if "processctl adoption check" in step.get("run", "")
    ]
    assert adoption_steps == [
        {
            "name": "Validate managed process distribution",
            "if": "runner.os == 'Linux'",
            "run": "processctl adoption check --project-root . --requirements-lock requirements/process.txt",
        }
    ]
    assert extract_policy_job(workflow) == EXPECTED_POLICY_JOB
    assert jobs["python"]["name"] == "Python 3.12 (${{ matrix.os }})"
    assert jobs["rust"]["name"] == "Rust (${{ matrix.os }})"

    authority_steps = [
        step
        for job in jobs.values()
        for step in job.get("steps", [])
        if step.get("name") == "Install published engineering-process authority"
    ]
    assert len(authority_steps) == 4
    assert all("--require-hashes" in step["run"] for step in authority_steps)
    cache_steps = [
        step
        for job in (jobs["rust"], jobs["security"])
        for step in job["steps"]
        if step.get("uses") == "./.github/actions/cargo-cache"
    ]
    assert len(cache_steps) == 2
    assert jobs["rust"]["steps"][2]["id"] == "cargo-cache"
    assert jobs["security"]["steps"][2]["id"] == "cargo-cache"
    assert jobs["rust"]["env"]["CARGO_TARGET_DIR"] == (
        "${{ github.workspace }}/.dev/target-${{ matrix.os }}"
    )
    assert jobs["security"]["env"]["CARGO_TARGET_DIR"] == (
        "${{ github.workspace }}/.dev/target-security"
    )
    assert cache_steps[0]["with"]["toolchain-fingerprint"] == "stable"
    assert cache_steps[1]["with"]["toolchain-fingerprint"] == "${{ env.CARGO_FUZZ_TOOLCHAIN }}"
    security_install = next(
        step["run"]
        for step in jobs["security"]["steps"]
        if step.get("name") == "Install Rust security toolchain"
    )
    assert 'rustup toolchain install "$CARGO_FUZZ_TOOLCHAIN"' in security_install
    assert 'cargo install cargo-audit --version "$CARGO_AUDIT_VERSION" --locked --force' in security_install
    assert 'cargo install cargo-fuzz --version "$CARGO_FUZZ_VERSION" --locked --force' in security_install
    assert 'rm -f "$HOME/.cargo/bin/cargo-audit"' in security_install
    assert 'rm -f "$HOME/.cargo/bin/cargo-fuzz"' in security_install
    cache_action = yaml.safe_load(
        (ROOT / ".github" / "actions" / "cargo-cache" / "action.yml").read_text(
            encoding="utf-8"
        )
    )
    cache_action_steps = cache_action["runs"]["steps"]
    assert set(cache_action["outputs"]) == {
        "dependency-cache-hit",
        "target-cache-hit",
        "security-tools-cache-hit",
    }
    assert cache_action["outputs"]["dependency-cache-hit"]["value"] == (
        "${{ steps.dependencies-cache.outputs.cache-hit }}"
    )
    assert cache_action["outputs"]["target-cache-hit"]["value"] == (
        "${{ steps.target-cache.outputs.cache-hit }}"
    )
    assert cache_action["outputs"]["security-tools-cache-hit"]["value"] == (
        "${{ steps.security-tools-cache.outputs.cache-hit }}"
    )
    assert [step["id"] for step in cache_action_steps] == [
        "dependencies-cache",
        "target-cache",
        "security-tools-cache",
    ]
    assert [step["uses"] for step in cache_action_steps] == [
        "actions/cache@0400d5f644dc74513175e3cd8d07132dd4860809",
        "actions/cache@0400d5f644dc74513175e3cd8d07132dd4860809",
        "actions/cache@0400d5f644dc74513175e3cd8d07132dd4860809",
    ]
    assert all("restore-keys" not in step["with"] for step in cache_action_steps)
    assert cache_action_steps[0]["with"]["path"].splitlines() == [
        "~/.cargo/registry",
        "~/.cargo/git",
    ]
    assert cache_action_steps[2]["with"]["path"].splitlines() == [
        "~/.cargo/bin/cargo-audit",
        "~/.cargo/bin/cargo-fuzz",
    ]
    assert "scripts/run_package_fuzz_smoke.py" in cache_action_steps[1]["with"]["key"]
    assert "${{ inputs.toolchain-fingerprint }}" in cache_action_steps[1]["with"]["key"]
    assert any(
        step.get("if") == "runner.os != 'Windows'"
        for step in jobs["rust"]["steps"]
    )
    windows_gates = {
        "Validate Windows Rust environment": "processctl doctor --project-root .",
        "Run Windows Rust formatting gate": "cargo fmt --all --check",
        "Run Windows Rust test gate": "cargo test --workspace --locked",
        "Run Windows Rust lint gate": "cargo clippy --workspace --all-targets --locked -- -D warnings",
    }
    for name, command in windows_gates.items():
        matching_steps = [
            step
            for step in jobs["rust"]["steps"]
            if step.get("name") == name
            and step.get("if") == "runner.os == 'Windows'"
            and step.get("run") == command
        ]
        assert len(matching_steps) == 1

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
