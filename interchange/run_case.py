#!/usr/bin/env python3
"""Package-bound step runner for avila-labs.actinv/run-case@1.

Runs inside a Core-staged workspace. Verifies every staged data input against
the digest the emitted spec pins, resolves those staged paths into a working
spec, then execs `actinv run`. Exit codes: 0 success; 2 contract_gap-style
refusal (digest mismatch, missing input, spec invalid); actinv's own exit
code propagates otherwise.
"""
import hashlib
import json
import os
import subprocess
import sys


def sh(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def die(msg, code=2):
    print(f"run_case: {msg}", file=sys.stderr)
    sys.exit(code)


def main(argv):
    args = {}
    it = iter(argv)
    for a in it:
        if a.startswith("--"):
            args[a[2:]] = next(it, None)
    need = ["actinv", "spec", "library", "index",
            "decay-primary", "decay-fallback", "out", "summary"]
    for k in need:
        if not args.get(k):
            die(f"missing argument --{k}")
        if k not in ("out", "summary") and not os.path.isfile(args[k]):
            die(f"missing or unreadable staged input --{k} {args[k]}")

    spec = json.load(open(args["spec"]))
    if spec.get("spec") != "actinv-spec-1":
        die("spec is not actinv-spec-1")

    # the library is the only spec-pinned digest; the other staged inputs are
    # bound by the receipt's staged-input digests
    want_lib = (spec.get("library") or {}).get("sha256")
    if want_lib and sh(args["library"]) != want_lib.removeprefix("sha256:"):
        die("staged library digest != spec pin")

    # resolve staged paths into a working spec; absolute study paths stay out
    resolved = dict(spec)
    resolved["library"] = dict(spec.get("library") or {})
    resolved["library"]["path"] = os.path.abspath(args["library"])
    resolved["decay"] = dict(spec.get("decay") or {})
    for k, slot in (("primary", "decay-primary"),
                    ("fallback", "decay-fallback")):
        if k in resolved["decay"]:
            resolved["decay"][k] = os.path.abspath(args[slot])
    wspec = os.path.abspath("resolved-spec.json")
    with open(wspec, "w") as f:
        json.dump(resolved, f)

    env = dict(os.environ)
    env.setdefault("ACTINV_CACHE_DIR",
                   os.path.abspath(".cache/actinv"))
    os.makedirs(env["ACTINV_CACHE_DIR"], exist_ok=True)
    r = subprocess.run(
        [os.path.abspath(args["actinv"]), "run", wspec,
         os.path.abspath(args["out"])],
        capture_output=True, text=True, timeout=3300, env=env)
    if r.stderr:
        print(r.stderr[-4000:], file=sys.stderr)
    if r.returncode != 0:
        sys.exit(r.returncode if r.returncode > 0 else 2)
    if not os.path.isfile(args["out"]):
        die("actinv exited 0 but produced no output")

    # authoritative summary: strings/integers only — the raw result carries
    # binary floats, which Core cannot admit as claim-bearing JSON
    out = json.load(open(args["out"]))
    n_steps = len(out.get("steps", []))
    summary = {
        "case_id": os.path.basename(args["spec"]).removesuffix(".json"),
        "entry_point": out.get("entry_point", "cli"),
        "n_steps": n_steps,
    }
    with open(args.get("summary", "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main(sys.argv[1:])
