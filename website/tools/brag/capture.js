// capture.js URL OUTDIR (--times a,b,c | --fps N SECONDS): frames of the composition as PNGs
const { chromium } = require("playwright-core");
const fs = require("fs");
(async () => {
  const [url, out, mode, arg, arg2] = process.argv.slice(2);
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.HOME + "/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome", args: ["--no-sandbox"] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await page.goto(url);
  await page.evaluate(() => window.ready);
  const times = mode === "--times" ? arg.split(",").map(Number)
    : Array.from({ length: Math.round(+arg2 * +arg) }, (_, i) => i / +arg);
  const el = await page.$("#stage");
  for (let i = 0; i < times.length; i++) {
    await page.evaluate(t => window.render(t), times[i]);
    const name = mode === "--times" ? `t${times[i].toFixed(2)}.png` : `${String(i).padStart(5, "0")}.png`;
    await el.screenshot({ path: `${out}/${name}` });
  }
  await browser.close();
})();
