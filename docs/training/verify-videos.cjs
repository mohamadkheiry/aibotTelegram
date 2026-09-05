"use strict";

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const { pathToFileURL } = require("node:url");
const { spawnSync } = require("node:child_process");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

async function main() {
  const argumentIndex = process.argv.indexOf("--work-dir");
  const baseWork = argumentIndex < 0 ? os.tmpdir() : path.resolve(process.argv[argumentIndex + 1]);
  fs.mkdirSync(baseWork, { recursive: true });
  const work = fs.mkdtempSync(path.join(baseWork, "alone-video-verify-"));
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const results = [];
  try {
    for (const name of ["admin-usage-fa", "deployment-transfer-fa"]) {
      const file = path.join(__dirname, `${name}.mp4`);
      const decode = spawnSync(process.env.FFMPEG_PATH || "ffmpeg", ["-v", "error", "-i", file, "-f", "null", "-"], { encoding: "utf8" });
      if (decode.status !== 0 || decode.stderr.trim()) throw new Error(`Decode failed: ${decode.stderr}`);
      const player = path.join(work, `${name}.html`);
      fs.writeFileSync(player, `<html><body style="margin:0;background:#000"><video id="player" muted controls preload="auto" style="width:1920px;height:1080px" src="${pathToFileURL(file).href}"></video></body></html>`);
      await page.goto(pathToFileURL(player).href);
      const playback = await page.evaluate(async () => {
        const video = document.querySelector("video");
        if (video.readyState < 1) await new Promise((resolve, reject) => {
          video.addEventListener("loadedmetadata", resolve, { once: true });
          video.addEventListener("error", () => reject(new Error("Video metadata failed")), { once: true });
          setTimeout(() => reject(new Error("Video metadata timed out")), 10000);
        });
        await video.play();
        await new Promise((resolve, reject) => {
          video.addEventListener("timeupdate", () => { if (video.currentTime >= 0.2) resolve(); });
          setTimeout(() => reject(new Error("Video playback did not advance")), 10000);
        });
        video.pause();
        return { width: video.videoWidth, height: video.videoHeight, duration: video.duration, currentTime: video.currentTime, readyState: video.readyState };
      });
      if (playback.width !== 1920 || playback.height !== 1080 || playback.duration < 160) throw new Error("Unexpected video dimensions/duration");
      const bytes = fs.readFileSync(file);
      results.push({ name, bytes: bytes.length, sha256: crypto.createHash("sha256").update(bytes).digest("hex"), decode: "passed", browserPlayback: playback });
    }
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(results, null, 2) + "\n");
}

main().catch((error) => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; });
