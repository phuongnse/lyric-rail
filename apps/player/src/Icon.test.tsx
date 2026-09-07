import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ICON_NAMES, Icon, IconButton, placeTooltip } from "./Icon";

describe("LyricRail icon system", () => {
  it("renders every repository-owned icon as SVG geometry", () => {
    for (const name of ICON_NAMES) {
      const markup = renderToStaticMarkup(<Icon name={name} />);
      expect(markup).toContain("<svg");
      expect(markup).toContain('aria-hidden="true"');
      expect(markup).not.toContain("<title>");
      expect(markup).not.toContain("undefined");
    }
  });

  it("binds icon-only buttons to matching accessible and visual help text", () => {
    const markup = renderToStaticMarkup(
      <IconButton
        icon="volume-high"
        label="Mute volume"
      />,
    );
    expect(markup).toContain('aria-label="Mute volume"');
    expect(markup).not.toContain("title=");
    expect(markup).not.toContain("data-tooltip");
    expect(markup).toContain('class="icon-control"');
  });

  it("applies the shared top and viewport-edge placement matrix", () => {
    const cases = [
      [{ left: 300, right: 340, top: 200, bottom: 240 }, 100, { side: "top", align: "center", left: 320, top: 192, maxWidth: 280 }],
      [{ left: 300, right: 340, top: 20, bottom: 60 }, 100, { side: "bottom", align: "center", left: 320, top: 68, maxWidth: 280 }],
      [{ left: 0, right: 40, top: 200, bottom: 240 }, 100, { side: "top", align: "left", left: 12, top: 192, maxWidth: 280 }],
      [{ left: 720, right: 760, top: 200, bottom: 240 }, 100, { side: "top", align: "right", left: 748, top: 192, maxWidth: 280 }],
      [{ left: 0, right: 40, top: 20, bottom: 60 }, 100, { side: "bottom", align: "left", left: 12, top: 68, maxWidth: 280 }],
      [{ left: 720, right: 760, top: 20, bottom: 60 }, 100, { side: "bottom", align: "right", left: 748, top: 68, maxWidth: 280 }],
    ] as const;
    for (const [rect, tooltipWidth, expected] of cases) {
      expect(placeTooltip(rect, 760, tooltipWidth)).toEqual(expected);
    }
    expect(placeTooltip({ left: 1170, right: 1210, top: 20, bottom: 60 }, 1280, 100)).toEqual({
      side: "bottom", align: "center", left: 1190, top: 68, maxWidth: 280,
    });
    expect(placeTooltip({ left: 1170, right: 1210, top: 20, bottom: 60 }, 1280, 180)).toEqual({
      side: "bottom", align: "right", left: 1210, top: 68, maxWidth: 280,
    });
    expect(placeTooltip({ left: 1230, right: 1270, top: 20, bottom: 60 }, 1280, 100)).toEqual({
      side: "bottom", align: "right", left: 1268, top: 68, maxWidth: 280,
    });
    expect(placeTooltip({ left: 100, right: 140, top: 200, bottom: 240 }, 140, 200)).toEqual({
      side: "top", align: "right", left: 128, top: 192, maxWidth: 116,
    });
  });
});
