import type { CSSProperties } from "react";

export type Syllable = {
  text: string;
  start?: number;
  end?: number;
  visualStart?: number;
  visualEnd?: number;
};

export type RenderEvent = {
  lineIndex?: number;
  slot?: "top" | "bottom";
  displayStart?: number;
  displayEnd?: number;
  vocalStart?: number;
  vocalEnd?: number;
  role?: string;
  showRoleCue?: boolean;
  roleCueReason?: "initial" | "role-change" | "long-pause" | "every-line";
  line?: {
    text?: string;
    role?: string;
    fontSizeAt1080p?: number;
    syllables?: Syllable[];
  };
};

export type KaraokePresentation = {
  referenceResolution: [number, number];
  layout: {
    lineMode: "alternating-two-lines";
    alignment: "top-left-bottom-right";
    bottomMargin: number;
    lineGap: number;
    safeAreaPercent: number;
    maximumLineWidthPercent: number;
  };
  font: {
    family: string;
    bold: boolean;
    sizeAt1080p: number;
    scaleX: number;
    scaleY: number;
    letterSpacing: number;
  };
  roleChangeCue: {
    enabled: boolean;
    dotCount: number;
    dotFontSizeAt1080p: number;
  };
  unsung: {
    fill: string;
    outerOutline: string;
    outerOutlineWidth: number;
    shadow: string;
    shadowOffset: number;
  };
  sung: {
    direction: "left-to-right";
    timing: "syllable";
    innerOutline: string;
    innerOutlineWidth: number;
    colors: { male: string; female: string; duet: string };
  };
};

function clampPercent(value: number): number {
  return Math.min(100, Math.max(0, Number.isFinite(value) ? value : 0));
}

export function roleClass(role: string | undefined): "male" | "female" | "duet" {
  return role === "female" ? "female" : role === "duet" ? "duet" : "male";
}

export function visibleLyricEvents(events: RenderEvent[], time: number): RenderEvent[] {
  return events
    .filter((event) => {
      const start = event.displayStart ?? event.vocalStart ?? 0;
      const end = event.displayEnd ?? event.vocalEnd ?? start;
      return time >= start && time < end;
    })
    .sort((left, right) => (left.slot === "top" ? -1 : 1) - (right.slot === "top" ? -1 : 1))
    .slice(0, 2);
}

export function cueDotFill(
  event: RenderEvent,
  time: number,
  dotIndex: number,
  dotCount: number,
): number {
  if (!event.showRoleCue || dotCount < 1 || dotIndex < 0 || dotIndex >= dotCount) return 0;
  const cueStart = event.displayStart ?? event.vocalStart ?? 0;
  const cueEnd = event.vocalStart ?? cueStart;
  if (cueEnd <= cueStart) return time >= cueEnd ? 100 : 0;
  const dotDuration = (cueEnd - cueStart) / dotCount;
  const dotStart = cueStart + dotIndex * dotDuration;
  return clampPercent(((time - dotStart) / dotDuration) * 100);
}

function referenceHeightValue(value: number, referenceHeight: number): string {
  return `${((value / referenceHeight) * 100).toFixed(4)}cqh`;
}

export function boundedLineFontSize(
  requested: number | undefined,
  presentation: KaraokePresentation,
): number {
  return Number.isFinite(requested)
    && Number(requested) >= 24
    && Number(requested) <= 240
    ? Number(requested)
    : presentation.font.sizeAt1080p;
}

export function presentationStyle(presentation: KaraokePresentation): CSSProperties {
  const referenceHeight = presentation.referenceResolution[1];
  const browserFontFamily = presentation.font.family.replace(/ Bold$/, "");
  const lineStep = (
    presentation.font.sizeAt1080p * presentation.font.scaleY / 100
    + presentation.layout.lineGap
  );
  return {
    "--lyric-safe-x": `${presentation.layout.safeAreaPercent}%`,
    "--lyric-max-width": `${presentation.layout.maximumLineWidthPercent}%`,
    "--lyric-bottom": referenceHeightValue(presentation.layout.bottomMargin, referenceHeight),
    "--lyric-line-step": referenceHeightValue(lineStep, referenceHeight),
    "--lyric-base-font-size": referenceHeightValue(presentation.font.sizeAt1080p, referenceHeight),
    "--lyric-cue-font-size": referenceHeightValue(
      presentation.roleChangeCue.dotFontSizeAt1080p,
      referenceHeight,
    ),
    "--lyric-letter-spacing": referenceHeightValue(
      presentation.font.letterSpacing,
      referenceHeight,
    ),
    "--lyric-scale-x": presentation.font.scaleX / 100,
    "--lyric-scale-y": presentation.font.scaleY / 100,
    "--lyric-unsung": presentation.unsung.fill,
    "--lyric-outer": presentation.unsung.outerOutline,
    "--lyric-outer-width": referenceHeightValue(
      presentation.unsung.outerOutlineWidth + presentation.sung.innerOutlineWidth,
      referenceHeight,
    ),
    "--lyric-inner": presentation.sung.innerOutline,
    "--lyric-inner-width": referenceHeightValue(
      presentation.sung.innerOutlineWidth,
      referenceHeight,
    ),
    "--lyric-shadow": presentation.unsung.shadow,
    "--lyric-shadow-offset": referenceHeightValue(
      presentation.unsung.shadowOffset,
      referenceHeight,
    ),
    "--lyric-male": presentation.sung.colors.male,
    "--lyric-female": presentation.sung.colors.female,
    "--lyric-duet": presentation.sung.colors.duet,
    fontFamily: `"${browserFontFamily}", sans-serif`,
    fontWeight: presentation.font.bold ? 900 : 850,
  } as CSSProperties;
}

function KaraokeToken({
  text,
  fill,
  cue = false,
}: {
  text: string;
  fill: number;
  cue?: boolean;
}) {
  const style = { "--fill": `${clampPercent(fill)}%` } as CSSProperties;
  return (
    <span className={`lyric-token ${cue ? "lyric-cue-dot" : ""}`} style={style}>
      <span className="lyric-token-outline">{text}</span>
      <span className="lyric-word">{text}</span>
    </span>
  );
}

export function LyricOverlay({
  events,
  time,
  presentation,
}: {
  events: RenderEvent[];
  time: number;
  presentation: KaraokePresentation;
}) {
  const active = visibleLyricEvents(events, time);
  return (
    <div className="lyric-overlay" style={presentationStyle(presentation)} aria-hidden="true">
      {active.map((event, eventIndex) => {
        const line = event.line;
        const syllables = line?.syllables ?? [];
        const role = line?.role ?? event.role;
        const slot = event.slot === "top" ? "top" : "bottom";
        const fontSize = boundedLineFontSize(line?.fontSizeAt1080p, presentation);
        const lineStyle = {
          "--lyric-line-font-size": referenceHeightValue(
            fontSize,
            presentation.referenceResolution[1],
          ),
        } as CSSProperties;
        const cueCount = presentation.roleChangeCue.enabled && event.showRoleCue
          ? presentation.roleChangeCue.dotCount
          : 0;
        return (
          <div
            className={`lyric-line ${slot} ${roleClass(role)}`}
            style={lineStyle}
            data-cue-reason={event.showRoleCue ? event.roleCueReason : undefined}
            key={`${event.lineIndex ?? eventIndex}-${event.displayStart ?? event.vocalStart}`}
          >
            <div className="lyric-line-content">
              {cueCount > 0 && (
                <span className="lyric-cue">
                  {Array.from({ length: cueCount }, (_, dotIndex) => (
                    <KaraokeToken
                      cue
                      fill={cueDotFill(event, time, dotIndex, cueCount)}
                      key={`cue-${dotIndex}`}
                      text="●"
                    />
                  ))}
                </span>
              )}
              <span className="lyric-copy">
                {syllables.length
                  ? syllables.map((syllable, index) => {
                    const start = syllable.visualStart ?? syllable.start ?? 0;
                    const end = syllable.visualEnd ?? syllable.end ?? start;
                    const fill = time <= start
                      ? 0
                      : time >= end
                        ? 100
                        : ((time - start) / Math.max(0.001, end - start)) * 100;
                    return (
                      <KaraokeToken
                        fill={fill}
                        key={`${index}-${syllable.text}`}
                        text={syllable.text}
                      />
                    );
                  })
                  : <KaraokeToken fill={0} text={line?.text ?? ""} />}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
