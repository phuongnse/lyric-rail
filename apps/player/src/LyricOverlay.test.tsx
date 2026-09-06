// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  LYRIC_FONT_SIZE_AT_1080P,
  LyricOverlay,
  cueDotFill,
  fixedLyricFontSize,
  lyricTokenFill,
  lyricLayoutWindow,
  paginateLyricEvent,
  presentationStyle,
  sameLyricOverlayProps,
  visibleLyricEvents,
  visibleLyricPages,
  type KaraokePresentation,
  type RenderEvent,
} from "./LyricOverlay";

const presentation: KaraokePresentation = {
  referenceResolution: [1920, 1080],
  layout: {
    lineMode: "alternating-two-lines",
    alignment: "top-left-bottom-right",
    bottomMargin: 84,
    lineGap: 28,
    safeAreaPercent: 3.5,
    maximumLineWidthPercent: 93,
  },
  font: {
    family: "Be Vietnam Pro Bold",
    bold: false,
    sizeAt1080p: 134,
    scaleX: 76,
    scaleY: 124,
    letterSpacing: 8,
  },
  roleChangeCue: { enabled: true, dotCount: 3, dotFontSizeAt1080p: 160 },
  unsung: {
    fill: "#FFFFFF",
    outerOutline: "#000000",
    outerOutlineWidth: 20,
    shadow: "#000000",
    shadowOffset: 20,
  },
  sung: {
    direction: "left-to-right",
    timing: "syllable",
    innerOutline: "#FFFFFF",
    innerOutlineWidth: 16,
    colors: { male: "#153CFF", female: "#F02A2A", duet: "#FF3D9D" },
  },
};

const cueEvent: RenderEvent = {
  lineIndex: 2,
  slot: "top",
  role: "female",
  showRoleCue: true,
  roleCueReason: "role-change",
  displayStart: 10,
  vocalStart: 13,
  vocalEnd: 15,
  displayEnd: 15.2,
  line: {
    role: "female",
    text: "Em hát",
    fontSizeAt1080p: 220,
    syllables: [
      { text: "Em", visualStart: 13, visualEnd: 14 },
      { text: "hát", visualStart: 14, visualEnd: 15 },
    ],
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("shared karaoke core", () => {
  it("uses one smaller natural font size while retaining authenticated layout and palette", () => {
    const style = presentationStyle(presentation) as Record<string, string | number>;
    expect(LYRIC_FONT_SIZE_AT_1080P).toBe(96);
    expect(fixedLyricFontSize(presentation)).toBe(96);
    expect(style["--lyric-base-font-size"]).toBe("96px");
    expect(style["--lyric-bottom"]).toBe("84px");
    expect(style["--lyric-line-gap"]).toBe("28px");
    expect(style["--lyric-male"]).toBe("#153CFF");
    expect(style["--lyric-female"]).toBe("#F02A2A");
    expect(style["--lyric-duet"]).toBe("#FF3D9D");
    expect(style).not.toHaveProperty("--lyric-scale-x");
    expect(style).not.toHaveProperty("--lyric-scale-y");
    expect(style).not.toHaveProperty("--lyric-line-font-size");
  });

  it("keeps the same size for short, long and legacy per-line size hints", () => {
    for (const size of [24, 96, 134, 240]) {
      const markup = renderToStaticMarkup(
        <LyricOverlay
          events={[{ ...cueEvent, line: { ...cueEvent.line, fontSizeAt1080p: size } }]}
          presentation={presentation}
          time={11.5}
        />,
      );
      expect(markup).toContain("--lyric-base-font-size:96px");
      expect(markup).not.toContain("--lyric-line-font-size");
      expect(markup).not.toContain("transform:scale");
    }
    expect(fixedLyricFontSize({
      ...presentation,
      font: { ...presentation.font, sizeAt1080p: 24 },
    })).toBe(96);
  });

  it("paginates measured words into fixed-size two-row pages without changing order", () => {
    const narrow = { ...presentation, referenceResolution: [600, 1080] as [number, number] };
    const syllables = Array.from({ length: 9 }, (_, index) => ({
      text: `từ-${index}`,
      visualStart: index,
      visualEnd: index + 0.8,
    }));
    const event: RenderEvent = {
      ...cueEvent,
      displayStart: 0,
      vocalStart: 0,
      vocalEnd: 9,
      displayEnd: 10,
      line: { ...cueEvent.line, text: syllables.map(({ text }) => text).join(" "), syllables },
    };
    const pages = paginateLyricEvent(event, narrow, () => 180);
    expect(pages.length).toBeGreaterThan(1);
    expect(pages.every(({ rows }) => rows.length <= 2)).toBe(true);
    expect(pages.flatMap(({ rows }) => rows.flat().map(({ text }) => text))).toEqual(syllables.map(({ text }) => text));
    expect(fixedLyricFontSize(narrow)).toBeCloseTo(53.333, 2);
    expect(new Set(pages.flatMap(({ rows }) => rows).map(() => fixedLyricFontSize(narrow))).size).toBe(1);
  });

  it("splits an over-wide decomposed token only at grapheme boundaries and preserves timing anchors", () => {
    const narrow = { ...presentation, referenceResolution: [360, 1080] as [number, number] };
    const text = "a\u0301".repeat(18);
    const event: RenderEvent = {
      ...cueEvent,
      showRoleCue: false,
      displayStart: 0,
      vocalStart: 2,
      vocalEnd: 8,
      displayEnd: 9,
      line: { ...cueEvent.line, text, syllables: [{ text, visualStart: 2, visualEnd: 8 }] },
    };
    const fragments = paginateLyricEvent(event, narrow, (value) => [...value].length * 35)
      .flatMap(({ rows }) => rows.flat());
    expect(fragments.length).toBeGreaterThan(1);
    expect(fragments.map(({ text: value }) => value).join("")).toBe(text);
    expect(fragments.every(({ text: value }) => !/^\p{Mark}/u.test(value))).toBe(true);
    expect(fragments[0].visualStart).toBe(2);
    expect(fragments[fragments.length - 1].visualEnd).toBe(8);
  });

  it("reserves cue geometry before splitting the first over-wide token", () => {
    const narrow = { ...presentation, referenceResolution: [600, 1080] as [number, number] };
    const text = "a".repeat(20);
    const event: RenderEvent = {
      ...cueEvent,
      displayStart: 0,
      vocalStart: 2,
      vocalEnd: 8,
      displayEnd: 9,
      line: { ...cueEvent.line, text, syllables: [{ text, visualStart: 2, visualEnd: 8 }] },
    };
    const measure = (value: string) => value.length * 30;
    const first = paginateLyricEvent(event, narrow, measure)[0].rows[0][0];
    const fontSize = fixedLyricFontSize(narrow);
    const maximumWidth = 600 * 0.93 - 2 * fontSize * 0.11;
    const gap = fontSize * 0.2414;
    const cueWidth = 3 * fontSize * 0.2931 + 2 * gap;
    expect(cueWidth + gap + measure(first.text)).toBeLessThanOrEqual(maximumWidth);
    expect(first.text.length).toBeLessThan(text.length);
  });

  it("moves an indivisible grapheme below a cue cluster when only the full row can hold it", () => {
    const narrow = { ...presentation, referenceResolution: [600, 1080] as [number, number] };
    const event: RenderEvent = {
      ...cueEvent,
      displayStart: 0,
      vocalStart: 2,
      vocalEnd: 3,
      displayEnd: 4,
      line: { ...cueEvent.line, text: "界", syllables: [{ text: "界", visualStart: 2, visualEnd: 3 }] },
    };
    const page = paginateLyricEvent(event, narrow, () => 500)[0];
    expect(page.rows.map((row) => row.length)).toEqual([0, 1]);
    expect(page.rows.flat().map(({ text }) => text).join("")).toBe("界");
  });

  it("selects continuation pages from their original timing and never repeats source cues", () => {
    const narrow = { ...presentation, referenceResolution: [600, 1080] as [number, number] };
    const syllables = Array.from({ length: 8 }, (_, index) => ({ text: `w${index}`, visualStart: index + 2, visualEnd: index + 2.8 }));
    const event = { ...cueEvent, displayStart: 0, vocalStart: 2, vocalEnd: 10, displayEnd: 11, line: { ...cueEvent.line, syllables } };
    const pages = paginateLyricEvent(event, narrow, () => 180);
    const source = [{ event, pages }];
    expect(visibleLyricPages(source, 0.5)[0].index).toBe(0);
    expect(visibleLyricPages(source, pages[1].switchAt)[0].index).toBe(1);
    const longEvent = {
      ...event,
      line: {
        ...event.line,
        syllables: syllables.map((syllable) => ({ ...syllable, text: `${syllable.text}-rất-dài`.repeat(5) })),
      },
    };
    const continuation = renderToStaticMarkup(<LyricOverlay events={[longEvent]} presentation={narrow} time={9} />);
    expect(continuation).toMatch(/data-page="[1-9][0-9]*"/);
    expect(continuation).not.toContain("lyric-cue-dot");
  });

  it("keeps planned cue timing and exact lyric paint layers", () => {
    expect([0, 1, 2].map((index) => cueDotFill(cueEvent, 11.5, index, 3))).toEqual([100, 50, 0]);
    expect(lyricTokenFill({ text: "hát", visualStart: 14, visualEnd: 15 }, 14.25)).toBe(25);
    const bottom: RenderEvent = {
      ...cueEvent,
      lineIndex: 1,
      slot: "bottom",
      role: "duet",
      showRoleCue: false,
      line: { ...cueEvent.line, role: "duet", text: "Ấy, ta hát", syllables: [{ text: "Ấy,", start: 13, end: 14 }, { text: "ta", start: 14, end: 14.5 }, { text: "hát", start: 14.5, end: 15 }] },
    };
    expect(visibleLyricEvents([bottom, cueEvent], 11.5).map(({ slot }) => slot)).toEqual(["top", "bottom"]);
    const markup = renderToStaticMarkup(<LyricOverlay events={[bottom, cueEvent]} presentation={presentation} time={11.5} />);
    expect(markup).toContain("lyric-line top female");
    expect(markup).toContain("lyric-line bottom duet");
    expect(markup.match(/lyric-cue-dot/g)).toHaveLength(3);
    expect(markup).toContain("Ấy,");
    expect(markup).toContain("lyric-token-shadow");
    expect(markup).toContain("lyric-token-outline");
    expect(markup).toContain("lyric-word");
  });

  it("updates active paint from the media clock on animation frames and cancels cleanly", async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    let callback: FrameRequestCallback | undefined;
    const request = vi.fn((next: FrameRequestCallback) => { callback = next; return 7; });
    const cancel = vi.fn();
    vi.stubGlobal("requestAnimationFrame", request);
    vi.stubGlobal("cancelAnimationFrame", cancel);
    const media = { current: { currentTime: 13 } as HTMLAudioElement };
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    await act(async () => root.render(
      <LyricOverlay events={[cueEvent]} presentation={presentation} time={13} mediaRef={media} playing />,
    ));
    const word = container.querySelector<HTMLElement>('.lyric-token[data-start="13"]')!;
    expect(word.style.getPropertyValue("--fill")).toBe("0%");
    media.current.currentTime = 13.5;
    await act(async () => callback?.(16));
    expect(word.style.getPropertyValue("--fill")).toBe("50%");
    expect(request).toHaveBeenCalledTimes(2);
    await act(async () => root.unmount());
    expect(cancel).toHaveBeenCalledWith(7);
  });

  it("caches layout between event and page boundaries in both playback directions", () => {
    const boundaries = [2, 5, 8];
    expect(lyricLayoutWindow(boundaries, 0)).toEqual([Number.NEGATIVE_INFINITY, 2]);
    expect(lyricLayoutWindow(boundaries, 5)).toEqual([5, 8]);
    expect(lyricLayoutWindow(boundaries, 20)).toEqual([8, Number.POSITIVE_INFINITY]);
  });

  it("isolates steady playback ticks from parent React renders", () => {
    const media = { current: { currentTime: 13 } as HTMLAudioElement };
    const previous = { events: [cueEvent], presentation, time: 13, mediaRef: media, playing: true };
    expect(sameLyricOverlayProps(previous, { ...previous, time: 13.5 })).toBe(true);
    expect(sameLyricOverlayProps({ ...previous, playing: false }, { ...previous, playing: false, time: 13.5 })).toBe(false);
    expect(sameLyricOverlayProps(previous, { ...previous, events: [...previous.events] })).toBe(false);
  });
});
