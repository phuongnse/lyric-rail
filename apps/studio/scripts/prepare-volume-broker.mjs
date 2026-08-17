import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const workspaceRoot = resolve(scriptDirectory, "../../..");
const targetArchitecture = process.env.TAURI_ENV_ARCH ?? "x86_64";

if (process.platform !== "win32") {
  throw new Error("The LyricRail Volume Broker can be bundled only on Windows.");
}
if (targetArchitecture !== "x86_64") {
  throw new Error(
    `No reviewed Volume Broker bundle exists for architecture ${targetArchitecture}.`,
  );
}

const build = spawnSync(
  "cargo",
  ["build", "--release", "--locked", "-p", "lyricrail-volume-broker"],
  {
    cwd: workspaceRoot,
    encoding: "utf8",
    stdio: "inherit",
  },
);
if (build.error) throw build.error;
if (build.status !== 0) {
  throw new Error(`Volume Broker release build failed with status ${build.status}.`);
}

const source = resolve(
  workspaceRoot,
  "target/release/lyricrail-volume-broker.exe",
);
const destination = resolve(
  workspaceRoot,
  "apps/studio/src-tauri/windows/payload/lyricrail-volume-broker.exe",
);
const sourceInfo = statSync(source);
if (!sourceInfo.isFile() || sourceInfo.size < 64 * 1024) {
  throw new Error("Volume Broker build output is missing or implausibly small.");
}
mkdirSync(dirname(destination), { recursive: true });
copyFileSync(source, destination);

const sha256 = createHash("sha256").update(readFileSync(destination)).digest("hex");
process.stdout.write(
  `Prepared LyricRail Volume Broker (${sourceInfo.size} bytes, SHA-256 ${sha256}).\n`,
);
