#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const root = resolve(scriptDirectory, "..");
const source = join(root, "assets", "brand", "lyricrail-mark.svg");
const outputDirectory = join(root, "apps", "player", "src-tauri", "icons");
const cli = join(root, "node_modules", "@tauri-apps", "cli", "tauri.js");
const cliPackage = JSON.parse(
  readFileSync(join(root, "node_modules", "@tauri-apps", "cli", "package.json"), "utf8"),
);

function normalizeIcns(path) {
  const data = readFileSync(path);
  if (data.length < 8 || data.subarray(0, 4).toString("ascii") !== "icns") {
    throw new Error(`${path} is not an ICNS container`);
  }
  const chunks = [];
  let offset = 8;
  while (offset < data.length) {
    if (offset + 8 > data.length) throw new Error(`${path} has a truncated ICNS chunk`);
    const length = data.readUInt32BE(offset + 4);
    if (length < 8 || offset + length > data.length) {
      throw new Error(`${path} has an invalid ICNS chunk length`);
    }
    chunks.push(Buffer.from(data.subarray(offset, offset + length)));
    offset += length;
  }
  chunks.sort((left, right) => {
    const typeOrder = left.subarray(0, 4).compare(right.subarray(0, 4));
    return typeOrder || left.compare(right);
  });
  const header = Buffer.alloc(8);
  header.write("icns", 0, "ascii");
  header.writeUInt32BE(8 + chunks.reduce((size, chunk) => size + chunk.length, 0), 4);
  writeFileSync(path, Buffer.concat([header, ...chunks]));
}

const generated = spawnSync(
  process.execPath,
  [cli, "icon", source, "--output", outputDirectory],
  { cwd: root, stdio: "inherit", shell: false },
);
if (generated.error) throw generated.error;
if (generated.status !== 0) process.exit(generated.status ?? 1);
for (const mobileDirectory of ["android", "ios"]) {
  rmSync(join(outputDirectory, mobileDirectory), { recursive: true, force: true });
}
normalizeIcns(join(outputDirectory, "icon.icns"));

const digest = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");
const outputs = Object.fromEntries(
  readdirSync(outputDirectory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.(?:png|ico|icns)$/i.test(entry.name))
    .map((entry) => join(outputDirectory, entry.name))
    .sort((left, right) => left.localeCompare(right))
    .map((path) => [relative(root, path).replaceAll("\\", "/"), digest(path)]),
);
const manifest = {
  schemaVersion: 1,
  source: relative(root, source).replaceAll("\\", "/"),
  sourceSha256: digest(source),
  generator: {
    command: "npm run brand:icons",
    package: "@tauri-apps/cli",
    version: cliPackage.version,
  },
  outputs,
};
writeFileSync(
  join(root, "assets", "brand", "generated-icons.json"),
  `${JSON.stringify(manifest, null, 2)}\n`,
  "utf8",
);
console.log(`Recorded ${Object.keys(outputs).length} LyricRail icon outputs.`);
