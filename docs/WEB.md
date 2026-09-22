# ACTINV in the browser

Open [actinv.avilalabs.org](https://actinv.avilalabs.org/) without installing anything.
The browser workbench shares the desktop app's problem schema, result validation, and plotting code.

## Try it

1. Choose **Try the results tutorial** to explore a fictional nuclide with a one-hour half-life.
2. Switch between activity, total heat, and inventory. Select a nuclide to follow its history.
3. Open a result JSON produced by ACTINV, or drop it into the window. Use **Compare result** to overlay another run.
4. Open **Problem** to edit the iron example or your own problem. Download the JSON and open it in the desktop app to run it.

Result files can include photon sources, calculation details, and diagnostics; these appear under **Result record**.
Inventory CSV exports use the selected time step. Result JSON downloads retain the entire imported result.
Comparison plots use each file's absolute time and recorded normalization; check that the runs are comparable.

Files are read locally and are not uploaded. Download your edits before closing or reloading the tab;
the workbench does not retain them between visits. Open one file at a time, up to 32 MiB.
The interface works best on a laptop or desktop with WebGL enabled.

## Calculations

The browser workbench prepares inputs and displays results. It does not run the activation solver, fetch nuclear-data
libraries, or import transport tallies. Use the [desktop app](https://actinv.avilalabs.org/download/) or CLI for those tasks.
Problem validation checks the specification; local data paths are preserved and checked by the desktop app during setup.

## Desktop downloads

The [download page](https://actinv.avilalabs.org/download/) starts the matching installer when the browser identifies
a supported platform. It always offers manual choices. Some browsers hide the processor architecture, particularly
on Macs; the page then asks you to choose Apple Silicon or Intel. Phones and tablets stay on the chooser.

Release links are generated from `packaging/desktop.json`, using the same asset names as the desktop packaging script.

## Build and host

Install the `wasm32-unknown-unknown` Rust target and [Trunk](https://github.com/trunk-rs/trunk). From the repository root:

```sh
trunk build --config crates/actinv-gui/Trunk.toml --release --locked --public-url ./
python3 scripts/prepare_web.py dist/web
python3 -m http.server 8080 --directory dist/web --bind 127.0.0.1
```

On the maintainer's Linux workstation, run builds and tests within the enforced resource limits in `AGENTS.md`.
Open `http://localhost:8080/`. Serve the built files over HTTP; opening `index.html` directly does not load WebAssembly.

With that server running, `npm ci --prefix web` installs the pinned browser-test dependencies.
Run `web/node_modules/.bin/playwright install chromium`, `npm test --prefix web`, and
`npm run smoke --prefix web` to check platform detection, browser startup, and installer selection.

The `web.yml` workflow checks and builds the bundle, then saves an `actinv-web` artifact.
Hosting uses Cloudflare Workers Static Assets. After building and checking the bundle, run
`npx wrangler@4 deploy --config wrangler.jsonc` from an authenticated maintainer checkout.
The config connects `actinv.avilalabs.org`; Cloudflare manages its DNS record and TLS certificate.
Deployment is currently a maintainer command, separate from CI. The site is static: no application server,
accounts, or uploaded data are required.
