# Browser data pack

The workbench's **Run in browser** panel appears only where the site serves the library pack at
`data/tendl-2025-neutron-709g/` beside the app (the page checks for its `graph.json`). The pack is
built from the data catalog, never committed, and qualified by P196A (ledger 383).

## Build it

From the repository root, with the catalog's data in `actinv-data/v1.2.0` and a release `pack_build`:

```sh
ACTINV_PACK_IDS=tendl-2025-neutron-709g,endfb-viii-0-decay,jeff-3-3-decay \
  target/release/pack_build actinv-data/v1.2.0/activation/tendl-2025-neutron-709g.npz OUT \
  actinv-data/v1.2.0/decay/endf-b-viii-0_decay.dat actinv-data/v1.2.0/decay/jeff-3-3_decay.dat
```

`OUT` gets `graph.json`, `targets/` and `decay/`: 247 MB in 10,524 files, the largest 4.2 MB.

## Host it on the free plan

Serve it as static assets of the existing `actinv-workbench` Worker: copy `OUT` to
`data/tendl-2025-neutron-709g/` inside the bundle before `wrangler deploy --assets`. On the Workers
Free plan, requests to static assets are free and unlimited and do not count towards the daily
request limit, so browser runs cost nothing however many there are. Keep it that way:

- **No Worker script.** `wrangler.jsonc` has none. A script that runs before the assets would count
  every file fetched (about 1,400 for a typical problem) towards the 100,000 requests a day.
- **Not R2.** Serving the files from R2 counts them as Worker requests or R2 reads.
- **Under 20,000 files per deploy**, the Free plan's limit (25 MiB per file). The app, downloads and
  handbook are under 100 files. A second pack would pass the limit: bundle small files first.

Each deploy uploads only changed files, so the pack goes up once.

## The browser keeps what it fetches

The engine worker keeps every pack file it fetches in the browser's Cache Storage, keyed by the
file's URL and the SHA-256 the graph records, and uses a kept file only if its bytes still have that
SHA-256. A later run, or a later visit, fetches only files it has not kept. A pack built from other
data gets a new cache and the old one is deleted. Pages without a secure context (plain HTTP other
than from localhost) have no Cache Storage and fetch every file each run.

`web/pack-cache.mjs` checks this in headless Chromium against a local site with the pack. CI has
no pack, so run it locally after changing the worker:

```sh
node web/pack-cache.mjs SITE_DIR SPEC.json OUT_DIR
```

It runs the problem cold, warm, after a browser restart, with one kept file corrupted, beside a stale
pack's cache, and on an insecure origin, and checks how many files each run fetched. Compare the
results it writes with a native `pack_run` of the same problem.
