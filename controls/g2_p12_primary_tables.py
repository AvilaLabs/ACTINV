#!/usr/bin/env python3
"""P12-G2: independently rederive embedded abundances and masses from primary tables."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from p11_fixtures import make_fixture, specification, write_json


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g2_p12_primary_tables.json"
TARGET = ROOT / "results" / "tables" / "abundance_mass.json"
RUST_TABLE = ROOT / "crates" / "actinv-data" / "src" / "tables.rs"
PROTOCOL = ROOT / "protocols" / "ACTINV-P12_PROTOCOL.md"
MEIJA_PDF = Path(os.environ.get("ACTINV_P12_MEIJA_PDF", "/tmp/actinv-p12-meija2016.pdf"))
AME2020 = Path(os.environ.get("ACTINV_P12_AME2020", "/tmp/actinv-p12-mass_1.mas20.txt"))
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
MEIJA_SHA256 = "d9079171301dc440e6ee40378da1aa5aef7c43e99d815f4cf31c1eb76561dd89"
AME2020_SHA256 = "e8599c6d7f724fac91934e59f1b9de8fb8f63e820f4b39456b790665ed2a3307"
SUM_LIMIT = 2.0e-15
EXPECTED_PROVENANCE = (
    "Meija et al., Isotopic compositions of the elements 2013, Pure Appl. Chem. 88 (2016) "
    "293-306, DOI 10.1515/pac-2015-0503, NRC PDF SHA-256 "
    f"{MEIJA_SHA256} (Table 1 column 9 point values; column 6 when column 9 is an interval); "
    "AME2020 Huang et al./Wang et al., Chinese Physics C 45 (2021) 030002/030003, "
    f"mass_1.mas20 SHA-256 {AME2020_SHA256}"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def checked_input(path: Path, expected: str, label: str) -> str:
    if not path.is_file():
        raise RuntimeError(f"missing external P12 {label} input: {path}")
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(
            f"P12 {label} SHA-256 mismatch: expected {expected}, computed {actual}"
        )
    return actual


def numeric(token: str) -> float:
    return float(token.replace(" ", ""))


def parse_abundances(pdf: Path, work: Path) -> tuple[dict[str, float], dict]:
    text_path = work / "meija-layout.txt"
    extraction = subprocess.run(
        ["pdftotext", "-layout", str(pdf), str(text_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if extraction.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {extraction.stdout}{extraction.stderr}")
    version = subprocess.run(
        ["pdftotext", "-v"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    tool_version = (version.stderr or version.stdout).splitlines()[0].strip()
    text = text_path.read_text()
    for anchor in (
        "DOI 10.1515/pac-2015-0503",
        "Table 1: Isotopic compositions of the elements 2013.",
        "Column 9: Representative isotopic abundances.",
    ):
        if anchor not in text:
            raise RuntimeError(f"Meija extraction is missing anchor: {anchor}")
    table = text.split("Table 1: Isotopic compositions of the elements 2013.", 1)[1]
    table = table.split("Column 9: Representative isotopic abundances.", 1)[0]

    new_row = re.compile(
        r"^\s*(?P<z>\d{1,2})\s+(?P<symbol>[A-Z][a-z]?)\s+"
        r"(?P<mass>\d{1,3}|[–-])(?:\s|$)"
    )
    continuation = re.compile(r"^\s{8,}(?P<mass>\d{1,3})\s+")
    trailing_point = re.compile(
        r"(?P<value>(?:\d+\.\d(?:[\d ]*\d)?|\d+))(?:\(\d+\))?\s*$"
    )
    trailing_interval = re.compile(r"\[[^\]]+\][a-z]?\s*$")
    measured_point = re.compile(r"(?<![\d.])(\d+\.\d(?:[\d ]*\d)?)(?:\(\d+\))?")
    wrapped_point = re.compile(r"(?:\d+\.\d(?:[\d ]*\d)?|\d+)(?:\(\d+\))?")

    # Poppler wraps a few far-right Column 9 cells onto their own visual line. Reattach only
    # lines whose entire content is one point value; isotope rows always contain more fields.
    rows: list[str] = []
    for line in table.splitlines():
        if rows and wrapped_point.fullmatch(line.strip()):
            rows[-1] = f"{rows[-1]} {line.strip()}"
        else:
            rows.append(line)

    abundances: dict[str, float] = {}
    elements: dict[str, int] = {}
    selected_columns = defaultdict(int)
    symbol: str | None = None
    z: int | None = None
    for line in rows:
        match = new_row.match(line)
        if match:
            symbol = match.group("symbol")
            z = int(match.group("z"))
            if match.group("mass") in ("–", "-"):
                continue
            mass = int(match.group("mass"))
        else:
            match = continuation.match(line)
            if not match or symbol is None or z is None:
                continue
            mass = int(match.group("mass"))

        if trailing_interval.search(line):
            without_brackets = re.sub(r"\[[^\]]+\]", " ", line)
            values = measured_point.findall(without_brackets)
            if not values:
                raise RuntimeError(f"cannot find Column 6 abundance in row: {line}")
            value = numeric(values[0])
            selected_columns["column_6_interval_fallback"] += 1
        else:
            point = trailing_point.search(line)
            if point is None:
                raise RuntimeError(f"cannot find Column 9 abundance in row: {line}")
            value = numeric(point.group("value"))
            selected_columns["column_9_point"] += 1

        name = f"{symbol}{mass}"
        if name == "Ta180":
            name = "Ta180_m1"
        if name in abundances:
            raise RuntimeError(f"duplicate Meija isotope row: {name}")
        abundances[name] = value
        if symbol in elements and elements[symbol] != z:
            raise RuntimeError(f"inconsistent atomic number for {symbol}")
        elements[symbol] = z

    return abundances, {
        "pdftotext": tool_version,
        "extracted_text_sha256": sha256(text_path),
        "elements": len(elements),
        "selected_columns": dict(sorted(selected_columns.items())),
    }


def parse_masses(path: Path) -> dict[str, float]:
    masses: dict[str, float] = {}
    lines = path.read_text().splitlines()
    if len(lines) < 37 or "format    :  a1,i3,i5,i5,i5" not in "\n".join(lines[:36]):
        raise RuntimeError("AME2020 file lacks its fixed-width format declaration")
    for line_number, line in enumerate(lines[36:], 37):
        if len(line) < 123:
            raise RuntimeError(f"short AME2020 data row at line {line_number}")
        symbol = line[20:22].strip()
        mass_number = line[16:19].strip()
        integer = line[106:109].strip()
        micro_integer = line[110:116].strip()
        micro_fraction = line[117:123].strip()
        fields = (symbol, mass_number, integer, micro_integer, micro_fraction)
        if not all(fields) or any("*" in field or "#" in field for field in fields):
            continue
        name = f"{symbol}{int(mass_number)}"
        value = float(integer) + 1.0e-6 * float(f"{micro_integer}.{micro_fraction}")
        if name in masses:
            raise RuntimeError(f"duplicate AME2020 isotope row: {name}")
        masses[name] = value
    return masses


def regenerate_rust_table(work: Path) -> dict:
    temp_root = work / "regeneration"
    (temp_root / "controls").mkdir(parents=True)
    (temp_root / "results" / "tables").mkdir(parents=True)
    (temp_root / "crates" / "actinv-data" / "src").mkdir(parents=True)
    shutil.copy2(ROOT / "controls" / "gen_tables.py", temp_root / "controls" / "gen_tables.py")
    shutil.copy2(TARGET, temp_root / "results" / "tables" / TARGET.name)
    process = subprocess.run(
        [sys.executable, str(temp_root / "controls" / "gen_tables.py")],
        cwd=temp_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if process.returncode != 0:
        raise RuntimeError(f"table regeneration failed: {process.stdout}{process.stderr}")
    generated = temp_root / "crates" / "actinv-data" / "src" / "tables.rs"
    return {
        "rows_reported": process.stdout.strip(),
        "generated_sha256": sha256(generated),
        "tracked_sha256": sha256(RUST_TABLE),
        "byte_identical": generated.read_bytes() == RUST_TABLE.read_bytes(),
    }


def certificate_provenance(work: Path) -> dict:
    fixture = make_fixture(work / "certificate-fixture")
    spec = specification(fixture, mode="trace", cram_order=48, uncertainty=False)
    spec_path = work / "certificate-spec.json"
    output_path = work / "certificate-result.json"
    write_json(spec_path, spec)
    process = subprocess.run(
        [str(ACTINV), "run", str(spec_path), str(output_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if process.returncode != 0:
        raise RuntimeError(f"certificate probe failed: {process.stdout}{process.stderr}")
    certificate = json.loads(output_path.read_text())["certificate"]
    actual = certificate["tables_provenance"]
    return {
        "exact": actual == EXPECTED_PROVENANCE,
        "names_meija": "Meija et al." in actual,
        "names_ame2020": "AME2020" in actual,
        "contains_meija_sha256": MEIJA_SHA256 in actual,
        "contains_ame2020_sha256": AME2020_SHA256 in actual,
    }


def main() -> None:
    meija_hash = checked_input(MEIJA_PDF, MEIJA_SHA256, "Meija PDF")
    ame_hash = checked_input(AME2020, AME2020_SHA256, "AME2020 table")
    target = json.loads(TARGET.read_text())
    expected_abundance = {
        name: value for isotopes in target["abundance"].values() for name, value in isotopes.items()
    }
    expected_masses = target["mass_amu"]

    with tempfile.TemporaryDirectory(prefix="actinv-p12-g2-") as directory:
        work = Path(directory)
        abundance, extraction = parse_abundances(MEIJA_PDF, work)
        all_masses = parse_masses(AME2020)
        selected_masses = {
            name: all_masses[name.split("_", 1)[0]] for name in expected_masses
        }
        sums = {
            element: math.fsum(abundance[name] for name in isotopes)
            for element, isotopes in target["abundance"].items()
        }
        maximum_sum_deviation = max(abs(value - 1.0) for value in sums.values())
        abundance_checks = {
            "rows": len(abundance),
            "key_set_exact": set(abundance) == set(expected_abundance),
            "binary64_values_exact": abundance == expected_abundance,
            "elements": len(sums),
            "maximum_element_sum_deviation": maximum_sum_deviation,
            "element_sums_within_limit": maximum_sum_deviation <= SUM_LIMIT,
        }
        mass_checks = {
            "ame2020_numeric_rows": len(all_masses),
            "selected_rows": len(selected_masses),
            "key_set_exact": set(selected_masses) == set(expected_masses),
            "binary64_values_exact": selected_masses == expected_masses,
        }
        regeneration = regenerate_rust_table(work)
        certificate = certificate_provenance(work)

    source_checks = {
        "json_provenance_exact": target["source"] == EXPECTED_PROVENANCE,
        "rust_provenance_exact": f'pub const PROVENANCE: &str = "{EXPECTED_PROVENANCE}";'
        in RUST_TABLE.read_text(),
    }
    output = {
        "gate": "P12-G2",
        "protocol_sha256": sha256(PROTOCOL),
        "inputs": {
            "meija_pdf": {
                "sha256": meija_hash,
                "doi": "10.1515/pac-2015-0503",
                "work": "Isotopic compositions of the elements 2013",
            },
            "ame2020_mass_table": {
                "sha256": ame_hash,
                "papers": ["Chinese Physics C 45 030002", "Chinese Physics C 45 030003"],
            },
        },
        "extraction": extraction,
        "abundance": abundance_checks,
        "mass": mass_checks,
        "regeneration": regeneration,
        "provenance": source_checks,
        "certificate": certificate,
        "runtime_oracle": "primary Meija PDF and AME2020 fixed-width table only",
    }
    output["pass"] = bool(
        abundance_checks["rows"] == 289
        and abundance_checks["key_set_exact"]
        and abundance_checks["binary64_values_exact"]
        and abundance_checks["element_sums_within_limit"]
        and mass_checks["selected_rows"] == 289
        and mass_checks["key_set_exact"]
        and mass_checks["binary64_values_exact"]
        and regeneration["byte_identical"]
        and all(source_checks.values())
        and all(certificate.values())
    )
    RESULT.write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
