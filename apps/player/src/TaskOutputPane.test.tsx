// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TaskOutputPane } from "./App";
import type { TaskOutputLine } from "./tasks";

const line = (sequence: number, stream: TaskOutputLine["stream"] = "stdout"): TaskOutputLine => ({
  sequence,
  timestampMillis: sequence,
  taskId: "task-a",
  stream,
  stage: "probe",
  text: `line-${sequence}`,
});

describe("realtime task output view", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    globalThis.ResizeObserver = class {
      observe() { /* fixed test viewport */ }
      unobserve() { /* fixed test viewport */ }
      disconnect() { /* fixed test viewport */ }
    };
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  it("pauses only the view, resumes live lines, filters streams and copies diagnostics", () => {
    const onCopy = vi.fn();
    act(() => root.render(<TaskOutputPane lines={[line(1)]} truncated={false} onCopy={onCopy} />));
    const button = (label: string) => [...host.querySelectorAll("button")].find((item) => item.textContent === label)!;
    act(() => button("Pause view").click());
    act(() => root.render(<TaskOutputPane lines={[line(1), line(2, "stderr")]} truncated={false} onCopy={onCopy} />));
    expect(host.textContent).toContain("line-1");
    expect(host.textContent).not.toContain("line-2");

    act(() => button("Resume view").click());
    expect(host.textContent).toContain("line-2");
    act(() => button("stderr").click());
    expect(host.textContent).not.toContain("line-1");
    expect(host.textContent).toContain("line-2");
    act(() => button("Copy").click());
    expect(onCopy).toHaveBeenCalledOnce();
  });

  it("virtualizes a bounded window and preserves scroll when auto-scroll is off", () => {
    const lines = Array.from({ length: 500 }, (_, index) => line(index + 1));
    act(() => root.render(<TaskOutputPane lines={lines} truncated onCopy={() => undefined} />));
    expect(host.querySelectorAll(".task-output-line").length).toBeLessThan(40);
    expect(host.textContent).toContain("Older output was removed");
    const viewport = host.querySelector<HTMLElement>(".task-output-viewport")!;
    const checkbox = host.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    act(() => checkbox.click());
    viewport.scrollTop = 73;
    act(() => root.render(<TaskOutputPane lines={[...lines, line(501)]} truncated onCopy={() => undefined} />));
    expect(viewport.scrollTop).toBe(73);
  });

  it("uses compact friendly transfer lines by default and restores every exact line in Raw mode", () => {
    const transferLines: TaskOutputLine[] = [
      { ...line(1, "stderr"), timestampMillis: 1_100, stage: "download-and-verify", text: "4%|▍| 70.0M/1.57G [04:54<27:45:26]" },
      { ...line(2, "stderr"), timestampMillis: 1_900, stage: "download-and-verify", text: "5%|▌| 78.2M/1.57G [05:02<06:15:11]" },
    ];
    act(() => root.render(<TaskOutputPane lines={transferLines} truncated={false} onCopy={() => undefined} />));
    expect(host.textContent).toContain("5% · 78.2 MB of 1.57 GB");
    expect(host.textContent).not.toContain("27:45:26");
    expect(host.textContent).not.toContain("70.0M");
    expect(host.textContent).toContain("model setup");

    const raw = [...host.querySelectorAll("button")].find((button) => button.textContent === "Raw")!;
    act(() => raw.click());
    expect(host.textContent).toContain("download-and-verify");
    expect(host.textContent).toContain("4%|▍| 70.0M/1.57G [04:54<27:45:26]");
    expect(host.textContent).toContain("5%|▌| 78.2M/1.57G [05:02<06:15:11]");
  });
});
