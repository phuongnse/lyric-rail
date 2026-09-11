// @vitest-environment jsdom
import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ClipEditor, { frameAt, validSections, type ClipSection } from "./ClipEditor";
import { invoke } from "@tauri-apps/api/core";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

let host: HTMLDivElement, root: Root, tick: FrameRequestCallback;
const commit = vi.fn(async (_sections: ClipSection[]) => {}), close = vi.fn(), play = vi.fn();

const source = (durationMillis = 1000) => ({ clipId: "fixture", suggestedTitle: "First song", sizeBytes: 100,
  durationMillis, previewUrl: "http://fixture/audio", videoUrl: "http://fixture/video",
  frameTimesMillis: [0, 40, 110, 200, 500, 800].filter((time) => time <= durationMillis) });

beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  tick = () => {};
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { tick = callback; return 1; });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  vi.mocked(invoke).mockReset();
  commit.mockClear(); close.mockClear(); play.mockClear();
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<ClipEditor preview={source()} busy={false} containerRef={createRef()} onClose={close} onCommit={commit} onPlay={play} />));
});

afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const button = (label: string) => [...document.querySelectorAll("button")].find((item) => item.getAttribute("aria-label") === label || item.textContent === label);
const input = (label: string) => host.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
const output = (label: string) => host.querySelector<HTMLOutputElement>(`[aria-label="${label}"]`)!;

const setInput = async (label: string, value: string) => {
  await act(async () => {
    const element = input(label);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  });
};

const blur = async (label: string) => act(async () => input(label).dispatchEvent(new FocusEvent("focusout", { bubbles: true })));

const clickTimeline = async (x: number) => {
  const track = host.querySelector<HTMLElement>(".clip-section-track")!;
  Object.defineProperty(track, "getBoundingClientRect", { configurable: true, value: () => ({ left: 0, width: 1000 }) });
  await act(async () => track.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, clientX: x })));
};

const createSection = async (start: number, end: number) => { await clickTimeline(start); await clickTimeline(end); };

it("keeps preview controls inside the 16:9 frame as icon-only actions", async () => {
  const screen = host.querySelector<HTMLElement>(".clip-picker-screen")!;
  const overlay = screen.querySelector<HTMLElement>(".clip-picker-controls")!;
  expect(screen.classList.contains("media-player-frame")).toBe(true);
  expect(host.querySelector(".clip-picker-transport")).toBeNull();
  expect([...overlay.querySelectorAll("button")].every((control) => !control.textContent?.trim())).toBe(true);
  expect(button("Play")).toBeDefined();
  expect(button("Previous frame")).toBeDefined();
  expect(button("Next frame")).toBeDefined();
  expect(button("Mute preview")).toBeDefined();
  expect(overlay.querySelector('[aria-label="Preview time"]')?.textContent).toContain("00:00:00.000");

  await act(async () => button("Mute preview")!.click());
  expect(host.querySelector("audio")!.volume).toBe(0);
  expect(button("Unmute preview")).toBeDefined();
  await act(async () => button("Unmute preview")!.click());
  expect(host.querySelector("audio")!.volume).toBeCloseTo(.8, 5);
});

it("keeps the whole video ready and creates ordered sections from timeline click pairs", async () => {
  expect(host.querySelectorAll(".clip-section-block")).toHaveLength(1);
  expect(button("Add 1 song to queue")?.hasAttribute("disabled")).toBe(false);
  expect(button("Edit video")).toBeDefined();
  expect(host.textContent).toContain("Whole file selected");

  await createSection(110, 500);
  expect(host.querySelectorAll(".clip-section-block")).toHaveLength(1);
  expect(host.querySelector(".clip-section-block")?.getAttribute("aria-label")).toContain("00:00:00.110 to 00:00:00.500");
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(.11, 5);
  expect(button("Pause")).toBeDefined();

  await button("Pause")!.click();
  await createSection(600, 850);
  expect(host.querySelectorAll(".clip-section-block")).toHaveLength(2);
  expect(button("Add 2 songs to queue")?.hasAttribute("disabled")).toBe(false);
  expect(host.textContent).toContain("Section 2 selected");
});

it("keeps the first point pending and rejects an end before Start", async () => {
  await clickTimeline(600);
  expect(host.textContent).toContain("Start 00:00:00.500 selected");
  await clickTimeline(400);
  expect(host.textContent).toContain("End must be later than Start");
  expect(host.querySelectorAll(".clip-section-block")).toHaveLength(0);
  await clickTimeline(900);
  expect(host.querySelectorAll(".clip-section-block")).toHaveLength(1);
  expect(host.querySelector(".clip-section-block")?.getAttribute("aria-label")).toContain("00:00:00.500 to 00:00:00.800");
});

it("removes the selected section and does not expose More controls or Add section", async () => {
  await createSection(100, 500);
  expect(host.querySelector("summary")).toBeNull();
  expect(button("Add section")).toBeUndefined();
  await act(async () => button("Remove section 1")!.click());
  expect(host.querySelectorAll(".clip-section-block")).toHaveLength(0);
  expect(host.textContent).toContain("Click the timeline to set Start");
});

it("queues the untouched full video and edits its information without a cut", async () => {
  await act(async () => button("Edit video")!.click());
  const title = document.querySelector<HTMLInputElement>('[aria-label="Video name"]')!;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(title, "Whole song");
  title.dispatchEvent(new Event("input", { bubbles: true })); title.dispatchEvent(new Event("change", { bubbles: true }));
  await act(async () => button("Save")!.click());
  await act(async () => button("Add 1 song to queue")!.click());
  expect(commit).toHaveBeenCalledWith([{ title: "Whole song", startMillis: 0, endMillis: 1000 }]);
});

it("shows exact frame timestamps and plays the changed edge immediately", async () => {
  await createSection(110, 800);
  expect(output("Section 1 start frame").textContent).toBe("Frame at 00:00:00.110");
  expect(output("Section 1 end frame").textContent).toBe("Frame at 00:00:00.800");

  await act(async () => button("Move Start forward one frame")!.click());
  expect(input("Section 1 start time").value).toBe("00:00:00.200");
  expect(output("Section 1 start frame").textContent).toBe("Frame at 00:00:00.200");
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(.2, 5);
  expect(button("Pause")).toBeDefined();

  await act(async () => button("Move End back one frame")!.click());
  expect(input("Section 1 end time").value).toBe("00:00:00.500");
  expect(output("Section 1 end frame").textContent).toBe("Frame at 00:00:00.500");
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(.2, 5);
});

it("keeps selected playback inside the section while transport frame buttons remain visible", async () => {
  await createSection(110, 800);
  await act(async () => button("Pause")!.click());
  await act(async () => button("Play")!.click());
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(.11, 5);
  await act(async () => button("Next frame")!.click());
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(.20001, 5);
  await act(async () => button("Play")!.click());
  host.querySelector("audio")!.currentTime = .81;
  await act(async () => tick(1));
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(.8, 5);
});

it("preserves exact metadata and lyric text through the optional information dialog", async () => {
  await createSection(110, 800);
  await act(async () => button("Edit video")!.click());
  const field = (label: string) => document.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[aria-label="${label}"]`)!;
  const change = async (label: string, value: string) => act(async () => {
    const element = field(label), prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true })); element.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await change("Video name", " Edited title "); await change("Artist", "Artist"); await change("Composer", "Composer"); await change("Lyrics", "Dòng một\nDòng hai");
  await act(async () => button("Save")!.click());
  await act(async () => button("Add 1 song to queue")!.click());
  expect(commit).toHaveBeenCalledWith([{ title: "Edited title", artist: "Artist", composer: "Composer", lyrics: "Dòng một\nDòng hai", startMillis: 110, endMillis: 800 }]);
});

it("keeps invalid exact-time text visible until Escape discards it", async () => {
  await createSection(110, 800);
  await setInput("Section 1 start time", "bad"); await blur("Section 1 start time");
  expect(input("Section 1 start time").value).toBe("bad");
  expect(button("Add 1 song to queue")?.hasAttribute("disabled")).toBe(true);
  await act(async () => input("Section 1 start time").dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(input("Section 1 start time").value).toBe("00:00:00.110");
  expect(button("Add 1 song to queue")?.hasAttribute("disabled")).toBe(false);
});

it("retains frameAt and batch validation boundaries", () => {
  expect(frameAt([0, 40, 110], 109)).toBe(1);
  expect(validSections([], 1000)).toBe(false);
  expect(validSections([{ title: "Song", startMillis: 10, endMillis: 10 }], 1000)).toBe(false);
  expect(validSections([{ title: "Song", startMillis: 0, endMillis: 1000 }], 1000)).toBe(true);
});

it("waits for direct-video frame evidence before an edge nudge", async () => {
  let resolve: (value: unknown) => void = () => {};
  vi.mocked(invoke).mockImplementation((command) => command === "local_clip_frames" ? new Promise((done) => { resolve = done; }) : Promise.resolve(true));
  await act(async () => root.render(<ClipEditor key="direct" preview={{ clipId: "direct", suggestedTitle: "Long song", sizeBytes: 100, durationMillis: 120000,
    previewUrl: "http://fixture/source", videoUrl: "http://fixture/source/video", direct: true }} busy={false} containerRef={createRef()} onClose={close} onCommit={commit} onPlay={play} />));
  await clickTimeline(100); await clickTimeline(500);
  await act(async () => button("Move Start forward one frame")!.click());
  expect(host.textContent).toContain("Loading nearby frames");
  await act(async () => { await new Promise((done) => setTimeout(done, 180)); });
  await act(async () => resolve({ frameTimesMillis: [12000, 12033.367, 60000], fromMillis: 0, toMillis: 120000 }));
  await act(async () => { await new Promise((done) => setTimeout(done, 20)); });
  expect(input("Section 1 start time").value).toBe("00:00:12.033");
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(12.033, 3);
});
