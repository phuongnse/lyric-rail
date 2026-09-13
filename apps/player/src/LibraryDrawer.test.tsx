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
  canProcess: true,
  firstLyricLine: "First lyric line",
  sources: ["Disk", "Drive"],
};

const unfinishedLocalItem: LibraryItem = {
  ...item,
  id: "unfinished",
  title: "Unfinished song",
  status: "queued",
  progressPercent: 0,
  canProcess: true,
  sources: ["Disk"],
  canDelete: true,
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
        onEditVideo={() => undefined}
        onAddFiles={addFiles}
        onAddFolder={addFolder}
        onDrive={connectDrive}
        onRemoveItem={() => undefined}
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
        onEditVideo={() => undefined}
        onAddFiles={() => undefined}
        onAddFolder={() => undefined}
        onDrive={() => undefined}
        onRemoveItem={() => undefined}
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

  it("shows Remove from library only for a native-eligible unfinished local item", () => {
    const onRemoveItem = vi.fn();
    act(() => root.render(
      <LibraryDrawer
        open
        items={[unfinishedLocalItem, item]}
        catalog={{ ...catalog, items: [unfinishedLocalItem, item] }}
        tasksByItem={new Map()}
        query=""
        busy={false}
        blocked={false}
        onClose={() => undefined}
        onRescan={() => undefined}
        onQuery={() => undefined}
        onSelect={() => undefined}
        onPlay={() => undefined}
        onEditVideo={() => undefined}
        onAddFiles={() => undefined}
        onAddFolder={() => undefined}
        onDrive={() => undefined}
        onRemoveItem={onRemoveItem}
        onRemoveSource={() => undefined}
        onRecoveryExport={() => undefined}
        onRecoveryRestore={() => undefined}
      />,
    ));

    const trigger = host.querySelector<HTMLButtonElement>('[aria-label="Open actions for Unfinished song"]')!;
    act(() => trigger.click());
    const deletes = [...host.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .filter((button) => button.textContent === "Delete");
    expect(deletes).toHaveLength(1);
    expect(host.querySelector('[aria-label="Edit video for Unfinished song"]')).toBeNull();
    act(() => deletes[0]!.click());
    expect(onRemoveItem).toHaveBeenCalledWith(unfinishedLocalItem);
  });

  it("plays on row click, shows exact lyrics on hover and keeps only Edit/Delete actions", async () => {
    const onPlay = vi.fn();
    const onEditVideo = vi.fn();
    const onLyricsPreview = vi.fn(async () => "Exact full lyric text");
    act(() => root.render(
      <LibraryDrawer
        open
        items={[{ ...item, title: "Editable song", lyricSnippet: "Matched lyric" }]}
        catalog={{ ...catalog, items: [{ ...item, title: "Editable song", lyricSnippet: "Matched lyric" }] }}
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
        onPlay={onPlay}
        onEditVideo={onEditVideo}
        onLyricsPreview={onLyricsPreview}
        onAddFiles={() => undefined}
        onAddFolder={() => undefined}
        onDrive={() => undefined}
        onRemoveItem={() => undefined}
        onRemoveSource={() => undefined}
        onRecoveryExport={() => undefined}
        onRecoveryRestore={() => undefined}
      />,
    ));
    const row = host.querySelector<HTMLButtonElement>('[aria-label="Play Editable song"]')!;
    const article = row.closest<HTMLElement>(".song-row")!;
    expect(row.querySelector(".lyric-thumbnail")).toBeNull();
    expect(row.querySelector(".thumbnail-fallback")).not.toBeNull();
    expect(row.querySelector(".thumbnail-lyric")?.textContent).toContain("Matched lyric");
    act(() => { row.focus(); row.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: document.body })); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(onLyricsPreview).toHaveBeenCalledWith(expect.objectContaining({ title: "Editable song" }));
    expect(row.querySelector(".thumbnail-lyric")?.textContent).toContain("Exact full lyric text");
    act(() => row.focus());
    act(() => row.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })));
    expect(onPlay).toHaveBeenCalledTimes(1);
    act(() => row.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true })));
    expect(onPlay).toHaveBeenCalledTimes(2);
    act(() => row.click());
    expect(onPlay).toHaveBeenCalledTimes(3);
    expect(host.querySelector('[role="menu"]')).toBeNull();
    const trigger = article.querySelector<HTMLButtonElement>('[aria-label="Open actions for Editable song"]')!;
    act(() => trigger.click());
    expect(onPlay).toHaveBeenCalledTimes(3);
    const menu = article.querySelector<HTMLElement>('[role="menu"]')!;
    expect(menu.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')).toHaveLength(1);
    act(() => menu.querySelector<HTMLButtonElement>('[role="menuitem"]')!.click());
    expect(onEditVideo).toHaveBeenCalledWith(expect.objectContaining({ title: "Editable song" }));
    expect(article.querySelector('[aria-label*="Delete"]')).toBeNull();
  });

  it("refetches the lyric preview after a Library package or lyric revision", async () => {
    const onLyricsPreview = vi.fn()
      .mockResolvedValueOnce("Old exact lyric")
      .mockResolvedValueOnce("New exact lyric");
    const render = (next: LibraryItem) => root.render(
      <LibraryDrawer
        open
        items={[next]}
        catalog={{ ...catalog, items: [next] }}
        tasksByItem={new Map()}
        query=""
        busy={false}
        blocked={false}
        onClose={() => undefined}
        onRescan={() => undefined}
        onQuery={() => undefined}
        onSelect={() => undefined}
        onPlay={() => undefined}
        onEditVideo={() => undefined}
        onLyricsPreview={onLyricsPreview}
        onAddFiles={() => undefined}
        onAddFolder={() => undefined}
        onDrive={() => undefined}
        onRemoveItem={() => undefined}
        onRemoveSource={() => undefined}
        onRecoveryExport={() => undefined}
        onRecoveryRestore={() => undefined}
      />,
    );
    const first = { ...item, packageId: "package", lyricSha256: "hash-old", firstLyricLine: "Same line" };
    await act(async () => render(first));
    let row = host.querySelector<HTMLButtonElement>(".song-row-main")!;
    act(() => row.focus());
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(row.querySelector(".thumbnail-lyric")?.textContent).toContain("Old exact lyric");

    const next = { ...first, packageId: "package", lyricSha256: "hash-new", firstLyricLine: "Same line" };
    await act(async () => render(next));
    row = host.querySelector<HTMLButtonElement>(".song-row-main")!;
    act(() => { row.blur(); row.focus(); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(onLyricsPreview).toHaveBeenCalledTimes(2);
    expect(row.querySelector(".thumbnail-lyric")?.textContent).toContain("New exact lyric");
  });

  it("uses the same action menu for zero, one and multiple available actions", () => {
    const rows = [
      { ...item, id: "none", title: "No actions", canProcess: false },
      { ...item, id: "one", title: "One action" },
      { ...item, id: "two", title: "Two actions", status: "waiting-for-lyrics" as const, canDelete: true },
    ];
    act(() => root.render(
      <LibraryDrawer
        open items={rows} catalog={{ ...catalog, items: rows }} tasksByItem={new Map()} query="" busy={false} blocked={false}
        onClose={() => undefined} onRescan={() => undefined} onQuery={() => undefined} onSelect={() => undefined}
        onPlay={() => undefined} onEditVideo={() => undefined} onAddFiles={() => undefined} onAddFolder={() => undefined}
        onDrive={() => undefined} onRemoveItem={() => undefined} onRemoveSource={() => undefined}
        onRecoveryExport={() => undefined} onRecoveryRestore={() => undefined}
      />,
    ));
    for (const [title, count] of [["No actions", 0], ["One action", 1], ["Two actions", 2]] as const) {
      const row = [...host.querySelectorAll<HTMLElement>(".song-row")]
        .find((candidate) => candidate.textContent?.includes(title))!;
      const trigger = row.querySelector<HTMLButtonElement>('[aria-label^="Open actions for"]')!;
      act(() => trigger.click());
      expect(row.querySelector('[role="menu"]')).not.toBeNull();
      expect(row.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')).toHaveLength(count);
      if (count) expect(document.activeElement).toBe(row.querySelector('[role="menuitem"]'));
      if (count) {
        act(() => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
        expect(document.activeElement).toBe(trigger);
      }
      else act(() => trigger.click());
    }
  });
});
