const MEDIA_EXTENSIONS = new Set([
  "aac",
  "avi",
  "flac",
  "m4a",
  "mkv",
  "mov",
  "mp3",
  "mp4",
  "ogg",
  "opus",
  "wav",
  "webm",
  "wma",
]);

function extension(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

export function shouldOpenClipEditor(paths: string[]): boolean {
  return paths.length === 1 && MEDIA_EXTENSIONS.has(extension(paths[0]));
}

export function parseTimecodeMillis(value: string): number {
  const text = value.trim();
  if (!text) throw new Error("Timestamp is required");
  const parts = text.split(":");
  if (parts.length < 1 || parts.length > 3 || parts.some((part) => !part.trim())) {
    throw new Error("Use seconds, MM:SS.mmm, or HH:MM:SS.mmm");
  }
  const numbers = parts.map(Number);
  if (numbers.some((number) => !Number.isFinite(number) || number < 0)) {
    throw new Error("Timestamp must be a non-negative number");
  }
  if (parts.length > 1 && numbers[numbers.length - 1] >= 60) {
    throw new Error("Timestamp seconds must be below 60");
  }
  if (parts.length === 3 && numbers[1] >= 60) {
    throw new Error("Timestamp minutes must be below 60");
  }
  const seconds = parts.length === 1
    ? numbers[0]
    : parts.length === 2
      ? numbers[0] * 60 + numbers[1]
      : numbers[0] * 3600 + numbers[1] * 60 + numbers[2];
  const millis = Math.round(seconds * 1000);
  if (!Number.isSafeInteger(millis)) throw new Error("Timestamp is too large");
  return millis;
}

export function formatTimecodeMillis(value: number): string {
  const millis = Math.max(0, Math.round(value));
  const hours = Math.floor(millis / 3_600_000);
  const minutes = Math.floor((millis % 3_600_000) / 60_000);
  const seconds = Math.floor((millis % 60_000) / 1000);
  const remainder = millis % 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(remainder).padStart(3, "0")}`;
}

export function validateClipRange(
  startText: string,
  endText: string,
  durationMillis: number,
): { startMillis: number; endMillis: number } {
  const startMillis = parseTimecodeMillis(startText);
  const endMillis = parseTimecodeMillis(endText);
  if (endMillis <= startMillis) throw new Error("End must be later than Start");
  if (endMillis > durationMillis) throw new Error("End exceeds the media duration");
  return { startMillis, endMillis };
}

export function nudgedTimecode(
  currentText: string,
  deltaMillis: number,
  durationMillis: number,
): string {
  const current = parseTimecodeMillis(currentText);
  return formatTimecodeMillis(Math.min(durationMillis, Math.max(0, current + deltaMillis)));
}

export function loopedPreviewTime(
  currentMillis: number,
  startMillis: number,
  endMillis: number,
): number | undefined {
  return currentMillis < startMillis || currentMillis >= endMillis
    ? startMillis
    : undefined;
}
