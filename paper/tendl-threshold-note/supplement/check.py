#!/usr/bin/env python3
"""Independent TCS-1 parser and quadrature; imports no primary calculation code."""
import hashlib
import json
import math
import re
import sys
from decimal import Decimal
from pathlib import Path
from scipy.integrate import quad

ROOT = Path(__file__).resolve().parents[1]


def number(s):
    return Decimal(re.sub(r'([0-9.])([+-][0-9]+)$', r'\1e\2', s.strip()))


def extract(blob, mf, zap):
    text = blob.decode('ascii').splitlines()
    block = [s[:66] for s in text if s[70:72].strip() == str(mf) and s[72:75].strip() == '16']
    records = [tuple(s[i:i+11] for i in range(0, 66, 11)) for s in block]
    at = 1
    found = []
    while at < len(records):
        h = records[at]
        nr, np = int(h[4]), int(h[5])
        ni, nv = (2*nr+5)//6, (2*np+5)//6
        laws = [int(v) for r in records[at+1:at+1+ni] for v in r if v.strip()][:2*nr]
        assert laws[-2] == np and set(laws[1::2]) == {2}
        data = [number(v) for r in records[at+1+ni:at+1+ni+nv] for v in r if v.strip()][:2*np]
        assert len(data) == 2*np
        if mf == 3 or (int(h[2]) == zap and int(h[3]) == 0):
            assert number(h[1]) < 0
            found.append(list(zip(data[::2], data[1::2])))
        at += 1+ni+nv
    assert len(found) == 1
    return found[0]


def density(energy, gaussian):
    if not gaussian:
        return 1/20e6
    # Independent normalization by numerical integration in standardized coordinates.
    norm = quad(lambda z: math.exp(-z*z/2), -28, 12, epsabs=1e-13, epsrel=1e-13)[0]
    return math.exp(-0.5*((energy-14e6)/0.5e6)**2)/(0.5e6*norm)


def integrate(points, gaussian):
    terms = []
    for (x, y), (xx, yy) in zip(points, points[1:]):
        a, b = max(float(x), 0), min(float(xx), 20e6)
        if b <= a:
            continue
        y0, y1 = float(y), float(yy)
        width = float(xx-x)
        # Integrate on a dimensionless coordinate to avoid large-energy conditioning.
        lo, hi = (a-float(x))/width, (b-float(x))/width
        val, err = quad(lambda t: ((1-t)*y0+t*y1)*density(float(x)+width*t, gaussian)*width,
                        lo, hi, epsabs=1e-28, epsrel=1e-11)
        terms.append(val)
    return math.fsum(terms)


def validate_hash(blob, expected):
    if hashlib.sha256(blob).hexdigest() != expected:
        raise ValueError('source hash mismatch')


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-13), (a, b)


def main():
    group = Path('/sys/fs/cgroup')/Path('/proc/self/cgroup').read_text().strip().split('::')[1].lstrip('/')
    assert (group/'memory.max').read_text().strip() == '6442450944'
    assert (group/'memory.swap.max').read_text().strip() == '0'
    assert (group/'pids.max').read_text().strip() == '128'
    assert (group/'cpu.max').read_text().strip() == '200000 100000'
    payload = (ROOT/'supplement/comparison.json').read_bytes()
    report = json.loads(payload)
    for name, expected in report['code_sha256'].items():
        assert hashlib.sha256((ROOT/'supplement'/name).read_bytes()).hexdigest() == expected
    assert hashlib.sha256((ROOT/'PROTOCOL.md').read_bytes()).hexdigest() == report['protocol_sha256']
    checks = []
    for row in report['cases']:
        blob = (Path(sys.argv[1])/row['file']).read_bytes()
        validate_hash(blob, row['sha256'])
        try:
            validate_hash(b'X'+blob[1:], row['sha256'])
        except ValueError:
            pass
        else:
            raise AssertionError('modified source not rejected')
        prod = extract(blob, 10, row['zap'])
        total = extract(blob, 3, row['zap'])
        assert prod[0][0] == total[0][0] and total[0][1] == 0 and prod[0][1] > 0
        corr = [(prod[0][0], Decimal(0))]+prod[1:]
        lines = blob.splitlines(keepends=True)
        idx = row['changed_field']['line_1based']-1
        assert lines[idx].decode().rstrip('\r\n') == row['changed_field']['before']
        lines[idx] = lines[idx][:11]+b' 0.000000+0'+lines[idx][22:]
        assert lines[idx].decode().rstrip('\r\n') == row['changed_field']['after']
        validate_hash(b''.join(lines), row['variant_sha256'])
        assert extract(b''.join(lines), 10, row['zap']) == corr
        exact_area = prod[0][1]*(prod[1][0]-prod[0][0])/2
        close(float(exact_area), row['removed_area_b_eV'])
        detail = {}
        for label, observed in row['folds'].items():
            gaussian = label.startswith('gaussian')
            original, corrected = integrate(prod, gaussian), integrate(corr, gaussian)
            direct_delta = integrate([prod[0], (prod[1][0], Decimal(0))], gaussian)
            for a, b in ((original, observed['original_b']), (corrected, observed['corrected_b']),
                         (direct_delta, observed['delta_b_direct']),
                         (integrate(total, gaussian), observed['unmodified_mf3_total_b'])):
                close(a, b)
            if not gaussian:
                assert math.isclose(float(exact_area/Decimal(20000000)), observed['delta_b_direct'], rel_tol=1e-9)
            close(observed['delta_over_corrected'], observed['delta_b_direct']/observed['corrected_b'])
            for key in ('original', 'corrected', 'delta'):
                xs = observed['delta_b_direct'] if key == 'delta' else observed[key+'_b']
                close(observed[key+'_rate_per_target_s'], xs*1e-24*report['flux_cm2_s'])
            detail[label] = {'original_b_quadrature': original, 'corrected_b_quadrature': corrected,
                             'delta_b_quadrature': direct_delta,
                             'max_absolute_disagreement_b': max(abs(original-observed['original_b']), abs(corrected-observed['corrected_b']), abs(direct_delta-observed['delta_b_direct']))}
        # Independent 14.1 MeV control, using Decimal interpolation.
        e = Decimal(14100000)
        for (x, y), (xx, yy) in zip(prod, prod[1:]):
            if x <= e < xx:
                v = float(y+(yy-y)*(e-x)/(xx-x))
                close(v, row['original_at_14.1MeV_b'])
                close(v, row['corrected_at_14.1MeV_b'])
                break
        else:
            raise AssertionError('point control missing')
        checks.append({'file': row['file'], 'pass': True, 'folds': detail})
    for gaussian in (False, True):
        close(integrate([(Decimal(0), Decimal(2)), (Decimal(20000000), Decimal(2))], gaussian), 2)
        close(integrate([(Decimal(0), Decimal(0)), (Decimal(20000000), Decimal(2))], gaussian), 1.4 if gaussian else 1)
    out = {'pass': True, 'comparison_sha256': hashlib.sha256(payload).hexdigest(),
           'checker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           'independent_parser': True, 'analytic_fixture_checks': True, 'hash_mutation_rejected': True,
           'tolerance': {'relative': 1e-9, 'absolute_b': 1e-13},
           'tiny_gaussian_delta_caveat': 'below absolute tolerance; no resolved subtraction claim', 'cases': checks}
    (ROOT/'supplement/check.json').write_text(json.dumps(out, indent=2)+'\n')
    print(json.dumps({'pass': True, 'cases': len(checks), 'max_disagreement_b': max(v['max_absolute_disagreement_b'] for c in checks for v in c['folds'].values())}))


if __name__ == '__main__':
    main()
