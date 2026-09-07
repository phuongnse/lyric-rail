// @vitest-environment jsdom
import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ClipEditor, { frameAt, validSections, type ClipSection } from "./ClipEditor";

let host: HTMLDivElement, root: Root;
const commit = vi.fn(async (_sections: ClipSection[]) => {}), close = vi.fn(), play = vi.fn();
let tick: FrameRequestCallback;
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { tick = callback; return 1; });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  commit.mockClear(); close.mockClear(); play.mockClear();
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<ClipEditor preview={{ clipId: "fixture", suggestedTitle: "First song", sizeBytes: 100, durationMillis: 1000,
    previewUrl: "http://fixture/audio", videoUrl: "http://fixture/video", frameTimesMillis: [0, 40, 110, 200, 500, 800] }}
    busy={false} containerRef={createRef()} onClose={close} onCommit={commit} onPlay={play} />));
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const button = (label: string) => [...host.querySelectorAll("button")].find((button) => button.getAttribute("aria-label") === label || button.textContent === label)!;
const key = async (value: string) => { await act(async () => host.querySelector(".clip-screen")!.dispatchEvent(new KeyboardEvent("keydown", { key: value, bubbles: true, cancelable: true }))); };

it("steps actual variable frames, marks a section, reorders and submits separate songs", async () => {
  expect(host.querySelector("video")?.getAttribute("src")).toBe("http://fixture/video");
  await key("ArrowRight"); await key("ArrowRight");
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(.11001);
  await key("i"); await key("ArrowRight"); await key("o");
  await act(async () => button("＋ Add section at playhead").click());
  await act(async () => button("Move section 2 up").click());
  await act(async () => button("Add 2 songs to queue").click());
  expect(commit).toHaveBeenCalledWith([
    { startMillis: 200, endMillis: 1000, title: "First song · 2" },
    { startMillis: 110, endMillis: 200, title: "First song" },
  ]);
});

it("reviews only the selected interval and stops at End", async () => {
  await key("ArrowRight"); await key("o");
  await act(async () => button("▶ Review section").click());
  expect(play).toHaveBeenCalled();
  host.querySelector("audio")!.currentTime = .06;
  await act(async () => tick(1));
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(.04);
  expect(button("Play")).toBeDefined();
});

it("keeps typing separate from shortcuts and removes only the selected section", async () => {
  const input = host.querySelector<HTMLInputElement>('[aria-label="Song 1 title"]')!;
  await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
  expect(host.querySelector("audio")!.currentTime).toBe(0);
  await act(async () => button("＋ Add section at playhead").click());
  await act(async () => button("Remove section 1").click());
  await act(async () => button("Add 1 song to queue").click());
  expect(commit.mock.calls[0][0]).toHaveLength(1);
});

it("rejects invalid batch bounds and preserves frame lookups", () => {
  expect(frameAt([0, 40, 110], 109)).toBe(1);
  for (const batch of [[], [{ title: "", startMillis: 0, endMillis: 1 }], [{ title: "Song", startMillis: 10, endMillis: 10 }], [{ title: "Song", startMillis: 0, endMillis: 1001 }]]) expect(validSections(batch, 1000)).toBe(false);
});

it("retains all selected songs when batch publication fails", async () => {
  commit.mockRejectedValueOnce(new Error("synthetic failure"));
  await act(async () => button("＋ Add section at playhead").click());
  await act(async () => button("Add 2 songs to queue").click());
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("Your sections are kept");
  expect(host.querySelectorAll(".clip-sections li")).toHaveLength(2);
  expect(close).not.toHaveBeenCalled();
  await act(async () => button("Add 2 songs to queue").click());
  expect(commit).toHaveBeenCalledTimes(2);
  expect(commit.mock.calls[0][0]).toEqual(commit.mock.calls[1][0]);
});
