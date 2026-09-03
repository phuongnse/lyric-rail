export const APP_COMMANDS = [
  "open-files",
  "open-folder",
  "toggle-library",
  "rescan-library",
  "play-pause",
  "previous-song",
  "next-song",
  "toggle-shuffle",
  "toggle-mute",
  "toggle-fullscreen",
] as const;

export type AppCommand = (typeof APP_COMMANDS)[number];
export type CommandHandlers = Record<AppCommand, () => void>;

function targetTag(target: EventTarget | null): string {
  return String((target as { tagName?: string } | null)?.tagName || "").toLowerCase();
}

export function isEditableTarget(target: EventTarget | null): boolean {
  const tag = targetTag(target);
  return tag === "input"
    || tag === "textarea"
    || Boolean((target as { isContentEditable?: boolean } | null)?.isContentEditable);
}

function isActivationTarget(target: EventTarget | null): boolean {
  return isEditableTarget(target)
    || ["button", "a", "select", "summary"].includes(targetTag(target));
}

export function commandForKey(event: KeyboardEvent): AppCommand | undefined {
  const command = event.ctrlKey || event.metaKey;
  const key = event.key.toLowerCase();
  if (isEditableTarget(event.target)) return undefined;
  if (command && key === "o" && event.shiftKey) return "open-folder";
  if (command && key === "o") return "open-files";
  if (command && key === "l") return "toggle-library";
  if (event.key === "F5") return "rescan-library";
  if (event.key === "F11") return "toggle-fullscreen";
  if (event.code === "Space" && !command && !event.altKey && !isActivationTarget(event.target)) return "play-pause";
  return undefined;
}

export function dispatchCommand(
  event: KeyboardEvent,
  handlers: CommandHandlers,
): boolean {
  const command = commandForKey(event);
  if (!command) return false;
  event.preventDefault();
  handlers[command]();
  return true;
}
