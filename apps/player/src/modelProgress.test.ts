import { describe, expect, it } from "vitest";
import {
  compactFriendlyOutput,
  friendlyOutputText,
  latestModelTransferProgress,
  isModelDownloadMarker,
  modelProgressMessage,
  outputStageLabel,
  parseModelTransferProgress,
} from "./modelProgress";
import type { TaskOutputLine } from "./tasks";

const line = (sequence: number, text: string, timestampMillis = sequence * 100): TaskOutputLine => ({
  sequence,
  timestampMillis,
  taskId: "model-install",
  stream: "stderr",
  stage: "download-and-verify",
  text,
});

describe("model transfer presentation", () => {
  it("extracts useful byte progress and ignores the graphical bar and unstable source ETA", () => {
    const parsed = parseModelTransferProgress("  5%|▍         | 78.2M/1.57G [07:02<6:15:11, 39.3kiB/s]");
    expect(parsed).toEqual({
      percent: 5,
      completed: "78.2M",
      total: "1.57G",
      completedLabel: "78.2 MB",
      totalLabel: "1.57 GB",
    });
    expect(friendlyOutputText(line(1, "  5%|▍ | 78.2M/1.57G [07:02<6:15:11]")))
      .toBe("5% · 78.2 MB of 1.57 GB");
    expect(friendlyOutputText(line(2, "37%| | 75.6M/195M [verified download]")))
      .toBe("37% · 75.6 MB of 195 MB");
  });

  it("preserves zero, rejects malformed data and finds the latest valid transfer", () => {
    expect(parseModelTransferProgress("0%| | 0.00/1.00G [00:00<?, ?iB/s]")?.percent).toBe(0);
    expect(parseModelTransferProgress("15% overall setup progress")).toBeUndefined();
    expect(parseModelTransferProgress("101%|x| 2M/4M [00:01<00:01]")).toBeUndefined();
    expect(parseModelTransferProgress("5%|x| 1.2.3M/4M [00:01<00:01]")).toBeUndefined();
    expect(parseModelTransferProgress("5%|x| .../... [00:01<00:01]")).toBeUndefined();
    expect(latestModelTransferProgress([
      line(1, "1%| | 10M/1.00G [00:01<01:00]"),
      line(2, "ordinary diagnostic"),
      line(3, "5%|▍| 50M/1.00G [00:05<01:35]"),
    ])?.percent).toBe(5);
  });

  it("never reuses the previous file after a newer model marker", () => {
    const marker = JSON.stringify({
      kind: "lyricrail.model-install.progress",
      message: "Downloading pinned model 2 of 6: aligner",
      progressPercent: 15,
    });
    const prior = line(1, "100%|████| 1.57G/1.57G [10:00<00:00]");
    const boundary = line(2, marker);
    expect(isModelDownloadMarker(marker)).toBe(true);
    expect(modelProgressMessage(marker)).toBe("Downloading pinned model 2 of 6: aligner");
    expect(friendlyOutputText(line(9, marker))).toBe("Downloading pinned model 2 of 6: aligner");
    expect(latestModelTransferProgress([prior, boundary])).toBeUndefined();
    expect(latestModelTransferProgress([
      prior,
      boundary,
      line(3, "1%| | 5M/500M [00:01<01:20]"),
    ])?.percent).toBe(1);
  });

  it("leaves malformed and unrelated structured-looking output untouched", () => {
    const emptyMessage = '{"kind":"lyricrail.model-install.progress","message":""}';
    const multilineMessage = '{"kind":"lyricrail.model-install.progress","message":"first\\nsecond"}';
    const unrelated = '{"kind":"another.progress","message":"Downloading"}';
    expect(modelProgressMessage(emptyMessage)).toBeUndefined();
    expect(modelProgressMessage(multilineMessage)).toBeUndefined();
    expect(modelProgressMessage(unrelated)).toBeUndefined();
    expect(friendlyOutputText(line(10, emptyMessage))).toBe(emptyMessage);
    expect(friendlyOutputText(line(11, unrelated))).toBe(unrelated);
  });

  it("keeps only the latest transfer refresh per second in friendly mode and shortens known stages", () => {
    const raw = [
      line(1, "1%| | 10M/1G [00:01<01:00]", 1_100),
      line(2, "2%| | 20M/1G [00:01<00:50]", 1_900),
      line(3, "important warning", 1_950),
      line(4, "3%| | 30M/1G [00:02<00:45]", 2_100),
    ];
    expect(compactFriendlyOutput(raw).map((entry) => entry.sequence)).toEqual([2, 3, 4]);
    expect(outputStageLabel("download-and-verify")).toBe("model setup");
    expect(outputStageLabel("align_lyrics")).toBe("align lyrics");
  });
});
