"""Closed diagnostic projection; free text is never evidence of safe content."""
from __future__ import annotations

import json
import math
from importlib.resources import files

CONTRACT = json.loads(files("lyricrail").joinpath("diagnostic_contract.json").read_text(encoding="utf-8"))


def project_diagnostic(value: str) -> str:
    if type(value) is not str:
        return CONTRACT["withheld"]
    if (value == CONTRACT["withheld"] or value in CONTRACT["messages"]
        or value in CONTRACT["phases"].values() or value in CONTRACT["stages"]
        or value in CONTRACT["stages"].values()):
        return value
    if len(value) > 16384:
        return CONTRACT["withheld"]
    try:
        item = json.loads(value)
    except (ValueError, TypeError, RecursionError):
        return CONTRACT["withheld"]
    if not isinstance(item, dict) or item.get("kind") != CONTRACT["progressKind"]:
        return CONTRACT["withheld"]
    phase = item.get("phase")
    if not isinstance(phase, str) or phase not in CONTRACT["phases"]:
        return CONTRACT["withheld"]
    allowed = {"kind", "phase", "message", *CONTRACT["numericFields"]}
    if item.keys() - allowed:
        return CONTRACT["withheld"]
    output = {"kind": CONTRACT["progressKind"], "phase": phase, "message": CONTRACT["phases"][phase]}
    for field, maximum in CONTRACT["numericFields"].items():
        if field in item:
            number = item[field]
            if type(number) not in (int, float) or not 0 <= number <= maximum or not math.isfinite(number):
                return CONTRACT["withheld"]
            output[field] = number
    return json.dumps(output, ensure_ascii=False, separators=(",", ":"))
