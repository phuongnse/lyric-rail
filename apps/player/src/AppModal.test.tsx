// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import type { CatalogSnapshot } from "./library";
import type { KaraokePresentation } from "./LyricOverlay";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

const readyCatalog: CatalogSnapshot = { items: [{ id: "song", title: "Song", status: "ready", progressPercent: 100, sources: ["Disk"], canProcess: false, hasThumbnail: false }], localSources: [], driveSources: [] };
let catalogFixture: CatalogSnapshot = readyCatalog;
const presentation: KaraokePresentation = {
  referenceResolution: [1920, 1080],
  layout: { lineMode: "alternating-two-lines", alignment: "top-left-bottom-right", bottomMargin: 84, lineGap: 28, safeAreaPercent: 3.5, maximumLineWidthPercent: 93 },
  font: { family: "Be Vietnam Pro Bold", bold: false, sizeAt1080p: 134, scaleX: 76, scaleY: 124, letterSpacing: 8 },
  roleChangeCue: { enabled: false, dotCount: 0, dotFontSizeAt1080p: 160 },
  unsung: { fill: "#FFFFFF", outerOutline: "#000000", outerOutlineWidth: 20, shadow: "#000000", shadowOffset: 20 },
  sung: { direction: "left-to-right", timing: "syllable", innerOutline: "#FFFFFF", innerOutlineWidth: 16, colors: { male: "#153CFF", female: "#F02A2A", duet: "#FF3D9D" } },
};

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async (command: string) => {
  if (command === "catalog_snapshot") return catalogFixture;
  if (command === "remove_unprocessed_local_item") return { items: [], localSources: [], driveSources: [] };
  if (command === "player_status") return { version: "0.8.0", platform: "windows", vaultAvailable: true, processing: { pendingJobs: 0, runtimeAvailable: true } };
  if (command === "system_issues") return [];
  if (command === "task_runtime_snapshot") return { sequence: 0, tasks: [], activeTaskCount: 0, historyCount: 0 };
  if (command === "open_library_item") return { packageId: "package", metadata: {}, renderPlan: { events: [] }, presentation, media: { videoUrl: "http://fixture/video", audioTracks: [{ id: "karaoke", name: "Karaoke", url: "http://fixture/karaoke", default: true }, { id: "original-reference", name: "Original", url: "http://fixture/original", default: false }] } };
  if (command === "prepare_local_clip") return { clipId: "clip", suggestedTitle: "Song", sizeBytes: 10, durationMillis: 3000, previewUrl: "http://fixture/preview" };
  if (command === "item_lyrics") return "Exact words";
  return null;
}) }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async () => () => {}) }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn(async () => ["song.mp4"]), save: vi.fn() }));

let host: HTMLDivElement;
let root: Root;
beforeEach(async () => {
  catalogFixture = readyCatalog;
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.assign(window, { __TAURI_INTERNALS__: {} });
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as typeof ResizeObserver;
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => { root.render(<App />); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 160)); });
});
afterEach(() => {
  act(() => root.unmount()); host.remove();
  delete (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
  vi.restoreAllMocks();
});

async function checkDialog(launcher: HTMLButtonElement) {
  const dialog = host.querySelector<HTMLElement>('[role="dialog"]')!;
  expect(dialog).not.toBeNull();
  expect(dialog.contains(document.activeElement)).toBe(true);
  expect(host.querySelector(".topbar")?.hasAttribute("inert")).toBe(true);
  expect(host.querySelector("#library-drawer")?.hasAttribute("inert")).toBe(true);
  const buttons = dialog.querySelectorAll<HTMLButtonElement>("button:not([disabled])");
  const first = buttons[0]; const last = buttons[buttons.length - 1];
  vi.mocked(open).mockClear();
  vi.mocked(invoke).mockClear();
  for (const shortcut of [{ key: "o", ctrlKey: true }, { key: "o", metaKey: true }, { key: "o", ctrlKey: true, shiftKey: true }, { key: "F5" }]) {
    await act(async () => {
      first.focus();
      const event = new KeyboardEvent("keydown", { ...shortcut, bubbles: true, cancelable: true });
      first.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
    });
  }
  expect(open).not.toHaveBeenCalled();
  expect(vi.mocked(invoke).mock.calls.some(([command]) => ["prepare_local_clip", "rescan_library", "add_local_files", "add_local_folder"].includes(command))).toBe(false);
  expect(host.querySelector('[role="dialog"]')).toBe(dialog);
  act(() => { last.focus(); last.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true })); });
  expect(document.activeElement).toBe(first);
  act(() => { first.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true })); });
  expect(document.activeElement).toBe(last);
  await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(document.activeElement).toBe(launcher);
}

it("keeps main playback actions in a focused icon-only overlay", async () => {
  const firstSong = readyCatalog.items[0]!;
  await act(async () => root.unmount());
  catalogFixture = {
    ...readyCatalog,
    items: [firstSong, { ...firstSong, id: "song-2", title: "Second song" }],
  };
  root = createRoot(host);
  await act(async () => root.render(<App />));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 160)); });

  const libraryPlay = host.querySelector<HTMLButtonElement>('[aria-label="Play Song"]')!;
  await act(async () => { libraryPlay.click(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  const frame = host.querySelector<HTMLElement>(".video-stage.media-player-frame")!;
  const overlay = frame.querySelector<HTMLElement>('.media-control-overlay.player-controls')!;
  const control = (label: string) => overlay.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`)!;
  const audio = frame.querySelector<HTMLAudioElement>("audio")!;
  const video = frame.querySelector<HTMLVideoElement>("video")!;

  expect(host.querySelector(".transport")).toBeNull();
  expect([...overlay.querySelectorAll("button")].every((button) => !button.textContent?.trim())).toBe(true);
  for (const label of ["Play song", "Previous ready song", "Next ready song", "Use Karaoke audio", "Use Original audio", "Mute volume", "Enter fullscreen"]) {
    expect(control(label)).toBeDefined();
  }
  for (const action of overlay.querySelectorAll<HTMLButtonElement>("button")) {
    action.focus();
    expect(document.activeElement).toBe(action);
  }
  expect(overlay.querySelector('[aria-label="Seek song"]')).not.toBeNull();
  expect(control("Previous ready song").disabled).toBe(false);
  expect(control("Next ready song").disabled).toBe(false);
  Object.defineProperty(audio, "duration", { configurable: true, value: 30 });
  await act(async () => audio.dispatchEvent(new Event("loadedmetadata", { bubbles: true })));

  await act(async () => control("Play song").click());
  expect(control("Pause song")).toBeDefined();
  await act(async () => control("Pause song").click());
  expect(control("Play song")).toBeDefined();

  const seek = overlay.querySelector<HTMLInputElement>('[aria-label="Seek song"]')!;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(seek, "12");
  await act(async () => {
    seek.dispatchEvent(new Event("input", { bubbles: true }));
    seek.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(audio.currentTime).toBe(12);
  expect(video.currentTime).toBe(12);

  await act(async () => control("Use Original audio").click());
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(control("Use Original audio").getAttribute("aria-pressed")).toBe("true");
  expect(audio.src).toContain("/original");

  await act(async () => control("Mute volume").click());
  expect(audio.volume).toBe(0);
  await act(async () => control("Unmute volume").click());
  expect(audio.volume).toBeCloseTo(.9, 5);

  vi.mocked(invoke).mockClear();
  await act(async () => control("Next ready song").click());
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(vi.mocked(invoke).mock.calls).toContainEqual(["open_library_item", { itemId: "song-2" }]);
  expect(host.querySelector(".now-playing")?.textContent).toContain("Second song");
  vi.mocked(invoke).mockClear();
  await act(async () => control("Previous ready song").click());
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(vi.mocked(invoke).mock.calls).toContainEqual(["open_library_item", { itemId: "song" }]);

  const requestFullscreen = vi.fn().mockResolvedValue(undefined);
  const originalRequestFullscreen = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "requestFullscreen");
  Object.defineProperty(HTMLElement.prototype, "requestFullscreen", { configurable: true, value: requestFullscreen });
  try {
    const fullscreen = control("Enter fullscreen");
    fullscreen.focus();
    const event = new KeyboardEvent("keydown", { key: "F11", code: "F11", bubbles: true, cancelable: true });
    await act(async () => fullscreen.dispatchEvent(event));
    expect(event.defaultPrevented).toBe(true);
    expect(requestFullscreen).toHaveBeenCalled();
  } finally {
    if (originalRequestFullscreen) Object.defineProperty(HTMLElement.prototype, "requestFullscreen", originalRequestFullscreen);
    else Reflect.deleteProperty(HTMLElement.prototype, "requestFullscreen");
  }
});

it("keeps queue navigation disabled when no ready songs are available", async () => {
  const offline = { ...readyCatalog.items[0]!, status: "offline" as const };
  await act(async () => root.unmount());
  catalogFixture = { ...readyCatalog, items: [offline] };
  root = createRoot(host);
  await act(async () => root.render(<App />));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 160)); });
  await act(async () => { host.querySelector<HTMLButtonElement>('[aria-label="Play Song"]')!.click(); await Promise.resolve(); });
  const overlay = host.querySelector<HTMLElement>('.media-control-overlay.player-controls')!;
  expect(overlay.querySelector<HTMLButtonElement>('[aria-label="Previous ready song"]')!.disabled).toBe(true);
  expect(overlay.querySelector<HTMLButtonElement>('[aria-label="Next ready song"]')!.disabled).toBe(true);
});

it("contains the clip editor and restores its persistent Local launcher", async () => {
  const local = Array.from(host.querySelectorAll("button")).find((button) => button.textContent === "Local")!;
  act(() => { local.focus(); local.click(); });
  const files = host.querySelector<HTMLButtonElement>('[role="menuitem"]')!;
  await act(async () => { files.focus(); files.click(); });
  await checkDialog(local);
});

it("contains the lyric editor and restores the selected song's edit button", async () => {
  const edit = host.querySelector<HTMLButtonElement>('[aria-label="Edit lyrics for Song"]')!;
  await act(async () => { edit.focus(); edit.click(); });
  expect(document.activeElement?.tagName).toBe("TEXTAREA");
  await checkDialog(edit);
});

it("requires confirmation before removing an unfinished Library item", async () => {
  await act(async () => root.unmount());
  catalogFixture = { items: [{ id: "unfinished", title: "Unfinished", status: "queued", progressPercent: 0, sources: ["Disk"], canProcess: true, canDelete: true, hasThumbnail: false }], localSources: [], driveSources: [] };
  root = createRoot(host);
  await act(async () => { root.render(<App />); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 160)); });

  const rowRemove = () => [...host.querySelectorAll<HTMLButtonElement>("button")]
    .find((button) => button.textContent === "Remove from library" && !button.closest('[role="dialog"]'))!;
  expect(rowRemove()).toBeTruthy();
  vi.mocked(invoke).mockClear();
  await act(async () => rowRemove().click());
  const dialog = host.querySelector<HTMLElement>('[role="dialog"]')!;
  expect(dialog.textContent).toContain("Remove “Unfinished”?");
  expect(dialog.textContent).toContain("original media file and its lyric sidecar stay unchanged");
  expect(vi.mocked(invoke).mock.calls.some(([command]) => command === "remove_unprocessed_local_item")).toBe(false);
  await act(async () => [...dialog.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "Cancel")!.click());
  expect(host.querySelector('[role="dialog"]')).toBeNull();

  await act(async () => rowRemove().click());
  const confirm = [...host.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')]
    .find((button) => button.textContent === "Remove from library")!;
  await act(async () => confirm.click());
  expect(vi.mocked(invoke).mock.calls).toContainEqual(["remove_unprocessed_local_item", { itemId: "unfinished" }]);
});

it("wraps focus around visible controls while a clip commit disables the footer", async () => {
  const original = vi.mocked(invoke).getMockImplementation()!;
  let rejectCommit: (reason: Error) => void = () => {};
  vi.mocked(invoke).mockImplementation((command, args, options) => command === "commit_local_sections"
    ? new Promise((_resolve, reject) => { rejectCommit = reject; }) : original(command, args, options));
  try {
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "o", ctrlKey: true, bubbles: true, cancelable: true })));
    const dialog = host.querySelector<HTMLElement>('[role="dialog"]')!;
    const save = [...dialog.querySelectorAll("button")].find(button => button.textContent === "Add 1 song to queue")!;
    await act(async () => save.click());
    expect(save.disabled).toBe(true);
    expect(dialog.querySelector(".clip-section-track")).not.toBeNull();
    await act(async () => rejectCommit(new Error("Synthetic commit failure")));
  } finally {
    vi.mocked(invoke).mockImplementation(original);
  }
});

it("dismisses the clip editor before Activity and restores normal shortcuts", async () => {
  const activity = host.querySelector<HTMLButtonElement>(".issues-toggle")!;
  act(() => { activity.focus(); activity.click(); });
  const pressOpen = () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "o", ctrlKey: true, bubbles: true, cancelable: true }));
  await act(async () => { pressOpen(); });
  expect(host.querySelector('[role="dialog"]')).not.toBeNull();
  expect(activity.getAttribute("aria-expanded")).toBe("true");
  await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(activity.getAttribute("aria-expanded")).toBe("true");
  vi.mocked(open).mockClear();
  await act(async () => { pressOpen(); });
  expect(open).toHaveBeenCalledOnce();
  expect(host.querySelector('[role="dialog"]')).not.toBeNull();
});

it("shows cancellation before preparation resolves and never reopens a cancelled preview", async () => {
  let complete: (value: unknown) => void = () => {};
  const original = vi.mocked(invoke).getMockImplementation()!;
  vi.mocked(invoke).mockImplementation((command, args, options) => command === "prepare_local_clip"
    ? new Promise((resolve) => { complete = resolve; }) : original(command, args, options));
  await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "o", ctrlKey: true, bubbles: true, cancelable: true })));
  expect(host.querySelector("#clip-preparing-title")?.textContent).toBe("Opening local media");
  const cancel = [...host.querySelectorAll("button")].find((button) => button.textContent === "Cancel and close")!;
  expect(cancel.disabled).toBe(false);
  await act(async () => cancel.click());
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(vi.mocked(invoke).mock.calls.some(([command]) => command === "cancel_clip_preparation")).toBe(true);
  await act(async () => complete({ clipId: "late", suggestedTitle: "Late", sizeBytes: 1, durationMillis: 1000, previewUrl: "late" }));
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(vi.mocked(invoke).mock.calls).toContainEqual(["cancel_local_clip", { clipId: "late" }]);
  vi.mocked(invoke).mockImplementation(original);
});

it("keeps titles, ranges and invalid drafts across failed compatibility and successful retry", async () => {
  const original = vi.mocked(invoke).getMockImplementation()!;
  let fallbackAttempts = 0;
  vi.mocked(invoke).mockImplementation((command, args, options) => {
    if (command !== "prepare_local_clip") return original(command, args, options);
    const compatible = (args as { compatible: boolean }).compatible;
    if (compatible && ++fallbackAttempts === 1) return Promise.reject(new Error("Synthetic conversion failure"));
    return Promise.resolve({ clipId: compatible ? "compatible" : "direct", direct: !compatible, suggestedTitle: "Song", sizeBytes: 10, durationMillis: 3000, previewUrl: "http://fixture/preview" });
  });
  const surface = () => document.querySelector<HTMLElement>('.clip-video-dialog') ?? host;
  const button = (text: string) => [...surface().querySelectorAll('button')].find(element => element.textContent === text)!;
  const input = (label: string) => surface().querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
  const change = async (label: string, value: string) => act(async () => {
    const element = input(label);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'o', ctrlKey: true, bubbles: true, cancelable: true })));
  await change('Section 1 end handle', '1500');
  await act(async () => button('Edit video').click());
  await change('Video name', 'Keep exact title');
  await act(async () => button('Save').click());
  const track = host.querySelector<HTMLElement>('.clip-section-track')!;
  Object.defineProperty(track, 'getBoundingClientRect', { configurable: true, value: () => ({ left: 0, width: 1000 }) });
  await act(async () => track.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 600 })));
  await act(async () => track.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 900 })));
  await act(async () => button('Edit video').click());
  await change('Video name', 'Second title');
  await act(async () => button('Save').click());
  await change('Section 2 start time', 'unfinished');
  await act(async () => host.querySelector('.clip-screen audio')!.dispatchEvent(new Event('error')));
  vi.mocked(invoke).mockClear();
  await act(async () => button('Prepare compatible preview').click());
  expect(host.textContent).toContain('Compatible preview failed. Your sections are kept');
  expect(input('Section 2 start time').value).toBe('unfinished');
  expect(vi.mocked(invoke).mock.calls.some(([command]) => command === 'cancel_local_clip')).toBe(false);
  expect(vi.mocked(invoke).mock.calls.find(([command]) => command === 'prepare_local_clip')?.[1]).toMatchObject({ compatible: true, replaceClipId: 'direct' });
  await act(async () => button('Prepare compatible preview').click());
  expect(input('Section 2 start time').value).toBe('unfinished');
  await change('Section 2 start time', '0.500');
  await act(async () => input('Section 2 start time').dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
  expect(button('Add 2 songs to queue').disabled).toBe(false);
  await act(async () => button('Edit video').click());
  expect(input('Video name').value).toBe('Second title');
  await act(async () => button('Cancel').click());
  await act(async () => host.querySelectorAll<HTMLButtonElement>('.clip-section-block')[0].click());
  expect(input('Section 1 end handle').value).toBe('1500');
  await act(async () => button('Edit video').click());
  expect(input('Video name').value).toBe('Keep exact title');
  vi.mocked(invoke).mockImplementation(original);
});

it("keeps the old editor when compatible preview preparation is cancelled", async () => {
  const original = vi.mocked(invoke).getMockImplementation()!;
  let resolveCompatible: (value: unknown) => void = () => {};
  vi.mocked(invoke).mockImplementation((command, args, options) => {
    if (command !== "prepare_local_clip") return original(command, args, options);
    const compatible = (args as { compatible: boolean }).compatible;
    return compatible
      ? new Promise((resolve) => { resolveCompatible = resolve; })
      : Promise.resolve({ clipId: "direct", direct: true, suggestedTitle: "Song", sizeBytes: 10, durationMillis: 3000, previewUrl: "http://fixture/preview" });
  });
  try {
    const button = (text: string) => [...host.querySelectorAll("button")].find((element) => element.textContent === text)!;
    const input = () => host.querySelector<HTMLInputElement>('[aria-label="Section 1 end handle"]')!;
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "o", ctrlKey: true, bubbles: true, cancelable: true })));
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input(), "1500");
    await act(async () => input().dispatchEvent(new Event("input", { bubbles: true })));
    await act(async () => host.querySelector(".clip-screen audio")!.dispatchEvent(new Event("error")));
    vi.mocked(invoke).mockClear();
    await act(async () => button("Prepare compatible preview").click());
    expect(host.querySelector("#clip-preparing-title")?.textContent).toBe("Preparing compatible preview");
    await act(async () => button("Cancel and close").click());
    expect(host.querySelector("#clip-editor-title")).not.toBeNull();
    expect(input().value).toBe("1500");
    expect(host.textContent).toContain("Preparation cancelled. Your sections are kept");
    expect(vi.mocked(invoke).mock.calls.some(([command]) => command === "cancel_clip_preparation")).toBe(true);
    expect(vi.mocked(invoke).mock.calls.some(([command]) => command === "cancel_local_clip")).toBe(false);
    await act(async () => resolveCompatible({ clipId: "compatible", direct: false, suggestedTitle: "Compatible Song", sizeBytes: 10, durationMillis: 3000, previewUrl: "http://fixture/compatible" }));
    expect(host.querySelector("#clip-editor-title")).not.toBeNull();
    expect(input().value).toBe("1500");
    expect(host.textContent).toContain("Compatible Song");
  } finally {
    vi.mocked(invoke).mockImplementation(original);
  }
});
