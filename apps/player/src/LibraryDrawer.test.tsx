// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LibraryDrawer } from "./App";
import type { SystemIssue } from "./issues";
import { issueForLibraryItem, sourceDisplayLabel, type CatalogSnapshot, type LibraryItem } from "./library";

const item: LibraryItem = {
  id: "song",
  title: "Song",
  status: "ready",
  progressPercent: 100,
  hasThumbnail: false,
  canProcess: false,
  sources: ["Disk", "Drive"],
};

const catalog: CatalogSnapshot = {
  items: [item],
  localSources: [{ id: "local", path: "C:\\Music" }],
  driveSources: [{ id: "drive", name: "Google Drive" }],
};

describe("Library source groups", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
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

  it("maps source labels without changing catalog data", () => {
    expect(sourceDisplayLabel("Disk")).toBe("Local");
    expect(sourceDisplayLabel("Drive")).toBe("Cloud · Google Drive");
    expect(sourceDisplayLabel("Other provider")).toBe("Other provider");
  });

  it("routes failed and setup-required items to applicable Issues without task-kind branching", () => {
    const setupIssue: SystemIssue = {
      id: "setup",
      code: "processing.models-missing",
      scope: "processing",
      severity: "blocking",
      title: "Processing setup required",
      summary: "Install models.",
      state: "open",
      occurrences: 1,
      createdAtMillis: 1,
      updatedAtMillis: 1,
      actions: [{ kind: "install-models", label: "Install processing models", requiresConfirmation: true }],
    };
    const failedIssue: SystemIssue = {
      ...setupIssue,
      id: "failed",
      code: "processing.job-failed",
      severity: "error",
      relatedItemId: "failed-song",
      actions: [],
    };
    expect(issueForLibraryItem({ ...item, id: "setup-song", status: "setup-required" }, [setupIssue, failedIssue])?.id).toBe("setup");
    expect(issueForLibraryItem({ ...item, id: "failed-song", status: "failed" }, [setupIssue, failedIssue])?.id).toBe("failed");
    expect(issueForLibraryItem(item, [setupIssue, failedIssue])).toBeUndefined();
  });

  it("groups exact existing callbacks under Local and Cloud menus", () => {
    const addFiles = vi.fn();
    const addFolder = vi.fn();
    const connectDrive = vi.fn();
    act(() => root.render(
      <LibraryDrawer
        open
        items={[item]}
        catalog={catalog}
        tasksByItem={new Map()}
        selectedId={undefined}
        currentId={undefined}
        query=""
        busy={false}
        blocked={false}
        onClose={() => undefined}
        onRescan={() => undefined}
        onQuery={() => undefined}
        onSelect={() => undefined}
        onPlay={() => undefined}
        onAddFiles={addFiles}
        onAddFolder={addFolder}
        onDrive={connectDrive}
        onLyricsFile={() => undefined}
        onLyricsPaste={() => undefined}
        onEditLyrics={() => undefined}
        onRetry={() => undefined}
        onShowContext={() => undefined}
        onRemoveSource={() => undefined}
        onRecoveryExport={() => undefined}
        onRecoveryRestore={() => undefined}
      />,
    ));

    const button = (label: string) => [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((candidate) => candidate.textContent === label)!;
    expect(button("Local").getAttribute("aria-expanded")).toBe("false");
    expect(button("Cloud").getAttribute("aria-expanded")).toBe("false");
    expect(host.querySelector('[role="menu"]')).toBeNull();

    act(() => button("Local").click());
    const localMenu = host.querySelector<HTMLElement>('[role="menu"]')!;
    expect(localMenu.getAttribute("aria-label")).toBe("Local sources");
    expect(document.activeElement?.textContent).toBe("Files");
    act(() => localMenu.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true })));
    expect(document.activeElement?.textContent).toBe("Folder");
    act(() => localMenu.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true, cancelable: true })));
    expect(document.activeElement?.textContent).toBe("Files");
    act(() => button("Files").click());
    expect(addFiles).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[role="menu"]')).toBeNull();

    act(() => button("Local").click());
    act(() => button("Folder").click());
    expect(addFolder).toHaveBeenCalledTimes(1);

    act(() => button("Cloud").click());
    expect(host.querySelector('[role="menu"]')?.getAttribute("aria-label")).toBe("Cloud providers");
    expect(button("Google Drive")).toBeTruthy();
    act(() => button("Google Drive").click());
    expect(connectDrive).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[role="menu"]')).toBeNull();

    expect(host.textContent).toContain("Local");
    expect(host.textContent).toContain("Cloud · Google Drive");
    expect(host.textContent).not.toContain(" URL ");
  });

  it("keeps one menu open and returns focus to its trigger on Escape", () => {
    act(() => root.render(
      <LibraryDrawer
        open
        items={[]}
        catalog={{ items: [], localSources: [], driveSources: [] }}
        tasksByItem={new Map()}
        query=""
        busy={false}
        blocked={false}
        onClose={() => undefined}
        onRescan={() => undefined}
        onQuery={() => undefined}
        onSelect={() => undefined}
        onPlay={() => undefined}
        onAddFiles={() => undefined}
        onAddFolder={() => undefined}
        onDrive={() => undefined}
        onLyricsFile={() => undefined}
        onLyricsPaste={() => undefined}
        onEditLyrics={() => undefined}
        onRetry={() => undefined}
        onShowContext={() => undefined}
        onRemoveSource={() => undefined}
        onRecoveryExport={() => undefined}
        onRecoveryRestore={() => undefined}
      />,
    ));
    const buttons = () => [...host.querySelectorAll<HTMLButtonElement>("button")];
    const local = buttons().find((button) => button.textContent === "Local")!;
    const cloud = buttons().find((button) => button.textContent === "Cloud")!;
    act(() => local.click());
    act(() => cloud.click());
    expect(host.querySelector('[aria-label="Local sources"]')).toBeNull();
    expect(host.querySelector('[aria-label="Cloud providers"]')).not.toBeNull();
    const escapedToWindow = vi.fn();
    window.addEventListener("keydown", escapedToWindow);
    act(() => document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
    expect(host.querySelector('[role="menu"]')).toBeNull();
    expect(document.activeElement).toBe(cloud);
    expect(escapedToWindow).not.toHaveBeenCalled();
    window.removeEventListener("keydown", escapedToWindow);
  });
});
