// P196: run one problem in headless Chromium through the browser's engine worker, as the page does.
//   node controls/p196_browser.mjs BASE_URL SPEC.json OUT.json
// BASE_URL serves the browser bundle with the pack at data/tendl-2025-neutron-709g. Prints one JSON line:
// {error?, seconds, fetched_files, events, page_errors}; writes the result JSON text to OUT.json.
import fs from "node:fs";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const [base, specPath, out] = process.argv.slice(2);
const spec = fs.readFileSync(specPath, "utf8");
const browser = await chromium.launch({ headless: true, args: ["--renderer-process-limit=2"] });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto(base);
  const outcome = await page.evaluate(async spec => {
    const started = performance.now();
    const worker = new Worker("./actinv-web-engine_loader.js");
    const events = [];
    const result = await new Promise(resolve => {
      worker.onerror = e => resolve({ error: "worker error: " + e.message });
      worker.onmessage = e => {
        const m = e.data;
        if (m.kind === "ready") {
          worker.postMessage({ id: 1, spec, pack: "./data/tendl-2025-neutron-709g" });
          return;
        }
        events.push({ kind: m.kind, done: m.done, total: m.total, ms: performance.now() - started });
        if (m.kind === "result") resolve({ result: m.result });
        if (m.kind === "error") resolve({ error: m.message });
      };
    });
    worker.terminate();
    const fetched = events.filter(e => e.kind === "progress").map(e => e.total)[0];
    const solving = events.find(e => e.kind === "solving");
    return { ...result, seconds: (performance.now() - started) / 1000, fetched_files: fetched,
             fetch_seconds: solving ? solving.ms / 1000 : null };
  }, spec);
  if (outcome.result) fs.writeFileSync(out, outcome.result);
  delete outcome.result;
  console.log(JSON.stringify({ ...outcome, page_errors: errors }));
} finally {
  await browser.close();
}
