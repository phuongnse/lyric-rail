import { describe, expect, it } from "vitest";
import cases from "../../../tests/fixtures/diagnostics-v1.json";
import contract from "../../../src/lyricrail/diagnostic_contract.json";
import { projectDiagnostic } from "./diagnostics";
import { latestModelTransferProgress } from "./modelProgress";

describe("closed diagnostic contract", () => {
  it("matches the native and Python projection fixture", () => {
    for (const item of cases) {
      const output = projectDiagnostic(item.input);
      expect(output).not.toContain("TOPSECRET");
      if (item.expected && typeof item.expected === "object") expect(JSON.parse(output)).toEqual(item.expected);
      else expect(output).toBe(item.expected ?? contract.withheld);
    }
  });
  it("renders measured installer bytes from the producer protocol", () => {
    const text = projectDiagnostic(cases[5].input);
    const progress = latestModelTransferProgress([{ sequence: 1, timestampMillis: 0, taskId: "model-install", stream: "progress", stage: "download-and-verify", text }]);
    expect(progress?.percent).toBe(25);
    expect(progress?.completed).toBe("3");
    expect(progress?.total).toBe("12");
  });
});
