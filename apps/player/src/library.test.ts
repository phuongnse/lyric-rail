import { describe, expect, it } from "vitest";
import {
  activeProcessingTasksByItem,
  adjacentReadyItem,
  shuffledReadyItem,
  type LibraryItem,
  visibleRange,
} from "./library";
import type { TaskRecord } from "./tasks";

function item(id: string, status: LibraryItem["status"]): LibraryItem {
  return {
    id,
    title: id,
    status,
    progressPercent: 0,
    hasThumbnail: false,
    canProcess: false,
    sources: ["Disk"],
  };
}

describe("unified library ordering", () => {
  const items = [item("a", "ready"), item("b", "processing"), item("c", "ready")];

  it("skips non-ready queue rows for next and previous", () => {
    expect(adjacentReadyItem(items, "a", 1)?.id).toBe("c");
    expect(adjacentReadyItem(items, "a", -1)?.id).toBe("c");
  });

  it("shuffle never selects processing rows or the current row", () => {
    expect(shuffledReadyItem(items, "a", () => 0)?.id).toBe("c");
  });

  it("renders a bounded window for a ten-thousand item catalog", () => {
    const range = visibleRange(10_000, 50_000, 720, 92);
    expect(range.start).toBeGreaterThan(0);
    expect(range.end - range.start).toBeLessThan(20);
  });

  it("projects only active processing tasks into Library row actions", () => {
    const task = (id: string, status: TaskRecord["status"]): TaskRecord => ({
      id,
      kind: "processing",
      title: id,
      status,
      progressMode: "indeterminate",
      cancellable: status === "queued" || status === "running",
      relatedItemId: id,
      startedAtMillis: 1,
      updatedAtMillis: 1,
      outputLineCount: 0,
      outputTruncated: false,
    });
    const projected = activeProcessingTasksByItem([
      task("queued", "queued"),
      task("running", "running"),
      task("ready-row", "succeeded"),
      task("cancelled-row", "cancelled"),
      { ...task("scan", "running"), kind: "local-scan", relatedItemId: undefined },
    ]);
    expect([...projected.keys()]).toEqual(["queued", "running"]);
  });
});
