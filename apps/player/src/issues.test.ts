import { describe, expect, it } from "vitest";
import { clientIssue, mergeIssueSources, safeIssueDetail, shouldShowIssueNotice, upsertIssue } from "./issues";

describe("system issue model", () => {
  it("deduplicates a repeated client failure and keeps its first timestamp", () => {
    const first = clientIssue("drive", "Drive unavailable", "first");
    const second = { ...clientIssue("drive", "Drive unavailable", "second"), updatedAtMillis: first.updatedAtMillis + 1 };
    const issues = upsertIssue(upsertIssue([], first), second);
    expect(issues).toHaveLength(1);
    expect(issues[0].occurrences).toBe(2);
    expect(issues[0].createdAtMillis).toBe(first.createdAtMillis);
  });

  it("retains distinct failures that share one scope", () => {
    const video = clientIssue("playback", "Video playback failed", "video");
    const audio = clientIssue("playback", "Audio playback failed", "audio");
    const issues = upsertIssue(upsertIssue([], video), audio);
    expect(issues).toHaveLength(2);
    expect(new Set(issues.map((issue) => issue.code)).size).toBe(2);
  });

  it("redacts remote addresses, credentials and Windows paths from secondary detail", () => {
    const detail = safeIssueDetail("failed at C:\\Music Library\\Private Song.mp4 trailing-name.mp4");
    const remote = safeIssueDetail("https://host/path?token=x authorization=Bearer-secret api_key=TOPSECRET x-goog-signature=GOOG signature=RAW");
    expect(detail).not.toContain("secret.txt");
    expect(detail).not.toContain("Private Song.mp4");
    expect(detail).not.toContain("trailing-name.mp4");
    expect(remote).not.toContain("token=x");
    expect(remote).not.toContain("Bearer-secret");
    expect(remote).not.toContain("TOPSECRET");
    expect(remote).not.toContain("GOOG");
    expect(remote).not.toContain("RAW");
    expect(detail).toContain("<local path>");
    expect(remote).toContain("<remote address>");
    expect(safeIssueDetail("failed at /home/name/private.txt")).toBe("failed at <local path>");
  });

  it("places native and client issues into one newest-first center", () => {
    const client = clientIssue("player", "Playback failed", "detail");
    const native = { ...client, id: "native", relatedTaskId: "task-id", updatedAtMillis: client.updatedAtMillis + 1 };
    const merged = mergeIssueSources([native], [client]);
    expect(merged.map((issue) => issue.id)).toEqual(["native", client.id]);
    expect(merged[0].native).toBe(true);
    expect(merged[0].relatedTaskId).toBe("task-id");
  });

  it("never exposes an issue notice above an open modal", () => {
    const issue = clientIssue("system", "Action failed", "detail");
    expect(shouldShowIssueNotice(true, false, issue, undefined)).toBe(false);
    expect(shouldShowIssueNotice(false, true, issue, undefined)).toBe(false);
    expect(shouldShowIssueNotice(false, false, issue, undefined)).toBe(true);
  });
});
