#!/usr/bin/env python3
"""P24 frozen corrected measurement definitions (D1--D4).

This module is the frozen definition layer required by the P24 protocol.
It is written and frozen before any fresh-partition numerical row is
read; the fresh tables remain sealed until the G3 authorization.

D1 -- end-of-irradiation observables: where a table prints the final
observable, the printed value is the measurement. Where a table prints
EOI activities (P17 Tables 23/25; fresh ACRR bucket Tables 41--44), the
experimental spectral index is reconstructed in the instantaneous-pulse
limit, SI = (A_alpha/A_m) * (lambda_m/lambda_alpha), with half-lives from
the pinned IRDFF-II decay archive. This is the publication's own CoESI
convention: renormalized measured activities represent the same end-of-
irradiation state for every foil, and the pulse-limit identity is what
that convention implies. Demonstrated at G1 on the consumed partition
(max residual 3.58% on the discriminator row Al27p, within that row's
printed experimental uncertainty; all other rows <= ~1%).

D2 -- evaluated-state aliases: each publication reaction label binds to
the evaluated product state by the IRDFF-II evaluation's own MF=8/10
declared level structure. A single declared product state binds the
label regardless of LFS index (the Ag109g -> Ag-110m LFS=2 case); with
multiple declared states the printed suffix selects, and an unresolved
abbreviation is ledgered ``undefined_state_alias``.

D3 -- cover and dilute validity: any cover token other than the source's
bare field label is ``unsupported_self_shielding``. Bare-field capture
(resonance-structured) rows in fields where the source states shielding
corrections were applied but not printed are ``unsupported_self_shielding``;
threshold reactions on thin foils remain dilute-scorable. No shielding or
cover transport is implemented or approximated.

D4 -- monitor and mixture definitions: the Ni58p monitor convention and
the Table 22 composite fission-foil atom fractions carry forward from
P17 verbatim for any fresh family using those mixtures.
"""
from __future__ import annotations

import math
from typing import Any


# ---------------------------------------------------------------------------
# D1 -- EOI observable reconstruction
# ---------------------------------------------------------------------------

# Monitor convention (D4): the Ni-58(n,p)Co-58 monitor is the reference
# reaction for all CoESI spectral indices in the source.
MONITOR_LABEL = "Ni58p"
MONITOR_PRODUCT_ZA = 27058
MONITOR_PRODUCT_LISO = 0


def select_decay_record(candidates: list[dict]) -> dict:
    """Frozen decay-record selection for a (ZA, LISO) product identity.

    ``candidates`` are the parsed MF=8/MT=457 records from the pinned
    IRDFF-II decay archive sharing one (ZA, LISO).  The archive carries
    older IRDF-2012 and newer IRDF-30 copies of some products; the
    larger MAT number is the later distributed record (same rule as the
    P17 held-out machinery).
    """
    if not candidates:
        raise KeyError("no decay record for product identity")
    return max(candidates, key=lambda r: int(r["mat"]))


def decay_half_life_s(product_za: int, liso: int,
                      decays_by_identity: dict[tuple[int, int], dict]) -> float:
    """Half-life in seconds for a product identity from the parsed,
    hash-pinned IRDFF-II decay archive (``(za, liso) -> selected record``)."""
    rec = decays_by_identity.get((int(product_za), int(liso)))
    if rec is None or float(rec["half_life"]) <= 0.0:
        raise KeyError(f"missing radioactive decay for ({product_za}, {liso})")
    return float(rec["half_life"])


def eoi_spectral_index_pulse(
    activity_ratio: float,
    product_half_life_s: float | None,
    monitor_half_life_s: float,
    *,
    is_fission: bool = False,
) -> float:
    """Corrected EOI spectral index in the instantaneous-pulse limit.

    ``activity_ratio`` is the measured EOI activity of the row's product
    divided by the monitor's measured EOI activity in the same (or
    renormalized baseline) operation. For a radioactive product,
    SI = ratio * lambda_m / lambda_alpha. For a fission-counted monitor
    row the numerator is a fission count, SI = ratio * lambda_m.

    This replaces the falsified P17 Amendment-1 formula, which dropped
    the lambda ratio for finite irradiations and applied a guessed
    uniform 960 s history to every case.
    """
    lam_m = math.log(2.0) / float(monitor_half_life_s)
    if is_fission:
        return float(activity_ratio) * lam_m
    if product_half_life_s is None or float(product_half_life_s) <= 0.0:
        raise ValueError("pulse-limit EOI requires a positive product half-life")
    lam_a = math.log(2.0) / float(product_half_life_s)
    return float(activity_ratio) * lam_m / lam_a


def eoi_spectral_index_finite(
    activity_ratio: float,
    product_half_life_s: float | None,
    monitor_half_life_s: float,
    irradiation_s: float,
    *,
    is_fission: bool = False,
) -> float:
    """EOI spectral index for a finite uniform-power irradiation.

    With activity at EOI ``A = R * (1 - exp(-lambda t))`` the spectral
    index ``R_product / R_monitor`` is
    ``ratio * sat_m(t) / sat_product(t)`` for a radioactive product and
    ``ratio * sat_m(t) / t`` for a fission-counted numerator (counts
    accumulate linearly).  Applied only where the source's operation
    record states a finite duration — the consumed-partition evidence
    shows the publication's renormalized convention is the pulse limit,
    so the finite branch is used only for rows whose operation record
    explicitly gives a non-pulse history.
    """
    lam_m = math.log(2.0) / float(monitor_half_life_s)
    t = float(irradiation_s)
    sat_m = -math.expm1(-lam_m * t)
    if is_fission:
        return float(activity_ratio) * sat_m / t
    if product_half_life_s is None or float(product_half_life_s) <= 0.0:
        raise ValueError("finite-history EOI requires a positive product half-life")
    lam_a = math.log(2.0) / float(product_half_life_s)
    sat_a = -math.expm1(-lam_a * t)
    return float(activity_ratio) * sat_m / sat_a


# ---------------------------------------------------------------------------
# D2 -- evaluated-state alias grammar
# ---------------------------------------------------------------------------

# Suffix -> (MT, role). The publication notation is element+mass+suffix.
SUFFIX_MT = {
    "g": 102,   # (n,gamma)
    "gg": 102,  # (n,gamma) ground-state product
    "gm": 102,  # (n,gamma) metastable product
    "m": 4,     # (n,n') inelastic to isomer
    "nm": 4,
    "n": 4,
    "2": 16,    # (n,2n)
    "2m": 16,
    "3": 17,    # (n,3n)
    "p": 103,   # (n,p)
    "pm": 103,
    "a": 107,   # (n,alpha)
    "f": 18,    # fission
}

# Product ZA from target ZA by MT (two-body kinematics, no chains).
def product_za_for_mt(target_za: int, mt: int) -> int | None:
    z, a = divmod(int(target_za), 1000)
    if mt == 102:
        return z * 1000 + a + 1
    if mt in (103,):
        return (z - 1) * 1000 + a
    if mt == 104:
        return (z - 1) * 1000 + a + 1
    if mt == 105:
        return (z - 1) * 1000 + a + 2
    if mt == 106:
        return (z - 1) * 1000 + a + 1  # He-3 in, residual Z-1,A-2
    if mt == 107:
        return (z - 2) * 1000 + a - 3
    if mt == 4:
        return target_za
    if mt == 16:
        return z * 1000 + a - 1
    if mt == 17:
        return z * 1000 + a - 2
    if mt == 18:
        return None  # fission: total channel, no single product
    return None


def decay_liso_for_binding(
    bound_lfs: int,
    decay_isomer_lisos: list[int],
    has_ground_record: bool,
) -> dict[str, Any]:
    """Decay-archive LISO leg of a bound state.

    The reaction file's LFS is a sparse level identifier; the decay
    archive's LISO is an isomer ordinal.  Rules (frozen):
      - bound LFS 0 -> decay LISO 0 when a ground record exists;
      - bound LFS > 0 -> the decay archive's isomer identities for the
        product ZA; a single isomer binds it; multiple isomers are
        ``decay_identity_ambiguous`` unless a source citation resolves
        them (never guessed).
    """
    isomers = sorted(set(int(v) for v in decay_isomer_lisos))
    if int(bound_lfs) == 0:
        if has_ground_record:
            return {"status": "bound", "decay_product_liso": 0}
        # ground class but no radioactive ground record: a single
        # radioactive isomer is the counted product (e.g. a stable
        # ground state)
        if len(isomers) == 1:
            return {"status": "bound", "decay_product_liso": isomers[0],
                    "rule": "single_decay_isomer"}
        if not isomers:
            return {"status": "no_radioactive_product"}
        return {"status": "decay_identity_ambiguous",
                "candidate_lisos": isomers}
    if len(isomers) == 1:
        return {"status": "bound", "decay_product_liso": isomers[0],
                "rule": "single_decay_isomer"}
    if not isomers:
        return {"status": "no_radioactive_isomer"}
    return {"status": "decay_identity_ambiguous",
            "candidate_lisos": isomers}


# Publication label state-suffix classes.  ``bare`` labels carry no
# explicit state request (``g``, ``2``, ``p``, ``a``, ``3``, ``f``);
# ``gg`` requests the declared ground-state partial; the isomer class
# (``gm``, ``nm``, ``m``, ``n``, ``2m``, ``pm``) requests a declared
# isomer partial.
SUFFIX_CLASS = {
    "gg": "explicit_ground",
    "gm": "isomer", "nm": "isomer", "m": "isomer", "n": "isomer",
    "2m": "isomer", "pm": "isomer",
    "g": "bare", "2": "bare", "3": "bare", "p": "bare", "a": "bare",
    "f": "bare",
}


def alias_binding(
    suffix_class: str,
    declared_states: list[int],
    product_zas: list[int],
    decay_isomer_lisos: list[int] | None = None,
    has_ground_record: bool = True,
) -> dict[str, Any]:
    """Resolve a publication state suffix against the evaluation's own
    declared MF=10 subsections.

    ``declared_states``/``product_zas`` are the (ZAP, LFS) pairs the
    evaluation declares for this (target, MT) pair.
    ``decay_isomer_lisos``/``has_ground_record`` describe the product's
    radioactive-state identities in the pinned decay archive.

    Frozen rules, validated against the publication's own calculated
    columns on the consumed partition:

      - ``bare`` suffix: zero declared states -> the MF=3 total channel;
        exactly one declared state -> that state regardless of LFS index
        (the ``Ag109g`` -> LFS=2 correction, C/E 1.42 matches the
        published fold); multiple declared states -> the MF=3 total
        channel (``Nb93g`` -> total, C/E 0.94).
      - ``explicit_ground`` (``gg``): the declared LFS=0 partial if
        present (``In113gg`` C/E 0.978), else ``undefined_state_alias``.
      - ``isomer`` class: the lowest declared LFS>0 partial
        (``In113gm``/``In115gm`` -> LFS=1), else
        ``undefined_state_alias``.
      - every MF=10 binding also carries its decay-archive LISO leg via
        ``decay_liso_for_binding``; the MF=3 total channel carries no
        product state.
    """
    decay_isomer_lisos = decay_isomer_lisos or []
    pairs = sorted(zip(declared_states, product_zas))

    def with_decay(result: dict) -> dict:
        if result["status"] != "bound":
            return result
        if result.get("channel") == "mf10_partial":
            bound_lfs = result["raw_evaluation_lfs"]
        else:
            # total-channel binding: the counted product for a bare label
            # is treated as the ground class (liso 0 when a ground record
            # exists, else the single declared isomer)
            bound_lfs = 0
        result["decay_leg"] = decay_liso_for_binding(
            bound_lfs, decay_isomer_lisos, has_ground_record)
        return result

    if suffix_class == "bare":
        if len(pairs) != 1:
            return with_decay({"status": "bound", "channel": "mf3_total",
                    "rule": "bare_suffix_" + ("no" if not pairs else "multi") + "_declared"})
        lfs, zap = pairs[0]
        return with_decay({
            "status": "bound", "channel": "mf10_partial",
            "raw_evaluation_lfs": lfs, "product_za": zap,
            "rule": "bare_suffix_single_declared_state",
        })
    if suffix_class == "explicit_ground":
        ground = [(l, z_) for l, z_ in pairs if l == 0]
        if not ground:
            return {"status": "undefined_state_alias",
                    "reason": "ground_suffix_no_declared_ground"}
        lfs, zap = ground[0]
        return with_decay({
            "status": "bound", "channel": "mf10_partial",
            "raw_evaluation_lfs": lfs, "product_za": zap,
            "rule": "ground_suffix_declared_ground",
        })
    if suffix_class == "isomer":
        isomers = [(l, z_) for l, z_ in pairs if l > 0]
        if not isomers:
            return {"status": "undefined_state_alias",
                    "reason": "isomer_suffix_no_declared_isomer"}
        lfs, zap = isomers[0]
        return with_decay({
            "status": "bound", "channel": "mf10_partial",
            "raw_evaluation_lfs": lfs, "product_za": zap,
            "rule": "isomer_suffix_lowest_isomer",
        })
    raise ValueError(f"unknown suffix class {suffix_class!r}")


# ---------------------------------------------------------------------------
# D3 -- cover and dilute validity
# ---------------------------------------------------------------------------

SUPPORTED_COVER = "bare"
UNSUPPORTED_COVERS = {"Cd", "Cdtk", "Cdtk/B4C", "Cdna"}

# Reaction classes whose bare-foil rows are dilute-scorable (threshold
# reactions, smooth cross sections): the source states shielding
# corrections were applied but not printed for finite foils, so capture
# (resonance-structured) bare rows are NOT dilute-scorable in those
# fields.
DILUTE_SCORABLE_MT = {16, 17, 103, 104, 105, 106, 107, 4, 18}
RESONANCE_STRUCTURED_MT = {102}

# Fields where the source states shielding corrections were applied to
# bare-foil capture rows without printing the factors (P17 finding:
# unshielded fold differs by ~240% on Ag-109).
SHIELDED_BARE_FIELDS = {"SPR-III central cavity", "ACRR central cavity"}

# Fields with positive evidence that bare resonance-structured rows are
# dilute (the Maxwellian folds reproduce the published calculated
# columns without correction on the consumed partition).
DILUTE_VERIFIED_FIELDS = {"LB44 Maxwellian"}


def cover_outcome(cover: str) -> str | None:
    """Return the exclusion reason for a cover token, or None if bare."""
    if cover == SUPPORTED_COVER:
        return None
    if cover in UNSUPPORTED_COVERS:
        return "unsupported_self_shielding"
    return "unsupported_self_shielding"  # unknown tokens fail closed


def dilute_scorable(mt: int, cover: str, field: str) -> tuple[bool, str | None]:
    """Whether a bare-field row may be scored as infinitely dilute.

    Non-bare covers always deny.  Bare resonance-structured (capture)
    rows are scorable only in ``DILUTE_VERIFIED_FIELDS``; fields with
    documented finite-foil shielding corrections and fields with no
    statement both deny.
    """
    reason = cover_outcome(cover)
    if reason is not None:
        return False, reason
    if int(mt) in RESONANCE_STRUCTURED_MT and field not in DILUTE_VERIFIED_FIELDS:
        return False, "unsupported_self_shielding"
    return True, None


# ---------------------------------------------------------------------------
# D4 -- composite fission-foil mixtures (Table 22, verbatim from P17)
# ---------------------------------------------------------------------------

FOIL_COMPOSITIONS: dict[str, list[tuple[int, float]]] = {
    "enriched_u": [(92235, 0.9300), (92234, 0.00981), (92236, 0.00359), (92238, 0.0566)],
    "depleted_u": [(92238, 0.9979), (92234, 0.00001), (92235, 0.00205), (92236, 0.00004)],
    "plutonium": [
        (94239, 0.869965),
        (94238, 0.0006798),
        (94240, 0.115968688),
        (94241, 0.010797),
        (94242, 0.00235936),
        (92235, 0.000199946),
        (93237, 0.00002999),
    ],
}
COMPOSITE_LABELS = {
    "U235f": "enriched_u",
    "rmleu": "enriched_u",
    "U238f": "depleted_u",
    "rmldu": "depleted_u",
    "Pu239f": "plutonium",
    "rmlpu": "plutonium",
}
