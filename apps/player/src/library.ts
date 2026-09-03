import type { SystemIssue } from "./issues";
import type { TaskRecord } from "./tasks";

export type ItemStatus =
  | "ready"
  | "queued"
  | "processing"
  | "waiting-for-lyrics"
  | "setup-required"
  | "failed"
  | "offline";

export type LibraryItem = {
  id: string;
  packageId?: string;
  title: string;
  artist?: string;
  composer?: string;
  firstLyricLine?: string;
  status: ItemStatus;
  progressPercent: number;
  statusMessage?: string;
  hasThumbnail: boolean;
  canProcess: boolean;
  sources: string[];
  lyricSnippet?: string;
};

export type CatalogSnapshot = {
  items: LibraryItem[];
  localSources: { id: string; path: string }[];
  driveSources: { id: string; name: string }[];
};

export function sourceDisplayLabel(source: string): string {
  if (source === "Disk") return "Local";
  if (source === "Drive") return "Cloud · Google Drive";
  return source;
}

export function issueForLibraryItem(item: LibraryItem, issues: SystemIssue[]): SystemIssue | undefined {
  return issues.find((issue) => issue.relatedItemId === item.id)
    ?? (item.status === "setup-required"
      ? issues.find((issue) => issue.actions.some((action) => action.kind === "install-models"))
      : undefined);
}

export function activeProcessingTasksByItem(tasks: TaskRecord[]): Map<string, TaskRecord> {
  return new Map(tasks
    .filter((task) => task.kind === "processing"
      && task.relatedItemId
      && (task.status === "queued" || task.status === "running"))
    .map((task) => [task.relatedItemId!, task]));
}

export function readyItems(items: LibraryItem[]): LibraryItem[] {
  return items.filter((item) => item.status === "ready");
}

export function adjacentReadyItem(
  items: LibraryItem[],
  currentId: string | undefined,
  direction: -1 | 1,
): LibraryItem | undefined {
  const ready = readyItems(items);
  if (!ready.length) return undefined;
  const index = ready.findIndex((item) => item.id === currentId);
  if (index < 0) return ready[direction > 0 ? 0 : ready.length - 1];
  return ready[(index + direction + ready.length) % ready.length];
}

export function shuffledReadyItem(
  items: LibraryItem[],
  currentId: string | undefined,
  random = Math.random,
): LibraryItem | undefined {
  const choices = readyItems(items).filter((item) => item.id !== currentId);
  if (!choices.length) return readyItems(items)[0];
  return choices[Math.floor(random() * choices.length)];
}

export function visibleRange(
  itemCount: number,
  scrollTop: number,
  viewportHeight: number,
  rowHeight: number,
  overscan = 4,
): { start: number; end: number } {
  if (itemCount <= 0 || rowHeight <= 0) return { start: 0, end: 0 };
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const end = Math.min(
    itemCount,
    Math.ceil((scrollTop + viewportHeight) / rowHeight) + overscan,
  );
  return { start, end };
}
