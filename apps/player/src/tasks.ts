export type TaskKind =
  | "processing"
  | "model-install"
  | "clip-preparation"
  | "local-scan"
  | "drive-scan"
  | "drive-download";

export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type ProgressMode = "indeterminate" | "determinate";
export type OutputStream = "progress" | "stdout" | "stderr" | "system";

export type TaskRecord = {
  id: string;
  kind: TaskKind;
  title: string;
  status: TaskStatus;
  progressMode: ProgressMode;
  stageKey?: string | null;
  stageTitle?: string | null;
  stageProgressPercent?: number | null;
  progressPercent?: number | null;
  completedUnits?: number | null;
  totalUnits?: number | null;
  unitLabel?: string | null;
  etaSeconds?: number | null;
  cancellable: boolean;
  relatedItemId?: string | null;
  startedAtMillis: number;
  updatedAtMillis: number;
  finishedAtMillis?: number | null;
  outputLineCount: number;
  outputTruncated: boolean;
  statusMessage?: string | null;
};

export type TaskOutputLine = {
  sequence: number;
  timestampMillis: number;
  taskId: string;
  stream: OutputStream;
  stage?: string | null;
  text: string;
};

export type TaskSnapshot = {
  sequence: number;
  tasks: TaskRecord[];
  activeTaskCount: number;
  historyCount: number;
};
export type TaskOutputSnapshot = { sequence: number; lines: TaskOutputLine[]; truncated: boolean };
export type TaskRuntimeUpdate = {
  sequence: number;
  tasks: TaskRecord[];
  output: TaskOutputLine[];
  outputGaps: string[];
  outputGapAll: boolean;
  removedTaskIds: string[];
  tasksReset: boolean;
  activeTaskCount: number;
  historyCount: number;
};

export type TaskClientState = {
  sequence: number;
  tasks: TaskRecord[];
  output: Record<string, TaskOutputLine[]>;
  activeTaskCount: number;
  historyCount: number;
};

export const EMPTY_TASK_STATE: TaskClientState = {
  sequence: 0,
  tasks: [],
  output: {},
  activeTaskCount: 0,
  historyCount: 0,
};
const MAX_CLIENT_OUTPUT_LINES = 1_000;
const MAX_CLIENT_OUTPUT_BYTES = 1024 * 1024;
const MAX_CLIENT_OUTPUT_TASKS = 32;
const MAX_CLIENT_TASKS = 250;
const textEncoder = new TextEncoder();

export function normalizeTaskRecord(task: TaskRecord): TaskRecord {
  const hasMeasurement = task.progressPercent != null
    || task.stageProgressPercent != null
    || task.totalUnits != null;
  return {
    ...task,
    progressMode: task.progressMode === "determinate" && !hasMeasurement
      ? "indeterminate"
      : task.progressMode,
    stageKey: task.stageKey ?? undefined,
    stageTitle: task.stageTitle ?? undefined,
    stageProgressPercent: task.stageProgressPercent ?? undefined,
    progressPercent: task.progressPercent ?? undefined,
    completedUnits: task.completedUnits ?? undefined,
    totalUnits: task.totalUnits ?? undefined,
    unitLabel: task.unitLabel ?? undefined,
    etaSeconds: task.etaSeconds ?? undefined,
    relatedItemId: task.relatedItemId ?? undefined,
    finishedAtMillis: task.finishedAtMillis ?? undefined,
    statusMessage: task.statusMessage ?? undefined,
  };
}

function normalizeOutputLine(line: TaskOutputLine): TaskOutputLine {
  return { ...line, stage: line.stage ?? undefined };
}

function boundOutputLines(lines: TaskOutputLine[]): TaskOutputLine[] {
  const bySequence = new Map(lines.map((line) => [line.sequence, line]));
  const sorted = [...bySequence.values()]
    .sort((left, right) => left.sequence - right.sequence)
    .slice(-MAX_CLIENT_OUTPUT_LINES);
  let bytes = 0;
  let start = sorted.length;
  while (start > 0) {
    const lineBytes = textEncoder.encode(sorted[start - 1].text).byteLength;
    if (bytes + lineBytes > MAX_CLIENT_OUTPUT_BYTES) break;
    bytes += lineBytes;
    start -= 1;
  }
  return sorted.slice(start);
}

function boundOutputTasks(output: Record<string, TaskOutputLine[]>): Record<string, TaskOutputLine[]> {
  return Object.fromEntries(Object.entries(output)
    .sort((left, right) => (right[1][right[1].length - 1]?.sequence ?? 0) - (left[1][left[1].length - 1]?.sequence ?? 0))
    .slice(0, MAX_CLIENT_OUTPUT_TASKS));
}

function newestFirst(tasks: TaskRecord[]): TaskRecord[] {
  const priority = (task: TaskRecord) => task.status === "running" ? 0 : task.status === "queued" ? 2 : 1;
  return [...tasks]
    .sort((left, right) => priority(left) - priority(right) || right.updatedAtMillis - left.updatedAtMillis)
    .slice(0, MAX_CLIENT_TASKS);
}

export function applyTaskSnapshot(
  state: TaskClientState,
  snapshot: TaskSnapshot,
): TaskClientState {
  if (snapshot.sequence < state.sequence) return state;
  const normalizedTasks = snapshot.tasks.map(normalizeTaskRecord);
  const visible = new Set(normalizedTasks.map((task) => task.id));
  return {
    ...state,
    sequence: snapshot.sequence,
    tasks: newestFirst(normalizedTasks),
    output: Object.fromEntries(Object.entries(state.output).filter(([taskId]) => visible.has(taskId))),
    activeTaskCount: snapshot.activeTaskCount,
    historyCount: snapshot.historyCount,
  };
}

export function applyTaskUpdate(
  state: TaskClientState,
  update: TaskRuntimeUpdate,
): TaskClientState {
  if (update.sequence <= state.sequence) return state;
  const removed = new Set(update.removedTaskIds);
  const normalizedTasks = update.tasks.map(normalizeTaskRecord);
  const replacements = new Map(normalizedTasks.map((task) => [task.id, task]));
  const tasks = newestFirst([
    ...(update.tasksReset ? [] : state.tasks)
      .filter((task) => !removed.has(task.id))
      .map((task) => replacements.get(task.id) ?? task),
    ...normalizedTasks.filter((task) => update.tasksReset || !state.tasks.some((existing) => existing.id === task.id)),
  ]);
  const output = update.tasksReset ? {} : { ...state.output };
  for (const taskId of removed) delete output[taskId];
  const additions = new Map<string, TaskOutputLine[]>();
  for (const rawLine of update.output) {
    const line = normalizeOutputLine(rawLine);
    const current = additions.get(line.taskId) ?? [];
    current.push(line);
    additions.set(line.taskId, current);
  }
  for (const [taskId, lines] of additions) {
    output[taskId] = boundOutputLines([...(output[taskId] ?? []), ...lines]);
  }
  return {
    sequence: update.sequence,
    tasks,
    output: boundOutputTasks(output),
    activeTaskCount: update.activeTaskCount,
    historyCount: update.historyCount,
  };
}

export function mergeOutputSnapshot(
  state: TaskClientState,
  taskId: string,
  snapshot: TaskOutputSnapshot,
): TaskClientState {
  const existing = state.output[taskId] ?? [];
  return {
    ...state,
    output: boundOutputTasks({
      ...state.output,
      [taskId]: boundOutputLines([...existing, ...snapshot.lines.map(normalizeOutputLine)]),
    }),
  };
}

export function elapsedSeconds(task: TaskRecord, nowMillis: number): number {
  return Math.max(0, Math.floor(((task.finishedAtMillis ?? nowMillis) - task.startedAtMillis) / 1000));
}

export function formatTaskDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours > 0
    ? `${hours}h ${String(minutes).padStart(2, "0")}m ${String(remainder).padStart(2, "0")}s`
    : minutes > 0
      ? `${minutes}m ${String(remainder).padStart(2, "0")}s`
      : `${remainder}s`;
}

export function visibleTasks(tasks: TaskRecord[], nowMillis: number): TaskRecord[] {
  return tasks.filter((task) => task.status !== "running" && task.status !== "queued"
    || nowMillis - task.startedAtMillis >= 400);
}

export function filterTaskOutput(
  lines: TaskOutputLine[],
  filter: "all" | OutputStream,
): TaskOutputLine[] {
  return filter === "all" ? lines : lines.filter((line) => line.stream === filter);
}

export function taskOutputNeedsReplay(update: TaskRuntimeUpdate, taskId: string | undefined): boolean {
  return Boolean(taskId && (update.outputGapAll || update.outputGaps.includes(taskId)));
}
