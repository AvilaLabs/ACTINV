#!/usr/bin/env python3
"""Explicit, bounded tool installation for the manual verification workflow."""
import argparse
import os
import sys
import verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=(*verify.SUITES,"all"))
    args = parser.parse_args()
    verify.verify_limits()
    temporary = verify.WORK / "tmp"
    temporary.mkdir(parents=True,exist_ok=True)
    env = os.environ.copy()
    env.update(TMPDIR=str(temporary),CARGO_BUILD_JOBS="1",RUST_TEST_THREADS="1",RAYON_NUM_THREADS="2")
    versions = verify.TOOLS
    tools = verify.SUITES if args.suite == "all" else (args.suite,)
    commands = []
    for suite in tools:
        if suite in {"fuzz","miri"}:
            command = ["rustup","toolchain","install",versions["nightly"],"--profile","minimal"]
            if suite == "miri":
                command += ["--component","miri,rust-src"]
            commands.append(command)
        package = {"fuzz":"cargo-fuzz","mutants":"cargo-mutants","kani":"kani-verifier"}.get(suite)
        if package:
            version = versions["kani" if suite == "kani" else package]
            commands.append(["cargo",f"+{versions['rust']}","install","--locked","--version",version,package])
        if suite == "kani":
            commands.append(["cargo","kani","setup"])
    for command in commands:
        if not verify.run(command,1200,env)["passed"]:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
