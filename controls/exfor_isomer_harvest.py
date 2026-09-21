#!/usr/bin/env python3
"""EXFOR isomer-split harvest for stable-foil-isotope channels.

Reads results/exfor_isomer_channels.json (TENDL-2017 isomer-resolved production
channels on stable isotopes of the FNS foil elements, MT in {4,16,102}).
Fetches https://nds.iaea.org/exfor/x4dat?Target=..&Reaction=..&Quantity=SIG
per channel, caches raw text under target/exfor-harvest/, and records which
datasets carry isomer-resolved products (-M, -G, -M1, -M2 flags or
isomer SIG/RAT rows) into results/exfor_isomer_coverage.json.

Resumable: cached fetches are skipped. Bounded: at most WORKERS concurrent
requests with a delay between launches.
"""
import concurrent.futures
import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = json.load(open(ROOT / "results/exfor_isomer_channels.json"))
CACHE = ROOT / "target/exfor-harvest"
OUT = ROOT / "results/exfor_isomer_coverage.json"
WORKERS = 3
DELAY = 0.4
ISO_RE = re.compile(r"#REACTION\s+(\S+)")


def fetch(chan):
    tgt, rxn = chan["target"], chan["exfor_rxn"]
    fn = CACHE / f"{tgt}_{rxn.replace(',','_')}.txt"
    if fn.exists() and fn.stat().st_size > 0:
        return fn
    url = f"https://nds.iaea.org/exfor/x4dat?Target={tgt}&Reaction={rxn}&Quantity=SIG&op=txt"
    req = urllib.request.Request(url, headers={"User-Agent": "actinv-isomer-census/1.0"})
    try:
        body = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    except Exception as e:
        return {"error": str(e)}
    fn.write_text(body)
    return fn


def classify(chan, text):
    reactions = ISO_RE.findall(text)
    iso = [r for r in reactions if re.search(r"-(M\d?|G)(/|,|\)|,M|T/)", r) or "/RAT" in r]
    return {
        "target": chan["target"], "mt": chan["mt"], "lfs": chan["lfs"],
        "n_datasets": len(re.findall(r"#SUBENT", text)),
        "n_isomer_resolved": len(iso),
        "isomer_reactions": sorted(set(iso)),
    }


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    results = []
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
        futs = {}
        for chan in CHANNELS:
            futs[ex.submit(fetch, chan)] = chan
            time.sleep(DELAY)
        for fut in concurrent.futures.as_completed(futs):
            chan = futs[fut]
            got = fut.result()
            if isinstance(got, dict):
                results.append({**{k: chan[k] for k in ("target", "mt", "lfs")}, "error": got["error"]})
                continue
            results.append(classify(chan, got.read_text()))
    OUT.write_text(json.dumps(sorted(results, key=lambda r: (r["target"], r["mt"])), indent=1))
    covered = [r for r in results if r.get("n_isomer_resolved", 0) > 0]
    print(f"{len(results)} channels checked; {len(covered)} have isomer-resolved EXFOR datasets")
    for r in covered:
        print(f"  {r['target']:8s} MT{r['mt']:3d} resolved={r['n_isomer_resolved']} of {r['n_datasets']} datasets")


if __name__ == "__main__":
    main()
