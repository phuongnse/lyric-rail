import { describe, expect, it } from "vitest";
import {
  EMPTY_TASK_STATE,
  applyTaskSnapshot,
  applyTaskUpdate,
  elapsedSeconds,
  filterTaskOutput,
  formatTaskDuration,
  mergeOutputSnapshot,
  normalizeTaskRecord,
  taskOutputNeedsReplay,
  visibleTasks,
  type TaskOutputLine,
  type TaskRecord,
} from "./tasks";

const task = (id: string, updatedAtMillis = 1): TaskRecord => ({
  id,
  kind: "processing",
  title: id,
  status: "running",
  progressMode: "determinate",
  progressPercent: 10,
  cancellable: true,
  startedAtMillis: 0,
  updatedAtMillis,
  outputLineCount: 0,
  outputTruncated: false,
});

const line = (sequence: number, stream: TaskOutputLine["stream"] = "stdout"): TaskOutputLine => ({
  sequence,
  timestampMillis: sequence,
  taskId: "a",
  stream,
  text: String(sequence),
});

describe("task client runtime", () => {
  it("applies snapshot then gap-free live updates and rejects stale delivery", () => {
    let state = applyTaskSnapshot(EMPTY_TASK_STATE, { sequence: 4, tasks: [task("a")], activeTaskCount: 1, historyCount: 0 });
    state = applyTaskUpdate(state, { sequence: 6, tasks: [{ ...task("a"), progressPercent: 30 }], output: [line(5), line(6)], outputGaps: [], outputGapAll: false, removedTaskIds: [], tasksReset: false, activeTaskCount: 1, historyCount: 0 });
    expect(state.tasks[0].progressPercent).toBe(30);
    expect(state.output.a.map((entry) => entry.sequence)).toEqual([5, 6]);
    expect(applyTaskUpdate(state, { sequence: 5, tasks: [], output: [line(5)], outputGaps: [], outputGapAll: false, removedTaskIds: [], tasksReset: false, activeTaskCount: 1, historyCount: 0 })).toBe(state);
  });

  it("merges replay without duplicating live output", () => {
    const live = applyTaskUpdate(
      { ...EMPTY_TASK_STATE, tasks: [task("a")] },
      { sequence: 3, tasks: [], output: [line(2), line(3)], outputGaps: [], outputGapAll: false, removedTaskIds: [], tasksReset: false, activeTaskCount: 1, historyCount: 0 },
    );
    const merged = mergeOutputSnapshot(live, "a", { sequence: 3, lines: [line(1), line(2)], truncated: false });
    expect(merged.output.a.map((entry) => entry.sequence)).toEqual([1, 2, 3]);
  });

  it("formats exact elapsed time, hides fast running work and filters streams", () => {
    expect(elapsedSeconds(task("a"), 65_000)).toBe(65);
    expect(formatTaskDuration(65)).toBe("1m 05s");
    expect(visibleTasks([task("a")], 399)).toHaveLength(0);
    expect(visibleTasks([task("a")], 400)).toHaveLength(1);
    expect(filterTaskOutput([line(1), line(2, "stderr")], "stderr")).toEqual([line(2, "stderr")]);
  });

  it("normalizes JSON null as absent while preserving real zero measurements", () => {
    const nullable = normalizeTaskRecord({
      ...task("model-install"),
      progressMode: "determinate",
      stageKey: null,
      stageTitle: null,
      stageProgressPercent: null,
      progressPercent: null,
      completedUnits: null,
      totalUnits: null,
      unitLabel: null,
      etaSeconds: null,
      relatedItemId: null,
      finishedAtMillis: null,
      statusMessage: null,
    });
    expect(nullable.progressMode).toBe("indeterminate");
    expect(nullable.stageProgressPercent).toBeUndefined();
    expect(nullable.totalUnits).toBeUndefined();
    expect(nullable.etaSeconds).toBeUndefined();

    const zero = normalizeTaskRecord({
      ...task("zero"),
      progressMode: "determinate",
      stageProgressPercent: 0,
      progressPercent: 0,
      completedUnits: 0,
      totalUnits: 0,
      etaSeconds: 0,
    });
    expect(zero.progressMode).toBe("determinate");
    expect(zero.progressPercent).toBe(0);
    expect(zero.totalUnits).toBe(0);
    expect(zero.etaSeconds).toBe(0);
  });

  it("bounds client output by bytes and active task buffers without per-line sorting", () => {
    const large = Array.from({ length: 80 }, (_, index) => ({
      ...line(index + 1),
      text: "x".repeat(20_000),
    }));
    const bounded = applyTaskUpdate(
      { ...EMPTY_TASK_STATE, tasks: [task("a")] },
      { sequence: 100, tasks: [], output: large, outputGaps: [], outputGapAll: false, removedTaskIds: [], tasksReset: false, activeTaskCount: 1, historyCount: 0 },
    );
    expect(bounded.output.a.length).toBeLessThan(80);
    expect(bounded.output.a.reduce((bytes, entry) => bytes + new TextEncoder().encode(entry.text).byteLength, 0)).toBeLessThanOrEqual(1024 * 1024);

    const manyTasks = applyTaskUpdate(
      EMPTY_TASK_STATE,
      {
        sequence: 200,
        tasks: [],
        output: Array.from({ length: 40 }, (_, index) => ({ ...line(index + 101), taskId: `task-${index}` })),
        outputGaps: [],
        outputGapAll: false,
        removedTaskIds: [],
        tasksReset: false,
        activeTaskCount: 0,
        historyCount: 0,
      },
    );
    expect(Object.keys(manyTasks.output)).toHaveLength(32);
  });

  it("detects a bounded live burst gap and replays the retained ring before continuing", () => {
    const burst = {
      sequence: 300,
      tasks: [task("a", 300)],
      output: Array.from({ length: 51 }, (_, index) => line(index + 250)),
      outputGaps: ["a"],
      outputGapAll: false,
      removedTaskIds: [],
      tasksReset: false,
      activeTaskCount: 1,
      historyCount: 0,
    };
    let state = applyTaskUpdate(
      applyTaskSnapshot(EMPTY_TASK_STATE, { sequence: 1, tasks: [task("a")], activeTaskCount: 1, historyCount: 0 }),
      burst,
    );
    expect(taskOutputNeedsReplay(burst, "a")).toBe(true);
    expect(taskOutputNeedsReplay({ ...burst, outputGaps: [], outputGapAll: true }, "a")).toBe(true);
    expect(state.output.a[0].sequence).toBe(250);
    state = mergeOutputSnapshot(state, "a", {
      sequence: 300,
      lines: Array.from({ length: 300 }, (_, index) => line(index + 1)),
      truncated: false,
    });
    state = applyTaskUpdate(state, {
      sequence: 301,
      tasks: [],
      output: [line(301)],
      outputGaps: [],
      outputGapAll: false,
      removedTaskIds: [],
      tasksReset: false,
      activeTaskCount: 1,
      historyCount: 0,
    });
    expect(state.output.a.map((entry) => entry.sequence)).toEqual(
      Array.from({ length: 301 }, (_, index) => index + 1),
    );
  });

  it("accepts an authoritative bounded task reset after bulk terminal eviction", () => {
    const initial = applyTaskUpdate(
      { ...EMPTY_TASK_STATE, tasks: [task("a")] },
      { sequence: 1, tasks: [], output: [line(1)], outputGaps: [], outputGapAll: false, removedTaskIds: [], tasksReset: false, activeTaskCount: 1, historyCount: 0 },
    );
    const reset = applyTaskUpdate(initial, {
      sequence: 2,
      tasks: [task("b", 2)],
      output: [],
      outputGaps: [],
      outputGapAll: false,
      removedTaskIds: [],
      tasksReset: true,
      activeTaskCount: 1,
      historyCount: 0,
    });
    expect(reset.tasks.map((entry) => entry.id)).toEqual(["b"]);
    expect(reset.output).toEqual({});
  });
});
