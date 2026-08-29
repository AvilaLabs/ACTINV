#!/usr/bin/env python3
"""Load one ACTINV extension in an isolated process and run one JSON specification."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: p16_python_runner.py EXTENSION SPEC OUTPUT")
    extension, specification, output = map(Path, sys.argv[1:])
    module_spec = importlib.util.spec_from_file_location("actinv", extension)
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load ACTINV extension {extension}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    result = json.loads(module.run(specification.read_text(encoding="utf-8")))
    output.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
