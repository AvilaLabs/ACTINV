import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "controls"))
import check_p114 as control
import check_p114_verdict as verdict


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def retain_log(source, destination, expected):
    assert source.is_file() and not source.is_symlink()
    assert sha(source) == expected
    source.resolve(strict=True).relative_to(root.resolve(strict=True))
    assert not destination.exists() and not destination.is_symlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    assert sha(destination) == expected


assert len(verdict.REQUIRED_GATES) == 28
gates = {}
for name in sorted(verdict.REQUIRED_GATES):
    receipt_relative = f"results/quality/p114/{name}.json"
    receipt_path = root / receipt_relative
    assert receipt_path.is_file() and not receipt_path.is_symlink(), name
    receipt = json.loads(receipt_path.read_text())
    assert receipt["phase"] == "P114" and receipt["gate"] == name
    assert receipt["status"] == "completed" and receipt["error"] is None
    assert type(receipt["child_exit_code"]) is int and receipt["child_exit_code"] == 0
    assert receipt["cwd"] == "."
    assert receipt["log_path"] == f"target/p114-{name}.log"
    retained_relative = f"results/quality/p114/{name}.log"
    retain_log(root / receipt["log_path"], root / retained_relative, receipt["log_sha256"])
    gate = {"name": name, "exit_code": receipt["child_exit_code"],
            "argv": receipt["argv"], "log_path": retained_relative,
            "log_sha256": receipt["log_sha256"], "resources": receipt["resources"],
            "receipt_path": receipt_relative, "receipt_sha256": sha(receipt_path)}
    assert verdict._receipt_ok(name, gate), name
    gates[name] = gate

test_text = (root / gates["workspace_tests"]["log_path"]).read_text()
summaries = re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored", test_text)
assert summaries
tests = {name: sum(int(row[index]) for row in summaries)
         for index, name in enumerate(("passed", "failed", "ignored"))}
assert tests["passed"] > 474 and tests["failed"] == 0 and tests["ignored"] == 2

inspection = root / "target/p114-resource-limits.log"
limits = json.loads(inspection.read_text().splitlines()[-1])
assert limits == {"memory.max": "6442450944", "memory.swap.max": "0",
                  "pids.max": "128", "cpu.max": "200000 100000"}
inspection_relative = "results/quality/p114/resource_inspection.log"
retain_log(inspection, root / inspection_relative, sha(inspection))
g0 = json.loads((root / "results/g0_p114_twin_waste.json").read_text())
assert g0["pass"] is True and type(g0["repair_rounds"]) is int and g0["repair_rounds"] == 0
sources = control._current_rust_source_hashes()
assert len(sources) == 100 and sources == g0["inherited_rust_sha256"]
for path in ("results/g1_p114_twin_waste.json", "results/g2_p114_twin_waste.json"):
    assert json.loads((root / path).read_text())["pass"] is True
binary = root / "target/build/release/actinv"
assert binary.is_file() and not binary.is_symlink()
quality = {
    "schema": "actinv-p114-quality-1", "phase": "P114", "pass": True,
    "repair_rounds": 0, "ci_status": "pending exact-commit implementation CI",
    "gates": gates, "workspace_tests": tests, "production_rust_sha256": sources,
    "resource_limits": verdict.RESOURCES,
    "resource_inspection": {"exit_code": 0, "log_path": inspection_relative,
                            "log_sha256": sha(inspection), "limits": limits},
    "release_build": {"exit_code": 0, "binary_sha256": sha(binary),
                      "log_sha256": gates["release_build"]["log_sha256"]},
}
assert verdict._quality_ok(quality, g0)
assert verdict._source_commit_matches(quality, None)
output = root / "results/g3_p114_quality.json"
assert not output.exists()
output.write_text(json.dumps(quality, sort_keys=True, indent=2) + "\n")
print(json.dumps({"quality_sha256": sha(output), "gate_count": len(gates),
                  "rust_source_count": len(sources), "workspace_tests": tests}, sort_keys=True))
