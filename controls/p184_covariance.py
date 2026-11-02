#!/usr/bin/env python3
"""P184 harness: TENDL-2025 MF=33 sections replaced by IRDFF-II's where IRDFF-II evaluates the reaction.

TENDL-2025 takes the cross sections of the dosimetry reactions from IRDFF-II, but its MF=33 covariances come from
TALYS parameter variation, so for those reactions it pairs a measured mean with a model uncertainty that is often
far wider (Fe-56(n,p) at 14 MeV: 50% against IRDFF-II's 1.1%) or, for actinide fission, far narrower. For every
reaction this harness selects (`select`), the TENDL section is replaced by one LB=5 relative matrix on IRDFF-II's
energy grid,

    C_T[k, l] = r_k r_l C_I[k, l] + b_k b_l,    r_k = s_I,k / s_T,k,    b_k = (s_T,k - s_I,k) / s_T,k,

with s_I,k IRDFF-II's cross section averaged over interval k (flat weighting, exact lin-lin integration) and s_T,k
the same average of the activation library's reaction-total row (ZAP=-1), each group's cross section flat in energy,
as ACTINV applies covariances. The library, not TENDL-2025's MF=3, is TENDL's side: inside the resolved-resonance
range MF=3 holds only a background, and the library is what ACTINV computes with. r rescales IRDFF-II's covariance, relative to its own mean, onto TENDL's; b b^T is the
squared mean difference, so the matrix is the mean-squared error of TENDL's cross section about IRDFF-II's
estimate rather than IRDFF-II's covariance alone. An IRDFF-II LB=8 short-range term is carried over, scaled by r_k^2.
TENDL cross-covariances with a replaced reaction (LB=6 subsections, MT1 != MT) are dropped: they were correlations
of TENDL's model uncertainty, not of the replacement.

    python3 controls/p184_covariance.py stage IRDFF.endf ACTIVATION.npz COV_INDEX.json STAGE_DIR RECORD.json SOURCE_DIR...
        [--only ZA,...]

takes, for every target of the shipped sidecar's index, the file in the SOURCE_DIRs whose SHA-256 is the one the index
records (so the input is the sidecar's own), and writes STAGE_DIR/inputs (every source, hard-linked), STAGE_DIR/mf33
(the patched evaluations) and the selection record.
`actinv build-covariance STAGE_DIR/inputs LIBRARY.npz OUT.cov.npz --mf33-from STAGE_DIR/mf33` then builds the sidecar.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

SCHEMA = "actinv-p184-stage-1"
SYMBOLS = ("n H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr "
           "Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re "
           "Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm").split()


# -- ENDF-6 text --------------------------------------------------------------------------------------------------
def endf_float(text: str) -> float:
    s = text.replace(" ", "")
    if not s:
        return 0.0
    if "e" in s.lower():
        return float(s)
    for k in range(1, len(s)):
        if s[k] in "+-":
            return float(s[:k] + "e" + s[k:])
    return float(s)


def endf_int(text: str) -> int:
    s = text.strip()
    return int(s) if s else 0


def fmt_float(x: float) -> str:
    """11-character ENDF-6 real: 7 significant digits, exponent without the 'e'."""
    if x == 0.0:
        return " 0.000000+0"
    mantissa, exponent = f"{x:.6e}".split("e")
    e = int(exponent)
    if abs(e) >= 10:
        mantissa, exponent = f"{x:.5e}".split("e")
        e = int(exponent)
    text = f"{mantissa}{'+' if e >= 0 else '-'}{abs(e)}"
    return text.rjust(11)


def fmt_int(n: int) -> str:
    return f"{n:11d}"


def tag(mat: int, mf: int, mt: int, ns: int) -> str:
    return f"{mat:4d}{mf:2d}{mt:3d}{ns:5d}"


def line_id(line: str) -> tuple[int, int, int] | None:
    if len(line) < 75:
        return None
    try:
        return int(line[66:70]), int(line[70:72]), int(line[72:75])
    except ValueError:
        return None


def sections(lines: list[str], mf: int, mat: int | None = None) -> dict[int, list[str]]:
    """MT -> the section's lines (HEAD to the last record, SEND excluded) for one MF (of one MAT, if given)."""
    out: dict[int, list[str]] = {}
    for line in lines:
        ident = line_id(line)
        if ident and ident[1] == mf and ident[2] > 0 and (mat is None or ident[0] == mat):
            out.setdefault(ident[2], []).append(line)
    return out


def list_values(lines: list[str], start: int, n: int) -> tuple[list[float], int]:
    values: list[float] = []
    pos = start
    while len(values) < n:
        line = lines[pos]
        pos += 1
        for k in range(6):
            if len(values) < n:
                values.append(endf_float(line[11 * k:11 * k + 11]))
    return values, pos


# -- MF=3 ---------------------------------------------------------------------------------------------------------
def mf3(lines: list[str], mt: int, mat: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    section = sections(lines, 3, mat).get(mt)
    if section is None:
        raise KeyError(f"no MF=3 MT={mt}")
    nr, npoints = endf_int(section[1][44:55]), endf_int(section[1][55:66])
    _, pos = list_values(section, 2, 2 * nr)
    pairs, _ = list_values(section, pos, 2 * npoints)
    return np.array(pairs[0::2]), np.array(pairs[1::2])


def integral(x: np.ndarray, y: np.ndarray, e: float) -> float:
    """Integral from x[0] to e of the lin-lin function (x, y); repeated x (a step) adds nothing, so a step is exact."""
    if e <= x[0]:
        return 0.0
    seg = np.diff(x) * 0.5 * (y[1:] + y[:-1])
    j = int(np.searchsorted(x, e, side="right")) - 1  # x[j] <= e < x[j+1]
    if j >= len(x) - 1:
        return float(seg.sum())
    t = e - x[j]
    slope = (y[j + 1] - y[j]) / (x[j + 1] - x[j])
    return float(seg[:j].sum() + t * (y[j] + 0.5 * slope * t))


def support(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Energies from the start of the table to its last nonzero point. TENDL-2025 tabulates MT=103, 107, ... as
    zero above 30 MeV, where it moves the production into MT=5; those zeros are not a cross section to compare."""
    nonzero = np.nonzero(y > 0.0)[0]
    return float(x[0]), float(x[nonzero[-1]]) if len(nonzero) else float(x[0])


def interval_means(x: np.ndarray, y: np.ndarray, grid: np.ndarray, domain: tuple[float, float]) -> np.ndarray:
    """Flat-weighted mean of the lin-lin function (x, y) over each interval of grid clipped to domain (zero where
    the clipped interval is empty)."""
    out = np.zeros(len(grid) - 1)
    for k in range(len(grid) - 1):
        lo, hi = max(grid[k], domain[0]), min(grid[k + 1], domain[1])
        if hi > lo:
            out[k] = (integral(x, y, hi) - integral(x, y, lo)) / (hi - lo)
    return out


class GroupLibrary:
    """Reaction-total rows (ZAP=-1) of an activation library: {(ZA, MT): group cross sections}, and the bounds."""

    def __init__(self, npz: Path):
        import zipfile
        index = json.loads(npz.with_name(npz.stem + "_index.json").read_text())
        za_of = {i: t["za"] for i, t in enumerate(index["targets"]) if t["liso"] == 0}
        with zipfile.ZipFile(npz) as z:
            rows = np.lib.format.read_array(z.open("rows.npy"))
            self.bounds = np.lib.format.read_array(z.open("bounds.npy"))
            sig = np.lib.format.read_array(z.open("sig.npy"))
        self.sigma = {}
        for k in np.flatnonzero(rows[:, 2] == -1):
            target, mt = int(rows[k, 0]), int(rows[k, 1])
            if target in za_of:
                self.sigma[(za_of[target], mt)] = sig[k]
        self.sha256 = sha256(npz)

    def support(self, za: int, mt: int) -> tuple[float, float]:
        nonzero = np.flatnonzero(self.sigma[(za, mt)] > 0.0)
        if not len(nonzero):
            return float(self.bounds[0]), float(self.bounds[0])
        return float(self.bounds[nonzero[0]]), float(self.bounds[nonzero[-1] + 1])

    def interval_means(self, za: int, mt: int, grid: np.ndarray, domain: tuple[float, float]) -> np.ndarray:
        """Mean over each interval of grid, clipped to domain, of the group histogram (flat in energy per group)."""
        sigma, lo_g, hi_g = self.sigma[(za, mt)], self.bounds[:-1], self.bounds[1:]
        out = np.zeros(len(grid) - 1)
        for k in range(len(grid) - 1):
            lo, hi = max(grid[k], domain[0]), min(grid[k + 1], domain[1])
            if hi > lo:
                overlap = np.clip(np.minimum(hi_g, hi) - np.maximum(lo_g, lo), 0.0, None)
                out[k] = float(np.dot(sigma, overlap)) / (hi - lo)
        return out


# -- MF=33 --------------------------------------------------------------------------------------------------------
def parse_mf33(section: list[str]) -> dict:
    """HEAD fields and subsections; each subsection keeps its raw lines and its NI sub-subsections."""
    head = section[0]
    nl = endf_int(head[55:66])
    pos = 1
    subs = []
    for _ in range(nl):
        start = pos
        cont = section[pos]
        pos += 1
        mat1, mt1, nc, ni = (endf_int(cont[22:33]), endf_int(cont[33:44]), endf_int(cont[44:55]),
                             endf_int(cont[55:66]))
        if nc:
            raise ValueError(f"NC={nc} sub-subsections are not handled")
        blocks = []
        for _ in range(ni):
            c = section[pos]
            lt, lb, nt, ne = (endf_int(c[22:33]), endf_int(c[33:44]), endf_int(c[44:55]), endf_int(c[55:66]))
            values, pos = list_values(section, pos + 1, nt)
            blocks.append({"lt": lt, "lb": lb, "ne": ne, "values": values})
        subs.append({"mat1": mat1, "mt1": mt1, "blocks": blocks, "lines": section[start:pos]})
    if pos != len(section):
        raise ValueError(f"MF=33 section has {len(section) - pos} unread lines")
    return {"za": endf_float(head[0:11]), "mtl": endf_int(head[33:44]), "nl": nl, "head": head, "subs": subs}


def irdff_matrix(section: dict, mt: int) -> tuple[np.ndarray, np.ndarray, list[tuple[np.ndarray, np.ndarray]]]:
    """IRDFF-II's self-covariance: (grid, LB=5 LS=1 relative matrix, [(LB=8 grid, LB=8 F), ...])."""
    if section["mtl"] or len(section["subs"]) != 1 or section["subs"][0]["mt1"] != mt or section["subs"][0]["mat1"]:
        raise ValueError("IRDFF-II section is not a single self-covariance")
    grid = matrix = None
    lb8 = []
    for block in section["subs"][0]["blocks"]:
        if block["lb"] == 5 and block["lt"] == 1 and grid is None:
            ne = block["ne"]
            grid = np.array(block["values"][:ne])
            m = ne - 1
            matrix = np.zeros((m, m))
            iu = np.triu_indices(m)
            matrix[iu] = block["values"][ne:]
            matrix = matrix + matrix.T - np.diag(np.diag(matrix))
        elif block["lb"] == 8 and block["lt"] == 0:
            pairs = np.array(block["values"]).reshape(-1, 2)
            lb8.append((pairs[:, 0], pairs[:-1, 1]))
        else:
            raise ValueError(f"unhandled IRDFF-II block LB={block['lb']} LS/LT={block['lt']}")
    if grid is None:
        raise ValueError("IRDFF-II section has no LB=5 matrix")
    return grid, matrix, lb8


def replacement(irdff_lines: list[str], tendl_lines: list[str], library: GroupLibrary, za: int, mt: int,
                irdff_section: dict) -> dict:
    grid, c_i, lb8 = irdff_matrix(irdff_section, mt)
    xi, yi = mf3(irdff_lines, mt, irdff_section["mat"])
    xt, yt = mf3(tendl_lines, mt)
    # both means over the energies both give the cross section: a bin the library covers only in part (TENDL-2025's
    # MT=103 ends at 30 MeV, IRDFF-II's at 60) is not given a bias from the part it leaves out. The upper end is also
    # where TENDL-2025's MF=3 ends: the library group holding 30 MeV straddles it
    (ilo, ihi), (glo, ghi), (_, thi) = support(xi, yi), library.support(za, mt), support(xt, yt)
    domain = (max(ilo, glo), min(ihi, ghi, thi))
    s_i = interval_means(xi, yi, grid, domain)
    s_t = library.interval_means(za, mt, grid, domain)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(s_t > 0.0, s_i / s_t, 0.0)
        b = np.where(s_t > 0.0, (s_t - s_i) / s_t, 0.0)
    c_t = np.outer(r, r) * c_i + np.outer(b, b)
    out = {"grid": grid, "matrix": c_t, "irdff_matrix": c_i, "r": r, "b": b, "s_irdff": s_i, "s_tendl": s_t,
           "lb8": []}
    for lb8_grid, f in lb8:
        mids = 0.5 * (lb8_grid[:-1] + lb8_grid[1:])
        idx = np.clip(np.searchsorted(grid, mids, side="right") - 1, 0, len(r) - 1)
        out["lb8"].append((lb8_grid, f * r[idx] ** 2))
    return out


def list_lines(c1: float, c2: float, l1: int, l2: int, n1: int, n2: int, values, mat: int, mt: int) -> list[str]:
    rows = [fmt_float(c1) + fmt_float(c2) + fmt_int(l1) + fmt_int(l2) + fmt_int(n1) + fmt_int(n2)]
    vals = [fmt_float(v) for v in values]
    for k in range(0, len(vals), 6):
        rows.append("".join(vals[k:k + 6]).ljust(66))
    return [row + "\0" for row in rows]  # tagged later


def new_section(tendl_section: dict, mat: int, mt: int, rep: dict) -> list[str]:
    head = tendl_section["head"]
    nl_head = head[0:22] + fmt_int(0) + fmt_int(0) + fmt_int(0) + fmt_int(1)  # ZA, AWR kept; MTL=0, NL=1
    blocks = []
    grid, c = rep["grid"], rep["matrix"]
    m = len(grid) - 1
    iu = np.triu_indices(m)
    blocks.append(list_lines(0.0, 0.0, 1, 5, len(grid) + len(iu[0]), len(grid), list(grid) + list(c[iu]), mat, mt))
    for lb8_grid, f in rep["lb8"]:
        pairs = []
        for e, v in zip(lb8_grid, list(f) + [0.0]):
            pairs += [e, v]
        blocks.append(list_lines(0.0, 0.0, 0, 8, len(pairs), len(pairs) // 2, pairs, mat, mt))
    sub = fmt_float(0.0) + fmt_float(0.0) + fmt_int(0) + fmt_int(mt) + fmt_int(0) + fmt_int(len(blocks))
    rows = [nl_head + "\0", sub + "\0"] + [row for block in blocks for row in block]
    return rows


def tagged(rows: list[str], mat: int, mf: int, mt: int) -> list[str]:
    out = []
    for ns, row in enumerate(rows, start=1):
        body = row[:66] if not row.endswith("\0") else row[:-1]
        out.append(body.ljust(66)[:66] + tag(mat, mf, mt, ns) + "\n")
    return out


def drop_cross(tendl_section: dict, mt: int, replaced: set[int]) -> list[str] | None:
    """The section without subsections whose MT1 is replaced (None if nothing changes)."""
    keep = [s for s in tendl_section["subs"] if s["mt1"] == mt or s["mt1"] not in replaced]
    if len(keep) == len(tendl_section["subs"]):
        return None
    head = tendl_section["head"]
    rows = [head[0:55] + fmt_int(len(keep)) + "\0"]
    for sub in keep:
        rows += [line[:66] + "\0" for line in sub["lines"]]
    return rows


def patch_file(tendl_path: Path, irdff_lines: list[str], library: GroupLibrary, za: int,
               wanted: dict[int, dict]) -> tuple[str, dict]:
    """The TENDL file text with every MT in `wanted` replaced, and a per-MT summary."""
    lines = tendl_path.read_text().splitlines(keepends=True)
    mat = line_id(next(l for l in lines if line_id(l) and line_id(l)[1] == 33))[0]
    mf33 = sections(lines, 33)
    parsed = {mt: parse_mf33(sec) for mt, sec in mf33.items()}
    replaced = set(wanted)
    new: dict[int, list[str]] = {}
    summary = {}
    for mt, irdff_section in wanted.items():
        if parsed[mt]["mtl"]:
            raise ValueError(f"TENDL MT={mt} is part of a lumped covariance (MTL={parsed[mt]['mtl']})")
        rep = replacement(irdff_lines, lines, library, za, mt, irdff_section)
        new[mt] = tagged(new_section(parsed[mt], mat, mt, rep), mat, 33, mt)
        summary[mt] = rep
    for mt, sec in parsed.items():
        if mt in replaced:
            continue
        rows = drop_cross(sec, mt, replaced)
        if rows is not None:
            new[mt] = tagged(rows, mat, 33, mt)
    out: list[str] = []
    i = 0
    while i < len(lines):
        ident = line_id(lines[i])
        if ident and ident[1] == 33 and ident[2] in new:
            mt = ident[2]
            out += new[mt]
            while i < len(lines) and line_id(lines[i]) == (mat, 33, mt):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    # MF=1/MT=451 directory: NC of each rewritten MF=33 section
    counts = {mt: len(rows) for mt, rows in new.items()}
    for j, line in enumerate(out):
        ident = line_id(line)
        if ident and ident[1] == 1 and ident[2] == 451 and line[0:22].strip() == "":
            try:
                mf, mt = endf_int(line[22:33]), endf_int(line[33:44])
                endf_int(line[44:55]), endf_int(line[55:66])
            except ValueError:
                continue
            if mf == 33 and mt in counts:
                out[j] = line[0:44] + fmt_int(counts[mt]) + line[55:]
    return "".join(out), summary


# -- selection ----------------------------------------------------------------------------------------------------
def tendl_name(za: int) -> str:
    z, a = divmod(za, 1000)
    return f"n-{SYMBOLS[z]}{a:03d}.tendl"


def resolve_sources(cov_index: Path, dirs: list[Path]) -> dict[str, Path]:
    """File name -> the path, in the first of dirs that has it, whose SHA-256 is the index's source_sha256."""
    out = {}
    for target in json.loads(cov_index.read_text())["targets"]:
        for d in dirs:
            path = d / target["file"]
            if path.is_file() and sha256(path) == target["source_sha256"]:
                out[target["file"]] = path
                break
        else:
            raise SystemExit(f"no source of {target['file']} has SHA-256 {target['source_sha256']}")
    return out


def select(irdff_lines: list[str], sources: dict[str, Path],
           library: GroupLibrary) -> tuple[dict[int, dict[int, dict]], list[dict]]:
    """{ZA: {MT: IRDFF-II section}} to replace, and the IRDFF-II reactions left out with the reason."""
    chosen: dict[int, dict[int, dict]] = {}
    skipped = []
    by_mat: dict[int, dict[int, list[str]]] = {}
    for line in irdff_lines:
        ident = line_id(line)
        if ident and ident[1] == 33 and ident[2] > 0:
            by_mat.setdefault(ident[0], {}).setdefault(ident[2], []).append(line)
    for mat, secs in sorted(by_mat.items()):
        for mt, sec in sorted(secs.items()):
            parsed = parse_mf33(sec)
            za = int(round(parsed["za"]))
            entry = {"irdff_mat": mat, "za": za, "mt": mt}
            if za % 1000 == 0:
                skipped.append({**entry, "reason": "natural element (no single TENDL target)"}); continue
            path = sources.get(tendl_name(za))
            if path is None:
                skipped.append({**entry, "reason": f"no TENDL file {tendl_name(za)}"}); continue
            tendl_mf33 = sections(path.read_text().splitlines(keepends=True), 33)
            if mt not in tendl_mf33:
                skipped.append({**entry, "reason": "TENDL has no MF=33 section for this MT"}); continue
            if (za, mt) not in library.sigma:
                skipped.append({**entry, "reason": "the activation library has no reaction-total row"}); continue
            try:
                irdff_matrix(parsed, mt)
                mf3(irdff_lines, mt, mat)
                mf3(path.read_text().splitlines(keepends=True), mt)
            except (ValueError, KeyError) as exc:
                skipped.append({**entry, "reason": str(exc)}); continue
            parsed["mat"] = mat
            chosen.setdefault(za, {})[mt] = parsed
    return chosen, skipped


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage(irdff: Path, activation: Path, cov_index: Path, dirs: list[Path], out: Path, record_path: Path,
          only: set[int] | None) -> dict:
    irdff_lines = irdff.read_text(errors="replace").splitlines(keepends=True)
    sources = resolve_sources(cov_index, dirs)
    library = GroupLibrary(activation)
    chosen, skipped = select(irdff_lines, sources, library)
    if only is not None:
        chosen = {za: v for za, v in chosen.items() if za in only}
    inputs, mf33 = out / "inputs", out / "mf33"
    inputs.mkdir(parents=True, exist_ok=False)
    mf33.mkdir()
    for name, path in sorted(sources.items()):
        os.link(path, inputs / name)
    replaced = []
    for za, wanted in sorted(chosen.items()):
        path = sources[tendl_name(za)]
        text, summary = patch_file(path, irdff_lines, library, za, wanted)
        (mf33 / path.name).write_text(text)
        for mt, rep in summary.items():
            c, ci = rep["matrix"], rep["irdff_matrix"]
            ev = np.linalg.eigvalsh(c)
            replaced.append({
                "za": za, "mt": mt, "file": path.name, "tendl_sha256": sha256(path),
                "patched_sha256": sha256(mf33 / path.name), "grid_eV": rep["grid"].tolist(),
                "r": rep["r"].tolist(), "b": rep["b"].tolist(),
                "max_abs_b": float(np.max(np.abs(rep["b"]))) if len(rep["b"]) else 0.0,
                "min_eigenvalue": float(ev.min()), "max_eigenvalue": float(ev.max()),
                "irdff_min_eigenvalue": float(np.linalg.eigvalsh(ci).min()),
                "lb8_blocks": len(rep["lb8"]),
            })
    record = {"schema": SCHEMA, "irdff": {"path": str(irdff), "sha256": sha256(irdff)},
              "activation": {"path": str(activation), "sha256": library.sha256},
              "cov_index": {"path": str(cov_index), "sha256": sha256(cov_index)},
              "source_dirs": [str(d) for d in dirs], "only": sorted(only) if only else None,
              "replaced": replaced, "skipped": skipped}
    record_path.write_text(json.dumps(record, indent=1) + "\n")
    return record


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    st = sub.add_parser("stage")
    st.add_argument("irdff", type=Path)
    st.add_argument("activation", type=Path)
    st.add_argument("cov_index", type=Path)
    st.add_argument("out", type=Path)
    st.add_argument("record", type=Path)
    st.add_argument("source_dirs", type=Path, nargs="+")
    st.add_argument("--only", help="comma-separated ZA values (development subsets)")
    args = ap.parse_args(argv)
    only = {int(z) for z in args.only.split(",")} if args.only else None
    record = stage(args.irdff, args.activation, args.cov_index, args.source_dirs, args.out, args.record, only)
    print(f"{len(record['replaced'])} reactions replaced in {len({r['file'] for r in record['replaced']})} files; "
          f"{len(record['skipped'])} IRDFF-II reactions left out")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
