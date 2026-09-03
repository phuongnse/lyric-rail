// @vitest-environment jsdom

import { act, useRef } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useFocusContainment } from "./focus";

function Harness({ open }: { open: boolean }) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const boundaryRef = useRef<HTMLElement>(null);
  const initialRef = useRef<HTMLButtonElement>(null);
  useFocusContainment(open, boundaryRef, initialRef, triggerRef);
  return (
    <>
      <button ref={triggerRef}>Trigger</button>
      <section ref={boundaryRef} tabIndex={-1} aria-hidden={!open} inert={!open}>
        <button ref={initialRef}>First</button>
        <button>Last</button>
      </section>
    </>
  );
}

describe("focus containment", () => {
  let host: HTMLDivElement;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.append(host);
  });

  afterEach(() => host.remove());

  it("contains forward and reverse Tab then restores the trigger", () => {
    const root = createRoot(host);
    act(() => root.render(<Harness open={false} />));
    const trigger = host.querySelector<HTMLButtonElement>("button")!;
    trigger.focus();
    act(() => root.render(<Harness open />));
    const buttons = host.querySelectorAll<HTMLButtonElement>("section button");
    expect(document.activeElement).toBe(buttons[0]);

    buttons[1].focus();
    buttons[1].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(buttons[0]);

    buttons[0].focus();
    buttons[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(buttons[1]);

    act(() => root.render(<Harness open={false} />));
    expect(document.activeElement).toBe(trigger);
    act(() => root.unmount());
  });
});
