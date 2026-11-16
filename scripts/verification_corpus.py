#!/usr/bin/env python3
"""Build tiny synthetic seeds; never fetch or copy nuclear-data libraries."""
from pathlib import Path
import io
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def seeds() -> dict[str, dict[str, bytes]]:
    def record(fields: list[str]) -> bytes:
        return ("".join(f"{value:>11}" for value in fields) + " 125 1451    1\n").encode()
    fixture = (ROOT / "verification/fuzz/seeds/npz/small-library.npz").read_bytes()
    npz = {"small-library.npz":fixture}
    with zipfile.ZipFile(io.BytesIO(fixture)) as archive:
        for selector,name in enumerate(("rows.npy","sig.npy","bounds.npy"),1):
            npz[f"member-{name}"] = bytes([selector])+archive.read(name)
    return {
        "spec": {"example.json": (ROOT / "examples/fns_fe_5min.json").read_bytes(),
                 "unknown-field.json": b'{"spec":"actinv-spec-1","typo":true}'},
        "endf": {"number": b" 1.234567+3", "list": record(["0","0","0","0","6","0"])+record(["1","2","3","4","5","6"]),
                 "oversized-count": record(["0","0","0","0","2147483647","0"])},
        "npz": npz,
    }


if __name__ == "__main__":
    for family, entries in seeds().items():
        directory = ROOT / "target/verification/corpus" / family
        directory.mkdir(parents=True, exist_ok=True)
        for name, data in entries.items():
            (directory / name).write_bytes(data)
