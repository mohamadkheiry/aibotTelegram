/* Reproducible, offline Persian captioned training-video generator.
 * Needs Node.js, Playwright, a local Chromium browser and ffmpeg/ffprobe.
 * Browser rendering handles Persian shaping and RTL; no network requests.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawnSync } = require("node:child_process");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const videos = JSON.parse(fs.readFileSync(path.join(__dirname, "slides.json"), "utf8"));
const args = process.argv.slice(2);
const argument = (name, fallback) => {
  const index = args.indexOf(name);
  return index < 0 ? fallback : args[index + 1];
};
const selectedWorkDir = argument("--work-dir", null);
const workDir = path.resolve(selectedWorkDir || fs.mkdtempSync(path.join(os.tmpdir(), "alone-training-")));
const outputDir = path.resolve(argument("--output-dir", __dirname));
const ffmpeg = process.env.FFMPEG_PATH || "ffmpeg";
const browserPath = process.env.CHROME_PATH;
const escape = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const bidi = (value) => escape(value).replace(/(?:\/?[A-Za-z_][A-Za-z0-9_./=-]*)(?:[ ]+(?:[A-Za-z_][A-Za-z0-9_./=-]*|-[A-Za-z0-9_-]+))*/g, '<bdi dir="ltr">$&</bdi>');

function documentFor(video, slide, index) {
  const total = video.slides.length;
  const commandClass = (slide.commands || []).length >= 5 || (slide.commands || []).some((line) => line.length > 100) ? "compact" : "";
  const left = slide.commands
    ? `<div class="code-label">فرمان‌ها و مسیرهای نمونه</div><div class="commands ${commandClass}">${slide.commands.map((line) => `<code>${escape(line)}</code>`).join("")}</div>`
    : `<div class="steps">${slide.steps.map((step, stepIndex) => `<div class="step"><span>${stepIndex + 1}</span><b>${escape(step)}</b></div>`).join("")}</div>`;
  return `<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;width:1920px;height:1080px;overflow:hidden}body{font-family:Tahoma,'Segoe UI',Arial,sans-serif;background:#0b1827;color:#f2f6f8;padding:64px 76px}
.top{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #294057;padding-bottom:25px;font-size:24px;color:#bac8d6}.brand{font-weight:bold;color:#65dec2}.badge{font-size:21px;color:#adc0d1}.heading{height:175px;padding-top:38px}.label{font-size:25px;color:#65dec2;margin-bottom:13px}h1{font-size:57px;line-height:1.5;margin:0;font-weight:700;white-space:nowrap}.main{display:grid;grid-template-columns:1fr 1.03fr;gap:45px;height:570px;direction:rtl}.copy{padding:8px 0}.point{display:flex;gap:20px;margin-bottom:28px;align-items:flex-start}.point i{flex:0 0 9px;height:9px;background:#65dec2;border-radius:50%;margin-top:20px}.point p{font-size:32px;line-height:1.9;margin:0;color:#deE8ef}.panel{background:#12263a;border:2px solid #2b435a;border-radius:24px;padding:28px 30px;align-self:start;min-height:415px;max-height:570px;overflow:hidden}.code-label{font-size:22px;color:#a6bbcd;margin-bottom:26px}.commands{direction:ltr;display:flex;flex-direction:column;gap:18px}.commands code{direction:ltr;unicode-bidi:embed;display:block;white-space:pre-wrap;overflow-wrap:anywhere;font-family:Consolas,'Courier New',monospace;font-size:24px;line-height:1.5;color:#a0f0d9;background:#0b1c2b;border-radius:8px;padding:12px 16px}.commands.compact{gap:13px}.commands.compact code{font-size:21px;padding:10px 13px}.steps{display:flex;flex-direction:column;gap:22px}.step{display:flex;align-items:center;gap:22px;padding:15px 12px;font-size:33px}.step span{width:58px;height:58px;flex:0 0 58px;border:2px solid #5bdbc0;color:#65dec2;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:26px}.reference{direction:ltr;font-family:Consolas,monospace;font-size:20px;color:#abc1d1;margin-top:23px;line-height:1.6}.caption{height:118px;border-top:2px solid #294057;margin-top:10px;padding-top:21px;font-size:26px;line-height:1.8;color:#cedbe6;display:flex;align-items:flex-start;gap:28px}.number{color:#65dec2;font-size:24px;white-space:nowrap;padding-top:5px}.caption-text{flex:1}.bar{position:absolute;bottom:0;right:0;height:8px;background:#65dec2;width:${((index + 1) / total) * 100}%}
.number{direction:ltr}
</style></head><body><div class="top"><span class="brand">الون اکانت</span><span>${escape(video.subtitle)}</span><span class="badge">آموزشی · مثال‌های ساختگی · بدون صدا</span></div><div class="heading"><div class="label">${escape(slide.label)}</div><h1>${escape(slide.title)}</h1></div><div class="main"><div class="copy">${slide.points.map((point) => `<div class="point"><i></i><p>${bidi(point)}</p></div>`).join("")}</div><div class="panel">${left}${slide.reference ? `<div class="reference">${escape(slide.reference)}</div>` : ""}</div></div><div class="caption"><span class="number">${index + 1} / ${total}</span><div class="caption-text">${bidi(slide.caption)}</div></div><div class="bar"></div></body></html>`;
}

async function main() {
  fs.mkdirSync(workDir, { recursive: true });
  fs.mkdirSync(outputDir, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(browserPath ? { executablePath: browserPath } : {}) });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await page.route("http://**/*", (route) => route.abort());
  await page.route("https://**/*", (route) => route.abort());
  const report = [];
  try {
    for (const [name, video] of Object.entries(videos)) {
      const dir = path.join(workDir, name);
      fs.mkdirSync(dir, { recursive: true });
      const frames = [];
      for (let index = 0; index < video.slides.length; index += 1) {
        const slide = video.slides[index];
        const html = documentFor(video, slide, index);
        fs.writeFileSync(path.join(dir, `${index + 1}.html`), html, "utf8");
        await page.setContent(html, { waitUntil: "load" });
        await page.evaluate(() => document.fonts.ready);
        const overflow = await page.evaluate(() => [...document.querySelectorAll(".panel,.copy,.commands,h1,.caption-text")].flatMap((node) => {
          const box = node.getBoundingClientRect();
          return node.scrollHeight > node.clientHeight + 1 || node.scrollWidth > node.clientWidth + 1 || box.bottom > 1070
            ? [{ tag: node.className || node.tagName, scrollHeight: node.scrollHeight, clientHeight: node.clientHeight, bottom: box.bottom }] : [];
        }));
        if (overflow.length) throw new Error(`${name} slide ${index + 1} overflows: ${JSON.stringify(overflow)}`);
        const frame = path.join(dir, `${String(index + 1).padStart(2, "0")}.png`);
        await page.screenshot({ path: frame });
        frames.push({ path: frame, seconds: slide.seconds });
      }
      const escapedPath = (value) => value.replaceAll("\\", "/").replaceAll("'", "'\\''");
      const concat = path.join(dir, "slides.ffconcat");
      fs.writeFileSync(concat, "ffconcat version 1.0\n" + frames.map((frame) => `file '${escapedPath(frame.path)}'\nduration ${frame.seconds}\n`).join("") + `file '${escapedPath(frames.at(-1).path)}'\n`);
      const target = path.join(outputDir, `${name}.mp4`);
      const duration = frames.reduce((sum, frame) => sum + frame.seconds, 0);
      const run = spawnSync(ffmpeg, ["-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-t", String(duration), "-r", "15", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", target], { encoding: "utf8" });
      if (run.status !== 0) throw new Error(`ffmpeg failed: ${run.stderr}`);
      const item = { name, title: video.title, frames: frames.length, durationSeconds: duration, width: 1920, height: 1080, bytes: fs.statSync(target).size };
      report.push(item);
      process.stdout.write(`${JSON.stringify(item)}\n`);
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(workDir, "render-report.json"), JSON.stringify(report, null, 2) + "\n");
}

main().catch((error) => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; });
