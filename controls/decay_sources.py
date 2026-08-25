"""P3-G1: merged decay table — primary ENDF/B-VIII.0, fallback JEFF-3.3 for nuclides absent from the primary; the source
is recorded per nuclide. Exposes merged_records() used by chain.py and decayheat.py."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from endf_decay import parse_decay_file
PRIMARY = os.path.expanduser("~/nuclear-data/endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat")
FALLBACK = os.path.expanduser("~/nuclear-data/jeff-3.3-decay/bulk/jeff-3-3_decay.dat")
_cache = None
def merged_records():
    """Returns (records dict keyed by synthetic id, provenance dict (za, liso) -> source name, stats)."""
    global _cache
    if _cache is None:
        prim = parse_decay_file(PRIMARY); have = {(int(round(r["za"])), r["liso"]) for r in prim.values()}
        prov = {(int(round(r["za"])), r["liso"]): "ENDF/B-VIII.0" for r in prim.values()}
        recs = {("P", m): r for m, r in prim.items()}; added = []
        if os.path.exists(FALLBACK):
            fb = parse_decay_file(FALLBACK)
            for m, r in fb.items():
                key = (int(round(r["za"])), r["liso"])
                if key not in have: recs[("F", m)] = r; prov[key] = "JEFF-3.3"; added.append(key)
        _cache = (recs, prov, {"primary": PRIMARY, "n_primary": len(prim), "fallback": FALLBACK if os.path.exists(FALLBACK) else None, "n_added_from_fallback": len(added), "added": sorted(added)})
    return _cache
