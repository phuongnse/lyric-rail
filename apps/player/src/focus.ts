import { useEffect, useRef, type RefObject } from "react";

export const FOCUSABLE = "button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])";

export function useFocusContainment(
  open: boolean,
  containerRef: RefObject<HTMLElement | null>,
  initialRef?: RefObject<HTMLElement | null>,
  restoreRef?: RefObject<HTMLElement | null>,
) {
  const previousFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!open) return;
    previousFocus.current = restoreRef?.current
      ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    const container = containerRef.current;
    if (!container) return;
    (initialRef?.current ?? container.querySelector<HTMLElement>(FOCUSABLE) ?? container).focus();
    const contain = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusable = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE))
        .filter((element) => !element.hasAttribute("inert") && element.getAttribute("aria-hidden") !== "true");
      if (!focusable.length) {
        event.preventDefault();
        container.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!focusable.includes(document.activeElement as HTMLElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    container.addEventListener("keydown", contain);
    return () => {
      container.removeEventListener("keydown", contain);
      const restoreContainer = restoreRef?.current ?? previousFocus.current;
      const restore = restoreContainer?.matches(FOCUSABLE)
        ? restoreContainer
        : restoreContainer?.querySelector<HTMLElement>(FOCUSABLE);
      if (restore?.isConnected) restore.focus();
    };
  }, [containerRef, initialRef, open, restoreRef]);
}
