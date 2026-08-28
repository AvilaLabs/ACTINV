#!/usr/bin/env python3
"""Fail closed if the crates.io release workflow loses its security contract."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-crates.yml"
AUTH_ACTION = (
    "rust-lang/crates-io-auth-action@"
    "c6f97d42243bad5fab37ca0427f495c86d5b1a18"
)
CRATES = ("actinv-data", "actinv-core", "actinv-cli")


def main() -> int:
    text = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.is_file() else ""
    checks = {
        "tag_or_explicit_recovery": 'tags: ["v*"]' in text
        and "workflow_dispatch" in text
        and "release_tag:" in text
        and "required: true" in text
        and "pull_request" not in text,
        "read_only_default": "permissions:\n  contents: read" in text,
        "version_bound_to_existing_tag": "ACTINV_RELEASE_TAG" in text
        and "['workspace']['package']['version']" in text
        and "refs/tags/{tag}" in text
        and "tagged == head" in text,
        "checkout_is_release_tag": text.count(
            "ref: ${{ env.ACTINV_RELEASE_TAG }}"
        )
        == len(CRATES) + 1,
        "strict_rust_gates": all(
            command in text
            for command in (
                "cargo fmt --all -- --check",
                "cargo check --workspace --all-targets --all-features",
                "cargo clippy --workspace --all-targets --all-features -- -D warnings",
                "cargo test --workspace --all-targets --all-features",
            )
        ),
        "separate_resumable_jobs": all(
            marker in text
            for marker in (
                "  publish-data:",
                "    needs: verify",
                "  publish-core:",
                "    needs: publish-data",
                "  publish-cli:",
                "    needs: publish-core",
            )
        ),
        "protected_environment": text.count("name: crates.io") == len(CRATES),
        "oidc_is_job_scoped": text.count("id-token: write") == len(CRATES),
        "official_action_is_immutable": text.count(AUTH_ACTION) == len(CRATES),
        "temporary_token_is_environment_only": text.count(
            "CARGO_REGISTRY_TOKEN: ${{ steps.auth.outputs.token }}"
        )
        == len(CRATES)
        and "secrets." not in text
        and "--token" not in text,
        "locked_dependency_order": all(
            f"cargo publish --locked --package {crate}" in text for crate in CRATES
        ),
    }
    publish_positions = [
        text.find(f"cargo publish --locked --package {crate}") for crate in CRATES
    ]
    checks["dependency_order"] = all(position >= 0 for position in publish_positions) and (
        publish_positions == sorted(publish_positions)
    )
    output = {
        "workflow": str(WORKFLOW.relative_to(ROOT)),
        "checks": checks,
        "pass": all(checks.values()),
    }
    print(json.dumps(output, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
