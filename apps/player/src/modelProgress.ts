import type { TaskOutputLine } from "./tasks";
import { projectDiagnostic } from "./diagnostics";

export type ModelTransferProgress = {
  percent: number;
  completed: string;
  total: string;
  completedLabel: string;
  totalLabel: string;
};

const ANSI_ESCAPE = /\u001b\[[0-?]*[ -/]*[@-~]/g;
const DECIMAL_AMOUNT = "\\d+(?:\\.\\d+)?";
const TRANSFER_LINE = new RegExp(
  `^\\s*(\\d{1,3})%\\|[^|]*\\|\\s*(${DECIMAL_AMOUNT}(?:[kMGT])?)(?:i?B)?/(${DECIMAL_AMOUNT}(?:[kMGT])?)(?:i?B)?\\s*\\[`,
  "i",
);
const DOWNLOAD_MESSAGE = /^Downloading pinned model \d+ of \d+: \S/;

function amountLabel(token: string): string {
  const match = token.match(/^(\d+(?:\.\d+)?)([kMGT])?$/i);
  if (!match) return token;
  const suffix = match[2]?.toUpperCase();
  return suffix ? `${match[1]} ${suffix === "K" ? "k" : suffix}B` : match[1];
}

export function parseModelTransferProgress(text: string): ModelTransferProgress | undefined {
  if (text.startsWith("{")) {
    try {
      const value = JSON.parse(projectDiagnostic(text)) as { phase?: string; completedBytes?: number; totalBytes?: number };
      if (value.phase !== "downloading" || value.completedBytes == null || !value.totalBytes) return undefined;
      const completed = String(value.completedBytes);
      const total = String(value.totalBytes);
      return { percent: Math.min(100, value.completedBytes / value.totalBytes * 100), completed, total,
        completedLabel: `${completed} bytes`, totalLabel: `${total} bytes` };
    } catch { return undefined; }
  }
  const match = text.replace(ANSI_ESCAPE, "").match(TRANSFER_LINE);
  if (!match) return undefined;
  const percent = Number(match[1]);
  if (!Number.isFinite(percent) || percent < 0 || percent > 100) return undefined;
  return {
    percent,
    completed: match[2],
    total: match[3],
    completedLabel: amountLabel(match[2]),
    totalLabel: amountLabel(match[3]),
  };
}

export function latestModelTransferProgress(lines: TaskOutputLine[]): ModelTransferProgress | undefined {
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const parsed = parseModelTransferProgress(lines[index].text);
    if (parsed) return parsed;
    if (isModelDownloadMarker(lines[index].text)) return undefined;
  }
  return undefined;
}

export function modelProgressMessage(text: string): string | undefined {
  if (!text.startsWith("{") || text.length > 16 * 1024) return undefined;
  try {
    const value = JSON.parse(text) as { kind?: unknown; message?: unknown };
    if (value.kind !== "lyricrail.model-install.progress" || typeof value.message !== "string") {
      return undefined;
    }
    const message = value.message;
    return message.length > 0
      && message.length <= 512
      && message === message.trim()
      && !/[\r\n\u0000]/.test(message)
      ? message
      : undefined;
  } catch {
    return undefined;
  }
}

export function isModelDownloadMarker(text: string): boolean {
  try {
    const value = JSON.parse(projectDiagnostic(text)) as { phase?: string };
    if (value.phase) return true;
  } catch { /* Legacy output has no typed phase. */ }
  const message = modelProgressMessage(text);
  return Boolean(message && DOWNLOAD_MESSAGE.test(message));
}

export function friendlyOutputText(line: TaskOutputLine): string {
  const transfer = parseModelTransferProgress(line.text);
  if (transfer) return `${transfer.percent}% · ${transfer.completedLabel} of ${transfer.totalLabel}`;
  return modelProgressMessage(line.text) ?? line.text;
}

export function compactFriendlyOutput(lines: TaskOutputLine[]): TaskOutputLine[] {
  const seenTransferSeconds = new Set<string>();
  const compacted: TaskOutputLine[] = [];
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = lines[index];
    if (parseModelTransferProgress(line.text)) {
      const key = `${line.taskId}:${Math.floor(line.timestampMillis / 1_000)}`;
      if (seenTransferSeconds.has(key)) continue;
      seenTransferSeconds.add(key);
    }
    compacted.push(line);
  }
  return compacted.reverse();
}

export function outputStageLabel(stage: string | null | undefined): string {
  if (!stage) return "—";
  const known: Record<string, string> = {
    "download-and-verify": "model setup",
    "portable-preview": "clip preview",
    "ciphertext-cache": "Drive cache",
    "render-player-media": "encode media",
    render_player_media: "encode media",
  };
  return known[stage] ?? stage.replace(/[-_]+/g, " ");
}
