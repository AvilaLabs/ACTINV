// Serve dist/web before running. Desktop assets are intercepted, never downloaded.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.ACTINV_WEB_URL || "http://127.0.0.1:8080";
for (let attempt = 0; ; attempt++) {
  try { if ((await fetch(base)).ok) break; } catch { /* local server starting */ }
  if (attempt === 49) throw new Error(`Web server did not become ready: ${base}`);
  await new Promise(resolve => setTimeout(resolve, 100));
}
const browser = await chromium.launch({
  headless: true,
  args: ["--enable-unsafe-swiftshader", "--renderer-process-limit=2"],
});
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(base);
  await page.locator("#loading").waitFor({ state: "detached" });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.title(), "ACTINV workbench · Avila Labs");
  const canvas = await page.locator("#actinv-canvas").boundingBox();
  assert.ok(canvas.width > 1000 && canvas.height > 700, "workbench fills the viewport");
  if (process.env.ACTINV_WEB_SCREENSHOT) await page.screenshot({ path: process.env.ACTINV_WEB_SCREENSHOT });
  await context.close();
  assert.deepEqual(errors, [], "WebAssembly starts without runtime errors");

  for (const [platform, userAgent, expected] of [
    ["Win32", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "windows-x86_64-setup.exe"],
    ["Linux x86_64", "Mozilla/5.0 (X11; Linux x86_64)", "linux-x86_64.AppImage"],
    ["MacIntel", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", null],
    ["iPhone", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)", null],
  ]) {
    const context = await browser.newContext({ userAgent });
    await context.addInitScript(({ platform }) => {
      Object.defineProperty(navigator, "platform", { value: platform });
      Object.defineProperty(navigator, "userAgentData", { value: undefined });
    }, { platform });
    const page = await context.newPage();
    let requested;
    await page.route("https://github.com/AvilaLabs/ACTINV/releases/download/**", route => {
      requested = route.request().url();
      return route.fulfill({ status: 200, contentType: "application/octet-stream", headers: { "Content-Disposition": "attachment; filename=installer-test.txt" }, body: "intercepted desktop asset" });
    });
    const download = expected ? page.waitForEvent("download", { timeout: 15000 }) : null;
    await page.goto(`${base}/download/`);
    await page.waitForFunction(() => document.querySelectorAll("#choices a").length === 4);
    if (expected) {
      await download;
      assert.ok(requested.endsWith(expected), `correct installer for ${platform}`);
      assert.equal(await page.locator("#download").getAttribute("href"), requested);
    } else {
      await page.waitForFunction(() => !document.querySelector("#status").textContent.includes("Finding"));
      assert.equal(requested, undefined, `no guessed installer for ${platform}`);
      assert.equal(await page.locator("#download").isVisible(), false);
      if (platform === "MacIntel") assert.equal(await page.locator("#mac-help").isVisible(), true);
    }
    await context.close();
  }
  console.log("Browser startup and four download scenarios passed.");
} finally {
  await browser.close();
}
