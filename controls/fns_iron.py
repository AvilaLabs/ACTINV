#!/usr/bin/env python3
"""Run the public FNS iron experiment through ACTINV's CLI and report measured C/E."""
import argparse
import csv
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "examples/fns_iron/case.json"
PROTOCOL = ROOT / "protocols/FNS-IRON-001.md"
CATALOG = ROOT / "crates/actinv-cli/data/actinv-data-catalog-v1.1.0.json"
EXAMPLE = ROOT / "examples/fns_fe_5min.json"
RECORDED = ROOT / "results/fns-iron-001/comparison.json"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pinned(path, expected):
    require(digest(path) == expected, f"SHA-256 mismatch: {path}")


def measurements(text):
    rows = []
    for line in text.splitlines():
        values = line.split()
        require(len(values) == 3, "measurement must have exactly three columns")
        time, heat, error = (Decimal(value) for value in values)
        require(all(v.is_finite() and v > 0 for v in (time, heat, error)), "invalid measurement")
        seconds = time * 60
        require(not rows or seconds > Decimal(str(rows[-1]["cooling_seconds"])), "unordered times")
        rows.append({"cooling_seconds": float(seconds), "measured_microW_per_g": float(heat),
                     "reported_error_microW_per_g": float(error)})
    require(len(rows) == 20, "expected all twenty measurements")
    return rows


def schedule(rows):
    result = [{"dt": "300 s", "flux": 1.0}]
    previous = Decimal(0)
    for row in rows:
        current = Decimal(str(row["cooling_seconds"]))
        require(current > previous, "cooling increments must be positive")
        result.append({"dt": f"{current - previous} s", "flux": 0.0})
        previous = current
    return result


def fetch_archive(path, case):
    if path.exists():
        pinned(path, case["archive_sha256"])
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Fixed public URL, bounded download, verified before publishing. No archive extraction.
    with tempfile.TemporaryDirectory(dir=path.parent, prefix="fns-download-") as directory:
        part = Path(directory) / "fns.zip"
        request = urllib.request.Request(case["archive_url"], headers={
            "User-Agent": "ACTINV-FNS-Iron/1 (+https://github.com/AvilaLabs/ACTINV)"})
        with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as output:
            size = 0
            for block in iter(lambda: response.read(1024 * 1024), b""):
                size += len(block)
                require(size <= 64 * 1024 * 1024, "archive exceeds download limit")
                output.write(block)
        pinned(part, case["archive_sha256"])
        part.replace(path)


def prepare(archive_path, data_root, output):
    pinned(CASE, "e04b539d4ab2176a3120dba67609b88dd09d5ff0ddadca790f7fc0388a1c6155")
    pinned(PROTOCOL, "4a9203cb5bc1be9d39cb7419cfacabbba552293510699d8b569183a9d56da01d")
    pinned(CATALOG, "25638bddfd6bb183c51720e53254a6529b15a63cadae2202174c662f0cea3314")
    pinned(EXAMPLE, "a9ce69083f0112b32c477ae1bcb45417f133dd7fcdd383777ae6892cf29d9207")
    case = json.loads(CASE.read_text())
    fetch_archive(archive_path, case)
    members = {}
    with zipfile.ZipFile(archive_path) as archive:
        for name, expected in case["members"].items():
            info = archive.getinfo(name)
            require(info.file_size < 1024 * 1024, "oversized source member")
            content = archive.read(name)
            require(hashlib.sha256(content).hexdigest() == expected, f"source member changed: {name}")
            members[Path(name).name] = content
            (output / Path(name).name).write_bytes(content)
    rows = measurements(members["1996exp_5min.exp"].decode())
    deck = members["TENDL-2017_1996exp_5min.i"].decode().splitlines()
    tokens = [line.split() for line in deck]
    for line in (["MASS", "1.0E-3", "1"], ["FE", "100.0"], ["FLUX", "1.116E+10"],
                 ["TIME", "5.0", "MINS"]):
        require(line in tokens, f"source deck assumption changed: {line}")
    spec = json.loads(EXAMPLE.read_text())
    flux = []
    for line in members["1996exp_5min_fluxes"].decode().splitlines():
        try:
            flux.extend(float(value) for value in line.split())
        except ValueError:
            break
    require(len(flux) == 710 and flux[-1] == 1.0, "unexpected spectrum/footer format")
    require(spec["spectrum"]["flux_per_group"] == flux[:709], "example spectrum differs from source")
    require(spec["spectrum"]["total"] == case["total_flux_n_cm2_s"], "wrong flux normalization")
    require(spec["material"] == {"mass_g": 1.0, "basis": "wt_percent", "composition": {"FE": 100.0}},
            "wrong material")
    spec["title"] = "FNS Iron 001: exact published measurement times"
    spec["schedule"] = schedule(rows)
    catalog = json.loads(CATALOG.read_text())
    artifacts = {a["id"]: a for a in catalog["artifacts"]}
    selected = ["tendl-2025-patched-neutron-709g", "tendl-2025-patched-neutron-709g-index",
                "endfb-viii-0-decay", "jeff-3-3-decay"]
    paths, hashes = {}, {}
    for name in selected:
        artifact = artifacts[name]
        path = data_root / "v1.1.0" / artifact["path"]
        pinned(path, artifact["sha256"])
        paths[name] = str(path.resolve())
        hashes[name] = artifact["sha256"]
    spec["library"] = {"path": paths[selected[0]], "sha256": hashes[selected[0]]}
    spec["decay"] = {"primary": paths[selected[2]], "fallback": paths[selected[3]]}
    return spec, rows, hashes


def execute(binary, spec, output):
    # A fresh leaf CLI process; subprocess.run kills/reaps on timeout. No stale output reuse.
    with tempfile.TemporaryDirectory(prefix="fns-cli-", dir=output) as directory:
        inp, out = Path(directory) / "spec.json", Path(directory) / "cli.json"
        inp.write_text(json.dumps(spec, indent=2, allow_nan=False) + "\n")
        completed = subprocess.run([str(binary), "run", str(inp), str(out)],
                                   capture_output=True, text=True, check=True, timeout=180)
        result = json.loads(out.read_text())
        shutil.copyfile(inp, output / "spec.json")
        shutil.copyfile(out, output / "cli.json")
        (output / "cli.log").write_text(completed.stdout + completed.stderr)
        return result


def score(result, rows):
    steps = result["steps"]
    require(len(steps) == 21 and len(rows) == 20, "incomplete output or measurements")
    require(abs(steps[0]["t_s"] - 300) <= 1e-6, "wrong end-of-irradiation time")
    comparison = []
    for row, step in zip(rows, steps[1:]):
        require(abs(step["t_s"] - 300 - row["cooling_seconds"]) <= 1e-6, "cooling time mismatch")
        require(step["flux"] == 0, "nonzero cooling flux")
        heat = step["heat_W_per_g"]
        require(all(math.isfinite(heat[k]) and heat[k] >= 0 for k in ("alpha", "beta", "gamma", "total")),
                "invalid heat")
        require(heat["total"] > 0, "nonpositive heat")
        require(math.isclose(heat["total"], heat["alpha"] + heat["beta"] + heat["gamma"],
                             rel_tol=1e-12, abs_tol=1e-20), "heat components do not close")
        calculated = heat["total"] * 1e6
        measured, error = row["measured_microW_per_g"], row["reported_error_microW_per_g"]
        ratio = calculated / measured
        comparison.append({**row, "calculated_microW_per_g": calculated, "calculated_over_measured": ratio,
                           "relative_residual": ratio - 1,
                           "residual_over_reported_error": (calculated - measured) / error})
    ratios = [r["calculated_over_measured"] for r in comparison]
    summary = {"points": len(comparison), "geometric_mean_CE": math.exp(math.fsum(map(math.log, ratios)) / len(ratios)),
               "min_CE": min(ratios), "max_CE": max(ratios),
               "max_absolute_relative_residual": max(abs(r - 1) for r in ratios),
               "points_inside_reported_error_bars": sum(abs(r["residual_over_reported_error"]) <= 1 for r in comparison)}
    return comparison, summary


def check_recorded(recorded, fresh):
    for key in ("case", "integrity", "physical_agreement", "input_hashes", "data_hashes", "uncertainty_note"):
        require(recorded[key] == fresh[key], f"recorded {key} changed")
    require(len(recorded["comparison"]) == len(fresh["comparison"]) == 20, "recorded coverage changed")
    for old, new in zip(recorded["comparison"], fresh["comparison"]):
        for key in ("cooling_seconds", "measured_microW_per_g", "reported_error_microW_per_g"):
            require(old[key] == new[key], f"recorded measurement changed: {key}")
        a, b = old["calculated_microW_per_g"], new["calculated_microW_per_g"]
        require(math.isfinite(a) and abs(a - b) <= 1e-9 + 1e-8 * abs(b), "CLI numerical regression")
        for key in ("calculated_over_measured", "relative_residual", "residual_over_reported_error"):
            require(math.isclose(old[key], new[key], rel_tol=1e-6, abs_tol=1e-7), f"recorded score changed: {key}")
    for key in fresh["summary"]:
        require(math.isclose(recorded["summary"][key], fresh["summary"][key], rel_tol=1e-6, abs_tol=1e-9),
                f"recorded summary changed: {key}")


def report(receipt):
    s = receipt["summary"]
    lines = ["# FNS iron: calculated versus measured decay heat", "",
             "Execution and input-integrity checks passed. Physical agreement is reported, not pass/fail.", "",
             f"All {s['points']} points; geometric mean C/E **{s['geometric_mean_CE']:.4f}**; "
             f"range **{s['min_CE']:.4f}–{s['max_CE']:.4f}**; maximum absolute difference "
             f"**{s['max_absolute_relative_residual']:.2%}**.", "",
             f"{s['points_inside_reported_error_bars']}/20 predictions fall inside the reported error bars. "
             "No confidence level or experimental-error covariance is assumed. Model uncertainty is not included.", "",
             "| Cooling (min) | Measured (µW/g) | Reported error (µW/g) | ACTINV (µW/g) | C/E | Difference |",
             "| ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in receipt["comparison"]:
        lines.append(f"| {row['cooling_seconds']/60:.2f} | {row['measured_microW_per_g']:.5g} | "
                     f"{row['reported_error_microW_per_g']:.5g} | {row['calculated_microW_per_g']:.6g} | "
                     f"{row['calculated_over_measured']:.4f} | {row['relative_residual']:+.2%} |")
    return "\n".join(lines) + "\n"


def plot(receipt, destination):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = receipt["comparison"]
    times = [r["cooling_seconds"] / 60 for r in rows]
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(7, 6), layout="constrained")
    axes[0].errorbar(times, [r["measured_microW_per_g"] for r in rows],
                     yerr=[r["reported_error_microW_per_g"] for r in rows], fmt="o", label="FNS measurement / reported error")
    axes[0].plot(times, [r["calculated_microW_per_g"] for r in rows], "-", label="ACTINV / patched TENDL-2025")
    axes[0].set(ylabel="Decay heat (µW/g)", title="Iron after 5 minutes of FNS irradiation")
    axes[0].legend(fontsize=8)
    axes[1].errorbar(times, [1]*len(rows), yerr=[r["reported_error_microW_per_g"]/r["measured_microW_per_g"] for r in rows],
                     fmt="none", color="gray", label="Reported relative error")
    axes[1].plot(times, [r["calculated_over_measured"] for r in rows], "o-")
    axes[1].axhline(1, color="gray", linewidth=0.8)
    axes[1].set(xlabel="Time after irradiation (minutes)", ylabel="Calculated / measured")
    for ax in axes:
        ax.grid(alpha=0.2)
    fig.savefig(destination, metadata={"Date": None})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actinv", type=Path, default=ROOT / "target/release/actinv")
    parser.add_argument("--data-root", type=Path, default=ROOT / "actinv-data")
    parser.add_argument("--archive", type=Path, default=ROOT / "target/fns-iron/fns.zip")
    parser.add_argument("--output", type=Path, default=ROOT / "target/fns-iron/run")
    parser.add_argument("--record", action="store_true", help="Create the first receipt, never overwrite")
    parser.add_argument("--plot", action="store_true", help="Generate SVG (requires matplotlib)")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    spec, rows, data_hashes = prepare(args.archive.resolve(), args.data_root.resolve(), output)
    result = execute(args.actinv.resolve(), spec, output)
    comparison, summary = score(result, rows)
    case = json.loads(CASE.read_text())
    receipt = {"case": case["id"], "integrity": "passed", "physical_agreement": "reported_without_acceptance_gate",
               "uncertainty_note": case["uncertainty_interpretation"],
               "input_hashes": {"case": digest(CASE), "protocol": digest(PROTOCOL), "catalog": digest(CATALOG),
                                "archive": case["archive_sha256"], "source_members": case["members"]},
               "data_hashes": data_hashes,
               "execution": {"binary_sha256": digest(args.actinv), "control_sha256": digest(__file__),
                             "spec_sha256": digest(output / "spec.json"), "raw_cli_sha256": digest(output / "cli.json"),
                             "github_sha": os.environ.get("GITHUB_SHA"), "github_run_id": os.environ.get("GITHUB_RUN_ID")},
               "mode": result["mode"], "certificate": result["certificate"], "ledger": result["ledger"],
               "comparison": comparison, "summary": summary}
    (output / "comparison.json").write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    (output / "comparison.md").write_text(report(receipt))
    with (output / "comparison.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(comparison[0]))
        writer.writeheader()
        writer.writerows(comparison)
    if args.plot:
        plot(receipt, output / "comparison.svg")
    if args.record:
        RECORDED.parent.mkdir(parents=True, exist_ok=True)
        with RECORDED.open("x") as stream:
            json.dump(receipt, stream, indent=2, allow_nan=False)
            stream.write("\n")
    check_recorded(json.loads(RECORDED.read_text()), receipt)
    print(report(receipt))


if __name__ == "__main__":
    main()
