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


def test_process_adoption_remains_review_gated_until_host_cutover() -> None:
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
    assert "automation/renovate/engineering-process" in workflow
    assert "processctl adoption check" in workflow
    assert extract_policy_job(workflow) == EXPECTED_POLICY_JOB


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
