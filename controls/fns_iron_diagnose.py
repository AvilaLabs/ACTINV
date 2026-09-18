#!/usr/bin/env python3
"""Frozen FNS-IRON-DIAG-001: independent heat reconstruction and sibling campaign."""
import argparse
from decimal import Decimal
import hashlib
import json
import math
import os
import re
from pathlib import Path
import zipfile

import endf_decay
import fns_iron as base

ROOT = base.ROOT
PROTOCOL = ROOT / 'protocols/FNS-IRON-DIAG-001.md'
AMENDMENT = ROOT / 'protocols/FNS-IRON-DIAG-001-AMENDMENT-1.md'
MEMBERS = {
    '2000exp_5min.exp': '7c95977ba08e6cd2afc15d719cf6e55e6326952630c8b22d32ec02a0ef2cedd5',
    '2000exp_5min_fluxes': 'ccfe40519346d7525c7211dfefacb13f38fe933ed302c13ce060a75e03aebf50',
    'TENDL-2017_2000exp_5min.i': 'cad6fe7a947ae98aac019532f9c3e60192b20b3ae9fe13b511471dd6c3d71480',
}


def decay_records(spec):
    records = {}
    # Primary wins; fallback fills absent keys only, including stable records.
    for path in (spec['decay']['fallback'], spec['decay']['primary']):
        for rec in endf_decay.parse_decay_file(path).values():
            records[(int(rec['za']), rec['liso'])] = rec
    return records


def reconstruct(result, records):
    rows = []
    first_mn = None
    first_time = result['steps'][1]['t_s']
    for step in result['steps'][1:]:
        contributions = {}
        max_activity_error = 0.0
        mn_cooling = None
        for item in step['inventory']:
            key = (item['Z'] * 1000 + item['A'], item['LISO'])
            name, atoms = item['nuclide'], item['atoms_per_g']
            record = records.get(key)
            reported_activity = step['activity_Bq_per_g'].get(name, 0.0)
            if record is None:
                base.require(reported_activity == 0, f'missing active decay record: {name}')
                continue
            if record['nst'] != 0 or record['half_life'] <= 0:
                continue
            lam = math.log(2) / record['half_life']
            activity = atoms * lam
            base.require(math.isfinite(activity) and activity >= 0, 'invalid reconstructed activity')
            if activity > 1e-12:
                max_activity_error = max(max_activity_error, abs(activity - reported_activity) / activity)
            energies = record['energies']
            base.require(len(energies) >= 6, 'incomplete total mean energies')
            heat = activity * sum(energies[0:6:2]) * 1.602176634e-19
            base.require(math.isfinite(heat) and heat >= 0, 'invalid reconstructed heat')
            contributions[name] = heat * 1e6
            if name == 'Mn56':
                if first_mn is None:
                    first_mn = atoms
                analytic = first_mn * math.exp(-lam * (step['t_s'] - first_time))
                mn_cooling = atoms / analytic - 1
        total = math.fsum(contributions.values()) * 1e-6
        actual = step['heat_W_per_g']['total']
        base.require(math.isclose(total, actual, rel_tol=1e-10, abs_tol=1e-18),
                     'independent decay heat reconstruction failed')
        base.require(mn_cooling is not None, 'Mn56 missing')
        rows.append({'cooling_seconds': step['t_s'] - 300,
                     'total_microW_per_g': total * 1e6,
                     'heat_relative_difference': total / actual - 1,
                     'max_activity_relative_difference_above_1e-12_Bq_per_g': max_activity_error,
                     'Mn56_cooling_relative_difference': mn_cooling,
                     'contributions_microW_per_g': dict(sorted(contributions.items(), key=lambda x: -x[1]))})
    return rows


def sibling(archive, spec):
    contents = {}
    with zipfile.ZipFile(archive) as source:
        for name, sha in MEMBERS.items():
            key = 'fns/Fe/' + name
            base.require(source.getinfo(key).file_size < 1024 * 1024, 'oversized member')
            raw = source.read(key)
            base.require(hashlib.sha256(raw).hexdigest() == sha, 'sibling source hash changed')
            contents[name] = raw.decode()
    tokens = [line.split() for line in contents['TENDL-2017_2000exp_5min.i'].splitlines()]
    for expected in (['MASS', '1.0E-3', '1'], ['FE', '100.0'],
                     ['FLUX', '1.116E+10'], ['TIME', '5.0', 'MINS']):
        base.require(expected in tokens, f'sibling deck mismatch: {expected}')
    flux = []
    for line in contents['2000exp_5min_fluxes'].splitlines():
        try:
            flux.extend(float(x) for x in line.split())
        except ValueError:
            break
    base.require(len(flux) == 710 and flux[-1] == 1, 'sibling spectrum format')
    same = flux[:709] == spec['spectrum']['flux_per_group']
    rows = []
    for line in contents['2000exp_5min.exp'].splitlines():
        t, heat, err = map(Decimal, line.split())
        base.require(all(x.is_finite() and x > 0 for x in (t, heat, err)), 'invalid measurement')
        rows.append({'cooling_seconds': float(t * 60), 'measured_microW_per_g': float(heat),
                     'reported_error_microW_per_g': float(err)})
    base.require(len(rows) == 21, 'incomplete sibling measurements')
    spec = json.loads(json.dumps(spec))
    spec['title'] = 'FNS iron diagnosis: 2000 five-minute campaign'
    spec['spectrum']['flux_per_group'] = flux[:709]
    spec['schedule'] = base.schedule(rows)
    return spec, rows, same


def score_any(result, rows):
    base.require(len(result['steps']) == len(rows) + 1, 'incomplete sibling output')
    comparison = []
    for step, row in zip(result['steps'][1:], rows):
        base.require(abs(step['t_s'] - 300 - row['cooling_seconds']) <= 1e-6, 'wrong sibling time')
        base.require(step['flux'] == 0, 'nonzero cooling flux')
        heat = step['heat_W_per_g']['total'] * 1e6
        base.require(math.isfinite(heat) and heat > 0, 'invalid sibling heat')
        comparison.append({**row, 'calculated_microW_per_g': heat,
                           'calculated_over_measured': heat / row['measured_microW_per_g']})
    ratios = [r['calculated_over_measured'] for r in comparison]
    return {'comparison': comparison, 'summary': {
        'points': len(rows), 'geometric_mean_CE': math.exp(math.fsum(map(math.log, ratios)) / len(rows)),
        'min_CE': min(ratios), 'max_CE': max(ratios),
        'points_inside_reported_error_bars': sum(abs(r['calculated_microW_per_g'] - r['measured_microW_per_g'])
                                                <= r['reported_error_microW_per_g'] for r in comparison)}}


REFERENCE_MEMBER = 'fns/Fe/TENDL-2017_1996exp_5min.out'
REFERENCE_SHA = '29b21cd8936fd99095ecc99b761b7995459c61e2f2fd49654f7b4fa537db8b26'


def reference_first_inventory(text):
    # FISPACT prints total kW for this 1 g sample; compare at exactly 66 s.
    blocks = re.split(r'1 \* \* \* TIME INTERVAL\s+', text)
    first = next(b for b in blocks if b.startswith('3 *'))
    base.require('COOLING TIME IS   6.6000E+01 SECS' in first, 'reference cooling time')
    base.require('INITIAL TOTAL MASS OF MATERIAL       1.00000E-03 kg' in first, 'reference mass')
    out = {}
    for line in first.splitlines():
        cols = line.split()
        if len(cols) == 12 and cols[0] + cols[1] in ('Mn56', 'Mn57', 'Fe53'):
            out[cols[0] + cols[1]] = {
                'atoms_per_g': float(cols[2]), 'activity_Bq_per_g': float(cols[4]),
                'heat_microW_per_g': sum(map(float, cols[5:8])) * 1e9,
                'half_life_seconds': float(cols[11])}
    base.require(len(out) == 3, 'missing reference contributors')
    return out


def compare_first_inventory(archive, result, decomposition):
    with zipfile.ZipFile(archive) as source:
        base.require(source.getinfo(REFERENCE_MEMBER).file_size < 2 * 1024 * 1024, 'oversized reference')
        raw = source.read(REFERENCE_MEMBER)
        base.require(hashlib.sha256(raw).hexdigest() == REFERENCE_SHA, 'reference hash')
    reference = reference_first_inventory(raw.decode())
    step = result['steps'][1]
    base.require(step['t_s'] == 366, 'ACTINV reference comparison time')
    rows = {}
    for name, ref in reference.items():
        atoms = next(r['atoms_per_g'] for r in step['inventory'] if r['nuclide'] == name)
        activity = step['activity_Bq_per_g'][name]
        heat = decomposition[0]['contributions_microW_per_g'][name]
        rows[name] = {'actinv': {'atoms_per_g': atoms, 'activity_Bq_per_g': activity,
                                'heat_microW_per_g': heat},
                      'fispact_rounded_printout': ref,
                      'actinv_over_fispact_atoms': atoms / ref['atoms_per_g'],
                      'actinv_over_fispact_heat': heat / ref['heat_microW_per_g'],
                      'actinv_over_fispact_mean_energy': (heat / activity) / (ref['heat_microW_per_g'] / ref['activity_Bq_per_g'])}
    return {'source_member': REFERENCE_MEMBER, 'sha256': REFERENCE_SHA,
            'cooling_seconds': 66, 'nuclides': rows,
            'caveat': 'Different activation libraries and rounded FISPACT printout; not a same-data solver test.'}


def check_recorded(recorded, fresh):
    for key in ('protocol', 'amendment', 'archive', 'data', 'sibling_members', 'independent_parser',
                'historical_comparison'):
        base.require(recorded['hashes'][key] == fresh['hashes'][key], f'diagnostic input changed: {key}')
    base.require(recorded['spectra_numerically_equal'] == fresh['spectra_numerically_equal'], 'spectrum identity changed')
    for campaign in ('1996', '2000'):
        for key, old in recorded[campaign]['summary'].items():
            new = fresh[campaign]['summary'][key]
            base.require(math.isclose(old, new, rel_tol=1e-8, abs_tol=1e-10), 'campaign summary regression')
        old_rows, new_rows = recorded[campaign]['decomposition'], fresh[campaign]['decomposition']
        base.require(len(old_rows) == len(new_rows), 'decomposition coverage changed')
        for old, new in zip(old_rows, new_rows):
            base.require(old['cooling_seconds'] == new['cooling_seconds'], 'diagnostic times changed')
            for name in ('Mn56', 'Mn57', 'Fe53'):
                base.require(math.isclose(old['contributions_microW_per_g'][name],
                                          new['contributions_microW_per_g'][name], rel_tol=1e-8, abs_tol=1e-12),
                             f'nuclide heat regression: {name}')
    old_rows = recorded['counterfactual_spectrum_swap']['ratios']
    new_rows = fresh['counterfactual_spectrum_swap']['ratios']
    base.require(len(old_rows) == len(new_rows), 'spectrum comparison coverage changed')
    for old, new in zip(old_rows, new_rows):
        for key in old:
            base.require(math.isclose(old[key], new[key], rel_tol=1e-8, abs_tol=1e-10), 'spectrum comparison regression')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, default=ROOT / 'target/fns-iron/run')
    parser.add_argument('--output', type=Path, default=ROOT / 'target/fns-iron/run/diagnosis')
    args = parser.parse_args()
    base.pinned(PROTOCOL, "07c7e5002801f2bcc4c6d9fb73a2afafb18d06196cdda6610e071a5b77aad52a")
    base.pinned(AMENDMENT, "3764db3fd2217afa23b95fe22911f81e531378b3d67d4b56e2e2b1fcde09ebe4")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = ROOT / 'target/fns-iron/fns.zip'
    spec, rows, hashes = base.prepare(archive, args.data_root.resolve(), output)
    result = json.loads((args.baseline / 'cli.json').read_text())
    receipt = json.loads((args.baseline / 'comparison.json').read_text())
    base.require(base.digest(args.baseline / 'cli.json') == receipt['execution']['raw_cli_sha256'], 'baseline hash')
    base.require(base.digest(args.baseline / 'spec.json') == receipt['execution']['spec_sha256'], 'baseline spec hash')
    base.require(json.loads((args.baseline / 'spec.json').read_text()) == spec, 'baseline spec mismatch')
    comparison, summary = base.score(result, rows)
    base.require(comparison == receipt['comparison'] and summary == receipt['summary'], 'baseline receipt mismatch')
    records = decay_records(spec)
    decomposition = reconstruct(result, records)
    sibling_spec, sibling_rows, spectra_equal = sibling(archive, spec)
    sibling_result = base.execute(ROOT / 'target/release/actinv', sibling_spec, output)
    sibling_score = score_any(sibling_result, sibling_rows)
    sibling_heat = reconstruct(sibling_result, records)
    swapped_spec = json.loads(json.dumps(spec))
    swapped_spec['title'] = 'FNS iron counterfactual: 1996 schedule with 2000 spectrum'
    swapped_spec['spectrum']['flux_per_group'] = sibling_spec['spectrum']['flux_per_group']
    swapped_output = output / 'spectrum-swap'
    swapped_output.mkdir(exist_ok=True)
    swapped_result = base.execute(ROOT / 'target/release/actinv', swapped_spec, swapped_output)
    swapped_comparison, swapped_summary = base.score(swapped_result, rows)
    swapped_heat = reconstruct(swapped_result, records)
    swapped_ratios = [{'cooling_seconds': a['cooling_seconds'],
                       'heat_with_2000_spectrum_over_1996_spectrum': b['calculated_microW_per_g'] / a['calculated_microW_per_g']}
                      for a, b in zip(comparison, swapped_comparison)]
    historical_path = ROOT / 'results/fns_tendl/Fe_1996exp_5min.json'
    historical = json.loads(historical_path.read_text())
    report = {'protocol': 'FNS-IRON-DIAG-001', 'github_run_id': os.environ.get('GITHUB_RUN_ID'),
              'github_sha': os.environ.get('GITHUB_SHA'),
              'hashes': {'protocol': base.digest(PROTOCOL), 'amendment': base.digest(AMENDMENT),
                         'swapped_cli': base.digest(swapped_output / 'cli.json'),
                         'swapped_spec': base.digest(swapped_output / 'spec.json'), 'control': base.digest(__file__),
                         'independent_parser': base.digest(endf_decay.__file__),
                         'baseline_cli': base.digest(args.baseline / 'cli.json'),
                         'sibling_cli': base.digest(output / 'cli.json'),
                         'sibling_spec': base.digest(output / 'spec.json'),
                         'binary': base.digest(ROOT / 'target/release/actinv'),
                         'historical_comparison': base.digest(historical_path), 'data': hashes,
                         'archive': base.digest(archive), 'sibling_members': MEMBERS},
              'spectra_numerically_equal': spectra_equal,
              'counterfactual_spectrum_swap': {'ratios': swapped_ratios, 'summary_against_1996': swapped_summary,
                                              'decomposition': swapped_heat},
              'first_inventory_comparison': compare_first_inventory(archive, result, decomposition),
              '1996': {'summary': summary, 'decomposition': decomposition},
              '2000': {**sibling_score, 'decomposition': sibling_heat},
              'historical_different_library_and_rounded_times': {
                  'summary': historical['summary'],
                  'actinv_contributors': historical['top_contributors_actinv'],
                  'fispact_contributors': historical['top_contributors_fispact']},
              'limits': ['Heat reconstruction shares evaluated decay data, not independent experimental truth.',
                         'Cooling check does not verify irradiation production rates.',
                         'Campaign comparison does not identify which measurement, normalization or nuclear datum is responsible.',
                         'No parameter changes, statistical confidence claim or physical accuracy gate.']}
    (output / 'diagnosis.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    recorded = ROOT / 'results/fns-iron-diagnosis/diagnosis.json'
    if recorded.exists():
        check_recorded(json.loads(recorded.read_text()), report)
    else:
        print('Initial evidence generation: no recorded diagnostic comparison yet.')
    print(json.dumps({k: report[k]['summary'] for k in ('1996', '2000')}, indent=2))


if __name__ == '__main__':
    main()
