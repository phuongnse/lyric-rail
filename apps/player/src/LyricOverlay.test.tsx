// @vitest-environment jsdom
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
  it("retains authenticated layout and palette in reference-space pixels", () => {
    const style = presentationStyle(presentation) as Record<string, string | number>;
    expect(style["--lyric-base-font-size"]).toBe("134px");
    expect(style["--lyric-bottom"]).toBe("84px");
    expect(style["--lyric-line-step"]).toBe("162px");
    expect(style["--lyric-male"]).toBe("#153CFF");
    expect(style["--lyric-female"]).toBe("#F02A2A");
    expect(style["--lyric-duet"]).toBe("#FF3D9D");
    expect(style["--lyric-unsung"]).toBe("#FFFFFF");
    expect(style["--lyric-scale-y"]).toBe(1);
    expect(boundedLineFontSize(112, presentation)).toBe(112);
    expect(boundedLineFontSize(Number.POSITIVE_INFINITY, presentation)).toBe(134);
    expect(boundedLineFontSize(10000, presentation)).toBe(134);
  });

  it("accepts legacy cosmetic hints without replacing the Player typography", () => {
    expect(presentationStyle({
      ...presentation,
      font: { ...presentation.font, family: "Legacy Typeface Bold", bold: true },
      roleChangeCue: { ...presentation.roleChangeCue, dotFontSizeAt1080p: 160 },
      unsung: { ...presentation.unsung, outerOutlineWidth: 20, shadowOffset: 20 },
      sung: { ...presentation.sung, innerOutlineWidth: 16 },
    })).toEqual(presentationStyle(presentation));
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

  it("sweeps geometric cues without a fallback-font character", () => {
    const root = document.createElement("div");
    root.innerHTML = renderToStaticMarkup(
      <LyricOverlay events={[cueEvent]} presentation={presentation} time={11.5} />,
    );
    const dots = [...root.querySelectorAll<HTMLElement>(".lyric-cue-dot")];
    expect(dots.map((dot) => dot.style.getPropertyValue("--fill")))
      .toEqual(["100%", "50%", "0%"]);
    expect(dots.map((dot) => dot.textContent)).toEqual(["", "", ""]);
    expect(dots.map((dot) => Boolean(dot.querySelector(".lyric-token-shadow"))))
      .toEqual([true, true, true]);
    expect(dots.map((dot) => dot.querySelector<HTMLElement>(".lyric-word")?.style.clipPath))
      .toEqual(["none", "", undefined]);
  });

  it("preserves exact text and stationary base layers across sweep endpoints and seeks", () => {
    const text = 'Ấy, mình hát & <nghe> "nhé"!';
    const event: RenderEvent = {
      ...cueEvent,
      showRoleCue: false,
      line: { ...cueEvent.line, text, syllables: [{ text, visualStart: 13, visualEnd: 15 }] },
    };
    let base = "";
    let shadow = "";
    for (const [time, percent] of [[13, 0], [14, 50], [15, 100], [13.25, 12.5], [13, 0]]) {
      const root = document.createElement("div");
      root.innerHTML = renderToStaticMarkup(
        <LyricOverlay events={[event]} presentation={presentation} time={time} />,
      );
      const token = root.querySelector<HTMLElement>(".lyric-copy .lyric-token")!;
      const currentBase = token.querySelector(".lyric-token-outline")!;
      const currentShadow = token.querySelector(".lyric-token-shadow")!;
      const sung = token.querySelector<HTMLElement>(".lyric-word");
      expect(token.style.getPropertyValue("--fill")).toBe(String(percent) + "%");
      expect(currentBase.textContent).toBe(text);
      expect(currentShadow.textContent).toBe(text);
      base ||= currentBase.outerHTML;
      shadow ||= currentShadow.outerHTML;
      expect(currentBase.outerHTML).toBe(base);
      expect(currentShadow.outerHTML).toBe(shadow);
      if (percent === 0) {
        expect(sung).toBeNull();
      } else {
        expect(sung?.textContent).toBe(text);
        expect(sung?.style.clipPath).toBe(percent === 100 ? "none" : "");
      }
    }
  });
});
