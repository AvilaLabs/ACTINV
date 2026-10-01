# Maintain the ACTINV Handbook

The public handbook source is `docs/guide/`; `book.toml` builds that directory with **mdBook 0.5.4** and the Rust theme. `SUMMARY.md` is curated around user tasks. Only handbook chapters belong in that source directory: mdBook also copies unlisted static files into its output.

## Build and preview

Install the pinned tool with `cargo install --locked mdbook --version 0.5.4`, or use its release binary. Run:

```sh
mdbook build
python3 scripts/check_docs.py dist/docs
mdbook serve --hostname 127.0.0.1 --port 4173
```

On the maintainer's Linux workstation, run builds and executable checks in the resource-limited scope required by root `AGENTS.md`. Put temporary build files in `target/preflight-tmp` on disk and set `TMPDIR` accordingly.

The cheap **Build handbook** workflow builds the book, checks every local HTML/file/anchor target and the workspace version, and exercises navigation, search, themes, and the mobile sidebar in Chromium. It publishes a build artifact and does not deploy.

## Include in the official web bundle

```sh
mdbook build
python3 scripts/check_docs.py dist/docs
python3 scripts/prepare_web.py dist/web --docs dist/docs
```

The browser-workbench workflow runs these steps after its normal Trunk build. The resulting `actinv-web` artifact contains the app, downloads, and handbook at `/docs/`. `prepare_web.py` replaces the generated handbook directory so retired chapters and old search indexes do not linger.

The existing authenticated web deployment publishes that complete bundle to `actinv.avilalabs.org`. Adding this source or running CI does not publish the site. Keep deployment separate from documentation review.

For local browser checks, serve `dist/` on port 8080 and run `node web/docs-smoke.mjs` after installing the pinned dependencies from `web/package-lock.json`. Alternatively serve the combined `dist/web` bundle with the same `/docs/` path.

## Publish the first handbook

The public URL is `https://actinv.avilalabs.org/docs/`. The existing `wrangler.jsonc` selects the `actinv-workbench` Worker and its custom domain. Its static asset root serves the workbench at `/`, downloads at `/download/`, and the handbook at `/docs/`.

Use the CI-built **complete web bundle** for the committed docs revision. This avoids deploying local in-progress solver edits or mixing the new handbook with an older local app build. Prerequisites are GitHub CLI access to `AvilaLabs/ACTINV`, Node.js, and Cloudflare account access to the existing Worker.

1. Review and commit only the staged handbook changes, using the repository sign-off and manifest rules. Push the commit to `master` and confirm controls, handbook, and browser-workbench CI are green on that exact SHA. The workflows build artifacts; deployment is a separate command.
2. From the ActInv repository root, list the completed web runs for the revision:

   ```sh
   docs_sha=$(git rev-parse HEAD)
   gh run list --workflow web.yml --commit "$docs_sha"
   ```

3. Replace `WEB_RUN_ID` below with the successful **Build browser workbench** run ID for that SHA. Use an empty destination directory; if this path exists from an earlier download, choose a new directory name and use it consistently:

   ```sh
   gh run download WEB_RUN_ID --name actinv-web --dir dist/handbook-release
   test -f dist/handbook-release/index.html
   test -f dist/handbook-release/docs/index.html
   python3 scripts/check_docs.py dist/handbook-release/docs
   ```

   Download `actinv-web`, which contains the app, download page, and book. The separate `actinv-handbook` artifact contains only the book. [GitHub CLI download reference](https://cli.github.com/manual/gh_run_download).

4. Authenticate to Cloudflare if needed, confirm the account, and validate the deployment without publishing:

   ```sh
   npx wrangler@4 login
   npx wrangler@4 whoami
   npx wrangler@4 deploy --config wrangler.jsonc --assets dist/handbook-release --dry-run
   ```

5. Publish that verified bundle:

   ```sh
   npx wrangler@4 deploy --config wrangler.jsonc --assets dist/handbook-release
   ```

   `--assets` selects the downloaded complete bundle while retaining the existing Worker name/domain. No new DNS entry is needed for `/docs/`. See the [Wrangler deployment reference](https://developers.cloudflare.com/workers/wrangler/commands/workers/#deploy).

6. Open the workbench, download page, and handbook on the live domain. Check a chapter, search, mobile navigation, and the competitive-benchmark page. The README links to the handbook near the download/browser links and in **Documentation**; those hosted links become usable after this first deployment.

For future updates, repeat the same commit → green CI → complete artifact → deploy process. Automatic publishing is not configured by these documentation changes.

## Edit current content and preserve evidence

The handbook is the current user-facing source. `docs/SPEC.md` and `docs/QUALIFICATION.md` point to their current handbook pages. The old data and validation records retain provider details and historical evidence, with links to the current user guides.

Keep dated investigations, protocols, release procedures, drafts, competitive research, and session logs outside `docs/guide/`. Link a relevant evidence record from a current chapter instead of turning the record into a public navigation item. Do not rewrite a historical failed or conditional verdict as current qualification.

When behavior changes, update the relevant handbook task/reference page alongside the implementation. Check commands against `crates/actinv-cli/src/command.rs`, JSON fields and defaults against `crates/actinv-core/src/spec.rs`, result units against `run.rs`, and Python helpers against `python/src/objects.py`. Check the data catalog for bundle IDs and library/covariance pairing.

When versions change, update `index.md`, `install.md`, `cli.md`, and `releases.md`; distinguish solver, data catalog, and desktop package versions. Keep the complete generated example runnable and label JSON fragments as fragments. Do not publish measurements without their data identities, comparison metric, and evidence limits.

Run `git diff --check`, build the handbook, run its link checker and browser smoke, and stage changes before refreshing `MANIFEST.sha256` according to `AGENTS.md`. Rust gates are needed when Rust source changes; handbook prose and static packaging do not require a solver rerun.
