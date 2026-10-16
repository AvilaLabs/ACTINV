#!/usr/bin/env python3
"""P143 W1 recording wrapper. Stands in for ACTINV_BIN: runs the real binary (P143_REAL_ACTINV) with the exact argv,
stdin, environment and cwd, reproduces its stdout, stderr and exit code, and for every `waste ...` and `twin ...`
invocation then calls the matching Python function on the same input file and appends one comparison record to the
JSONL file P143_LOG. The comparison never changes what the caller sees. Launch it with `python -I`.

    P143_REAL_ACTINV=/abs/actinv P143_LOG=/abs/log.jsonl python3 -I controls/p143_recorder.py waste SPEC [OUT]
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile

TIMEOUT = 3600
WASTE = {"budget": "budget", "bounds": "bounds", "composition": "composition", "intrusion-screen": "intrusion_screen"}


def mapped(args: list[str]) -> tuple[str, list[str]] | None:
    """(Python function, positional arguments after the command words) for a waste/twin invocation, else None."""
    if not args or len(args) == 2 and args[1] in ("--help", "-h"):
        return None
    if args[0] == "twin":
        return "twin", args[1:]
    if args[0] != "waste":
        return None
    rest = args[1:]
    if rest[:2] == ["composition", "solve"]:
        return "composition_solve", rest[2:]
    if rest and rest[0] in WASTE:
        return WASTE[rest[0]], rest[1:]
    return "classify", rest


def digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def hint(cli: bytes, py: bytes) -> str:
    offset = next((i for i, (a, b) in enumerate(zip(cli, py)) if a != b), min(len(cli), len(py)))
    return f"first difference at byte {offset}; lengths cli={len(cli)} python={len(py)}"


def call_python(function: str, spec: str):
    """Returns ("ok", text) or ("refused", message) or ("error", description). Anything the call writes to the
    file descriptors is diverted to a scratch file so the caller's streams stay untouched."""
    with tempfile.TemporaryFile() as sink:
        saved = [os.dup(1), os.dup(2)]
        try:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            try:
                import actinv
                api = actinv if function == "twin" else actinv.waste
                result = ("ok", getattr(api, function)(spec, text=True))
            except ValueError as error:
                result = ("refused", str(error))
            except BaseException as error:
                result = ("error", f"{type(error).__name__}: {error}")
        finally:
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
        sink.seek(0, 2)
        return result, sink.tell()


def compare(function: str, positional: list[str], code: int, out: bytes, err: bytes) -> dict:
    if code == 2:
        return {"status": "not_compared", "reason": "argv_shape"}
    if code and err.startswith(b"cannot write"):
        return {"status": "not_compared", "reason": "output_target"}
    if len(positional) not in (1, 2):
        return {"status": "mismatch", "reason": "argv_shape_accepted"}
    spec, target = positional[0], positional[1] if len(positional) == 2 else None
    (kind, value), stray = call_python(function, spec)
    record = {"py_stray_bytes": stray}
    if kind == "error":
        return dict(record, status="mismatch", reason="python_error", detail=value[:300])
    if code:
        if kind != "refused":
            return dict(record, status="mismatch", reason="cli_refused_python_succeeded")
        cli, py = err, (value + "\n").encode()
        reason = "refusal"
    else:
        if kind != "ok":
            return dict(record, status="mismatch", reason="cli_succeeded_python_refused", detail=value[:300])
        py = value.encode()
        if target is not None:
            if not os.path.isfile(target):
                return dict(record, status="mismatch", reason="output_missing")
            with open(target, "rb") as stream:
                cli = stream.read()
            reason = "success_file"
        else:
            cli = out
            py = py if py.endswith(b"\n") else py + b"\n"
            reason = "success_stdout"
    record.update(reason=reason, cli_sha256=digest(cli), py_sha256=digest(py))
    if cli == py:
        return dict(record, status="match")
    return dict(record, status="mismatch", detail=hint(cli, py))


def main() -> int:
    args = sys.argv[1:]
    real = os.environ.get("P143_REAL_ACTINV")
    if not real:
        sys.stderr.write("p143_recorder: P143_REAL_ACTINV is not set\n")
        return 127
    try:
        child = subprocess.run([real, *args], capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"p143_recorder: real binary exceeded {TIMEOUT} s\n")
        return 124
    code = child.returncode if child.returncode >= 0 else 128 - child.returncode
    sys.stdout.buffer.write(child.stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(child.stderr)
    sys.stderr.buffer.flush()
    target = mapped(args)
    log = os.environ.get("P143_LOG")
    if target and log:
        function, positional = target
        record = {"function": function, "argv": args, "cwd": os.getcwd(), "cli_exit": code}
        try:
            record.update(compare(function, positional, code, child.stdout, child.stderr))
        except BaseException as error:
            record.update(status="mismatch", reason="recorder_error", detail=f"{type(error).__name__}: {error}"[:300])
        try:
            with open(log, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            pass  # never alter the caller's streams; the checker sees the missing record
    return code


if __name__ == "__main__":
    raise SystemExit(main())
