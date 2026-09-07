// @vitest-environment jsdom
import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ClipEditor, { frameAt, validSections, type ClipSection } from "./ClipEditor";
import { invoke } from "@tauri-apps/api/core";
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

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
const input = (label: string) => host.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
const change = async (label: string, value: string) => {
  await act(async () => {
    const element = input(label);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  });
};
const blur = async (label: string) => { await act(async () => input(label).dispatchEvent(new FocusEvent("focusout", { bubbles: true }))); };
const handleKey = async (label: string, key: string) => { await act(async () => input(label).dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }))); };
const renderFrames = async (frames: number[]) => {
  await act(async () => root.render(<ClipEditor preview={{ clipId: "fixture", suggestedTitle: "First song", sizeBytes: 100, durationMillis: 1000,
    previewUrl: "http://fixture/audio", videoUrl: frames.length ? "http://fixture/video" : undefined,
    videoOffsetMillis: frames[0] ?? 0, frameTimesMillis: frames }} busy={false} containerRef={createRef()} onClose={close} onCommit={commit} onPlay={play} />));
};

const settleFrames = async () => { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 180)); }); };
const renderDirect = async (requiresCompatibility = false) => {
  await act(async () => root.render(<ClipEditor key="direct" preview={{ direct: true, requiresCompatibility, clipId: "direct", suggestedTitle: "First song", sizeBytes: 100, durationMillis: 120000,
    previewUrl: "http://fixture/source", videoUrl: "http://fixture/video" }} busy={false} containerRef={createRef()} onClose={close} onCommit={commit} onPlay={play} onCompatible={() => {}} />));
};

it("loads earlier neighbors at a lower window edge without moving a section to file start", async () => {
  vi.mocked(invoke).mockImplementation(async (command, args) => {
    if (command !== "local_clip_frames") return true;
    const time = (args as { timeMillis: number }).timeMillis;
    return time >= 47000 ? { frameTimesMillis: [47000, 48000, 49000, 50000, 51000], fromMillis: 47000, toMillis: 56000 }
      : { frameTimesMillis: [44000, 45000, 46000, 47000, 48000], fromMillis: 41000, toMillis: 50000 };
  });
  await renderDirect(); await change("Seek preview", "50000"); await settleFrames();
  await change("Drag section start", "47000");
  await handleKey("Drag section start", "ArrowLeft");
  expect(input("Drag section start").value).toBe("47000");
  await settleFrames();
  await handleKey("Drag section start", "ArrowLeft");
  expect(input("Drag section start").value).toBe("46000");
  expect(host.querySelector("audio")!.currentTime).toBe(46);
});

it("loads forward neighbors for sparse timestamps before stepping beyond a probe edge", async () => {
  vi.mocked(invoke).mockImplementation(async (command, args) => {
    if (command !== "local_clip_frames") return true;
    return (args as { timeMillis: number }).timeMillis < 6000
      ? { frameTimesMillis: [0, 1000, 2000, 3000, 4000], fromMillis: 0, toMillis: 6000 }
      : { frameTimesMillis: [4000, 5000, 6000, 7000, 8000], fromMillis: 4000, toMillis: 13000 };
  });
  await renderDirect(); await settleFrames();
  await change("Drag section end", "4000");
  expect(button("Next frame").disabled).toBe(true);
  await handleKey("Drag section end", "ArrowRight");
  expect(input("Drag section end").value).toBe("4000");
  await settleFrames();
  await handleKey("Drag section end", "ArrowRight");
  expect(input("Drag section end").value).toBe("5000");
  await key("ArrowRight");
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(6.00001);
});

it("makes absent frame evidence visible and retryable without repeated background scans", async () => {
  vi.mocked(invoke).mockImplementation(async (command) => command === "local_clip_frames" ? { frameTimesMillis: [], fromMillis: 0, toMillis: 6000 } : true);
  vi.mocked(invoke).mockClear();
  await renderDirect(); await settleFrames(); await settleFrames();
  expect(host.textContent).toContain("No nearby frame timestamps");
  expect(vi.mocked(invoke).mock.calls.filter(([command]) => command === "local_clip_frames")).toHaveLength(1);
  expect(button("Next frame").disabled).toBe(true);
  await act(async () => button("Retry frame details").click()); await settleFrames();
  expect(vi.mocked(invoke).mock.calls.filter(([command]) => command === "local_clip_frames")).toHaveLength(2);
});

it("requires explicit compatibility before decoding media with an unsupported clock origin", async () => {
  vi.mocked(invoke).mockClear();
  await renderDirect(true); await settleFrames();
  expect(host.querySelector("video")).toBeNull();
  expect(host.querySelector("audio")!.getAttribute("src")).toBeNull();
  expect(button("Play").disabled).toBe(true);
  expect(button("Add 1 song to queue").disabled).toBe(true);
  expect(button("Prepare compatible preview").disabled).toBe(false);
  expect(vi.mocked(invoke).mock.calls.some(([command]) => command === "local_clip_frames")).toBe(false);
});

it("stops extending a single-frame video with a long audio tail when timestamps do not advance", async () => {
  vi.mocked(invoke).mockImplementation(async (command, args) => command === "local_clip_frames"
    ? { frameTimesMillis: [0], fromMillis: 0, toMillis: (args as { timeMillis: number }).timeMillis + 6000 } : true);
  vi.mocked(invoke).mockClear();
  await renderDirect(); await settleFrames(); await settleFrames(); await settleFrames();
  expect(host.textContent).toContain("No nearby frame timestamps");
  expect(vi.mocked(invoke).mock.calls.filter(([command]) => command === "local_clip_frames")).toHaveLength(2);
  expect(button("Next frame").disabled).toBe(true);
  await act(async () => button("Retry frame details").click()); await settleFrames();
  expect(vi.mocked(invoke).mock.calls.filter(([command]) => command === "local_clip_frames")).toHaveLength(3);
});

it("opens direct media before frame inspection, cancels stale seeks and keeps only the latest frame window", async () => {
  const pending: Array<{ time: number; resolve: (value: unknown) => void }> = [];
  vi.mocked(invoke).mockImplementation((command, args) => {
    if (command === "cancel_clip_frames") return Promise.resolve(true);
    return new Promise((resolve) => pending.push({ time: (args as { timeMillis: number }).timeMillis, resolve }));
  });
  await act(async () => root.render(<ClipEditor preview={{ direct: true, clipId: "direct", suggestedTitle: "Long song", sizeBytes: 100, durationMillis: 120000,
    previewUrl: "http://fixture/source", videoUrl: "http://fixture/source/video" }} busy={false} containerRef={createRef()} onClose={close} onCommit={commit} onPlay={play} />));
  expect(host.querySelector("video")?.getAttribute("src")).toBe("http://fixture/source/video");
  expect(button("Play").disabled).toBe(false);
  expect(button("Next frame").disabled).toBe(true);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 180)); });
  expect(pending.map((request) => request.time)).toEqual([0]);
  await change("Seek preview", "50000");
  await change("Seek preview", "90000");
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 180)); });
  expect(vi.mocked(invoke).mock.calls.some(([command]) => command === "cancel_clip_frames")).toBe(true);
  expect(pending).toHaveLength(1);
  await act(async () => pending[0].resolve({ frameTimesMillis: [0, 40], fromMillis: 0, toMillis: 6000 }));
  expect(pending.map((request) => request.time)).toEqual([0, 90000]);
  expect(button("Next frame").disabled).toBe(true);
  await act(async () => pending[1].resolve({ frameTimesMillis: [89990, 90023.367, 90080], fromMillis: 87000, toMillis: 96000 }));
  expect(button("Next frame").disabled).toBe(false);
  await key("ArrowRight");
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(90.023377, 6);
  await change("Drag section end", "110000");
  expect(input("Drag section end").value).toBe("110000");
});

it("round-trips fractional boundaries through marking, blur, dragging and adding", async () => {
  await renderFrames([0, 33.367, 100.1, 200.2]);
  await key("ArrowRight"); await key("i");
  expect(input("Section start time").value).toBe("00:00:00.033");
  await blur("Section start time"); await blur("Section start time");
  expect(input("Drag section start").value).toBe("33");
  await key("ArrowRight"); await key("o");
  await blur("Section end time"); await blur("Section end time");
  expect(input("Drag section end").value).toBe("100");
  await change("Drag section start", "34");
  expect(input("Drag section start").value).toBe("33");
  expect(host.querySelector("video")!.currentTime).toBeCloseTo(.033377, 6);
  await key("ArrowRight");
  await act(async () => button("＋ Add section at playhead").click());
  await blur("Section start time");
  await act(async () => button("Add 2 songs to queue").click());
  expect(commit.mock.calls[0][0].map(({ startMillis, endMillis }) => [startMillis, endMillis])).toEqual([[33, 100], [100, 1000]]);
});

it("retains zero, duration and audio before delayed video", async () => {
  await renderFrames([300.3, 400.4, 700.7]);
  await blur("Section start time"); await blur("Section end time");
  expect(input("Drag section start").value).toBe("0");
  expect(input("Drag section end").value).toBe("1000");
  await key("ArrowLeft");
  expect(host.querySelector("audio")!.currentTime).toBe(0);
  await key("ArrowRight");
  expect(host.querySelector("audio")!.currentTime).toBeCloseTo(.30031, 6);
  await change("Drag section end", "200");
  await change("Drag section start", "50");
  await blur("Section start time"); await blur("Section end time");
  await act(async () => button("Add 1 song to queue").click());
  expect(commit).toHaveBeenCalledWith([{ title: "First song", startMillis: 50, endMillis: 200 }]);
});

it.each(["invalid", "9999", "0"])("blocks invalid visible End %s even through blur-to-Add", async (value) => {
  await change("Section end time", value);
  expect(button("Add 1 song to queue").disabled).toBe(true);
  await blur("Section end time");
  expect(input("Section end time").value).toBe(value);
  await act(async () => button("Add 1 song to queue").click());
  expect(commit).not.toHaveBeenCalled();
  await change("Section end time", "0.500");
  // Confirmation also consumes the valid draft before blur has committed it.
  await act(async () => button("Add 1 song to queue").click());
  expect(commit).toHaveBeenCalledWith([{ title: "First song", startMillis: 0, endMillis: 500 }]);
});

it("steps both focused handles in either direction for fractional VFR and audio", async () => {
  await renderFrames([0, 33.367, 100.1, 200.2]);
  await handleKey("Drag section start", "ArrowRight");
  expect(input("Drag section start").value).toBe("33");
  await handleKey("Drag section start", "ArrowRight");
  expect(input("Drag section start").value).toBe("100");
  await handleKey("Drag section start", "ArrowLeft");
  expect(input("Drag section start").value).toBe("33");
  await handleKey("Drag section end", "ArrowLeft");
  expect(input("Drag section end").value).toBe("200");
  await handleKey("Drag section end", "ArrowRight");
  expect(input("Drag section end").value).toBe("1000");
  await handleKey("Drag section start", "Home");
  await renderFrames([]);
  await handleKey("Drag section start", "ArrowRight");
  expect(input("Drag section start").value).toBe("10");
  await handleKey("Drag section start", "ArrowLeft");
  expect(input("Drag section start").value).toBe("0");
  await handleKey("Drag section end", "ArrowLeft");
  expect(input("Drag section end").value).toBe("990");
  await handleKey("Drag section end", "ArrowRight");
  expect(input("Drag section end").value).toBe("1000");
});

it("keeps an invalid draft blocking across section switches until corrected or removed", async () => {
  await change("Section start time", "2");
  await blur("Section start time");
  await act(async () => button("＋ Add section at playhead").click());
  expect(button("Add 2 songs to queue").disabled).toBe(true);
  await act(async () => button("Edit section 1").click());
  expect(input("Section start time").value).toBe("2");
  await change("Section start time", "0.250");
  await blur("Section start time");
  await act(async () => button("Add 2 songs to queue").click());
  expect(commit.mock.calls[0][0][0].startMillis).toBe(250);
});

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
