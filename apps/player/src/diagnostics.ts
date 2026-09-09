import contract from "../../../src/lyricrail/diagnostic_contract.json";
import type { SystemIssue } from "./issues";
import type { TaskOutputLine, TaskRecord } from "./tasks";

export const MAX_ISSUE_DIAGNOSTIC_TASKS = 3;
export const MAX_ISSUE_DIAGNOSTIC_OUTPUT_LINES = 200;
export const MAX_ISSUE_DIAGNOSTIC_BYTES = 128 * 1024;

export type DiagnosticPlayerStatus = {
  version: string;
  platform: string;
  vaultAvailable: boolean;
  processing: {
    pendingJobs: number;
    runtimeAvailable: boolean;
    runtimeError?: string;
  };
};

export type IssueDiagnosticTask = {
  task: TaskRecord;
  lines: TaskOutputLine[];
  truncated: boolean;
};

const textEncoder = new TextEncoder();
const MAX_DIAGNOSTIC_FIELD_CHARS = 4_000;
const SAFE_METADATA = Object.values(contract.safeMetadata).flat();

function compact(value: string | null | undefined, maximum = MAX_DIAGNOSTIC_FIELD_CHARS): string {
  return (value || "unknown")
    .replace(/[\r\n\t]+/g, " ")
    .replace(/ {2,}/g, " ")
    .trim()
    .slice(0, maximum) || "unknown";
}

function safeDiagnostic(value: string | null | undefined): string {
  return value ? compact(projectDiagnostic(value)) : "unknown";
}

function timestamp(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "unknown";
  try {
    return new Date(value).toISOString();
  } catch {
    return "unknown";
  }
}

function numberValue(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "unknown";
}

function availability(value: boolean | undefined): string {
  return value == null ? "unknown" : value ? "yes" : "no";
}

function safeIdentifier(value: string | null | undefined, maximum = 180): string {
  const normalized = compact(value, maximum);
  return /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(normalized)
    ? normalized
    : contract.withheld;
}

function safeContractMetadata(value: string | null | undefined, maximum = MAX_DIAGNOSTIC_FIELD_CHARS): string {
  if (!value) return "unknown";
  const normalized = compact(value, maximum);
  const projected = projectDiagnostic(normalized);
  return projected === contract.withheld ? contract.withheld : projected;
}

export function selectDiagnosticTasks(issue: SystemIssue, tasks: TaskRecord[]): TaskRecord[] {
  const candidates = tasks.filter((task) => (
    task.id === issue.relatedTaskId
    || Boolean(issue.relatedItemId && task.relatedItemId === issue.relatedItemId)
  ));
  const seen = new Set<string>();
  return candidates
    .sort((left, right) => (
      (left.id === issue.relatedTaskId ? 0 : 1) - (right.id === issue.relatedTaskId ? 0 : 1)
      || right.updatedAtMillis - left.updatedAtMillis
    ))
    .filter((task) => {
      if (seen.has(task.id)) return false;
      seen.add(task.id);
      return true;
    })
    .slice(0, MAX_ISSUE_DIAGNOSTIC_TASKS);
}

function taskReportLines(entry: IssueDiagnosticTask): string[] {
  const { task } = entry;
  const lines = [...entry.lines]
    .sort((left, right) => left.sequence - right.sequence)
    .slice(-MAX_ISSUE_DIAGNOSTIC_OUTPUT_LINES);
  const outputTruncated = entry.truncated || task.outputTruncated || entry.lines.length > lines.length;
  return [
    `Task ${safeIdentifier(task.id)}`,
    `  Kind: ${safeIdentifier(task.kind, 80)}`,
    `  Title: ${safeContractMetadata(task.title, 240)}`,
    `  Status: ${safeIdentifier(task.status, 40)}`,
    `  Stage: ${safeDiagnostic(task.stageKey)}`,
    `  Stage title: ${safeContractMetadata(task.stageTitle, 240)}`,
    `  Progress: ${numberValue(task.progressPercent)}% (stage ${numberValue(task.stageProgressPercent)}%)`,
    `  Units: ${numberValue(task.completedUnits)} / ${numberValue(task.totalUnits)} ${safeIdentifier(task.unitLabel, 80)}`,
    `  Related item: ${safeIdentifier(task.relatedItemId)}`,
    `  Started: ${timestamp(task.startedAtMillis)}`,
    `  Updated: ${timestamp(task.updatedAtMillis)}`,
    `  Finished: ${timestamp(task.finishedAtMillis)}`,
    `  Status message: ${safeDiagnostic(task.statusMessage)}`,
    `  Output retained: ${lines.length} line(s); truncated: ${outputTruncated ? "yes" : "no"}`,
    "  Output:",
    ...(lines.length === 0
      ? ["    (none)"]
      : lines.map((line) => (
        `    ${timestamp(line.timestampMillis)} ${line.stream}/${safeDiagnostic(line.stage)}: ${safeDiagnostic(line.text)}`
      ))),
  ];
}

function boundReport(lines: string[]): string {
  const retained: string[] = [];
  for (const line of lines) {
    const candidate = [...retained, line].join("\n");
    if (textEncoder.encode(candidate).byteLength > MAX_ISSUE_DIAGNOSTIC_BYTES) break;
    retained.push(line);
  }
  if (retained.length < lines.length) {
    const marker = "[Further diagnostics omitted at the report size limit.]";
    while (retained.length && textEncoder.encode([...retained, marker].join("\n")).byteLength > MAX_ISSUE_DIAGNOSTIC_BYTES) retained.pop();
    retained.push(marker);
  }
  return retained.join("\n");
}

export function formatIssueDiagnostics({
  status,
  issue,
  tasks = [],
  capturedAtMillis = Date.now(),
}: {
  status?: DiagnosticPlayerStatus;
  issue: SystemIssue;
  tasks?: IssueDiagnosticTask[];
  capturedAtMillis?: number;
}): string {
  const processing = status?.processing;
  const actions = issue.actions.length === 0
    ? "none"
    : issue.actions.map((action) => `${action.kind}${action.requiresConfirmation ? " (confirmation required)" : ""}`).join(", ");
  const boundedTasks = tasks.slice(0, MAX_ISSUE_DIAGNOSTIC_TASKS);
  const lines = [
    "LyricRail diagnostics v1",
    `Captured: ${timestamp(capturedAtMillis)}`,
    `Version: ${safeIdentifier(status?.version, 120)}`,
    `Platform: ${safeIdentifier(status?.platform, 80)}`,
    `Vault available: ${availability(status?.vaultAvailable)}`,
    "",
    "Issue",
    `Code: ${safeIdentifier(issue.code, 120)}`,
    `Scope: ${safeIdentifier(issue.scope, 80)}`,
    `Severity: ${safeIdentifier(issue.severity, 40)}`,
    `State: ${safeIdentifier(issue.state, 40)}`,
    `Occurrences: ${numberValue(issue.occurrences)}`,
    `Created: ${timestamp(issue.createdAtMillis)}`,
    `Updated: ${timestamp(issue.updatedAtMillis)}`,
    `Title: ${safeContractMetadata(issue.title, 240)}`,
    `Summary: ${safeContractMetadata(issue.summary)}`,
    `Detail: ${safeDiagnostic(issue.detail)}`,
    `Progress: ${numberValue(issue.progressPercent)}%`,
    `Progress message: ${safeDiagnostic(issue.progressMessage)}`,
    `Actions: ${actions}`,
    `Related item: ${safeIdentifier(issue.relatedItemId)}`,
    `Related task: ${safeIdentifier(issue.relatedTaskId)}`,
    "",
    "Processing",
    `Runtime available: ${availability(processing?.runtimeAvailable)}`,
    `Pending jobs: ${numberValue(processing?.pendingJobs)}`,
    `Runtime detail: ${safeDiagnostic(processing?.runtimeError)}`,
    "",
    `Tasks (${boundedTasks.length}${tasks.length > boundedTasks.length ? ` of ${tasks.length}` : ""})`,
    ...(boundedTasks.length === 0 ? ["No related task record is available."] : boundedTasks.flatMap(taskReportLines)),
    ...(tasks.length > boundedTasks.length
      ? [`${tasks.length - boundedTasks.length} additional task record(s) omitted by the diagnostics bound.`]
      : []),
  ];
  return boundReport(lines);
}

export function projectDiagnostic(value: string): string {
  if (value === contract.withheld || contract.messages.includes(value) || SAFE_METADATA.includes(value) || Object.values(contract.phases).includes(value)
    || Object.prototype.hasOwnProperty.call(contract.stages, value) || Object.values(contract.stages).includes(value)) return value;
  if (value.length > 16384) return contract.withheld;
  try {
    const item = JSON.parse(value) as Record<string, unknown>;
    if (!item || typeof item !== "object" || item.kind !== contract.progressKind || typeof item.phase !== "string"
      || !Object.prototype.hasOwnProperty.call(contract.phases, item.phase)) return contract.withheld;
    if (Object.keys(item).some((key) => !["kind", "phase", "message"].includes(key) && !Object.prototype.hasOwnProperty.call(contract.numericFields, key))) return contract.withheld;
    const output: Record<string, unknown> = { kind: contract.progressKind, phase: item.phase, message: contract.phases[item.phase as keyof typeof contract.phases] };
    for (const [field, maximum] of Object.entries(contract.numericFields)) {
      if (!(field in item)) continue;
      const number = item[field];
      if (typeof number !== "number" || !Number.isFinite(number) || number < 0 || number > maximum) return contract.withheld;
      output[field] = number;
    }
    return JSON.stringify(output);
  } catch { return contract.withheld; }
}
