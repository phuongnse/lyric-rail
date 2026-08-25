import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
POLICY_REVISION = "2152dab51edd6c84163a71b48f50e6ad042eb331"
PROCESS_REVISION = "8458d9e211d95ae9749ccf71b5a5b9d90bc1503c"


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
    policy_job = workflow.split("  policy-verification:\n", maxsplit=1)[1].split(
        "\n  python:", maxsplit=1
    )[0]
    assert f"policy-verification.yml@{POLICY_REVISION}" in policy_job
    assert "      contents: read\n      pull-requests: read" in policy_job
    assert "contents: write" not in policy_job
    assert "pull-requests: write" not in policy_job

    process_action = f"phuongnse/engineering-process@{PROCESS_REVISION} # v0.5.0"
    assert workflow.count(process_action) == 4
    assert "# v0.4.0" not in workflow
    assert "cargo install cargo-audit --version 0.22.2 --locked" in workflow
