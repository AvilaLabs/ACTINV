import hashlib
import json
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'controls'))
import check_p116 as control
import check_p116_verdict as verdict

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def safe(relative):
    path = root / relative
    assert isinstance(relative, str) and not Path(relative).is_absolute()
    assert '..' not in Path(relative).parts and Path(relative).as_posix() == relative
    cursor = root
    for part in Path(relative).parts:
        cursor = cursor / part
        assert not cursor.is_symlink(), relative
    assert path.is_file(), relative
    path.resolve(strict=True).relative_to(root.resolve(strict=True))
    return path

archive = root / 'results/failures/p116_initial'
assert not archive.exists() and not archive.is_symlink()
assert control._current_round_is_zero()
assert not (root / 'results/g2_p116_twin_waste.json').exists()
assert not (root / 'results/g3_p116_quality.json').exists()
assert not (root / 'results/p116_implementation_commit.json').exists()
assert not (root / 'results/p116_ci_runs.json').exists()
g0 = json.loads(safe('results/g0_p116_twin_waste.json').read_text())
g1 = json.loads(safe('results/g1_p116_twin_waste.json').read_text())
assert g0['pass'] is True and type(g0['repair_rounds']) is int and g0['repair_rounds'] == 0
assert g1['pass'] is False and g1['request_count'] == 35 and g1['component_target_count'] == 138
assert len(g1['failures']) == 186
assert all('row_fractions[' in item and '.limit: integer value/type differs' in item for item in g1['failures'])
assert [name for name, ok in g1['mutations_rejected'].items() if not ok] == ['integer_count_type']
assert [name for name, ok in g1['refusal_controls']['checks'].items() if not ok] == ['unknown_external_field']
assert g1['repeat_byte_identical'] is True
sources = control._current_rust_source_hashes()
assert len(sources) == 100 and sources == g0['inherited_rust_sha256']
assert sources == control._checkpoint_source_hashes()
source_paths = set(control.CONTROL_FILES) | set(sources) | {
    '.github/workflows/ci.yml', 'protocols/protocol_hash.txt',
    'docs/ROADMAP.md', 'ledger.md', 'docs/history/sessions/P116.md'}
source_hashes = {relative: sha(safe(relative)) for relative in sorted(source_paths)}
assert {relative: source_hashes[relative] for relative in control.CONTROL_FILES} == g0['control_sha256']
paths = set()
receipts = {}
receipt_paths = sorted((root / 'results/quality/p116').glob('*.json'))
assert len(receipt_paths) == 25
for path in receipt_paths:
    receipt = json.loads(path.read_text())
    name = receipt['gate']
    assert name in verdict.REQUIRED_GATES and name not in receipts
    assert receipt['schema'] == 'actinv-roadmap-gate-receipt-1'
    assert receipt['phase'] == 'P116' and receipt['status'] == 'completed'
    assert receipt['cwd'] == '.' and receipt['error'] is None
    assert type(receipt['child_exit_code']) is int and receipt['child_exit_code'] == 0
    assert receipt['log_path'] == f'target/p116-{name}.log'
    assert sha(safe(receipt['log_path'])) == receipt['log_sha256']
    receipts[name] = 0
    paths.update((path.relative_to(root).as_posix(), receipt['log_path'], f'target/p116-{name}.scope.log'))
paths.update(('results/g0_p116_twin_waste.json', 'results/g1_p116_twin_waste.json',
    'target/p116-g0-seal.log', 'target/p116-g0-replay.log', 'target/p116-g1.log',
    'target/p116-resource-limits.log', 'target/p116-write-quality.py', 'target/p116-close-ci.py',
    'target/p116-preserve-initial.py',
    'target/p116-controls/g1/refusals/unknown_external_field/twin.json',
    'target/p116-controls/g1/refusals/unknown_external_field/mesh.ndjson',
    'target/p116-controls/g1/refusals/unknown_external_field/out.json',
    'target/p116-controls/g1/two_table_mixture/out.json'))
retained = {}
for relative in sorted(paths):
    source = safe(relative)
    target = archive / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    assert not target.exists()
    shutil.copyfile(source, target)
    assert sha(target) == sha(source)
    retained[target.relative_to(root).as_posix()] = sha(source)
discovery = {'schema': 'actinv-p116-initial-failure-1', 'phase': 'P116',
    'gate': 'G1', 'exit_code': 1, 'argv': ['python3', 'controls/check_p116.py', '--g1-only'],
    'cwd': '.', 'protocol_sha256': control.PROTOCOL_SHA256, 'repair_rounds': 0,
    'g0_sealed': True, 'g0_seal_exit_code': 0, 'g0_replay_exit_code': 0,
    'g0_sha256': sha(root / 'results/g0_p116_twin_waste.json'),
    'g1_executed': True, 'g1_sha256': sha(root / 'results/g1_p116_twin_waste.json'),
    'failed_log_path': 'results/failures/p116_initial/target/p116-g1.log',
    'failed_log_sha256': sha(root / 'target/p116-g1.log'),
    'g2_executed': False, 'implementation_record_present': False, 'ci_record_present': False,
    'comparison_failure_count': 186, 'failed_mutation': 'integer_count_type',
    'failed_refusal': 'unknown_external_field', 'repeat_byte_identical': True,
    'source_file_count': len(source_hashes), 'rust_source_file_count': len(sources),
    'retained_file_count': len(retained), 'observed_gate_count': len(receipts),
    'observed_gate_exit_codes': receipts, 'source_sha256': source_hashes,
    'files_sha256': retained}
output = archive / 'discovery.json'
output.write_text(json.dumps(discovery, sort_keys=True, indent=2) + '\n')
assert len(list(archive.rglob('*'))) > len(retained)
print(json.dumps({'discovery_sha256': sha(output), 'source_file_count': len(source_hashes),
    'rust_source_file_count': len(sources), 'retained_file_count': len(retained),
    'actual_successful_quality_gate_count': len(receipts), 'g1_exit_code': 1,
    'g0_sha256': discovery['g0_sha256'], 'g1_sha256': discovery['g1_sha256'],
    'failed_log_sha256': discovery['failed_log_sha256']}, sort_keys=True))
