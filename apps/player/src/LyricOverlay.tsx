import {
  memo,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";

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

type LyricPage = {
  id: string;
  event: RenderEvent;
  index: number;
  rows: Syllable[][];
  switchAt: number;
};

type PaginatedEvent = { event: RenderEvent; pages: LyricPage[] };
type MeasureText = (text: string) => number;

export const LYRIC_FONT_SIZE_AT_1080P = 92;
export const LYRIC_WORD_GAP_EM = 0.2414;
export const LYRIC_CUE_SIZE_EM = 0.2931;
const LYRIC_OUTER_WIDTH_EM = 0.125;
const LYRIC_INNER_WIDTH_EM = 0.085;
const LYRIC_SHADOW_X_EM = 0.0425;
const LYRIC_SHADOW_Y_EM = 0.065;
const LYRIC_PAINT_PADDING_EM = 0.12;
const MAX_PAGE_ROWS = 2;

function clampPercent(value: number): number {
  return Math.min(100, Math.max(0, Number.isFinite(value) ? value : 0));
}

function syllableStart(syllable: Syllable): number {
  return syllable.visualStart ?? syllable.start ?? 0;
}

function syllableEnd(syllable: Syllable): number {
  const start = syllableStart(syllable);
  return Math.max(start, syllable.visualEnd ?? syllable.end ?? start);
}

export function lyricTokenFill(syllable: Syllable, time: number): number {
  const start = syllableStart(syllable);
  const end = syllableEnd(syllable);
  if (time <= start) return 0;
  if (time >= end) return 100;
  return clampPercent(((time - start) / Math.max(0.001, end - start)) * 100);
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

export function cueDotInterval(
  event: RenderEvent,
  dotIndex: number,
  dotCount: number,
): [number, number] {
  const cueStart = event.displayStart ?? event.vocalStart ?? 0;
  const cueEnd = Math.max(cueStart, event.vocalStart ?? cueStart);
  if (dotCount < 1 || dotIndex < 0 || dotIndex >= dotCount) return [cueStart, cueStart];
  const duration = (cueEnd - cueStart) / dotCount;
  return [cueStart + dotIndex * duration, cueStart + (dotIndex + 1) * duration];
}

export function cueDotFill(
  event: RenderEvent,
  time: number,
  dotIndex: number,
  dotCount: number,
): number {
  if (!event.showRoleCue) return 0;
  const [start, end] = cueDotInterval(event, dotIndex, dotCount);
  return end <= start ? (time >= end ? 100 : 0) : clampPercent(((time - start) / (end - start)) * 100);
}

function referencePixelValue(value: number): string {
  return `${value}px`;
}

export function fixedLyricFontSize(presentation: KaraokePresentation): number {
  const shortSide = Math.min(...presentation.referenceResolution);
  return LYRIC_FONT_SIZE_AT_1080P * shortSide / 1080;
}

export function presentationStyle(presentation: KaraokePresentation): CSSProperties {
  return {
    "--lyric-safe-x": `${presentation.layout.safeAreaPercent}%`,
    "--lyric-max-width": `${presentation.layout.maximumLineWidthPercent}%`,
    "--lyric-bottom": referencePixelValue(presentation.layout.bottomMargin),
    "--lyric-line-gap": referencePixelValue(presentation.layout.lineGap),
    "--lyric-base-font-size": referencePixelValue(fixedLyricFontSize(presentation)),
    "--lyric-word-gap": `${LYRIC_WORD_GAP_EM}em`,
    "--lyric-cue-size": `${LYRIC_CUE_SIZE_EM}em`,
    "--lyric-outer-width": `${LYRIC_OUTER_WIDTH_EM}em`,
    "--lyric-inner-width": `${LYRIC_INNER_WIDTH_EM}em`,
    "--lyric-shadow-x": `${LYRIC_SHADOW_X_EM}em`,
    "--lyric-shadow-y": `${LYRIC_SHADOW_Y_EM}em`,
    "--lyric-paint-padding": `${LYRIC_PAINT_PADDING_EM}em`,
    "--lyric-unsung": presentation.unsung.fill,
    "--lyric-outer": presentation.unsung.outerOutline,
    "--lyric-inner": presentation.sung.innerOutline,
    "--lyric-shadow": presentation.unsung.shadow,
    "--lyric-male": presentation.sung.colors.male,
    "--lyric-female": presentation.sung.colors.female,
    "--lyric-duet": presentation.sung.colors.duet,
  } as CSSProperties;
}

function graphemes(text: string): string[] {
  const Constructor = (Intl as unknown as {
    Segmenter?: new (locale: string, options: { granularity: "grapheme" }) => {
      segment: (value: string) => Iterable<{ segment: string }>;
    };
  }).Segmenter;
  if (Constructor) return [...new Constructor("vi", { granularity: "grapheme" }).segment(text)].map(({ segment }) => segment);
  return Array.from(text).reduce<string[]>((parts, character) => {
    if (parts.length && /\p{Mark}/u.test(character)) parts[parts.length - 1] += character;
    else parts.push(character);
    return parts;
  }, []);
}

function splitOverwideSyllable(
  syllable: Syllable,
  maximumWidth: number,
  measureText: MeasureText,
): Syllable[] {
  if (measureText(syllable.text) <= maximumWidth) return [syllable];
  const units = graphemes(syllable.text);
  const chunks: { text: string; left: number; right: number }[] = [];
  let left = 0;
  let text = "";
  for (let index = 0; index < units.length; index += 1) {
    const candidate = text + units[index];
    if (text && measureText(candidate) > maximumWidth) {
      chunks.push({ text, left, right: index });
      left = index;
      text = units[index];
    } else text = candidate;
  }
  if (text || !chunks.length) chunks.push({ text, left, right: units.length });
  const start = syllableStart(syllable);
  const end = syllableEnd(syllable);
  const at = (index: number) => start + (end - start) * index / Math.max(1, units.length);
  return chunks.map((chunk) => ({
    ...syllable,
    text: chunk.text,
    visualStart: at(chunk.left),
    visualEnd: at(chunk.right),
  }));
}

export function paginateLyricEvent(
  event: RenderEvent,
  presentation: KaraokePresentation,
  measureText: MeasureText,
): LyricPage[] {
  const fontSize = fixedLyricFontSize(presentation);
  const [referenceWidth] = presentation.referenceResolution;
  const contentWidth = referenceWidth * Math.min(
    presentation.layout.maximumLineWidthPercent / 100,
    1 - 2 * presentation.layout.safeAreaPercent / 100,
  );
  const paintPadding = fontSize * LYRIC_PAINT_PADDING_EM;
  const maximumWidth = Math.max(fontSize, contentWidth - 2 * paintPadding);
  const gap = fontSize * LYRIC_WORD_GAP_EM;
  const cueCount = presentation.roleChangeCue.enabled && event.showRoleCue
    ? presentation.roleChangeCue.dotCount
    : 0;
  const cueWidth = cueCount
    ? cueCount * fontSize * LYRIC_CUE_SIZE_EM + (cueCount - 1) * gap
    : 0;
  const source = event.line?.syllables?.length
    ? event.line.syllables
    : [{ text: event.line?.text ?? "", start: event.vocalStart, end: event.vocalEnd }];
  const firstRowWidth = Math.max(fontSize, maximumWidth - cueWidth - (cueWidth ? gap : 0));
  const syllables = source.flatMap((syllable, index) => splitOverwideSyllable(
    syllable,
    index === 0 ? firstRowWidth : maximumWidth,
    measureText,
  ));
  const pages: Syllable[][][] = [];
  let rows: Syllable[][] = [[]];
  let rowWidth = cueWidth;
  for (const syllable of syllables) {
    const width = measureText(syllable.text);
    const spacing = rowWidth > 0 ? gap : 0;
    const rowIsEmpty = rows[rows.length - 1].length === 0;
    if ((rowIsEmpty && rowWidth === 0) || rowWidth + spacing + width <= maximumWidth) {
      rows[rows.length - 1].push(syllable);
      rowWidth += spacing + width;
    } else if (rows.length < MAX_PAGE_ROWS) {
      rows.push([syllable]);
      rowWidth = width;
    } else {
      pages.push(rows);
      rows = [[syllable]];
      rowWidth = width;
    }
  }
  if (rows.some((row) => row.length)) pages.push(rows);
  return pages.map((pageRows, index) => {
    const words = pageRows.flat();
    const start = syllableStart(words[0]);
    const previousWords = index ? pages[index - 1].flat() : [];
    const previousEnd = previousWords.length ? syllableEnd(previousWords[previousWords.length - 1]) : start;
    const switchAt = index ? previousEnd + Math.max(0, start - previousEnd) / 2 : (event.displayStart ?? event.vocalStart ?? start);
    return {
      id: `${event.lineIndex ?? "line"}-${event.displayStart ?? event.vocalStart ?? 0}-${index}`,
      event,
      index,
      rows: pageRows,
      switchAt,
    };
  });
}

function pageAtTime(paginated: PaginatedEvent, time: number): LyricPage {
  let page = paginated.pages[0];
  for (const candidate of paginated.pages.slice(1)) {
    if (time < candidate.switchAt) break;
    page = candidate;
  }
  return page;
}

export function visibleLyricPages(paginated: PaginatedEvent[], time: number): LyricPage[] {
  const visible = new Set(visibleLyricEvents(paginated.map(({ event }) => event), time));
  return paginated
    .filter(({ event }) => visible.has(event))
    .map((event) => pageAtTime(event, time));
}

export function lyricLayoutWindow(boundaries: number[], time: number): [number, number] {
  let left = Number.NEGATIVE_INFINITY;
  for (const boundary of boundaries) {
    if (time < boundary) return [left, boundary];
    left = boundary;
  }
  return [left, Number.POSITIVE_INFINITY];
}

export function lyricBottomSlotHeight(rows: number): number {
  const count = Math.min(MAX_PAGE_ROWS, Math.max(1, Math.trunc(rows)));
  return count * 1.08 + (count - 1) * 0.18;
}

function browserTextMeasurer(fontSize: number): MeasureText {
  if (typeof document === "undefined" || navigator.userAgent.includes("jsdom")) {
    return (text) => graphemes(text).length * fontSize * 0.56;
  }
  const context = document.createElement("canvas").getContext("2d");
  if (!context) return (text) => graphemes(text).length * fontSize * 0.56;
  context.font = `500 ${fontSize}px "Be Vietnam Pro"`;
  return (text) => context.measureText(text).width;
}

export function paintLyricToken(node: HTMLElement, time: number): void {
  const start = Number(node.dataset.start);
  const end = Number(node.dataset.end);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return;
  const fill = end <= start ? (time >= end ? 100 : 0) : clampPercent((time - start) / (end - start) * 100);
  node.style.setProperty("--fill", `${fill}%`);
  node.classList.toggle("is-empty", fill <= 0);
  node.classList.toggle("is-full", fill >= 100);
}

function KaraokeToken({
  text = "",
  start,
  end,
  time,
  cue = false,
}: {
  text?: string;
  start?: number;
  end?: number;
  time: number;
  cue?: boolean;
}) {
  const fill = start == null || end == null
    ? 0
    : end <= start ? (time >= end ? 100 : 0) : clampPercent((time - start) / (end - start) * 100);
  const style = { "--fill": `${fill}%` } as CSSProperties;
  return (
    <span
      className={`lyric-token${cue ? " lyric-cue-dot" : ""}${fill <= 0 ? " is-empty" : ""}${fill >= 100 ? " is-full" : ""}`}
      data-start={start}
      data-end={end}
      style={style}
    >
      <span className="lyric-token-shadow" aria-hidden="true">{text}</span>
      <span className="lyric-token-outline">{text}</span>
      <span className="lyric-word" aria-hidden="true">{text}</span>
    </span>
  );
}

export type LyricOverlayProps = {
  events: RenderEvent[];
  time: number;
  presentation: KaraokePresentation;
  mediaRef?: RefObject<HTMLAudioElement | null>;
  playing?: boolean;
};

function LyricOverlayView({ events, time, presentation, mediaRef, playing = false }: LyricOverlayProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const targetsRef = useRef<HTMLElement[]>([]);
  const layoutWindowRef = useRef<[number, number]>([Number.NEGATIVE_INFINITY, Number.POSITIVE_INFINITY]);
  const [layoutTime, setLayoutTime] = useState(time);
  const [fontEpoch, setFontEpoch] = useState(0);
  const fontSize = fixedLyricFontSize(presentation);
  const measureText = useMemo(() => browserTextMeasurer(fontSize), [fontEpoch, fontSize]);
  const paginated = useMemo(
    () => events.map((event, eventIndex) => ({
      event,
      pages: paginateLyricEvent(event, presentation, measureText).map((page) => ({
        ...page,
        id: `${eventIndex}-${page.id}`,
      })),
    })),
    [events, measureText, presentation],
  );
  const layoutBoundaries = useMemo(() => [...new Set(paginated.flatMap(({ event, pages }) => [
    event.displayStart ?? event.vocalStart ?? 0,
    event.displayEnd ?? event.vocalEnd ?? 0,
    ...pages.slice(1).map(({ switchAt }) => switchAt),
  ]))].sort((left, right) => left - right), [paginated]);
  const displayTime = playing ? layoutTime : (mediaRef?.current?.currentTime ?? time);
  const active = visibleLyricPages(paginated, displayTime);
  const bottomRows = active.find(({ event }) => event.slot !== "top")?.rows.length ?? 1;
  const stackStyle = {
    "--lyric-bottom-slot-height": `${lyricBottomSlotHeight(bottomRows)}em`,
  } as CSSProperties;
  const layoutKey = active.map(({ id }) => id).join("|");
  layoutWindowRef.current = lyricLayoutWindow(layoutBoundaries, displayTime);

  useEffect(() => {
    let cancelled = false;
    document.fonts?.load(`500 ${fontSize}px "Be Vietnam Pro"`).then(
      () => { if (!cancelled) setFontEpoch((value) => value + 1); },
      () => undefined,
    );
    return () => { cancelled = true; };
  }, [fontSize]);

  useLayoutEffect(() => {
    targetsRef.current = [...(rootRef.current?.querySelectorAll<HTMLElement>(".lyric-token[data-start]") ?? [])];
    const current = mediaRef?.current?.currentTime ?? displayTime;
    targetsRef.current.forEach((node) => paintLyricToken(node, current));
  }, [displayTime, layoutKey, mediaRef]);

  useEffect(() => {
    if (!playing || !mediaRef) return;
    let frame = 0;
    const update = () => {
      const current = mediaRef.current?.currentTime;
      if (Number.isFinite(current)) {
        const [windowStart, windowEnd] = layoutWindowRef.current;
        if (Number(current) < windowStart || Number(current) >= windowEnd) setLayoutTime(Number(current));
        else targetsRef.current.forEach((node) => paintLyricToken(node, Number(current)));
      }
      frame = requestAnimationFrame(update);
    };
    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, [mediaRef, playing]);

  const [referenceWidth, referenceHeight] = presentation.referenceResolution;
  return (
    <svg
      className="lyric-overlay"
      viewBox={`0 0 ${referenceWidth} ${referenceHeight}`}
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
      focusable="false"
    >
      <foreignObject width={referenceWidth} height={referenceHeight}>
        <div className="lyric-canvas" ref={rootRef} style={presentationStyle(presentation)}>
          <div className="lyric-stack" style={stackStyle}>
            {active.map((page) => {
              const event = page.event;
              const role = event.line?.role ?? event.role;
              const slot = event.slot === "top" ? "top" : "bottom";
              const cueCount = page.index === 0 && presentation.roleChangeCue.enabled && event.showRoleCue
                ? presentation.roleChangeCue.dotCount
                : 0;
              return (
                <div
                  className={`lyric-line ${slot} ${roleClass(role)}`}
                  data-cue-reason={cueCount ? event.roleCueReason : undefined}
                  data-page={page.index}
                  key={page.id}
                >
                  <div className="lyric-line-content">
                    {page.rows.map((row, rowIndex) => (
                      <span className="lyric-row" key={`${page.id}-row-${rowIndex}`}>
                        {rowIndex === 0 && cueCount > 0 && (
                          <span className="lyric-cue">
                            {Array.from({ length: cueCount }, (_, dotIndex) => {
                              const [start, end] = cueDotInterval(event, dotIndex, cueCount);
                              return <KaraokeToken cue start={start} end={end} time={displayTime} key={`cue-${dotIndex}`} />;
                            })}
                          </span>
                        )}
                        {row.map((syllable, index) => (
                          <KaraokeToken
                            start={syllableStart(syllable)}
                            end={syllableEnd(syllable)}
                            time={displayTime}
                            key={`${index}-${syllable.text}-${syllableStart(syllable)}`}
                            text={syllable.text}
                          />
                        ))}
                      </span>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </foreignObject>
    </svg>
  );
}

export function sameLyricOverlayProps(previous: LyricOverlayProps, next: LyricOverlayProps): boolean {
  return (
  previous.events === next.events
  && previous.presentation === next.presentation
  && previous.mediaRef === next.mediaRef
  && previous.playing === next.playing
  && (next.playing || previous.time === next.time)
  );
}

export const LyricOverlay = memo(LyricOverlayView, sameLyricOverlayProps);
