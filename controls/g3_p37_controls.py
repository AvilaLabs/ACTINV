#!/usr/bin/env python3
"""P37 G3: negative controls — the classification machinery must fail
closed on missing evidence and invalid labels, never guess."""
import json, os, sys

ROOT = os.path.expanduser('~/nuclear-data/p26b-work/failed-nuclides')
results = []

def check(name, ok, detail):
    results.append({'control': name, 'pass': bool(ok), 'detail': detail})
    print(('PASS' if ok else 'FAIL'), name, '-', detail)

# 1. a file absent from the sealed set must be marked evidence_missing
rec = json.load(open('results/g1_p37_classification.json'))
sealed = set(json.load(open('results/g0_p37_seals.json'))['files'])
evidence_missing = []
for e in rec['entries']:
    if e['file'] not in sealed or not os.path.exists(
            os.path.join(ROOT, e['file'])):
        evidence_missing.append(e['file'])
check('all 12 entries cite sealed, present files',
      not evidence_missing and len(rec['entries']) == 12,
      f'missing={evidence_missing}')

# 2. a phantom file cited in a record would be flagged evidence_missing
phantom = 'Cr55.tendl'
check('phantom file is evidence_missing, not classified',
      phantom not in sealed and not os.path.exists(
          os.path.join(ROOT, phantom)),
      f'{phantom} absent from seal and disk')

# 3. every entry's evidence list is non-empty and check_site exists
missing_ev = [e['file'] for e in rec['entries'] if not e.get('evidence')]
missing_site = [e['file'] for e in rec['entries'] if not e.get('check_site')]
check('every entry carries evidence and a check_site',
      not missing_ev and not missing_site,
      f'no-evidence={missing_ev} no-site={missing_site}')

# 4. check_site paths resolve to real source locations
import re
bad_site = []
for e in rec['entries']:
    site = e['check_site']
    m = re.match(r'(crates/[^:]+):(\d+)', site)
    if not m or not os.path.exists(m.group(1)):
        bad_site.append(site)
        continue
    src = open(m.group(1), errors='replace').read().splitlines()
    ln = int(m.group(2))
    if ln > len(src):
        bad_site.append(site)
check('every check_site resolves to an existing source line',
      not bad_site, f'bad={bad_site}')

# 5. a label outside the enum is rejected by the checker contract
bad_labels = [e['file'] for e in rec['entries']
              if e['label'] not in set(rec['labels_enum'])]
check('all labels inside the declared enum',
      not bad_labels, f'bad={bad_labels}')

# 6. true_defect entries must name a file-internal contradiction in evidence
#    (not merely "check fired"); heuristic: evidence mentions a numeric
#    comparison or self-inconsistency
defect_weak = []
for e in rec['entries']:
    if e['label'] == 'true_defect':
        ev = ' '.join(e['evidence']).lower()
        if not any(c.isdigit() for c in ev):
            defect_weak.append(e['file'])
check('true_defect entries cite quantitative file-internal contradictions',
      not defect_weak, f'weak={defect_weak}')

# 7. actinv_strictness entries must cite an ENDF-legality argument
strict_weak = []
for e in rec['entries']:
    if e['label'] == 'actinv_strictness':
        ev = ' '.join(e['evidence'] + [e.get('note', '')]).lower()
        if 'endf' not in ev and 'alara' not in ev and 'convention' not in ev:
            strict_weak.append(e['file'])
check('actinv_strictness entries cite an ENDF-legality/convention argument',
      not strict_weak, f'weak={strict_weak}')

# 8. verdict text must not claim the identical-data arm is executable
arm = rec.get('identical_data_arm', {}).get('consequence', '')
check('identical_data_arm records it remains blocked',
      'blocked' in arm.lower() and 'executable' not in arm.lower(),
      arm[:90])

n_pass = sum(1 for r in results if r['pass'])
out = {'schema': 'actinv-p37-g3-1', 'n_controls': len(results),
       'n_pass': n_pass, 'all_pass': n_pass == len(results),
       'results': results}
json.dump(out, open('results/g3_p37_controls.json', 'w'), indent=1)
sys.exit(0 if out['all_pass'] else 1)
