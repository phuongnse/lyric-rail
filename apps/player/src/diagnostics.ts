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
const SAFE_ISSUE_CODES = new Set([
  "processing.models-missing", "processing.runtime-repair-required", "processing.runtime-startup", "processing.job-failed",
  "drive.unavailable", "runtime.invalid", "remote.invalid",
  "tasks.task-output-could-not-be-replayed", "system.lyricrail-could-not-refresh", "system.action-failed",
  "library.package-import-failed", "library.startup-package-import-failed", "library.local-source-scan-failed", "library.library-search-failed",
  "library.library-item-could-not-be-deleted", "library.edit-video-unavailable", "library.video-changes-could-not-be-saved", "library.library-source-could-not-be-removed", "library.compatible-preview-failed", "library.files-could-not-be-added", "library.folder-could-not-be-added", "library.library-sources-could-not-be-rescanned",
  "recovery.library-refresh-after-recovery-failed", "recovery.recovery-bundle-could-not-be-exported", "recovery.recovery-bundle-could-not-be-restored",
  "drive.drive-source-scan-failed", "drive.drive-unavailable", "drive.google-drive-could-not-connect",
  "view.fullscreen-could-not-be-changed", "lyrics.lyrics-could-not-be-queued", "clip.songs-could-not-be-added",
  "processing.ai-processing-could-not-start", "processing.song-retry-failed",
  "playback.song-could-not-be-opened", "playback.playback-could-not-start", "playback.audio-track-could-not-resume", "playback.video-playback-failed", "playback.audio-playback-failed", "player.playback-failed",
  "settings.settings-location-could-not-be-selected", "settings.settings-could-not-be-saved", "issues.issue-could-not-be-dismissed", "issues.diagnostics-could-not-be-copied",
  "tasks.could-not-cancel-clip-preparation", "tasks.could-not-cancel-video-preview", "tasks.linked-task-output-could-not-be-opened", "tasks.linked-task-output-is-no-longer-available", "tasks.could-not-cancel-task", "tasks.could-not-pause-task", "tasks.could-not-resume-task", "tasks.task-output-could-not-be-copied",
]);
const SAFE_ISSUE_SCOPES = new Set(["system", "tasks", "library", "recovery", "drive", "view", "lyrics", "clip", "playback", "processing", "settings", "issues", "player"]);
const SAFE_ACTION_KINDS = new Set(["install-models", "retry-item", "reconnect-drive"]);

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

function safeIssueCode(value: string | null | undefined): string {
  return value && SAFE_ISSUE_CODES.has(value) ? value : "producer-code-unavailable";
}

function safeIssueScope(value: string | null | undefined): string {
  return value && SAFE_ISSUE_SCOPES.has(value) ? value : "unknown-scope";
}

function safeActionKind(value: string): string {
  return SAFE_ACTION_KINDS.has(value) ? value : "unknown-action";
}

function safeReference(value: string | null | undefined): string {
  return value ? "available" : "none";
}

function safeContractMetadata(value: string | null | undefined, maximum = MAX_DIAGNOSTIC_FIELD_CHARS): string {
  if (!value) return "unknown";
  const normalized = compact(value, maximum);
  const projected = projectDiagnostic(normalized);
  return projected === contract.withheld ? contract.withheld : projected;
}

function safeIssueContext(issue: SystemIssue): string[] {
  const actions = issue.actions.length === 0
    ? "none"
    : issue.actions.map((action) => safeActionKind(action.kind)).join(", ");
  return [
    "Raw technical text was withheld for privacy.",
    `Issue code: ${safeIssueCode(issue.code)}`,
    `Scope: ${safeIssueScope(issue.scope)}`,
    `Related task: ${safeReference(issue.relatedTaskId)}`,
    `Available actions: ${actions}`,
    issue.relatedTaskId
      ? "Open View output for bounded task diagnostics."
      : "Use the issue summary and available action to continue.",
  ];
}

export function formatIssueDetail(issue: SystemIssue): string {
  const detail = safeDiagnostic(issue.detail);
  return detail === contract.withheld || !issue.detail
    ? safeIssueContext(issue).join("\n")
    : detail;
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
    : issue.actions.map((action) => `${safeActionKind(action.kind)}${action.requiresConfirmation ? " (confirmation required)" : ""}`).join(", ");
  const boundedTasks = tasks.slice(0, MAX_ISSUE_DIAGNOSTIC_TASKS);
  const issueDetail = safeDiagnostic(issue.detail);
  const lines = [
    "LyricRail diagnostics v1",
    `Captured: ${timestamp(capturedAtMillis)}`,
    `Version: ${safeIdentifier(status?.version, 120)}`,
    `Platform: ${safeIdentifier(status?.platform, 80)}`,
    `Vault available: ${availability(status?.vaultAvailable)}`,
    "",
    "Issue",
    `Code: ${safeIssueCode(issue.code)}`,
    `Scope: ${safeIssueScope(issue.scope)}`,
    `Severity: ${safeIdentifier(issue.severity, 40)}`,
    `State: ${safeIdentifier(issue.state, 40)}`,
    `Occurrences: ${numberValue(issue.occurrences)}`,
    `Created: ${timestamp(issue.createdAtMillis)}`,
    `Updated: ${timestamp(issue.updatedAtMillis)}`,
    `Title: ${safeContractMetadata(issue.title, 240)}`,
    `Summary: ${safeContractMetadata(issue.summary)}`,
    `Detail: ${issueDetail}`,
    ...(!issue.detail || issueDetail === contract.withheld
      ? safeIssueContext(issue).map((line) => `  ${line}`)
      : []),
    `Progress: ${numberValue(issue.progressPercent)}%`,
    `Progress message: ${safeDiagnostic(issue.progressMessage)}`,
    `Actions: ${actions}`,
    `Related item: ${safeReference(issue.relatedItemId)}`,
    `Related task: ${safeReference(issue.relatedTaskId)}`,
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
