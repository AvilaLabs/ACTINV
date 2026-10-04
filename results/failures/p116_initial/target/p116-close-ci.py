import hashlib
import json
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'controls'))
import check_p116 as control
import check_p116_verdict as verdict

assert len(sys.argv) == 2 and re.fullmatch(r'[0-9a-f]{40}', sys.argv[1])
commit = sys.argv[1]
runs = json.loads((root / 'target/p116-ci-completed.json').read_text())
assert isinstance(runs, list) and len(runs) == 6
assert {run['workflowName'] for run in runs} == verdict.REQUIRED_WORKFLOWS
assert all(run['headSha'] == commit and run['status'] == 'completed'
           and run['conclusion'] == 'success' for run in runs)
record = {'schema': 'actinv-p116-implementation-1', 'commit_sha': commit}
for i, (relative, path) in enumerate(verdict.ARTIFACTS.items()):
    assert path.is_file() and not path.is_symlink()
    raw = path.read_bytes()
    assert control._git_blob(commit, relative) == raw
    record[f'g{i}_sha256'] = hashlib.sha256(raw).hexdigest()
assert verdict._implementation_record_ok(record)
assert verdict._ci_state(True, record, runs, True) == (True, True)
for relative, value in (('results/p116_ci_runs.json', runs),
                        ('results/p116_implementation_commit.json', record)):
    path = root / relative
    assert not path.exists() and not path.is_symlink()
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
assert verdict.derive()['verdict'] == 'P116-PASS'
print(json.dumps({'phase': 'P116', 'implementation_commit': commit,
                  'exact_commit_workflows_green': 6}, sort_keys=True))
