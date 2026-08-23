import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_process_adoption_is_reserved_for_the_prepublication_host() -> None:
    renovate = json.loads(
        (ROOT / ".github" / "renovate.json").read_text(encoding="utf-8")
    )
    assert renovate["enabled"] is True
    assert renovate["automerge"] is False
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
