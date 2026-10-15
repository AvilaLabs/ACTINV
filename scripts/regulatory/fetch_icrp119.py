#!/usr/bin/env python3
"""Build a user-local ICRP 119 (ICRP 72) public dose-coefficient table for ACTINV.

ICRP Publication 119 is copyrighted by ICRP/SAGE.  ACTINV does not bundle its numbers.
This script reads a copy of the PDF that YOU obtained and writes a table to a
location of your choosing; do not commit or redistribute the output.

Extracted (adult member of the public, committed effective dose, Sv/Bq):
  Annex F, Table F.1  ingestion, per nuclide, one row per f1 variant
  Annex G, Table G.1  inhalation, 1 um AMAD, per nuclide and lung-absorption type F/M/S

Responses written by default (nothing is chosen for you):
  ingestion-adult-variant1 / -variant2   first and second Table F.1 row of a nuclide
  inhalation-adult-F / -M / -S           one response per absorption type

With --fispact-convention two more responses reproduce the convention that FISPACT-II's
hazards file was found to use for its ICRP-72 rows (adult coefficient, maximum over the
f1 variants for ingestion and over F/M/S for inhalation):
  ingestion-adult-fispact-max, inhalation-adult-fispact-max

Besides the table, `<table>.variants.json` records, per nuclide and response, which
variant was chosen, its f1 and the competing values.

The PDF text layer encodes the minus sign of exponents as control character 0x02
("3.4E<0x02>09" = 3.4E-09); this script maps it to "-".  `pdftotext` (poppler) must be
installed.

Usage:
  python3 -I scripts/regulatory/fetch_icrp119.py --pdf P119.pdf --out table.json [--fispact-convention]
  python3 -I scripts/regulatory/fetch_icrp119.py --pdf P119.pdf --out table.json \\
      --download-url https://icrp.org/... --yes-download     # downloads to --pdf first
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


def load_common():
    path = Path(__file__).resolve().parent / "regulatory_common.py"
    spec = importlib.util.spec_from_file_location("regulatory_common", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load_common()

CONVERTER = "scripts/regulatory/fetch_icrp119.py"
PUBLICATION_URL = (
    "https://icrp.org/docs/P%20119%20JAICRP%2041(s)%20Compendium%20of%20Dose%20Coefficients"
    "%20based%20on%20ICRP%20Publication%2060.pdf"
)
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
EXPONENT_VALUE = re.compile(r"^\d\.\d+E-\d\d$")
NUCLIDE_NAME = re.compile(r"^[A-Z][a-z]?-\d+[mn]?$")


def pdf_to_text(pdf: Path) -> str:
    with tempfile.TemporaryDirectory() as scratch:
        out = Path(scratch) / "p119.txt"
        subprocess.run(
            ["pdftotext", "-layout", str(pdf), str(out)],
            check=True,
            timeout=300,
            stdin=subprocess.DEVNULL,
        )
        raw = out.read_bytes()
    return raw.decode("utf-8", errors="replace").replace("\x02", "-")


def section(lines: list[str], start_pattern: str, end_pattern: str) -> list[str]:
    start = next(i for i, line in enumerate(lines) if re.match(start_pattern, line.strip()))
    end = next(i for i, line in enumerate(lines) if i > start and re.search(end_pattern, line))
    return lines[start:end]


def table_rows(block: list[str]):
    """Yield (name or None, head tokens, six coefficients) for each data line."""
    for line in block:
        tokens = line.split()
        values = [i for i, tok in enumerate(tokens) if EXPONENT_VALUE.match(tok)]
        if len(values) < 6:
            continue
        first = values[-6]
        coefficients = [float(tokens[i]) for i in values[-6:]]
        head = tokens[:first]
        name = head[0] if head and NUCLIDE_NAME.match(head[0]) else None
        yield name, head, coefficients


def parse_ingestion(block: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    current = None
    for name, head, coefficients in table_rows(block):
        if name:
            current = name
            if current in out:
                raise ValueError(f"Table F.1: nuclide {current} appears twice")
            out[current] = []
        elif current is None:
            raise ValueError(f"Table F.1: data row before any nuclide: {head}")
        out[current].append({"f1_infant_text": head[-1], "adult": coefficients[-1], "all": coefficients})
    return out


def parse_inhalation(block: list[str]) -> dict[str, list[dict]]:
    """Rows per nuclide in source order.  A type can repeat for a nuclide with several
    chemical forms (e.g. mercury F with two footnote marks); each row is kept."""
    out: dict[str, list[dict]] = {}
    current = None
    for name, head, coefficients in table_rows(block):
        tokens = [tok for tok in head if re.fullmatch(r"[FMS][^A-Za-z0-9]*", tok)]
        if len(tokens) != 1:
            raise ValueError(f"Table G.1: cannot find one absorption type in {head}")
        if name:
            current = name
            if current in out:
                raise ValueError(f"Table G.1: nuclide {current} appears twice")
            out[current] = []
        elif current is None:
            raise ValueError(f"Table G.1: data row before any nuclide: {head}")
        out[current].append(
            {
                "type": tokens[0][0],
                "type_text": tokens[0],
                "f1_infant_text": head[-1],
                "adult": coefficients[-1],
            }
        )
    return out


def source_block(pdf: Path, retrieved: str) -> dict[str, str]:
    return C.source_block(
        citation="ICRP Publication 119, Compendium of Dose Coefficients based on ICRP Publication 60 (Ann. ICRP 41(Suppl.))",
        edition="2012; Annex F (ingestion) and Annex G (inhalation, 1 um AMAD) reproduce ICRP Publication 72",
        url=PUBLICATION_URL,
        jurisdiction="ICRP recommendations (not a legal limit): adult member of the public, committed effective dose",
        scope=(
            "Adult public effective-dose coefficients, Sv per Bq intake. Intake scenario, occupancy and "
            "any safety factor are chosen by the analyst."
        ),
        retrieved=retrieved,
        source_file=pdf.name,
        source_sha256=C.sha256_file(pdf),
        converter=CONVERTER,
        licence="Copyright ICRP/SAGE; generated locally from the user's copy of the PDF; do not redistribute.",
        row_provenance=None,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pdf", type=Path, required=True, help="path to the ICRP 119 PDF")
    parser.add_argument("--out", type=Path, required=True, help="table JSON to write (outside the repository)")
    parser.add_argument("--fispact-convention", action="store_true", help="add the max-over-variants responses")
    parser.add_argument("--retrieved", default=None, help="retrieval date for the source block (default: today)")
    parser.add_argument("--download-url", help="https URL to download the PDF to --pdf first")
    parser.add_argument("--yes-download", action="store_true", help="confirm that --download-url may be fetched")
    args = parser.parse_args(argv)

    if args.download_url:
        if not args.yes_download:
            print("error: --download-url needs --yes-download to confirm the download", file=sys.stderr)
            return 2
        if not args.download_url.startswith("https://"):
            print("error: the download URL must be https", file=sys.stderr)
            return 2
        with urllib.request.urlopen(args.download_url, timeout=120) as response:
            data = response.read(MAX_DOWNLOAD_BYTES + 1)
        if len(data) > MAX_DOWNLOAD_BYTES or not data.startswith(b"%PDF"):
            print("error: download is not a PDF within the size limit", file=sys.stderr)
            return 1
        args.pdf.write_bytes(data)

    text = pdf_to_text(args.pdf)
    lines = text.splitlines()
    f_block = section(lines, r"^Table F\.1\.", r"^\s*ANNEX G\.")
    g_start = next(i for i, line in enumerate(lines) if re.match(r"^Table G\.1\.", line.strip()))
    g_block = section(lines[g_start - 1 :], r"^Table G\.1\.", r"^\s*ANNEX H\.")
    ingestion = parse_ingestion(f_block)
    inhalation = parse_inhalation(g_block)

    responses: dict[str, dict[str, float]] = {}
    variants: dict[str, dict[str, dict]] = {}

    def put(response: str, name: str, value: float, record: dict) -> None:
        # ICRP 107 naming: 'm' is the first metastable state, 'n' the second (ACTINV m2).
        parsed = C.parse_nuclide(re.sub(r"n$", "m2", name))
        if parsed is None:
            raise ValueError(f"not a nuclide name: {name!r}")
        key = parsed[0]
        if not value > 0:
            raise ValueError(f"{response} {name}: coefficient must be positive")
        if key in responses.setdefault(response, {}):
            raise ValueError(f"{response}: duplicate nuclide {key}")
        responses[response][key] = value
        variants.setdefault(response, {})[key] = {"source_name": name, **record}

    for name, rows in ingestion.items():
        for ordinal in range(1, len(rows) + 1):
            row = rows[ordinal - 1]
            put(
                f"ingestion-adult-variant{ordinal}", name, row["adult"],
                {"variant_row": ordinal, "variants_in_source": len(rows), "f1_infant_text": row["f1_infant_text"]},
            )
        if args.fispact_convention:
            best = max(range(len(rows)), key=lambda i: rows[i]["adult"])
            put(
                "ingestion-adult-fispact-max", name, rows[best]["adult"],
                {"chosen_variant_row": best + 1, "candidates": [r["adult"] for r in rows]},
            )
    for name, rows in inhalation.items():
        seen: dict[str, int] = {}
        for row in rows:
            seen[row["type"]] = seen.get(row["type"], 0) + 1
            ordinal = seen[row["type"]]
            suffix = "" if ordinal == 1 else f"-variant{ordinal}"
            put(
                f"inhalation-adult-{row['type']}{suffix}", name, row["adult"],
                {"absorption_type": row["type"], "type_text_in_source": row["type_text"],
                 "f1_infant_text": row["f1_infant_text"], "row_of_this_type": ordinal},
            )
        if args.fispact_convention:
            best = max(range(len(rows)), key=lambda i: rows[i]["adult"])
            put(
                "inhalation-adult-fispact-max", name, rows[best]["adult"],
                {"chosen_absorption_type": rows[best]["type"], "chosen_type_text": rows[best]["type_text"],
                 "candidates": [{"type_text": r["type_text"], "adult": r["adult"]} for r in rows]},
            )

    retrieved = args.retrieved or datetime.date.today().isoformat()
    source = source_block(args.pdf, retrieved)
    def sort_key(rid: str):
        group = (0 if rid.startswith("ingestion") else 1, 1 if "fispact" in rid else 0)
        return group + (rid,)

    order = sorted(responses, key=sort_key)
    basis = {
        "variant": "Table F.1 row {n} of each nuclide that has one (adult committed effective dose, Sv/Bq; f1 and compound differ by row, see variants file)",
        "type": "Table G.1 absorption type {t}, 1 um AMAD, adult, Sv/Bq",
        "fispact-max": "maximum of the adult coefficients over the {what} of each nuclide (the convention found in FISPACT-II hazards_2012 for ICRP-72 rows)",
    }
    out_responses = []
    for rid in order:
        if rid not in responses:
            continue
        if rid.startswith("ingestion-adult-variant"):
            text = basis["variant"].format(n=rid.rsplit("variant", 1)[1])
        elif rid == "ingestion-adult-fispact-max":
            text = basis["fispact-max"].format(what="f1 variants")
        elif rid == "inhalation-adult-fispact-max":
            text = basis["fispact-max"].format(what="absorption types F/M/S at 1 um AMAD")
        else:
            text = basis["type"].format(t=rid.split("-")[2])
            if "variant" in rid:
                text += " (a further chemical form of the same type; see the variants file)"
        out_responses.append(
            {
                "id": rid,
                "kind": "ingestion_dose" if rid.startswith("ingestion") else "inhalation_dose",
                "basis": text,
                "coefficients": responses[rid],
            }
        )
    document = {
        "format": C.TABLE_FORMAT,
        "title": "ICRP 119 (ICRP 72) adult public ingestion and inhalation dose coefficients, user-generated",
        "source": source,
        "responses": out_responses,
    }
    table_sha = C.dump_json(args.out, document)
    variants_path = args.out.with_suffix(".variants.json")
    C.dump_json(
        variants_path,
        {
            "format": "actinv-icrp119-variants-1",
            "table": args.out.name,
            "table_sha256": table_sha,
            "variants": variants,
        },
    )
    print(f"wrote {args.out} sha256 {table_sha}")
    print(f"Table F.1 nuclides {len(ingestion)}, Table G.1 nuclides {len(inhalation)}")
    for response in out_responses:
        print(f"  {response['id']}: {len(response['coefficients'])} nuclides")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
