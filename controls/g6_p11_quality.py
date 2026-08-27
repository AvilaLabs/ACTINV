#!/usr/bin/env python3
"""Run and record the exact Rust/Python quality commands required by P11-G6."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g6_p11_quality.json"
COMMANDS = [
    ["cargo", "fmt", "--all", "--", "--check"],
    ["cargo", "check", "--workspace", "--all-targets", "--all-features"],
    ["cargo", "clippy", "--workspace", "--all-targets", "--all-features", "--", "-D", "warnings"],
    ["cargo", "test", "--workspace", "--all-targets", "--all-features"],
    ["python3", "-m", "compileall", "-q", "controls", "scripts"],
]


def tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.replace(str(ROOT), "<ROOT>").splitlines()[-lines:])


def main() -> None:
    environment = os.environ.copy()
    steps = []
    for command in COMMANDS:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        steps.append(
            {
                "command": " ".join(command),
                "returncode": completed.returncode,
                "stdout_tail": tail(completed.stdout),
                "stderr_tail": tail(completed.stderr),
                "pass": completed.returncode == 0,
            }
        )
    result = {
        "gate": "P11-G6 quality",
        "steps": steps,
        "pass": all(step["pass"] for step in steps),
    }
    RESULT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
