import { describe, expect, it } from "vitest";
import cases from "../../../tests/fixtures/diagnostics-v1.json";
import contract from "../../../src/lyricrail/diagnostic_contract.json";
import {
  formatIssueDiagnostics,
  formatIssueDetail,
  MAX_ISSUE_DIAGNOSTIC_BYTES,
  projectDiagnostic,
  selectDiagnosticTasks,
} from "./diagnostics";
import { clientIssue } from "./issues";
import { PRODUCER_ISSUE_CODES, clientIssueCode } from "./issueCodes";
import { latestModelTransferProgress } from "./modelProgress";
import type { SystemIssue } from "./issues";
import type { TaskOutputLine, TaskRecord } from "./tasks";

function task(id: string, kind: TaskRecord["kind"], updatedAtMillis: number): TaskRecord {
  return {
    id,
    kind,
    title: `Task ${id}`,
    status: "failed",
    progressMode: "indeterminate",
    cancellable: false,
    startedAtMillis: updatedAtMillis - 1000,
    updatedAtMillis,
    finishedAtMillis: updatedAtMillis,
    outputLineCount: 1,
    outputTruncated: false,
  };
}

const issue: SystemIssue = {
  id: "library.files-could-not-be-added:library:client",
  code: "library.files-could-not-be-added",
  scope: "library",
  severity: "error",
  title: "Files could not be added",
  summary: "The selected media could not be prepared.",
  detail: "C:\\TOPSECRET\\private-song.mp4",
  state: "open",
  occurrences: 2,
  createdAtMillis: 1_000,
  updatedAtMillis: 2_000,
  actions: [],
};

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

  it("copies actionable bounded context without releasing unsafe text", () => {
    const diagnosticLine: TaskOutputLine = {
      sequence: 1,
      timestampMillis: 3_000,
      taskId: "clip",
      stream: "stderr",
      stage: "portable-preview",
      text: "Clip preview requires the verified ffprobe tool",
    };
    const report = formatIssueDiagnostics({
      capturedAtMillis: 4_000,
      status: {
        version: "0.8.0",
        platform: "windows",
        vaultAvailable: true,
        processing: {
          pendingJobs: 2,
          runtimeAvailable: false,
          runtimeError: "C:\\TOPSECRET\\runtime",
        },
      },
      issue,
      tasks: [{ task: task("clip", "clip-preparation", 3_000), lines: [diagnosticLine], truncated: false }],
    });
    expect(report).toContain("Code: library.files-could-not-be-added");
    expect(report).toContain("Severity: error");
    expect(report).toContain("Occurrences: 2");
    expect(report).toContain("Platform: windows");
    expect(report).toContain("Pending jobs: 2");
    expect(report).toContain("Title: Files could not be added");
    expect(report).toContain("Summary: The selected media could not be prepared.");
    expect(report).toContain("Status: failed");
    expect(report).toContain("Progress: unknown% (stage unknown%)");
    expect(report).toContain("Output retained: 1 line(s); truncated: no");
    expect(report).toContain("Clip preview requires the verified ffprobe tool");
    expect(report).toContain(contract.withheld);
    expect(report).not.toContain("TOPSECRET");
    expect(new TextEncoder().encode(report).byteLength).toBeLessThanOrEqual(MAX_ISSUE_DIAGNOSTIC_BYTES);
  });

  it("keeps safe issue context when raw technical detail is withheld", () => {
    const detail = formatIssueDetail({ ...issue, detail: "C:\\TOPSECRET\\job.log" });
    expect(detail).toContain("Raw technical text was withheld for privacy.");
    expect(detail).toContain("Issue code: library.files-could-not-be-added");
    expect(detail).toContain("Available actions: none");
    expect(detail).not.toContain("TOPSECRET");
  });

  it("fails closed for identifier-shaped secrets and keeps Activity/copy context aligned", () => {
    const unsafe = {
      ...issue,
      code: "library.action-failed-TOPSECRET",
      scope: "TOPSECRET",
      relatedTaskId: "TOPSECRET",
      detail: undefined,
    };
    const detail = formatIssueDetail(unsafe);
    const report = formatIssueDiagnostics({ issue: unsafe });
    expect(detail).toContain("Issue code: producer-code-unavailable");
    expect(detail).toContain("Scope: unknown-scope");
    expect(detail).toContain(`Related task: ${contract.withheld}`);
    for (const line of detail.split("\n")) expect(report).toContain(line);
    expect(report).not.toContain("TOPSECRET");
  });

  it("retains current producer issue codes, closed actions and opaque references", () => {
    for (const [title, code] of [
      ["Task could not be cancelled", "tasks.task-could-not-be-cancelled"],
      ["Task could not be paused", "tasks.task-could-not-be-paused"],
      ["Task could not be resumed", "tasks.task-could-not-be-resumed"],
    ] as const) {
      const produced = clientIssue("tasks", title, "TOPSECRET", undefined, { kind: "retry-item", label: "Retry", requiresConfirmation: false });
      expect(produced.code).toBe(code);
      expect(formatIssueDetail({ ...produced, detail: undefined })).toContain(`Issue code: ${code}`);
    }
    const reference = "123e4567-e89b-12d3-a456-426614174000";
    const withReference = { ...issue, detail: undefined, relatedTaskId: reference, actions: [{ kind: "retry-item" as const, label: "Retry", requiresConfirmation: false }] };
    const detail = formatIssueDetail(withReference);
    const report = formatIssueDiagnostics({ issue: withReference });
    expect(detail).toContain(`Related task: ${reference}`);
    expect(detail).toContain("Available actions: retry-item");
    expect(report).toContain(`Related task: ${reference}`);
    expect(report).toContain("Actions: retry-item");
    for (const kind of ["install-models", "retry-item", "reconnect-drive"] as const) {
      expect(formatIssueDetail({ ...issue, detail: undefined, actions: [{ kind, label: "Action", requiresConfirmation: false }] }))
        .toContain(`Available actions: ${kind}`);
    }
    expect(PRODUCER_ISSUE_CODES).toContain(clientIssueCode("system", "Action could not be completed"));
  });

  it("accepts every producer-owned opaque reference form", () => {
    const uuid = "123e4567-e89b-12d3-a456-426614174000";
    for (const reference of [
      "clip-preparation",
      uuid,
      `local-${"a".repeat(64)}`,
      `drive-download-${"b".repeat(64)}`,
      `local-scan-${uuid}`,
      `folder-scan-${uuid}`,
      `drive-connect-${uuid}`,
      `drive-rescan-${uuid}`,
    ]) {
      expect(formatIssueDetail({ ...issue, detail: undefined, relatedTaskId: reference })).toContain(`Related task: ${reference}`);
    }
  });

  it("keeps allowlisted issue detail unchanged", () => {
    const detail = "Clip preview requires the verified ffprobe tool";
    expect(formatIssueDetail({ ...issue, detail })).toBe(detail);
  });

  it("projects structured producer progress detail", () => {
    const detail = JSON.stringify({
      kind: contract.progressKind,
      phase: "downloading",
      progressPercent: 25,
      completedBytes: 3,
      totalBytes: 12,
    });
    expect(formatIssueDetail({ ...issue, detail })).toBe(JSON.stringify({
      kind: contract.progressKind,
      phase: "downloading",
      message: contract.phases.downloading,
      progressPercent: 25,
      completedBytes: 3,
      totalBytes: 12,
    }));
  });

  it("projects untrusted issue and task metadata instead of copying it verbatim", () => {
    const report = formatIssueDiagnostics({
      issue: {
        ...issue,
        title: "TOPSECRET user title",
        summary: "C:\\TOPSECRET\\package.lrail",
        relatedItemId: "C:\\TOPSECRET\\item",
      },
      tasks: [{
        task: {
          ...task("task-id", "clip-preparation", 3_000),
          title: "TOPSECRET Drive package",
          stageTitle: "https://TOPSECRET.example/private",
          relatedItemId: "C:\\TOPSECRET\\item",
        },
        lines: [],
        truncated: false,
      }],
    });
    expect(report).toContain(`Title: ${contract.withheld}`);
    expect(report).toContain(`Summary: ${contract.withheld}`);
    expect(report).not.toContain("TOPSECRET");
  });

  it("retains allowlisted producer-owned task metadata", () => {
    const report = formatIssueDiagnostics({
      issue: {
        ...issue,
        title: "Processing setup required",
        summary: "Pinned karaoke models are not installed. Install and verify them, then LyricRail will retry affected songs automatically.",
      },
      tasks: [{ task: { ...task("clip", "clip-preparation", 3_000), title: "Prepare local clip" }, lines: [], truncated: false }],
    });
    expect(report).toContain("Title: Processing setup required");
    expect(report).toContain("Summary: Pinned karaoke models are not installed.");
    expect(report).toContain("Title: Prepare local clip");
  });

  it("selects the linked task first and caps related task context", () => {
    const linked = { ...task("linked", "local-scan", 1_000), relatedItemId: "song" };
    const selected = selectDiagnosticTasks(
      { ...issue, relatedTaskId: "linked", relatedItemId: "song" },
      [
        { ...task("new-clip", "clip-preparation", 5_000), relatedItemId: "song" },
        { ...task("new-scan", "local-scan", 4_000), relatedItemId: "song" },
        task("old-clip", "clip-preparation", 3_000),
        linked,
      ],
    );
    expect(selected.map((item) => item.id)).toEqual(["linked", "new-clip", "new-scan"]);
  });

  it("does not infer task context from an issue scope", () => {
    const selected = selectDiagnosticTasks(issue, [
      task("clip", "clip-preparation", 3_000),
      task("scan", "local-scan", 2_000),
    ]);
    expect(selected).toEqual([]);
  });
});
