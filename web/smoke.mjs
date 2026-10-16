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

  // The workbench is a canvas, so no control is reachable. The wasm module exports two hooks that
  // run the moved importer and report code in the browser; Trunk exposes its bindings on window.
  await page.waitForFunction(() => window.wasmBindings
    && typeof window.wasmBindings.convert_deck === "function"
    && typeof window.wasmBindings.report_csv === "function");
  const deckHead = "FISPACT\n* t\nMASS 1E-3 1\n FE 100\n";
  const hooks = await page.evaluate(({ decks, result }) => {
    const { convert_deck, report_csv } = window.wasmBindings;
    return {
      cooling: JSON.parse(convert_deck(decks.cooling, undefined)),
      irradiating: JSON.parse(convert_deck(decks.irradiating, undefined)),
      unsupported: JSON.parse(convert_deck(decks.unsupported, undefined)),
      totalsOne: report_csv(result, "totals", 1),
      totalsAll: report_csv(result, "totals", 0),
      unknownTable: JSON.parse(report_csv(result, "no-such-table", 1)),
      missingStep: JSON.parse(report_csv(result, "totals", 9)),
    };
  }, {
    decks: {
      cooling: `${deckHead}FLUX 0\nTIME 1 DAYS ATOMS\nEND\n`,
      irradiating: `${deckHead}FLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n`,
      unsupported: `${deckHead}FROBNICATE\nFLUX 0\nTIME 1 DAYS ATOMS\nEND\n`,
    },
    result: JSON.stringify({
      spec_title: "smoke",
      steps: [[1, 10, 1], [2, 20, 0.5]].map(([step, t_s, k]) => ({
        step,
        t_s,
        inventory: [
          { nuclide: "Fe56", Z: 26, A: 56, LISO: 0, atoms_per_g: 1e22 },
          { nuclide: "Co60", Z: 27, A: 60, LISO: 0, atoms_per_g: 2e15 * k },
        ],
        activity_Bq_per_g: { Co60: 8e4 * k, Mn54: 2e4 * k },
        heat_W_per_g: { total: 0.75 * k, alpha: 0, beta: 0.25 * k, gamma: 0.5 * k },
      })),
    }),
  });
  // a decay-only deck converts without fluxes; every keyword is counted once
  const cooling = hooks.cooling;
  assert.equal(cooling.error, undefined, "decay-only deck converts");
  assert.equal(cooling.report.schema, "actinv-fispact-import-report-1");
  const counted = Object.values(cooling.report.counts).reduce((a, b) => a + b, 0);
  assert.equal(counted, cooling.report.entries.length, "keyword counts add up to the report rows");
  assert.equal(cooling.report.counts.unsupported, 0);
  assert.ok(cooling.report.counts.mapped >= 3, "FISPACT, MASS and TIME are mapped");
  assert.equal(cooling.spec.schedule.length, 1, "one TIME interval is one schedule step");
  assert.equal(cooling.placeholder, false);
  // an irradiating deck without a fluxes file gets the placeholder spectrum and a requirement
  const irradiating = hooks.irradiating;
  assert.equal(irradiating.error, undefined, "irradiating deck converts on a placeholder spectrum");
  assert.equal(irradiating.placeholder, true);
  assert.equal(irradiating.report.spectrum.placeholder, true);
  assert.ok(irradiating.report.required_additions.some(item => item.field === "spectrum"));
  // an unknown keyword is reported, never dropped
  const unsupported = hooks.unsupported;
  assert.ok(unsupported.report.counts.unsupported >= 1, "unsupported keyword is counted");
  assert.ok(unsupported.report.entries.some(row => row.keyword === "FROBNICATE" && row.status === "unsupported"));
  // the report CSV is the one `actinv report --format csv` writes
  const totalsHeader = "step,time_s,activity_Bq_per_g,heat_total_W_per_g,heat_alpha_W_per_g,heat_beta_W_per_g,heat_gamma_W_per_g";
  assert.deepEqual(hooks.totalsOne.split("\n"), [totalsHeader, "1,1e1,1e5,7.5e-1,0e0,2.5e-1,5e-1"]);
  const all = hooks.totalsAll.split("\n");
  assert.equal(all.length, 3, "all steps: header and two rows");
  assert.equal(all[0], totalsHeader);
  assert.ok(all[2].startsWith("2,2e1,5e4,"), all[2]);
  assert.match(hooks.unknownTable.error, /unknown table/);
  assert.match(hooks.missingStep.error, /step 9 is not in the result/);

  // The Waste class page reads the three result schemas the CLI writes (fixtures are control outputs of
  // P105, P112 and P116 with the checkout prefix removed from paths), shows each step's class, and turns
  // anything else into a message. ACTINV_WEB_WASTE_FILES adds result files, separated by the path delimiter.
  const { readFileSync } = await import("node:fs");
  const { delimiter, dirname, join } = await import("node:path");
  const fixtures = join(dirname(new URL(import.meta.url).pathname), "smoke-fixtures");
  const wasteFiles = {
    waste: join(fixtures, "waste-result.json"),
    intrusion_screen: join(fixtures, "intrusion-screen-result.json"),
    twin: join(fixtures, "twin-result.json"),
  };
  const extra = (process.env.ACTINV_WEB_WASTE_FILES || "").split(delimiter).filter(Boolean);
  const texts = [...Object.values(wasteFiles), ...extra].map(path => readFileSync(path, "utf8"));
  const views = await page.evaluate(({ texts, malformed }) => {
    const { waste_view } = window.wasmBindings;
    return { opened: texts.map(text => JSON.parse(waste_view(text))), malformed: malformed.map(text => JSON.parse(waste_view(text))) };
  }, { texts, malformed: ["{ not json", JSON.stringify({ schema: "actinv-waste-result-1" }), JSON.stringify({ steps: [] })] });
  const expectedClasses = text => {
    const doc = JSON.parse(text);
    const waste = doc.waste_classification || doc.classification || doc;
    return waste.components.map(component => component.targets.map(target => target.class));
  };
  views.opened.forEach((view, index) => {
    assert.equal(view.error, undefined, `waste file ${index} opens: ${view.error}`);
    assert.deepEqual(view.components.map(component => component.steps.map(step => step.class)), expectedClasses(texts[index]));
  });
  assert.deepEqual(views.opened.slice(0, 3).map(view => view.source), Object.keys(wasteFiles));
  for (const view of views.malformed) assert.equal(typeof view.error, "string", "a malformed waste file is a message");
  if (process.env.ACTINV_WEB_WASTE_REPORT) {
    const { writeFileSync } = await import("node:fs");
    writeFileSync(process.env.ACTINV_WEB_WASTE_REPORT, JSON.stringify({ files: [...Object.values(wasteFiles), ...extra], views }, null, 1) + "\n");
  }

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
  console.log("Browser startup, import and report hooks, and four download scenarios passed.");
} finally {
  await browser.close();
}
