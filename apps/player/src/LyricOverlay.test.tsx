import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  LyricOverlay,
  boundedLineFontSize,
  cueDotFill,
  presentationStyle,
  visibleLyricEvents,
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
    scaleX: 96,
    scaleY: 100,
    letterSpacing: 0,
  },
  roleChangeCue: { enabled: true, dotCount: 3, dotFontSizeAt1080p: 82 },
  unsung: {
    fill: "#FFFFFF",
    outerOutline: "#000000",
    outerOutlineWidth: 4.5,
    shadow: "#000000",
    shadowOffset: 2,
  },
  sung: {
    direction: "left-to-right",
    timing: "syllable",
    innerOutline: "#FFFFFF",
    innerOutlineWidth: 4.5,
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
    fontSizeAt1080p: 134,
    syllables: [
      { text: "Em", visualStart: 13, visualEnd: 14 },
      { text: "hát", visualStart: 14, visualEnd: 15 },
    ],
  },
};

describe("authenticated karaoke presentation", () => {
  it("uses the exact classic style as reference-space pixels", () => {
    const style = presentationStyle(presentation) as Record<string, string | number>;
    expect(style["--lyric-base-font-size"]).toBe("134px");
    expect(style["--lyric-cue-font-size"]).toBe("82px");
    expect(style["--lyric-bottom"]).toBe("84px");
    expect(style["--lyric-line-step"]).toBe("162px");
    expect(style["--lyric-outer-width"]).toBe("9px");
    expect(style["--lyric-inner-width"]).toBe("4.5px");
    expect(style["--lyric-male"]).toBe("#153CFF");
    expect(style["--lyric-female"]).toBe("#F02A2A");
    expect(style["--lyric-duet"]).toBe("#FF3D9D");
    expect(style["--lyric-unsung"]).toBe("#FFFFFF");
    expect(style["--lyric-scale-y"]).toBe(1);
    expect(style.fontFamily).toBe('"Be Vietnam Pro", sans-serif');
    expect(style.fontWeight).toBe(850);
    expect(boundedLineFontSize(112, presentation)).toBe(112);
    expect(boundedLineFontSize(Number.POSITIVE_INFINITY, presentation)).toBe(134);
    expect(boundedLineFontSize(10000, presentation)).toBe(134);
  });

  it("contains its reference canvas inside the video-shaped meet viewport", () => {
    const markup = renderToStaticMarkup(
      <LyricOverlay events={[cueEvent]} presentation={presentation} time={11.5} />,
    );
    expect(markup).toContain("<svg");
    expect(markup).toContain('viewBox="0 0 1920 1080"');
    expect(markup).toContain('preserveAspectRatio="xMidYMid meet"');
    expect(markup).toContain('<foreignObject width="1920" height="1080">');
    expect(markup).toContain('class="lyric-canvas"');
    expect(markup).toContain("--lyric-line-font-size:134px");
  });

  it("sweeps exactly three planned dots across display lead time", () => {
    expect([0, 1, 2].map((index) => cueDotFill(cueEvent, 10, index, 3))).toEqual([0, 0, 0]);
    expect([0, 1, 2].map((index) => cueDotFill(cueEvent, 11.5, index, 3))).toEqual([100, 50, 0]);
    expect([0, 1, 2].map((index) => cueDotFill(cueEvent, 13, index, 3))).toEqual([100, 100, 100]);
    expect(cueDotFill({ ...cueEvent, showRoleCue: false }, 12, 0, 3)).toBe(0);
  });

  it("renders authenticated slots, role classes and only planned cue dots", () => {
    const bottom: RenderEvent = {
      ...cueEvent,
      lineIndex: 1,
      slot: "bottom",
      role: "duet",
      showRoleCue: false,
      roleCueReason: undefined,
      line: { ...cueEvent.line, role: "duet", text: "Ta hát" },
    };
    const visible = visibleLyricEvents([bottom, cueEvent], 11.5);
    expect(visible.map((event) => event.slot)).toEqual(["top", "bottom"]);
    const markup = renderToStaticMarkup(
      <LyricOverlay events={[bottom, cueEvent]} presentation={presentation} time={11.5} />,
    );
    expect(markup).toContain("lyric-line top female");
    expect(markup).toContain("lyric-line bottom duet");
    expect(markup).toContain('data-cue-reason="role-change"');
    expect(markup.match(/lyric-cue-dot/g)).toHaveLength(3);
    expect(markup).toContain("--lyric-female:#F02A2A");
    expect(markup).toContain("--fill:50%");
  });

  it("renders the core-planned initial, role-change and long-pause reasons only", () => {
    for (const reason of ["initial", "role-change", "long-pause"] as const) {
      const markup = renderToStaticMarkup(
        <LyricOverlay
          events={[{ ...cueEvent, roleCueReason: reason }]}
          presentation={presentation}
          time={11.5}
        />,
      );
      expect(markup).toContain(`data-cue-reason="${reason}"`);
      expect(markup.match(/lyric-cue-dot/g)).toHaveLength(3);
    }
    const unplanned = renderToStaticMarkup(
      <LyricOverlay
        events={[{ ...cueEvent, showRoleCue: false, roleCueReason: undefined }]}
        presentation={presentation}
        time={11.5}
      />,
    );
    expect(unplanned).not.toContain("lyric-cue-dot");
    expect(unplanned).not.toContain("data-cue-reason");
  });
});
