// The engine worker's pack cache, in headless Chromium. Needs a site with the library pack, which CI
// does not have, so it runs locally:
//   node web/pack-cache.mjs SITE_DIR SPEC.json OUT_DIR
// SITE_DIR is the browser bundle with the pack at data/tendl-2025-neutron-709g. The script serves it
// itself, counting the pack files the server sends, and runs SPEC.json through the worker:
//   cold      a first visit fetches every file the problem needs;
//   warm      a second run in the same page fetches none;
//   revisit   so does a run after the browser restarts on the same profile;
//   repaired  a cached file whose bytes were replaced is fetched again, once;
//   stale     another pack's cache is deleted, this one kept;
//   no-cache  a page without Cache Storage (an insecure origin) fetches every file and still runs.
// Writes each run's result to OUT_DIR/RUN.json and prints one JSON line per run; exits 1 on a failure.
// Comparing the results with each other or with a native run is left to the caller.
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const [siteArg, specPath, out] = process.argv.slice(2);
const site = path.resolve(siteArg);
const spec = fs.readFileSync(specPath, "utf8");
const pack = "./data/tendl-2025-neutron-709g";
fs.mkdirSync(out, { recursive: true });

const types = { ".html": "text/html", ".js": "text/javascript", ".wasm": "application/wasm", ".json": "application/json" };
let served = [];
const server = http.createServer((request, response) => {
  const url = new URL(request.url, "http://x");
  const file = path.join(site, decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname));
  if (!file.startsWith(site + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    response.writeHead(404).end();
    return;
  }
  if (url.pathname.includes("/data/") && !url.pathname.endsWith("/graph.json")) served.push(url.pathname);
  response.writeHead(200, { "content-type": types[path.extname(file)] || "application/octet-stream" });
  fs.createReadStream(file).pipe(response);
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;
const secure = `http://127.0.0.1:${port}/`;
// A .test name is not a secure context, so its pages have no Cache Storage.
const insecure = `http://insecure.test:${port}/`;
const profile = fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(), "actinv-pack-cache-"));
const launch = () => chromium.launchPersistentContext(profile, {
  headless: true,
  args: ["--renderer-process-limit=2", `--host-resolver-rules=MAP insecure.test 127.0.0.1`],
});

async function run(page, name) {
  served = [];
  const outcome = await page.evaluate(async ({ spec, pack }) => {
    const started = performance.now();
    const worker = new Worker("./actinv-web-engine_loader.js");
    const result = await new Promise(resolve => {
      worker.onerror = e => resolve({ error: "worker error: " + e.message });
      worker.onmessage = e => {
        const m = e.data;
        if (m.kind === "ready") worker.postMessage({ id: 1, spec, pack });
        if (m.kind === "result") resolve({ result: m.result });
        if (m.kind === "error") resolve({ error: m.message });
      };
    });
    worker.terminate();
    return { ...result, seconds: (performance.now() - started) / 1000 };
  }, { spec, pack });
  const caches = await page.evaluate(async () => (typeof caches === "undefined" ? null : await caches.keys()));
  if (outcome.result) fs.writeFileSync(path.join(out, `${name}.json`), outcome.result);
  const row = { run: name, error: outcome.error, seconds: outcome.seconds, fetched: served.length, caches };
  console.log(JSON.stringify(row));
  return row;
}

const failures = [];
const check = (condition, message) => { if (!condition) failures.push(message); };
let context = await launch();
try {
  let page = context.pages()[0] || await context.newPage();
  await page.goto(secure);
  const cold = await run(page, "cold");
  assert.ok(!cold.error, cold.error);
  check(cold.fetched > 0, "cold: no files fetched");
  check(cold.caches?.length === 1, "cold: not exactly one pack cache");
  const warm = await run(page, "warm");
  check(!warm.error && warm.fetched === 0, "warm: files fetched again");

  await context.close();
  context = await launch();
  page = context.pages()[0] || await context.newPage();
  await page.goto(secure);
  const revisit = await run(page, "revisit");
  check(!revisit.error && revisit.fetched === 0, "revisit: files fetched again");

  const replaced = await page.evaluate(async () => {
    const cache = await caches.open((await caches.keys())[0]);
    const key = (await cache.keys())[0];
    await cache.put(key, new Response(new Uint8Array([1, 2, 3])));
    return new URL(key.url).pathname;
  });
  const repaired = await run(page, "repaired");
  check(!repaired.error && repaired.fetched === 1 && served[0] === replaced,
        `repaired: expected only ${replaced} fetched`);
  const after = await run(page, "after-repair");
  check(!after.error && after.fetched === 0, "after-repair: the repaired file was not cached");

  const name = cold.caches[0];
  await page.evaluate(() => caches.open("actinv-pack-stale").then(c => c.put("./x", new Response("x"))));
  const stale = await run(page, "stale");
  check(!stale.error && stale.fetched === 0, "stale: files fetched again");
  check(JSON.stringify(stale.caches) === JSON.stringify([name]), "stale: another pack's cache was kept");

  await page.goto(insecure);
  check(await page.evaluate(() => typeof caches === "undefined"), "no-cache: the page has Cache Storage");
  const bare = await run(page, "no-cache");
  check(!bare.error && bare.fetched === cold.fetched, "no-cache: did not fetch every file");
} finally {
  await context.close();
  server.close();
  fs.rmSync(profile, { recursive: true, force: true });
}
if (failures.length) {
  console.log(JSON.stringify({ failures }));
  process.exit(1);
}
console.log(JSON.stringify({ pass: true }));
