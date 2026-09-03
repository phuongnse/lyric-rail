import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

export const ICON_NAMES = [
  "refresh",
  "close",
  "search",
  "play",
  "pause",
  "previous",
  "next",
  "shuffle",
  "volume-high",
  "volume-muted",
  "fullscreen",
  "fullscreen-exit",
  "edit",
  "plus",
  "alert",
  "activity",
  "more",
] as const;

export type IconName = (typeof ICON_NAMES)[number];

function glyph(name: IconName): ReactNode {
  switch (name) {
    case "refresh":
      return <><path d="M20 6v5h-5"/><path d="M19.1 15a8 8 0 1 1-.2-6"/></>;
    case "close":
      return <><path d="M6 6l12 12"/><path d="M18 6L6 18"/></>;
    case "search":
      return <><circle cx="11" cy="11" r="6"/><path d="M16 16l4 4"/></>;
    case "play":
      return <path d="M8 5.7v12.6a1 1 0 0 0 1.55.83l8.4-6.3a1 1 0 0 0 0-1.66l-8.4-6.3A1 1 0 0 0 8 5.7Z" fill="currentColor" stroke="none"/>;
    case "pause":
      return <><path d="M9 6v12"/><path d="M15 6v12"/></>;
    case "previous":
      return <><path d="M6.5 5v14"/><path d="m17.5 6-8 6 8 6Z" fill="currentColor" stroke="none"/></>;
    case "next":
      return <><path d="M17.5 5v14"/><path d="m6.5 6 8 6-8 6Z" fill="currentColor" stroke="none"/></>;
    case "shuffle":
      return <><path d="M4 7h3.2c4.8 0 4.8 10 9.6 10H20"/><path d="m17 14 3 3-3 3"/><path d="M4 17h3.2c1.8 0 3-1.4 4.1-3.2"/><path d="M13.1 9.8C14.1 8.3 15.2 7 16.8 7H20"/><path d="m17 4 3 3-3 3"/></>;
    case "volume-high":
      return <><path d="M5 10v4h4l5 4V6l-5 4H5Z"/><path d="M17 9a4.2 4.2 0 0 1 0 6"/><path d="M19.5 6.5a7.8 7.8 0 0 1 0 11"/></>;
    case "volume-muted":
      return <><path d="M5 10v4h4l5 4V6l-5 4H5Z"/><path d="m17 9 5 6"/><path d="m22 9-5 6"/></>;
    case "fullscreen":
      return <><path d="M9 4H4v5"/><path d="M15 4h5v5"/><path d="M20 15v5h-5"/><path d="M4 15v5h5"/></>;
    case "fullscreen-exit":
      return <><path d="M9 4v5H4"/><path d="M15 4v5h5"/><path d="M20 15h-5v5"/><path d="M4 15h5v5"/></>;
    case "edit":
      return <><path d="m14.5 5.5 4 4"/><path d="M5 19l1-4L16.5 4.5a1.4 1.4 0 0 1 2 0l1 1a1.4 1.4 0 0 1 0 2L9 18l-4 1Z"/><path d="M13.5 7.5l4 4"/></>;
    case "plus":
      return <><path d="M12 5v14"/><path d="M5 12h14"/></>;
    case "alert":
      return <><path d="M12 3 2.8 19a1.2 1.2 0 0 0 1 1.8h16.4a1.2 1.2 0 0 0 1-1.8L12 3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></>;
    case "activity":
      return <><circle cx="12" cy="12" r="8.5"/><path d="M6.5 12h3l1.6-3.4 2.2 7 1.5-3.6h2.7"/></>;
    case "more":
      return <><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none"/></>;
  }
  const exhaustive: never = name;
  return exhaustive;
}

export function Icon({
  name,
  size = 20,
}: {
  name: IconName;
  size?: number;
}) {
  return (
    <svg
      className="ui-icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
      aria-hidden="true"
    >
      {glyph(name)}
    </svg>
  );
}

type IconButtonProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "aria-label" | "children"
> & {
  label: string;
  icon: IconName;
  iconSize?: number;
};

export function placeTooltip(
  rect: Pick<DOMRect, "left" | "right" | "top" | "bottom">,
  viewportWidth: number,
  tooltipWidth: number,
) {
  const maxWidth = Math.min(280, Math.max(1, viewportWidth - 24));
  const center = rect.left + (rect.right - rect.left) / 2;
  const boundedTooltipWidth = Math.min(maxWidth, Math.max(0, tooltipWidth));
  const side = rect.top < 104 ? "bottom" : "top";
  const align = center - boundedTooltipWidth / 2 < 12
    ? "left"
    : center + boundedTooltipWidth / 2 > viewportWidth - 12
      ? "right"
      : "center";
  const left = align === "left"
    ? Math.max(12, rect.left)
    : align === "right"
      ? Math.min(viewportWidth - 12, rect.right)
      : center;
  return {
    side,
    align,
    left,
    top: side === "top" ? rect.top - 8 : rect.bottom + 8,
    maxWidth,
  };
}

export function IconButton({
  label,
  icon,
  iconSize = 20,
  className = "",
  type = "button",
  onMouseEnter,
  onMouseLeave,
  onFocus,
  onBlur,
  ...buttonProps
}: IconButtonProps) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const tooltipRef = useRef<HTMLSpanElement>(null);
  const tooltipId = useId();
  const [tooltip, setTooltip] = useState<
    (ReturnType<typeof placeTooltip> & { measured: boolean }) | undefined
  >();
  const prepareTooltip = useCallback(() => {
    const button = buttonRef.current;
    if (!button || typeof window === "undefined") return;
    setTooltip({
      ...placeTooltip(button.getBoundingClientRect(), window.innerWidth, 0),
      measured: false,
    });
  }, []);
  const updateTooltip = useCallback(() => {
    const button = buttonRef.current;
    const tooltipElement = tooltipRef.current;
    if (!button || !tooltipElement || typeof window === "undefined") return;
    setTooltip({
      ...placeTooltip(
        button.getBoundingClientRect(),
        window.innerWidth,
        tooltipElement.getBoundingClientRect().width,
      ),
      measured: true,
    });
  }, []);

  useLayoutEffect(() => {
    if (!tooltip || tooltip.measured) return;
    updateTooltip();
  }, [tooltip, updateTooltip]);

  useLayoutEffect(() => {
    if (!tooltipRef.current) return;
    prepareTooltip();
  }, [label, prepareTooltip]);

  useEffect(() => {
    if (!tooltip || typeof window === "undefined") return;
    window.addEventListener("resize", prepareTooltip);
    window.addEventListener("scroll", updateTooltip, true);
    return () => {
      window.removeEventListener("resize", prepareTooltip);
      window.removeEventListener("scroll", updateTooltip, true);
    };
  }, [prepareTooltip, tooltip, updateTooltip]);

  return (
    <>
      <button
        {...buttonProps}
        ref={buttonRef}
        type={type}
        className={`icon-control ${className}`.trim()}
        aria-label={label}
        aria-describedby={tooltip?.measured ? tooltipId : undefined}
        onMouseEnter={(event) => { prepareTooltip(); onMouseEnter?.(event); }}
        onMouseLeave={(event) => { setTooltip(undefined); onMouseLeave?.(event); }}
        onFocus={(event) => { prepareTooltip(); onFocus?.(event); }}
        onBlur={(event) => { setTooltip(undefined); onBlur?.(event); }}
      >
        <Icon name={icon} size={iconSize} />
      </button>
      {tooltip && typeof document !== "undefined" && createPortal(
        <span
          ref={tooltipRef}
          id={tooltipId}
          className={`icon-tooltip ${tooltip.side} ${tooltip.align}`}
          role="tooltip"
          style={{
            left: tooltip.left,
            top: tooltip.top,
            maxWidth: tooltip.maxWidth,
            visibility: tooltip.measured ? "visible" : "hidden",
          }}
        >
          {label}
        </span>,
        document.body,
      )}
    </>
  );
}
