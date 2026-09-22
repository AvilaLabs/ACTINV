#!/usr/bin/env python3
"""TCS-1: exact one-field intervention and analytic spectrum folds. No solver imports."""
import hashlib
import json
import math
import sys
from pathlib import Path
import reproduce_tendl2025_thresholds as source

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_SHA = '7b0cc7abccd94ec93aeae3fc4ecd320f66a710b1cc6eb386cf69f553be156f06'
UPPER = 20e6
MU, SD = 14e6, 0.5e6


def sha(data):
    return hashlib.sha256(data).hexdigest()


def limits():
    group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::')[1].lstrip('/')
    values = {n: (group / n).read_text().strip() for n in ('memory.max', 'memory.swap.max', 'pids.max', 'cpu.max')}
    assert values == {'memory.max': '6442450944', 'memory.swap.max': '0', 'pids.max': '128', 'cpu.max': '200000 100000'}, values
    return values


def read_table(raw, mf, zap):
    records = [(i, s) for i, s in enumerate(raw.decode('ascii').splitlines(), 1)
               if int(s[70:72]) == mf and int(s[72:75]) == 16]
    count = 1 if mf == 3 else int(records[0][1][44:55])
    index = 1
    matches = []
    for _ in range(count):
        start = index
        h, pts, index = source.tab1(records, index)
        if mf == 3 or (int(h[2]), int(h[3])) == (zap, 0):
            nr = int(h[4])
            ints = []
            for _, line in records[start + 1:start + 1 + (2 * nr + 5) // 6]:
                ints.extend(source.fields(line))
            assert all(int(x) == 2 for x in ints[1:2*nr:2]), 'only lin-lin permitted'
            assert source.real(h[1]) < 0
            pairs = [(float(p[0]), float(p[1])) for p in pts]
            assert all(a[0] <= b[0] for a, b in zip(pairs, pairs[1:]))
            assert pairs[-1][0] >= UPPER
            matches.append(pairs)
    assert len(matches) == 1 and index == len(records)
    return matches[0]


def mass(a, b):
    za, zb = (a-MU)/SD, (b-MU)/SD
    if za >= 0:
        return 0.5 * (math.erfc(za/math.sqrt(2))-math.erfc(zb/math.sqrt(2)))
    if zb <= 0:
        return 0.5 * (math.erfc(-zb/math.sqrt(2))-math.erfc(-za/math.sqrt(2)))
    return 0.5 * (math.erf(zb/math.sqrt(2))-math.erf(za/math.sqrt(2)))


def segment(a, b, ya, yb, spectrum):
    if spectrum == 'uniform_0_20MeV':
        return (b-a)*(ya+yb)/2/UPPER
    z0, z1 = (a-MU)/SD, (b-MU)/SD
    probability = mass(a, b)
    first = SD/math.sqrt(2*math.pi)*(math.exp(-z0*z0/2)-math.exp(-z1*z1/2))
    slope = (yb-ya)/(b-a)
    return ((ya+slope*(MU-a))*probability+slope*first)/mass(0, UPPER)


def fold(points, spectrum):
    parts = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        a, b = max(0, x0), min(UPPER, x1)
        if b > a:
            slope = (y1-y0)/(x1-x0)
            parts.append(segment(a, b, y0+slope*(a-x0), y0+slope*(b-x0), spectrum))
    return math.fsum(parts)


def point(points, energy):
    exact = [y for x, y in points if x == energy]
    if exact:
        return exact[-1]
    for (a, ya), (b, yb) in zip(points, points[1:]):
        if a < energy < b:
            return ya+(yb-ya)*(energy-a)/(b-a)
    raise ValueError('point outside source domain')


def main():
    enforced = limits()
    assert sha((ROOT/'PROTOCOL.md').read_bytes()) == PROTOCOL_SHA
    folder = Path(sys.argv[1])
    rows = []
    for case in source.CASES:
        proof = source.inspect(folder, case)
        name, zap, _, _, _ = case
        raw = (folder/name).read_bytes()
        points = read_table(raw, 10, zap)
        total = read_table(raw, 3, zap)
        assert points[0][0] == total[0][0] and total[0][1] == 0
        lines = raw.splitlines(keepends=True)
        i = proof['records']['10']['line_number']-1
        original_record = lines[i].decode('ascii').rstrip('\r\n')
        assert source.real(lines[i][11:22].decode('ascii')) == source.real(case[3])
        lines[i] = lines[i][:11]+b' 0.000000+0'+lines[i][22:]
        variant = b''.join(lines)
        assert len(variant) == len(raw)
        changed = [j for j, (a, b) in enumerate(zip(raw, variant)) if a != b]
        record_start = sum(map(len, lines[:i]))
        assert changed and all(record_start+11 <= j < record_start+22 for j in changed)
        corrected = read_table(variant, 10, zap)
        assert corrected[0] == (points[0][0], 0) and corrected[1:] == points[1:]
        assert read_table(variant, 3, zap) == total
        a, amplitude = points[0]
        b = points[1][0]
        folds = {}
        for spectrum in ('uniform_0_20MeV', 'gaussian_14MeV_sd0.5MeV'):
            original, modified = fold(points, spectrum), fold(corrected, spectrum)
            delta = segment(a, b, amplitude, 0, spectrum)
            assert original >= 0 and modified >= 0 and delta >= 0
            assert math.isclose(original, modified+delta, rel_tol=1e-12, abs_tol=1e-13)
            folds[spectrum] = {
                'original_b': original, 'corrected_b': modified,
                'delta_b_direct': delta, 'delta_over_corrected': delta/modified,
                'original_rate_per_target_s': original*1e-10,
                'corrected_rate_per_target_s': modified*1e-10,
                'delta_rate_per_target_s': delta*1e-10,
                'unmodified_mf3_total_b': fold(total, spectrum)}
        rows.append({**proof, 'variant_sha256': sha(variant),
                     'changed_field': {'line_1based': i+1, 'columns_1based': [12, 22],
                                       'before': original_record, 'after': lines[i].decode('ascii').rstrip('\r\n')},
                     'next_energy_eV': b, 'next_ordinate_b': points[1][1],
                     'removed_area_b_eV': amplitude*(b-a)/2,
                     'original_at_14.1MeV_b': point(points, 14.1e6),
                     'corrected_at_14.1MeV_b': point(corrected, 14.1e6),
                     'folds': folds})
    out = {'study': 'TCS-1', 'date': '2026-09-14', 'protocol_sha256': PROTOCOL_SHA,
           'method': 'analytic linear-segment integration; single-ordinate experimental variant',
           'flux_cm2_s': 1e14, 'gaussian': {'mean_eV': MU, 'sd_eV': SD, 'domain_eV': [0, UPPER]},
           'scope': 'rate sensitivity only; no inventory or qualified corrected-library claim',
           'enforced_cgroup': enforced,
           'code_sha256': {p.name: sha(p.read_bytes()) for p in (Path(__file__), Path(source.__file__))},
           'cases': rows}
    (ROOT/'supplement/comparison.json').write_text(json.dumps(out, indent=2)+'\n')
    for row in rows:
        print(row['file'], {k: (v['original_b'], v['corrected_b'], v['delta_over_corrected']) for k, v in row['folds'].items()})


if __name__ == '__main__':
    main()
