// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppErrorBoundary } from "./AppErrorBoundary";

function BrokenView(): never {
  throw new Error("controlled render failure");
}

describe("top-level Player error boundary", () => {
  let host: HTMLDivElement;
  let root: Root;
  let consoleError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    consoleError.mockRestore();
  });

  it("replaces a render crash with a visible reload surface without claiming native work stopped", () => {
    const reload = vi.fn();
    act(() => root.render(<AppErrorBoundary reload={reload}><BrokenView /></AppErrorBoundary>));
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    expect(host.textContent).toContain("The interface needs to reload");
    expect(host.textContent).toContain("Native background work");
    expect(host.textContent).not.toContain("controlled render failure");
    act(() => host.querySelector<HTMLButtonElement>("button")!.click());
    expect(reload).toHaveBeenCalledOnce();
  });
});
