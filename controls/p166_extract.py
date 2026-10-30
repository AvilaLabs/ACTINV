#!/usr/bin/env python3
"""P166 extraction (protocols/ACTINV-P166_PROTOCOL.md): measured fission decay heat from Gauld's 2019 CoNDERC report.

Reads Tables 1-11 of the pinned report through `pdftotext -layout` and writes one row per measured point and channel
to results/p166/conderc_fission_tables.csv. Every row's printed ORIGEN C/M is recomputed from the printed calculated
and measured values to the precision of the printed digits; a row that does not reproduce stops the extraction.
Table 12 (Johnson 1965) and the Gunst experiments are outside the registered scope.

Usage: p166_extract.py [--pdf PATH] [--out CSV] [--report JSON]
"""
import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = Path.home() / "nuclear-data" / "conderc-fission" / "references" / "Gauld_SummaryReport_2019.pdf"
PDF_SHA256 = "71f22abd8993f72656b00ae80bff02099bbb1bea8f8db4781e33b58f9d273f74"
OUT = ROOT / "results" / "p166" / "conderc_fission_tables.csv"
REPORT = ROOT / "results" / "p166" / "extraction.json"

HOUR, DAY = 3600.0, 86400.0
# table: (dataset, laboratory, first author, year, parent, irradiation_s, normalization, layout)
# normalization: "rate" = power / constant fission rate (MeV per fission);
#                "integrated" = power / fissions in the irradiation (MeV s^-1 per fission).
TABLES = {
    1: ("FR76-U235-24h", "IRT", "Friesenhahn", 1976, "U235", 24 * HOUR, "rate", "three_channel_three_cm"),
    2: ("FR79-Pu239-1000s", "IRT", "Friesenhahn", 1979, "Pu239", 1000.0, "rate", "three_channel_total_cm"),
    3: ("FR79-Pu239-24h", "IRT", "Friesenhahn", 1979, "Pu239", 24 * HOUR, "rate", "three_channel_total_cm"),
    4: ("FR79-U235-1000s", "IRT", "Friesenhahn", 1979, "U235", 1000.0, "rate", "three_channel_total_cm"),
    5: ("FR79-U235-20000s", "IRT", "Friesenhahn", 1979, "U235", 20000.0, "rate", "three_channel_total_cm"),
    6: ("FR79-U235-24h", "IRT", "Friesenhahn", 1979, "U235", 24 * HOUR, "rate", "three_channel_total_cm"),
    7: ("FR79-U235-35d", "IRT", "Friesenhahn", 1979, "U235", 35 * DAY, "rate", "three_channel_total_cm"),
    8: ("SC78-U235-1h", "UC Berkeley", "Schrock", 1978, "U235", 1 * HOUR, "rate", "runs:1,12"),
    9: ("SC78-U235-4h", "UC Berkeley", "Schrock", 1978, "U235", 4 * HOUR, "rate", "runs:3,4,9"),
    10: ("SC78-U235-22.35h", "UC Berkeley", "Schrock", 1978, "U235", 22.35 * HOUR, "rate", "runs:6,8"),
    11: ("FI76", "CEA Cadarache", "Fiche", 1976, None, 1.0, "integrated", "fiche"),
}
# Table 11 rows with an empty measured column, pinned verbatim (whitespace-normalized) so the column assignment is
# not inferred from token counts.
FICHE_PARTIAL = {
    "50 1.94E-02 14 2.18E-02 1.122 2.08E-02": {
        "Pu239": (1.94e-2, 14.0, 2.18e-2, 1.122), "U233": None, "U233_calc": 2.08e-2},
    "60 1.52E-02 25 1.82E-02 1.74E-02 1.146": {
        "Pu239": None, "U233": (1.52e-2, 25.0, 1.74e-2, 1.146), "Pu239_calc": 1.82e-2},
}
NUMBER = re.compile(r"^[0-9]+(\.[0-9]+)?([Ee][+-]?[0-9]+)?$")
FIELDS = ["dataset", "table", "laboratory", "author", "year", "parent", "irradiation_s", "normalization",
          "cooling_time_s", "channel", "run", "measured", "sigma_percent", "origen_calculated", "origen_c_over_m"]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def half_ulp(token):
    """Half a unit in the last printed digit of a numeric token."""
    mantissa, _, exponent = token.upper().partition("E")
    decimals = len(mantissa.split(".")[1]) if "." in mantissa else 0
    return 0.5 * 10.0 ** (-decimals) * 10.0 ** (int(exponent) if exponent else 0)


def ratio_reproduces(calc_token, meas_token, cm_token):
    """True if calc/meas equals the printed C/M within the rounding of all three printed values."""
    calc, meas, cm = float(calc_token), float(meas_token), float(cm_token)
    ratio = calc / meas
    slack = half_ulp(cm_token) + ratio * (half_ulp(calc_token) / calc + half_ulp(meas_token) / meas) + 1e-12
    return abs(ratio - cm) <= slack


def table_blocks(text):
    """Map table number -> list of numeric-row token lists, in order, across page breaks."""
    blocks, current = {}, None
    for line in text.splitlines():
        heading = re.match(r"^\s*Table (\d+)\. ", line)
        if heading:
            number = int(heading.group(1))
            current = number if number in TABLES else None
            if current is not None:
                if current in blocks:
                    raise RuntimeError(f"Table {current} heading appears twice")
                blocks[current] = []
            continue
        if line.strip().startswith(("Appendix", "References", "Table A")):
            current = None
            continue
        tokens = line.split()
        if current is None or not tokens or "Page" in line or "P a g e" in line:
            continue
        if all(NUMBER.match(token) for token in tokens) and len(tokens) >= 5:
            blocks[current].append(tokens)
    return blocks


def extract(text):
    rows, checks, anomalies = [], [], []

    def emit(table, cooling, channel, run, measured, sigma, calc=None, cm=None, parent=None):
        dataset, lab, author, year, table_parent, irradiation, normalization, _ = TABLES[table]
        parent = parent or table_parent
        rows.append({
            "dataset": dataset if table != 11 else f"FI76-{parent}-pulse",
            "table": table, "laboratory": lab, "author": author, "year": year, "parent": parent,
            "irradiation_s": repr(irradiation), "normalization": normalization, "cooling_time_s": cooling,
            "channel": channel, "run": run, "measured": measured, "sigma_percent": sigma,
            "origen_calculated": calc or "", "origen_c_over_m": cm or "",
        })

    def check(table, cooling, label, calc, meas, cm):
        ok = ratio_reproduces(calc, meas, cm)
        checks.append({"table": table, "cooling_time_s": cooling, "column": label, "ok": ok})
        if not ok:
            raise RuntimeError(f"Table {table} t={cooling} {label}: {calc}/{meas} does not reproduce C/M {cm}")

    blocks = table_blocks(text)
    missing = sorted(set(TABLES) - set(blocks))
    if missing:
        raise RuntimeError(f"tables not found: {missing}")
    for table, block in sorted(blocks.items()):
        layout = TABLES[table][7]
        previous = -1.0
        for tokens in block:
            if layout == "fiche":
                key = " ".join(tokens)
                if key in FICHE_PARTIAL:
                    pinned = FICHE_PARTIAL.pop(key)
                    cooling = tokens[0]
                    for parent in ("Pu239", "U233"):
                        values = pinned[parent]
                        if values is None:
                            continue
                        # The one measured column present is always tokens 1-2; locate the printed
                        # calculation and C/M tokens by their pinned values.
                        meas, sigma = tokens[1], tokens[2]
                        calc = next(token for token in tokens if float(token) == values[2])
                        cm = next(token for token in tokens if float(token) == values[3])
                        if (float(meas), float(sigma)) != values[:2]:
                            raise RuntimeError(f"Table 11 pinned row changed: {key}")
                        check(table, cooling, f"{parent} total", calc, meas, cm)
                        emit(table, cooling, "total", "", meas, sigma, calc, cm, parent)
                elif len(tokens) == 9:
                    cooling, pu, pu_sigma, u, u_sigma, pu_calc, pu_cm, u_calc, u_cm = tokens
                    check(table, cooling, "Pu239 total", pu_calc, pu, pu_cm)
                    check(table, cooling, "U233 total", u_calc, u, u_cm)
                    emit(table, cooling, "total", "", pu, pu_sigma, pu_calc, pu_cm, "Pu239")
                    emit(table, cooling, "total", "", u, u_sigma, u_calc, u_cm, "U233")
                else:
                    raise RuntimeError(f"Table 11 row not understood: {tokens}")
            elif layout == "three_channel_three_cm":
                if len(tokens) != 13:
                    raise RuntimeError(f"Table {table} row has {len(tokens)} fields: {tokens}")
                (cooling, total, total_s, gamma, gamma_s, beta, beta_s,
                 c_total, c_gamma, c_beta, cm_total, cm_gamma, cm_beta) = tokens
                for label, calc, meas, cm in (("total", c_total, total, cm_total),
                                              ("gamma", c_gamma, gamma, cm_gamma),
                                              ("beta", c_beta, beta, cm_beta)):
                    check(table, cooling, label, calc, meas, cm)
                emit(table, cooling, "total", "", total, total_s, c_total, cm_total)
                emit(table, cooling, "gamma", "", gamma, gamma_s, c_gamma, cm_gamma)
                emit(table, cooling, "beta", "", beta, beta_s, c_beta, cm_beta)
            elif layout == "three_channel_total_cm":
                if len(tokens) != 9:
                    raise RuntimeError(f"Table {table} row has {len(tokens)} fields: {tokens}")
                cooling, total, total_s, gamma, gamma_s, beta, beta_s, c_total, cm_total = tokens
                check(table, cooling, "total", c_total, total, cm_total)
                emit(table, cooling, "total", "", total, total_s, c_total, cm_total)
                # gamma and beta carry no printed check; they are scored only where they sum to the total.
                if abs(float(gamma) + float(beta) - float(total)) <= (
                        half_ulp(gamma) + half_ulp(beta) + half_ulp(total) + 1e-12):
                    emit(table, cooling, "gamma", "", gamma, gamma_s)
                    emit(table, cooling, "beta", "", beta, beta_s)
                else:
                    anomalies.append({"table": table, "cooling_time_s": cooling,
                                      "total": total, "gamma": gamma, "beta": beta,
                                      "action": "gamma and beta not scored at this time; total kept"})
            elif layout.startswith("runs:"):
                runs = layout.split(":")[1].split(",")
                if not 5 <= len(tokens) <= len(runs) + 4:
                    raise RuntimeError(f"Table {table} row has {len(tokens)} fields: {tokens}")
                cooling, *measured, sigma, calc, cm = tokens
                # At long cooling times the leading runs stop; the blank cells are always on the left.
                check(table, cooling, f"run {runs[-1]} total", calc, measured[-1], cm)
                for run, value in zip(runs[len(runs) - len(measured):], measured):
                    emit(table, cooling, "total", run, value, sigma, calc,
                         cm if run == runs[-1] else "")
            if float(tokens[0]) <= previous and layout != "fiche":
                raise RuntimeError(f"Table {table} cooling times not increasing at {tokens[0]}")
            previous = float(tokens[0])
    if FICHE_PARTIAL:
        raise RuntimeError(f"pinned Table 11 rows not found: {sorted(FICHE_PARTIAL)}")
    return rows, checks, anomalies


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, default=PDF)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()
    digest = sha256(args.pdf)
    if digest != PDF_SHA256:
        sys.exit(f"report SHA-256 {digest} is not the pinned {PDF_SHA256}")
    version = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True, timeout=60)
    text = subprocess.run(["pdftotext", "-layout", str(args.pdf), "-"], capture_output=True, text=True,
                          check=True, timeout=120).stdout
    rows, checks, anomalies = extract(text)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    counts = {}
    for row in rows:
        key = f"{row['dataset']}/{row['channel']}"
        counts[key] = counts.get(key, 0) + 1
    report = {
        "schema": "actinv-p166-extraction-1",
        "pdf": {"path": str(args.pdf), "sha256": digest},
        "pdftotext": (version.stderr or version.stdout).splitlines()[0],
        "csv_sha256": sha256(args.out),
        "rows": len(rows),
        "printed_ratio_checks": len(checks),
        "printed_ratio_failures": sum(not check["ok"] for check in checks),
        "anomalies": anomalies,
        "points_by_dataset_channel": dict(sorted(counts.items())),
    }
    args.report.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({key: report[key] for key in ("csv_sha256", "rows", "printed_ratio_checks",
                                                    "printed_ratio_failures")}, indent=1))
    print(f"anomalies: {len(anomalies)}")


if __name__ == "__main__":
    main()
