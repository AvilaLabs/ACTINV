#!/usr/bin/env python3
"""P167 (protocols/ACTINV-P167_PROTOCOL.md): reaction rates in the NPI Rez 7Li(p,n) quasi-monoenergetic fields.

Source: Z. Matej, M. Kostal, M. Majerle et al., "The methodology for validation of cross sections in quasi
monoenergetic neutron field", arXiv:2306.09353v1 (2023), the pinned PDF in ~/nuclear-data/npi-rez-pli/. Tables 2 and 3
are the measured reaction rates (s^-1 per target atom) for the 12.4 and 14.4 MeV proton beams; Tables 4 and 5 the
evaluated neutron spectra at the foils (flux per 0.1 MeV bin, cm^-2 s^-1, bins (Eup - 0.1, Eup] MeV); Tables 6 and 7
the authors' C/E - 1 with IRDFF-II, JEFF-3.3, JENDL-4 and ENDF/B-VIII.0 and the comparison uncertainty.

`extract` reads those tables through `pdftotext -layout`. `fold` folds a histogram cross section (group edges in eV,
barns) with a tabulated spectrum, assuming flux flat in energy inside each 0.1 MeV bin. `actinv_rate` folds the
production of the measured product nuclide (every state, every reaction of that target) from ACTINV's 709-group
library; `irdff_rate` folds the IRDFF-II 725-group evaluation the authors used, which reproduces their published
IRDFF-II column and so checks the reading of the spectrum (development gate D in the protocol).

Usage: p167_rez.py [--record FILE]           write results/p167_rez.json
       p167_rez.py --reproduce-irdff          print the IRDFF-II reproduction only
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
from endf_common import endf_float, fields, read_tab1, sections  # noqa: E402

DATA = Path.home() / "nuclear-data"
PDF = DATA / "npi-rez-pli" / "2306.09353v1.pdf"
LIBRARY = ROOT.parent.parent / "actinv-data" / "v1.2.0" / "activation" / "tendl-2025-neutron-709g.npz"
IRDFF_GROUPS = DATA / "p17-irdff" / "IRDFF-II_g725.zip"
RECORD = ROOT / "results" / "p167_rez.json"
TABLES = ROOT / "results" / "p167" / "rez_tables.json"
BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
SYMBOLS = {9: "F", 12: "Mg", 13: "Al", 22: "Ti", 23: "V", 25: "Mn", 26: "Fe", 27: "Co", 28: "Ni", 79: "Au"}
SCHEMA = "actinv-p167-rez-1"
PINNED = {
    "pdf": "7ef44f1d94aa629639dec2e08555717d8582c85a3b8cbcc382834c847b6064c5",
    "library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "library_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
    "irdff_groups": "6ec2b33c0f67bed46d46be062a24ccedaa5ffea9bbba919958da4b1349f48c85",
}
FIELDS = {"12.4": {"rates": 2, "spectrum": 4, "ce": 6}, "14.4": {"rates": 3, "spectrum": 5, "ce": 7}}
# The spectra were normalized to these two reaction rates (paper section 4.3); they are not independent checks.
NORMALIZATION = {"58Ni(n,p)", "27Al(n,a)"}
# label -> (target ZA, IRDFF-II MT, measured product ZA)
REACTIONS = {
    "54Fe(n,p)": (26054, 103, 25054), "47Ti(n,p)": (22047, 103, 21047), "46Ti(n,p)": (22046, 103, 21046),
    "59Co(n,p)": (27059, 103, 26059), "60Ni(n,p)": (28060, 103, 27060), "56Fe(n,p)": (26056, 103, 25056),
    "24Mg(n,p)": (12024, 103, 11024), "59Co(n,a)": (27059, 107, 25056), "48Ti(n,p)": (22048, 103, 21048),
    "51V(n,a)": (23051, 107, 21048), "197Au(n,2n)": (79197, 16, 79196), "58Ni(n,x)57Co": (28058, None, 27057),
    "59Co(n,2n)": (27059, 16, 27058), "58Ni(n,p)": (28058, 103, 27058), "27Al(n,a)": (13027, 107, 11024),
    "54Fe(n,a)": (26054, 107, 24051), "19F(n,2n)": (9019, 16, 9018), "55Mn(n,2n)": (25055, 16, 25054),
    "58Ni(n,2n)": (28058, 16, 28057),
}
NUMBER = r"[0-9]+(?:\.[0-9]+)?(?:E[+-]?[0-9]+)?"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def label_of(text):
    """'54 Fe(n,p)' / 'Fe(n.p)' after a mass line -> canonical label."""
    text = text.replace("n.", "n,").replace("α", "a").replace(" ", "")
    return text


def table_lines(text, number):
    start = text.index(f"Table {number}:")
    following = [text.find(f"Table {n}:", start + 1) for n in range(number + 1, 10)]
    following = [position for position in following if position > 0] + [text.find("4.5 7Be", start)]
    return text[start:min(position for position in following if position > 0)].splitlines()


def rate_table(text, number):
    """Tables 2-3: mass number on its own line, then 'Fe(n,p) E50 rate unc %'."""
    rows, mass = [], None
    for line in table_lines(text, number)[1:]:
        stripped = line.strip()
        if re.fullmatch(r"\d{1,3}", stripped):
            mass = stripped
            continue
        match = re.match(rf"^([A-Z][a-z]?\([^)]*\)(?:\d+[A-Z][a-z]?)?)\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s*%$",
                         stripped)
        if match and mass:
            rows.append({"reaction": label_of(mass + match.group(1)), "E50_MeV": float(match.group(2)),
                         "rate_per_s": float(match.group(3)), "uncertainty_percent": float(match.group(4))})
            mass = None
    return rows


def spectrum_table(text, number):
    """Tables 4-5: three (Eup, flux, unc %) columns per line."""
    bins = []
    for line in table_lines(text, number):
        for eup, flux, unc in re.findall(rf"({NUMBER})\s+({NUMBER})\s+({NUMBER})\s*%", line):
            bins.append((float(eup), float(flux), float(unc)))
    bins.sort()
    edges = [round(eup, 1) for eup, _, _ in bins]
    if any(abs(b - a - 0.1) > 1e-9 for a, b in zip(edges, edges[1:])):
        raise RuntimeError(f"Table {number}: bins are not contiguous 0.1 MeV steps")
    return [{"E_low_MeV": round(eup - 0.1, 1), "E_up_MeV": eup, "flux": flux, "uncertainty_percent": unc}
            for eup, flux, unc in bins]


def ce_table(text, number):
    """Tables 6-7: published C/E - 1 (%) per library and the comparison uncertainty.

    Table 6 prints each row's numbers on the line before its reaction name, Table 7 on the same line; a row is
    complete once both a name and a set of numbers have been seen.
    """
    values_re = re.compile(rf"({NUMBER})\s+(-|-?{NUMBER}\s*%)\s+(-?{NUMBER})\s*%\s+(-?{NUMBER})\s*%\s+"
                           rf"(-?{NUMBER})\s*%\s+({NUMBER})\s*%\s*$")
    name_re = re.compile(r"([A-Z][a-z]?)\(([^)]*)\)")
    rows, values, name = [], None, None
    for line in table_lines(text, number)[3:]:
        found_values, found_name = values_re.search(line), name_re.search(line)
        if found_values:
            values = found_values.groups()
        if found_name:
            name = found_name.group(1) + "(" + found_name.group(2).replace(".", ",").replace("α", "a") + ")"
        if values and name:
            rows.append({"element_reaction": name, "E50_MeV": float(values[0]),
                         "IRDFF-II": None if values[1] == "-" else float(values[1].rstrip("% ")),
                         "JEFF-3.3": float(values[2]), "JENDL-4": float(values[3]),
                         "ENDF/B-VIII": float(values[4]), "uncertainty_percent": float(values[5])})
            values, name = None, None
    return rows


def extract(text):
    fields = {}
    for field, tables in FIELDS.items():
        rates = rate_table(text, tables["rates"])
        spectrum = spectrum_table(text, tables["spectrum"])
        published = ce_table(text, tables["ce"])
        by_e50 = {}
        for row in published:
            by_e50.setdefault(row["E50_MeV"], []).append(row)
        for row in rates:
            if row["reaction"] not in REACTIONS:
                raise RuntimeError(f"unmapped reaction {row['reaction']}")
            element_reaction = re.sub(r"^\d+", "", row["reaction"]).split(")")[0] + ")"
            matches = [entry for entry in by_e50.get(row["E50_MeV"], [])
                       if entry["element_reaction"] == element_reaction]
            row["published"] = matches[0] if len(matches) == 1 else None
        fields[field] = {"rates": rates, "spectrum": spectrum, "published_rows": len(published)}
    return fields


def fold(group_edges_eV, sigma_b, spectrum):
    """Reaction rate per atom (s^-1): sum over 0.1 MeV bins of flux * mean group cross section over the bin."""
    edges = np.asarray(group_edges_eV, dtype=float)
    sigma = np.asarray(sigma_b, dtype=float)
    rate = 0.0
    for entry in spectrum:
        low, high = entry["E_low_MeV"] * 1e6, entry["E_up_MeV"] * 1e6
        lo = np.clip(edges[:-1], low, high)
        hi = np.clip(edges[1:], low, high)
        mean_sigma = float(np.dot(sigma, np.maximum(hi - lo, 0.0)) / (high - low))
        rate += entry["flux"] * mean_sigma * 1e-24
    return rate


class ActinvLibrary:
    def __init__(self, path):
        archive = np.load(path, allow_pickle=False)
        self.rows, self.sigma, self.bounds = archive["rows"], archive["sig"], archive["bounds"]
        index = json.loads(path.with_name(path.stem + "_index.json").read_text())
        self.target = {int(t["za"]): i for i, t in enumerate(index["targets"]) if int(t.get("liso", 0)) == 0}

    def production(self, target_za, product_za):
        """Group cross section (b) for producing product_za from target_za, summed over reactions and states."""
        rows = self.rows
        mask = (rows[:, 0] == self.target[target_za]) & (rows[:, 2] == product_za)
        if not mask.any():
            raise RuntimeError(f"no ACTINV production rows {target_za} -> {product_za}")
        return self.sigma[mask].sum(axis=0), rows[mask].tolist()


def irdff_catalog(work):
    with zipfile.ZipFile(IRDFF_GROUPS) as archive:
        archive.extract("IRDFF-II.g725", work)
    catalog = {}
    for (_, mf, mt), lines in sections(Path(work) / "IRDFF-II.g725"):
        if mf != 3 or mt == 261:
            continue
        za = int(endf_float(fields(lines[0])[0]))
        record, _ = read_tab1(lines, 1)
        if (za, mt) in catalog:
            raise RuntimeError(f"duplicate IRDFF-II MF3 entry {(za, mt)}")
        catalog[(za, mt)] = record
    return catalog


def irdff_rate(catalog, target_za, mt, spectrum):
    record = catalog.get((target_za, mt))
    if record is None:
        return None
    _, _, _, _, _, _, nbt, x, y = record
    if any(law != 1 for _, law in nbt):
        raise RuntimeError("IRDFF-II g725 is expected to be histogram (law 1)")
    return fold(np.asarray(x, dtype=float), np.asarray(y[:-1], dtype=float), spectrum)


def text_of(pdf):
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True,
                          timeout=120).stdout


def status_of(field, reaction):
    if reaction in NORMALIZATION:
        return "normalization"
    if field == "14.4" and reaction == "58Ni(n,2n)":
        return "secondary"
    return "scored"


def tables_document():
    tables = extract(text_of(PDF))
    for field, data in tables.items():
        for row in data["rates"]:
            row["status"] = status_of(field, row["reaction"])
            if row["status"] == "scored" and row["published"] is None:
                raise RuntimeError(f"{field} {row['reaction']}: scored reaction without a published C/E row")
    return {"schema": "actinv-p167-tables-1", "pdf_sha256": PINNED["pdf"], "fields": tables}


def spectrum_file(path, spectrum, convention):
    edges = [entry["E_low_MeV"] * 1e6 for entry in spectrum] + [spectrum[-1]["E_up_MeV"] * 1e6]
    path.write_text(json.dumps({"spectrum": {
        "structure": "fispact-709", "boundaries_eV": edges, "flux_per_group": [entry["flux"] for entry in spectrum],
        "descending": False, "rebin": {"convention": convention}}}, indent=1) + "\n")


def actinv_production(work, field, spectrum, convention, cache):
    """target ZA -> inspect-xs report for this field's spectrum, run once per target."""
    spec = work / f"spectrum-{field}-{convention}.json"
    spectrum_file(spec, spectrum, convention)

    def rate(target_za, product_za):
        key = (field, convention, target_za)
        if key not in cache:
            out = work / f"xs-{field}-{convention}-{target_za}.json"
            symbol = SYMBOLS[target_za // 1000]
            subprocess.run([str(BIN), "inspect", "xs", str(LIBRARY), "--target", f"{symbol}{target_za % 1000}",
                            "--spectrum", str(spec), "--out", str(out)], check=True, timeout=600,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            cache[key] = json.loads(out.read_text())["reactions"]
        rows = [r for r in cache[key] if r["product_za"] == product_za]
        if not rows:
            raise RuntimeError(f"ACTINV has no production rows {target_za} -> {product_za}")
        return math.fsum(r["reaction_rate_per_target_atom_per_s"] for r in rows), [
            {k: r[k] for k in ("mt", "product", "product_lfs", "reaction_rate_per_target_atom_per_s")} for r in rows]
    return rate


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()


def cgroup_record():
    try:
        relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    except (OSError, IndexError):
        return {"path": None}
    base = Path("/sys/fs/cgroup") / relative.lstrip("/")
    record = {"path": relative}
    for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max"):
        try:
            record[name] = (base / name).read_text().strip()
        except OSError:
            record[name] = None
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--extract", action="store_true", help="write results/p167/rez_tables.json and stop")
    parser.add_argument("--reproduce-irdff", action="store_true")
    args = parser.parse_args()
    paths = {"pdf": PDF, "library": LIBRARY, "library_index": LIBRARY.with_name(LIBRARY.stem + "_index.json"),
             "irdff_groups": IRDFF_GROUPS}
    hashes = {name: sha256(path) for name, path in paths.items()}
    if hashes != PINNED:
        sys.exit(f"pinned input mismatch: {[k for k in PINNED if PINNED[k] != hashes[k]]}")
    if args.extract:
        TABLES.parent.mkdir(parents=True, exist_ok=True)
        TABLES.write_text(json.dumps(tables_document(), indent=1, sort_keys=True) + "\n")
        print(f"wrote {TABLES} {sha256(TABLES)}")
        return
    document = json.loads(TABLES.read_text())
    if document != json.loads(json.dumps(tables_document())):
        sys.exit("results/p167/rez_tables.json does not match a fresh extraction")
    with tempfile.TemporaryDirectory(prefix="actinv-p167-", dir=os.environ.get("TMPDIR")) as temporary:
        work = Path(temporary)
        catalog = irdff_catalog(work)
        library = None if args.reproduce_irdff else ActinvLibrary(LIBRARY)
        cache, fields_out = {}, {}
        for field, data in document["fields"].items():
            spectrum = data["spectrum"]
            primary = None if args.reproduce_irdff else actinv_production(work, field, spectrum, "equal_energy", cache)
            lethargy = None if args.reproduce_irdff else actinv_production(work, field, spectrum, "equal_lethargy",
                                                                           cache)
            rows = []
            for row in data["rates"]:
                target, mt, product = REACTIONS[row["reaction"]]
                entry = dict(row)
                entry["irdff_rate"] = irdff_rate(catalog, target, mt, spectrum) if mt else None
                if not args.reproduce_irdff:
                    entry["actinv_rate"], entry["actinv_rows"] = primary(target, product)
                    entry["actinv_rate_equal_lethargy"], _ = lethargy(target, product)
                    sigma, _ = library.production(target, product)
                    entry["independent_fold_rate"] = fold(library.bounds, sigma, spectrum)
                rows.append(entry)
            fields_out[field] = {"spectrum_bins": len(spectrum),
                                 "spectrum_range_MeV": [spectrum[0]["E_low_MeV"], spectrum[-1]["E_up_MeV"]],
                                 "spectrum_integral": math.fsum(entry["flux"] for entry in spectrum), "rates": rows}
    if args.reproduce_irdff:
        for field, data in fields_out.items():
            for row in data["rates"]:
                published = (row["published"] or {}).get("IRDFF-II")
                if row["irdff_rate"] is not None and published is not None:
                    mine = 100.0 * (row["irdff_rate"] / row["rate_per_s"] - 1.0)
                    print(f"{field} {row['reaction']:15s} {mine:6.2f}% published {published:5.1f}%")
        return
    status = git("status", "--porcelain", "--untracked-files=no")
    record = {
        "schema": SCHEMA,
        "environment": {"binary": str(BIN), "binary_sha256": sha256(BIN), "git_head": git("rev-parse", "HEAD"),
                        "crates_tree": git("rev-parse", "HEAD:crates"), "clean_tree": status == "",
                        "cgroup": cgroup_record()},
        "inputs": {name: {"path": str(path), "sha256": hashes[name]} for name, path in paths.items()},
        "tables": {"path": str(TABLES.relative_to(ROOT)), "sha256": sha256(TABLES)},
        "fields": fields_out,
    }
    args.record.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"wrote {args.record}")


if __name__ == "__main__":
    main()
