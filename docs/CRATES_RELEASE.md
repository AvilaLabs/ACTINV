# crates.io release procedure

ACTINV is published as three Rust packages because the command depends on the solver and data-reader libraries:

1. `actinv-data`
2. `actinv-core`
3. `actinv-cli`

The public command installs with `cargo install --locked actinv-cli`. Version `1.0.0` was published manually in the
order above because crates.io requires the first release of a new package to use an API token. That temporary local
credential was removed after a clean registry-install smoke test.

## One-time trusted-publisher setup

The repository's `crates-io` GitHub environment requires maintainer approval. The same GitHub Actions trusted
publisher is configured for each package on crates.io using these values:

- GitHub owner: `AvilaLabs`
- Repository: `ACTINV`
- Workflow: `publish-crates.yml`
- Environment: `crates-io`

The configuration covers [`actinv-data`](https://crates.io/crates/actinv-data),
[`actinv-core`](https://crates.io/crates/actinv-core), and
[`actinv-cli`](https://crates.io/crates/actinv-cli). The official authentication action exchanges GitHub's OIDC
identity for a short-lived token and revokes that token when each job ends; no crates.io token belongs in GitHub
Secrets. See the official [trusted-publishing documentation](https://crates.io/docs/trusted-publishing).

## Publishing a later version

1. Update the shared workspace and Python versions, exact internal dependency requirements, changelog, and release
   notes.
2. Run the repository's Rust and release gates and obtain a green commit on the default branch.
3. Create and push a signed `vX.Y.Z` tag whose version matches `Cargo.toml`.
4. Approve the `crates-io` environment when the **publish Rust crates** workflow requests it.
5. Let the workflow publish the three packages in dependency order. If one job fails after an earlier package was
   published, use GitHub's **Re-run failed jobs** action; the completed package jobs remain complete.
6. Verify the public result from a clean install root:

   ```bash
   cargo install --locked actinv-cli --version X.Y.Z
   actinv --version
   ```

Never move or reuse a release tag, and never attempt to overwrite a published version. A broken crates.io version can
be yanked, but its uploaded source remains part of the permanent registry archive.
