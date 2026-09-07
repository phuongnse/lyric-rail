// @vitest-environment jsdom

import { act, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ActivityCenter } from "./App";
import type { SystemIssue } from "./issues";
import type { TaskOutputLine, TaskRecord } from "./tasks";

const runningTask: TaskRecord = {
  id: "catalog-item",
  kind: "processing",
  title: "Create karaoke: Song",
  status: "running",
  progressMode: "determinate",
  stageTitle: "Align authoritative lyrics",
  stageProgressPercent: 40,
  progressPercent: 30,
  cancellable: true,
  relatedItemId: "catalog-item",
  startedAtMillis: 1_000,
  updatedAtMillis: 2_000,
  outputLineCount: 0,
  outputTruncated: false,
};

const issue = (id: string, relatedTaskId?: string): SystemIssue => ({
  id,
  code: `system.${id}`,
  scope: "system",
  severity: "error",
  title: `Issue ${id}`,
  summary: "A system action needs attention.",
  detail: "Bounded technical detail",
  relatedTaskId,
  state: "open",
  occurrences: 1,
  createdAtMillis: 1,
  updatedAtMillis: 1,
  actions: [],
});

function Harness({
  tasks = [runningTask],
  issues = [],
  output = {},
  initialTab = "tasks",
  initialSelectedTaskId,
  initialSelectedIssueId,
  initialFocusTaskId,
  initialFocusIssueId,
  onOpenIssueTask,
}: {
  tasks?: TaskRecord[];
  issues?: SystemIssue[];
  output?: Record<string, TaskOutputLine[]>;
  initialTab?: "tasks" | "issues";
  initialSelectedTaskId?: string;
  initialSelectedIssueId?: string;
  initialFocusTaskId?: string;
  initialFocusIssueId?: string;
  onOpenIssueTask?: (issue: SystemIssue) => void;
}) {
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<"tasks" | "issues">(initialTab);
  const [selectedTaskId, setSelectedTaskId] = useState(initialSelectedTaskId);
  const [selectedIssueId, setSelectedIssueId] = useState(initialSelectedIssueId);
  const [focusTaskId, setFocusTaskId] = useState(initialFocusTaskId);
  const [focusIssueId, setFocusIssueId] = useState(initialFocusIssueId);
  const heading = useRef<HTMLHeadingElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const activeTotal = tasks.filter((task) => task.status === "queued" || task.status === "running").length;
  return (
    <>
      <button ref={trigger}>Activity trigger</button>
      <ActivityCenter
        open={open}
        issues={issues}
        tasks={tasks}
        runningTotal={activeTotal}
        nowMillis={6_000}
        tab={tab}
        selectedTaskId={selectedTaskId}
        selectedIssueId={selectedIssueId}
        focusTaskId={focusTaskId}
        focusIssueId={focusIssueId}
        taskOutputById={output}
        taskOutputTruncatedById={{}}
        headingRef={heading}
        onClose={() => setOpen(false)}
        onTab={setTab}
        onSelectTask={(task) => setSelectedTaskId((current) => current === task.id ? undefined : task.id)}
        onOpenIssueTask={(issue) => {
          if (onOpenIssueTask) {
            onOpenIssueTask(issue);
            return;
          }
          if (selectedIssueId === issue.id) {
            setSelectedIssueId(undefined);
            setSelectedTaskId(undefined);
          } else {
            setSelectedIssueId(issue.id);
            setSelectedTaskId(issue.relatedTaskId);
          }
        }}
        onTaskFocusComplete={(taskId) => setFocusTaskId((current) => current === taskId ? undefined : current)}
        onIssueFocusComplete={(issueId) => setFocusIssueId((current) => current === issueId ? undefined : current)}
        onCancelTask={() => undefined}
        onCopyTaskOutput={() => undefined}
        onDismiss={() => undefined}
        onResolve={() => undefined}
        onCopyDiagnostics={() => undefined}
        blocked={false}
        restoreRef={trigger}
      />
    </>
  );
}

function IssueOutputHarness() {
  const failedTask: TaskRecord = {
    ...runningTask,
    id: "remote-terminal-task",
    title: "Generic subsystem task",
    status: "failed",
    cancellable: false,
    finishedAtMillis: 5_000,
  };
  const linkedIssue = issue("linked", failedTask.id);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string>();
  const [selectedIssueId, setSelectedIssueId] = useState<string>();
  const heading = useRef<HTMLHeadingElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const output: TaskOutputLine = {
    sequence: 1,
    timestampMillis: 5_000,
    taskId: failedTask.id,
    stream: "stderr",
    stage: "generic-stage",
    text: "exact retained diagnostic",
  };
  return <>
    <button ref={trigger}>Activity trigger</button>
    <ActivityCenter
      open
      issues={[linkedIssue]}
      tasks={tasks}
      runningTotal={0}
      nowMillis={6_000}
      tab="issues"
      selectedTaskId={selectedTaskId}
      selectedIssueId={selectedIssueId}
      taskOutputById={{ [failedTask.id]: [output] }}
      taskOutputTruncatedById={{}}
      headingRef={heading}
      onClose={() => undefined}
      onTab={() => undefined}
      onSelectTask={() => undefined}
      onOpenIssueTask={() => {
        if (selectedTaskId === failedTask.id) {
          setSelectedTaskId(undefined);
          setSelectedIssueId(undefined);
        } else {
          setTasks([failedTask]);
          setSelectedTaskId(failedTask.id);
          setSelectedIssueId(linkedIssue.id);
        }
      }}
      onTaskFocusComplete={() => undefined}
      onIssueFocusComplete={() => undefined}
      onCancelTask={() => undefined}
      onCopyTaskOutput={() => undefined}
      onDismiss={() => undefined}
      onResolve={() => undefined}
      onCopyDiagnostics={() => undefined}
      blocked={false}
      restoreRef={trigger}
    />
  </>;
}

describe("Activity Center", () => {
  let host: HTMLDivElement;
  let root: Root;
  let scrollIntoView: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as typeof ResizeObserver;
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  it("has exactly Tasks and Issues tabs with roving keys and focus restoration", () => {
    act(() => root.render(<Harness />));
    const tabs = host.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    expect([...tabs].map((tab) => tab.textContent?.replace(/\d+$/, "").trim())).toEqual(["Tasks", "Issues"]);
    expect(host.textContent).not.toContain("History");
    expect(host.textContent).not.toContain("Clear history");
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(host.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBe("30");

    tabs[0].focus();
    act(() => tabs[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true })));
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs[1]);
    act(() => host.querySelector<HTMLButtonElement>(".issues-scrim")!.click());
    expect(document.activeElement?.textContent).toBe("Activity trigger");
  });

  it("keeps terminal tasks out of Tasks", () => {
    const failed = { ...runningTask, id: "failed", status: "failed" as const, cancellable: false };
    const succeeded = { ...runningTask, id: "done", status: "succeeded" as const, cancellable: false };
    act(() => root.render(<Harness tasks={[runningTask, failed, succeeded]} />));
    expect(host.textContent).toContain("Create karaoke: Song");
    expect(host.querySelector('[aria-label$="failed task"]')).toBeNull();
    expect(host.querySelector('[aria-label$="succeeded task"]')).toBeNull();
  });

  it("renders a newly started model task with null optional fields and truthful indeterminate progress", () => {
    const modelTask: TaskRecord = {
      id: "model-install",
      kind: "model-install",
      title: "Install processing models",
      status: "running",
      progressMode: "determinate",
      stageKey: null,
      stageTitle: null,
      stageProgressPercent: null,
      progressPercent: null,
      completedUnits: null,
      totalUnits: null,
      unitLabel: null,
      etaSeconds: null,
      cancellable: true,
      relatedItemId: null,
      startedAtMillis: 1_000,
      updatedAtMillis: 1_000,
      finishedAtMillis: null,
      outputLineCount: 0,
      outputTruncated: false,
      statusMessage: null,
    };
    act(() => root.render(<Harness tasks={[modelTask]} />));
    expect(host.textContent).toContain("Preparing model setup");
    expect(host.querySelector('[role="progressbar"]')?.hasAttribute("aria-valuenow")).toBe(false);
  });

  it("prioritizes actual current model-file bytes and hides generic ETA", () => {
    const modelTask: TaskRecord = {
      ...runningTask,
      id: "model-install",
      kind: "model-install",
      title: "Install processing models",
      statusMessage: "Downloading pinned model 1 of 6: separator",
      stageTitle: "Download and verify pinned models",
      etaSeconds: 31,
    };
    const output: TaskOutputLine = {
      sequence: 1,
      timestampMillis: 5_000,
      taskId: modelTask.id,
      stream: "stderr",
      stage: "download-and-verify",
      text: "5%|▍| 78.2M/1.57G [07:02<6:15:11, 39.3kiB/s]",
    };
    act(() => root.render(<Harness tasks={[modelTask]} output={{ [modelTask.id]: [output] }} />));
    expect(host.textContent).toContain("78.2 MB / 1.57 GB");
    expect(host.textContent).not.toContain("About 31s left");
  });

  it("centers and focuses an explicitly requested active task once", () => {
    const tasks = Array.from({ length: 40 }, (_, index): TaskRecord => ({
      ...runningTask,
      id: `active-${index}`,
      title: `Active task ${index}`,
      updatedAtMillis: index,
    }));
    act(() => root.render(<Harness tasks={tasks} initialSelectedTaskId="active-34" initialFocusTaskId="active-34" />));
    const selected = host.querySelector<HTMLElement>('[aria-label="Active task 34, running task"]');
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "center", inline: "nearest" });
    expect(document.activeElement).toBe(selected);
    act(() => root.render(<Harness tasks={tasks} initialSelectedTaskId="active-34" initialFocusTaskId="active-34" />));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("centers an explicitly requested Issue but does not focus cards during ordinary browsing", () => {
    const issues = Array.from({ length: 30 }, (_, index) => issue(`issue-${index}`));
    act(() => root.render(<Harness tasks={[]} issues={issues} initialTab="issues" initialFocusIssueId="issue-24" />));
    const selected = host.querySelector<HTMLElement>('[aria-label="Issue issue-24, issue"]');
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(selected);

    act(() => root.unmount());
    host.remove();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    scrollIntoView.mockClear();
    act(() => root.render(<Harness tasks={[]} issues={issues} initialTab="issues" />));
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(document.activeElement?.textContent).toBe("Activity");
  });

  it("loads a generically linked task by ID and renders exact output inline in its Issue", () => {
    act(() => root.render(<IssueOutputHarness />));
    expect(host.textContent).not.toContain("Generic subsystem task");
    const view = [...host.querySelectorAll("button")].find((button) => button.textContent === "View output")!;
    act(() => view.click());
    const issueCard = host.querySelector<HTMLElement>('[aria-label="Issue linked, issue"]')!;
    expect(issueCard.textContent).toContain("exact retained diagnostic");
    expect(issueCard.textContent).toContain("Hide output");
    expect(issueCard.querySelector(".task-output")?.getAttribute("aria-live")).toBe("off");
    expect(host.querySelector('[role="tab"]')?.textContent).toContain("Tasks");
    expect(host.querySelectorAll('[role="tab"]')[1].getAttribute("aria-selected")).toBe("true");
  });

  it("expands output under only the selected Issue when task IDs are shared", () => {
    const sharedTask: TaskRecord = {
      ...runningTask,
      id: "shared-task",
      status: "failed",
      cancellable: false,
      finishedAtMillis: 5_000,
    };
    const first = issue("first", sharedTask.id);
    const second = issue("second", sharedTask.id);
    const output: TaskOutputLine = {
      sequence: 1,
      timestampMillis: 5_000,
      taskId: sharedTask.id,
      stream: "stderr",
      text: "shared exact output",
    };
    act(() => root.render(
      <Harness
        tasks={[sharedTask]}
        issues={[first, second]}
        output={{ [sharedTask.id]: [output] }}
        initialTab="issues"
        initialSelectedTaskId={sharedTask.id}
        initialSelectedIssueId={first.id}
      />,
    ));
    expect(host.querySelectorAll(".task-output")).toHaveLength(1);
    expect(host.querySelector('[aria-label="Issue first, issue"]')?.textContent).toContain("Hide output");
    expect(host.querySelector('[aria-label="Issue first, issue"]')?.textContent).toContain("shared exact output");
    expect(host.querySelector('[aria-label="Issue second, issue"]')?.textContent).toContain("View output");
    expect(host.querySelector('[aria-label="Issue second, issue"]')?.textContent).not.toContain("shared exact output");
    const secondView = [...host.querySelector<HTMLElement>('[aria-label="Issue second, issue"]')!.querySelectorAll("button")]
      .find((button) => button.textContent === "View output")!;
    act(() => secondView.click());
    expect(host.querySelectorAll(".task-output")).toHaveLength(1);
    expect(host.querySelector('[aria-label="Issue first, issue"]')?.textContent).not.toContain("shared exact output");
    expect(host.querySelector('[aria-label="Issue second, issue"]')?.textContent).toContain("shared exact output");
  });

  it("offers no output action when a setup Issue has no task yet", () => {
    const setup = {
      ...issue("setup"),
      code: "processing.models-missing",
      title: "Processing setup required",
      severity: "blocking" as const,
      actions: [{ kind: "install-models" as const, label: "Install processing models", requiresConfirmation: true }],
    };
    act(() => root.render(<Harness tasks={[]} issues={[setup]} initialTab="issues" />));
    expect(host.textContent).toContain("Processing setup required");
    expect(host.textContent).not.toContain("View output");
    expect(host.textContent).toContain("Install processing models");
  });
});
