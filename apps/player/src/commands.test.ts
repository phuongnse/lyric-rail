import { describe, expect, it, vi } from "vitest";
import { APP_COMMANDS, commandForKey, dispatchCommand, type CommandHandlers } from "./commands";

describe("in-window command registry", () => {
  const key = (values: Partial<KeyboardEvent>): KeyboardEvent => ({
    key: "",
    code: "",
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    altKey: false,
    target: null,
    preventDefault: vi.fn(),
    ...values,
  }) as unknown as KeyboardEvent;

  it("keeps command IDs unique", () => {
    expect(new Set(APP_COMMANDS).size).toBe(APP_COMMANDS.length);
  });

  it("maps desktop shortcuts without rendering duplicate menu actions", () => {
    expect(commandForKey(key({ key: "o", ctrlKey: true }))).toBe("open-files");
    expect(commandForKey(key({ key: "O", ctrlKey: true, shiftKey: true }))).toBe("open-folder");
    expect(commandForKey(key({ key: "F11" }))).toBe("toggle-fullscreen");
  });

  it("keeps app accelerators active on controls but protects text editing and Space", () => {
    for (const tagName of ["BUTTON", "A", "SELECT"]) {
      expect(commandForKey(key({ key: "F11", target: { tagName } as unknown as EventTarget }))).toBe("toggle-fullscreen");
    }
    for (const tagName of ["INPUT", "TEXTAREA"]) {
      expect(commandForKey(key({ key: "F11", target: { tagName } as unknown as EventTarget }))).toBeUndefined();
    }
    expect(commandForKey(key({ code: "Space", target: { tagName: "BUTTON" } as unknown as EventTarget }))).toBeUndefined();
    expect(commandForKey(key({ code: "Space", target: null }))).toBe("play-pause");
    expect(commandForKey(key({ key: "F5", target: { tagName: "SELECT" } as unknown as EventTarget }))).toBe("rescan-library");
    expect(commandForKey(key({ key: "o", ctrlKey: true, target: { tagName: "A" } as unknown as EventTarget }))).toBe("open-files");
    expect(commandForKey(key({ key: "F11", target: { isContentEditable: true } as unknown as EventTarget }))).toBeUndefined();
  });

  it("dispatches through one handler map", () => {
    const fullscreen = vi.fn();
    const handlers = Object.fromEntries(APP_COMMANDS.map((command) => [command, vi.fn()])) as unknown as CommandHandlers;
    handlers["toggle-fullscreen"] = fullscreen;
    const event = key({ key: "F11" });
    expect(dispatchCommand(event, handlers)).toBe(true);
    expect(fullscreen).toHaveBeenCalledOnce();
    expect(event.preventDefault).toHaveBeenCalledOnce();
  });
});
