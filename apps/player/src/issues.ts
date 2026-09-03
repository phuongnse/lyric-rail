export type IssueSeverity = "warning" | "error" | "blocking";
export type IssueState = "open" | "resolving";
export type IssueResolution = "install-models" | "retry-item" | "reconnect-drive";

export type IssueAction = {
  kind: IssueResolution;
  label: string;
  requiresConfirmation: boolean;
};

export type SystemIssue = {
  id: string;
  code: string;
  scope: string;
  severity: IssueSeverity;
  title: string;
  summary: string;
  detail?: string;
  relatedItemId?: string;
  relatedTaskId?: string;
  state: IssueState;
  progressPercent?: number;
  progressMessage?: string;
  occurrences: number;
  createdAtMillis: number;
  updatedAtMillis: number;
  actions: IssueAction[];
  native?: boolean;
};

const MAX_DETAIL = 4_000;

function issueKind(title: string): string {
  return title
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 64) || "action-failed";
}

export function safeIssueDetail(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  let value = raw
    .replace(/https?:\/\/\S+/gi, "<remote address>")
    .replace(/\bbearer\s+(?:"[^"]*"|'[^']*'|\S+)/gi, "Bearer <redacted>")
    .replace(/"?(token|access_token|refresh_token|id_token|password|secret|client_secret|private_key|authorization|credential|api[-_]?key|apikey|signature|x[-_]goog[-_]signature|x[-_]amz[-_]signature)"?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;}]+)/gi, "$1=<redacted>");
  const windowsPath = value.search(/(?:[A-Za-z]:[\\/]|\\\\)/);
  const unixPath = value.search(/(?:^|\s)\/(?!\/)/);
  const pathIndex = windowsPath < 0 ? unixPath : unixPath < 0 ? windowsPath : Math.min(windowsPath, unixPath);
  if (pathIndex >= 0) value = `${value.slice(0, pathIndex).trimEnd()} <local path>`.trim();
  return value.slice(0, MAX_DETAIL);
}

export function clientIssue(
  scope: string,
  title: string,
  error: unknown,
  summary = "The action could not be completed. Review the details, then try again.",
  action?: IssueAction,
): SystemIssue {
  const now = Date.now();
  const code = `${scope}.${issueKind(title)}`;
  return {
    id: `${code}:${scope}:client`,
    code,
    scope,
    severity: "error",
    title,
    summary,
    detail: safeIssueDetail(error),
    state: "open",
    occurrences: 1,
    createdAtMillis: now,
    updatedAtMillis: now,
    actions: action ? [action] : [],
    native: false,
  };
}

export function upsertIssue(
  issues: SystemIssue[],
  incoming: SystemIssue,
): SystemIssue[] {
  const existing = issues.find((issue) => issue.id === incoming.id);
  const merged = existing
    ? {
        ...incoming,
        createdAtMillis: existing.createdAtMillis,
        occurrences: existing.occurrences + 1,
      }
    : incoming;
  return [merged, ...issues.filter((issue) => issue.id !== incoming.id)]
    .sort((left, right) => right.updatedAtMillis - left.updatedAtMillis)
    .slice(0, 100);
}

export function mergeIssueSources(
  nativeIssues: SystemIssue[],
  clientIssues: SystemIssue[],
): SystemIssue[] {
  return [...nativeIssues.map((issue) => ({ ...issue, native: true })), ...clientIssues]
    .sort((left, right) => right.updatedAtMillis - left.updatedAtMillis)
    .slice(0, 100);
}

export function shouldShowIssueNotice(
  anyModalOpen: boolean,
  issuesOpen: boolean,
  issue: SystemIssue | undefined,
  seenNotice: string | undefined,
): boolean {
  return !anyModalOpen
    && !issuesOpen
    && issue?.state === "open"
    && `${issue.id}:${issue.updatedAtMillis}` !== seenNotice;
}
