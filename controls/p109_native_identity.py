"""Independent native-source and artificial MF8 identity checks for P109.

This module is read-only with respect to ACTINV and launches no subprocesses.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any


# AWR values are the literal values after the frozen ENDF writer's 11.4E
# encoding, not unrounded atomic masses used by the independent inventory oracle.
EXPECTED = {
    26054: (2601, 53.94, True, 0.0),
    26056: (2602, 55.935, True, 0.0),
    26057: (2603, 56.935, True, 0.0),
    26058: (2604, 57.933, True, 0.0),
    14028: (1401, 27.977, True, 0.0),
    14029: (1402, 28.976, True, 0.0),
    14030: (1403, 29.974, True, 0.0),
    41093: (4101, 92.906, True, 0.0),
    41094: (4102, 93.907, False, 1.0e11),
    42094: (4201, 93.905, True, 0.0),
    1003: (1001, 3.016, False, 1.0e9),
    2003: (2001, 3.016, True, 0.0),
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _float_field(raw: str) -> float:
    text = raw.strip()
    if not text:
        return 0.0
    if "e" not in text.lower():
        for index in range(1, len(text)):
            if text[index] in "+-" and text[index - 1] not in "eE":
                text = text[:index] + "e" + text[index:]
                break
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("nonfinite ENDF numeric field")
    return value


def _int_field(raw: str) -> int:
    text = raw.strip()
    return int(text) if text else 0


def _fields(line: str) -> tuple[list[float], int, int, int]:
    if len(line) < 75 or not line.isascii():
        raise ValueError("truncated/non-ASCII ENDF record")
    vals = [_float_field(line[i:i + 11]) for i in range(0, 66, 11)]
    return vals, _int_field(line[66:70]), _int_field(line[70:72]), _int_field(line[72:75])


def inspect_decay_bytes(raw: bytes, omit_nb94: bool) -> list[str]:
    """Parse and validate every generated MF8/MT457 record without ACTINV."""
    errors: list[str] = []
    try:
        text = raw.decode("ascii")
        lines = text.splitlines()
        sections: dict[tuple[int, int], list[list[float]]] = {}
        for line in lines:
            values, mat, mf, mt = _fields(line)
            if mf == 8 and mt == 457:
                # The first record of each MAT/section is its HEAD. Sequence numbers
                # distinguish the remaining LIST records without trusting names.
                seq = _int_field(line[75:80])
                if seq == 1:
                    za = int(round(values[0]))
                    key = (mat, za)
                    sections[key] = [values]
                else:
                    candidates = [k for k in sections if k[0] == mat]
                    if not candidates:
                        errors.append(f"MF8/MT457 record precedes HEAD for MAT {mat}")
                        continue
                    sections[candidates[-1]].append(values)
            elif mf == 8 and mt == 0:
                continue
        expected = dict(EXPECTED)
        if omit_nb94:
            expected.pop(41094)
        actual_za = {key[1] for key in sections}
        if actual_za != set(expected):
            errors.append("MF8/MT457 ZA population differs from frozen fixture")
        if len(sections) != len(expected):
            errors.append("MF8/MT457 record identities are duplicated or missing")
        for za, (mat_expected, awr_expected, stable, half_life) in expected.items():
            records = sections.get((mat_expected, za))
            if records is None:
                errors.append(f"missing MF8/MT457 record ZA={za}")
                continue
            head, energy = records[:2] if len(records) >= 2 else ([], [])
            if not head:
                errors.append(f"missing HEAD ZA={za}")
                continue
            if int(round(head[0])) != za or not math.isclose(head[1], awr_expected, rel_tol=1e-12, abs_tol=1e-12):
                errors.append(f"HEAD identity/AWR differs for ZA={za}")
            # HEAD fields encode LISO at field 4 and NST at field 5; the generator
            # freezes ground states (LISO=0) and stable NST=1 / radioactive NST=0.
            if int(head[3]) != 0 or int(head[4]) != int(stable):
                errors.append(f"HEAD LISO/NST differs for ZA={za}")
            expected_record_count = 4 if stable else 5
            if len(records) != expected_record_count:
                errors.append(f"unexpected MF8/MT457 record count for ZA={za}")
            if energy:
                if not math.isclose(energy[0], half_life, rel_tol=1e-12, abs_tol=0.0):
                    errors.append(f"half-life differs for ZA={za}")
                if int(energy[4]) != 6 or int(energy[5]) != 0:
                    errors.append(f"energy LIST shape differs for ZA={za}")
            if len(records) >= 3 and any(value != 0.0 for value in records[2]):
                errors.append(f"mean-energy payload differs for ZA={za}")
            modes = records[3] if len(records) >= 4 else []
            if modes:
                if stable:
                    if int(modes[4]) != 0 or int(modes[5]) != 0:
                        errors.append(f"stable ZA={za} has a decay mode")
                else:
                    if int(modes[4]) != 6 or int(modes[5]) != 1:
                        errors.append(f"radioactive ZA={za} mode LIST shape differs")
                    payload = records[4] if len(records) >= 5 else []
                    if (len(payload) < 6 or not math.isclose(payload[0], 1.0, abs_tol=0.0)
                            or any(payload[i] != 0.0 for i in (1, 2, 3, 5))
                            or not math.isclose(payload[4], 1.0, abs_tol=0.0)):
                        errors.append(f"radioactive ZA={za} is not the frozen RTYP1/RFS0/BR1 mode")
        if errors:
            return errors
    except (UnicodeDecodeError, ValueError, OverflowError, IndexError) as exc:
        errors.append(f"cannot independently parse artificial decay fixture: {exc}")
    return errors


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _nonempty(value: Any) -> bool:
    return isinstance(value, (list, dict, str)) and len(value) > 0


def _certificate_errors(record: Any, variant: dict, root: Path, label: str) -> list[str]:
    errors: list[str] = []
    record = _object(record)
    cert = _object(record.get("run_certificate"))
    inputs = _object(cert.get("inputs"))
    paths = variant.get("paths", {})
    hashes = variant.get("sha256", {})
    expected = {
        "library": (paths.get("library"), hashes.get("library")),
        "library_index": (paths.get("index"), hashes.get("index")),
        "decay_primary": (paths.get("decay"), hashes.get("decay")),
    }
    for key, (path, digest) in expected.items():
        actual = _object(inputs.get(key))
        if actual.get("path") != path or actual.get("sha256") != digest:
            errors.append(f"{label}: {key} certificate path/hash differs from frozen generated fixture")
        if isinstance(actual.get("path"), str) and Path(actual["path"]).is_absolute():
            errors.append(f"{label}: {key} certificate path is absolute")
        if path and digest:
            try:
                on_disk = (root / path).read_bytes()
                if _sha(on_disk) != digest:
                    errors.append(f"{label}: generated fixture file {path} differs from its frozen SHA")
            except OSError:
                errors.append(f"{label}: generated fixture file {path} is unavailable")
    if inputs.get("decay_fallback") is not None:
        errors.append(f"{label}: unexpected decay fallback input")
    if inputs.get("decay_overrides") is not None:
        errors.append(f"{label}: unexpected decay overrides input")
    if _nonempty(inputs.get("fission_yields")):
        errors.append(f"{label}: unexpected fission-yield inputs")
    if cert.get("mode") != "coupled" or cert.get("prune") != "reach":
        errors.append(f"{label}: certificate solver mode/prune is not coupled/reach")
    if cert.get("bmin_atoms_per_g") != 0.0:
        errors.append(f"{label}: certificate bmin is not zero")
    if not isinstance(cert.get("cram"), str) or not cert["cram"].startswith("CRAM-16,"):
        errors.append(f"{label}: certificate CRAM order is not 16")
    if cert.get("material_basis") != "wt_percent":
        errors.append(f"{label}: certificate material basis is not wt_percent")
    return errors


def _audit_errors(record: Any, case: dict, label: str) -> list[str]:
    errors: list[str] = []
    record = _object(record)
    evidence = _object(record.get("audit_evidence"))
    ledger = _object(evidence.get("ledger"))
    coverage = _object(evidence.get("coverage"))
    omissions = _object(coverage.get("omission_ledger"))
    completeness = _object(ledger.get("completeness"))
    reasons = record.get("coverage_reasons")
    scan_reasons = record.get("audit_evidence", {}).get("coverage_reasons") if isinstance(record.get("audit_evidence"), dict) else None
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        errors.append(f"{label}: coverage reasons are malformed")
        reasons = []
    if isinstance(scan_reasons, list) and sorted(reasons) != sorted(scan_reasons):
        errors.append(f"{label}: top-level coverage reasons differ from scan evidence")
    status = completeness.get("status")
    if status not in ("complete", "incomplete"):
        errors.append(f"{label}: audit completeness status is missing or malformed")
    for channel in ("reaction_channel", "decay_channel"):
        channel_obj = _object(completeness.get(channel))
        if not isinstance(channel_obj.get("defects"), list):
            errors.append(f"{label}: {channel} defect list is missing")
    if not isinstance(completeness.get("unquantified"), list):
        errors.append(f"{label}: audit unquantified list is missing")
    if case.get("variant") == "complete" and status != "complete":
        errors.append(f"{label}: complete artificial model has incomplete audit")
    if case.get("variant") == "complete":
        for channel in ("reaction_channel", "decay_channel"):
            if _object(completeness.get(channel)).get("defects") != []:
                errors.append(f"{label}: complete artificial model reports {channel} defects")
        if completeness.get("unquantified") != []:
            errors.append(f"{label}: complete artificial model reports unquantified audit items")
        for key in ("library_convergence_flags", "library_target_limitations"):
            if ledger.get(key) not in ([], None):
                errors.append(f"{label}: complete artificial model reports {key}")
    composition = _object(record.get("composition_wt_percent"))
    try:
        has_fe = float(composition.get("Fe", 0.0) or 0.0) > 0.0
        has_nb = float(composition.get("Nb", 0.0) or 0.0) > 0.0
    except (TypeError, ValueError, OverflowError):
        errors.append(f"{label}: native composition is malformed")
        has_fe = has_nb = False
    if case.get("variant") == "omit_fe58_target" and has_fe:
        if "positive_inventory_missing_activation_target:Fe58" not in reasons:
            errors.append(f"{label}: missing Fe58 target has no coverage downgrade reason")
        initial = coverage.get("initial_positive_inventory", [])
        if not any(_object(item).get("nuclide") == "Fe58"
                   and _object(_object(item).get("target")).get("activation_target_present") is False
                   for item in initial):
            errors.append(f"{label}: Fe58 omission lacks positive initial-inventory evidence")
    if case.get("variant") == "omit_nb94_decay" and has_nb:
        relevant = ("targets_absent_from_decay_library", "products_no_evaluated_decay_data",
                    "decay_daughters_missing")
        if not any(not (value is None or value == 0 or value == [] or value == {})
                   for key, value in omissions.items() if key in relevant):
            errors.append(f"{label}: missing Nb94 decay lacks supporting omission-ledger evidence")
        if not any(any(token in reason for token in ("targets_absent_from_decay_library",
                                                       "products_no_evaluated_decay_data",
                                                       "decay_daughters_missing")) for reason in reasons):
            errors.append(f"{label}: missing Nb94 decay lacks a downgrade reason")
    return errors


def validate_native_identity(case: dict, component: dict, generated_fixture: dict,
                              root: Path) -> list[str]:
    """Validate every basis/witness certificate and audit against generated sources."""
    errors: list[str] = []
    native = _object(_object(component).get("native"))
    variant = _object(generated_fixture.get(case.get("variant")))
    if not variant:
        return ["native identity: generated fixture variant is absent"]
    records: list[tuple[str, Any]] = []
    basis = native.get("solver_basis")
    if not isinstance(basis, list) or not basis:
        errors.append("native identity: solver basis is missing")
    else:
        records.extend((f"basis {row.get('element', '?')}", row) for row in basis if isinstance(row, dict))
    verification = _object(native.get("native_verification"))
    witnesses = verification.get("verified_witnesses")
    if not isinstance(witnesses, list) or not witnesses:
        errors.append("native identity: full witness evidence is missing")
    else:
        records.extend((f"witness {i}", row) for i, row in enumerate(witnesses) if isinstance(row, dict))
    for label, record in records:
        errors.extend(_certificate_errors(record, variant, root, label))
        errors.extend(_audit_errors(record, case, label))
    if case.get("variant") == "complete":
        evidence = native.get("coverage_evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append("native identity: complete model has no aggregate coverage evidence")
    return errors
