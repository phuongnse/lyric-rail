import json
from pathlib import Path

from lyricrail.diagnostics import CONTRACT, project_diagnostic
from lyricrail.job import STAGE_SPECS, sanitize_diagnostic_payload


def test_closed_contract_shared_with_native_and_frontend() -> None:
    cases = json.loads((Path(__file__).parent / "fixtures/diagnostics-v1.json").read_text())
    for case in cases:
        output = project_diagnostic(case["input"])
        assert "TOPSECRET" not in output
        if isinstance(case["expected"], dict):
            assert json.loads(output) == case["expected"]
        else:
            assert output == (case["expected"] or CONTRACT["withheld"])
    for stage in STAGE_SPECS:
        assert CONTRACT["stages"][stage.key] == stage.title
    safe = sanitize_diagnostic_payload({"error": {"message": "TOPSECRET", "TOPSECRET": "value"}, "progressPercent": 55})
    assert "TOPSECRET" not in json.dumps(safe)
    assert safe["progressPercent"] == 55
