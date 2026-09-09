// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async (command: string) => {
  if (command === "catalog_snapshot") return { items: [{ id: "song", title: "Song", status: "ready", progressPercent: 100, sources: ["Disk"], canProcess: false, hasThumbnail: false }], localSources: [], driveSources: [] };
  if (command === "player_status") return { version: "0.8.0", platform: "windows", vaultAvailable: true, processing: { pendingJobs: 0, runtimeAvailable: true } };
  if (command === "system_issues") return [];
  if (command === "task_runtime_snapshot") return { sequence: 0, tasks: [], activeTaskCount: 0, historyCount: 0 };
  if (command === "prepare_local_clip") return { clipId: "clip", suggestedTitle: "Song", sizeBytes: 10, durationMillis: 3000, previewUrl: "http://fixture/preview" };
  if (command === "item_lyrics") return "Exact words";
  return null;
}) }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async () => () => {}) }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn(async () => ["song.mp4"]), save: vi.fn() }));

let host: HTMLDivElement;
let root: Root;
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.assign(window, { __TAURI_INTERNALS__: {} });
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as typeof ResizeObserver;
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
  const button = (text: string) => [...host.querySelectorAll("button")].find((element) => element.textContent?.includes(text))!;
  const input = (label: string) => host.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
  const change = async (label: string, value: string) => act(async () => {
    const element = input(label);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "o", ctrlKey: true, bubbles: true, cancelable: true })));
  await change("Song 1 title", "Keep exact title");
  await change("Drag section end", "1500");
  await act(async () => button("Add another song").click());
  await change("Song 2 title", "Second title");
  await change("Section start time", "unfinished");
  await act(async () => host.querySelector(".clip-screen audio")!.dispatchEvent(new Event("error")));
  vi.mocked(invoke).mockClear();
  await act(async () => button("Prepare compatible preview").click());
  expect(host.textContent).toContain("Compatible preview failed. Your sections are kept");
  expect(host.querySelector(".clip-sections")?.textContent).toContain("Keep exact title");
  expect(input("Song 2 title").value).toBe("Second title");
  expect(input("Section start time").value).toBe("unfinished");
  expect(vi.mocked(invoke).mock.calls.some(([command]) => command === "cancel_local_clip")).toBe(false);
  expect(vi.mocked(invoke).mock.calls.find(([command]) => command === "prepare_local_clip")?.[1]).toMatchObject({ compatible: true, replaceClipId: "direct" });
  await act(async () => button("Prepare compatible preview").click());
  expect(host.querySelector(".clip-sections")?.textContent).toContain("Keep exact title");
  expect(input("Song 2 title").value).toBe("Second title");
  expect(input("Section start time").value).toBe("unfinished");
  expect(button("Add 2 songs to queue").disabled).toBe(true);
  await change("Section start time", "0.500");
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Edit section 1"]')!.click());
  expect(input("Drag section end").value).toBe("1500");
  vi.mocked(invoke).mockImplementation(original);
});
