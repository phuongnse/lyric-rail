// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";

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
