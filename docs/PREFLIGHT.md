# Release preflight

`python scripts/preflight.py` checks a release candidate exported from an exact
Git snapshot. With staged changes it uses the index (and refuses unstaged
tracked changes); otherwise `--snapshot head` is used. Untracked files such as
local corpus material and `paper/` are never copied into the check directory.
The manifest is compared directly to the selected tree; the command never
refreshes it.

Full mode is intentionally bounded and builds its wheel and desktop binary
from the exported snapshot (so artifacts cannot come from another checkout):

```text
python scripts/preflight.py
```

It runs the repository-wide Rust format/check/Clippy/test gates, builds and
installs a wheel into a disposable virtual environment and runs the Python
object regression there. The wheel smoke creates its own clean nested venv;
both environments use the built wheel rather than the source checkout. It
also builds and runs the desktop model smoke.
Pass `--require-native` to make the desktop native-rendering check mandatory;
without it, model success is reported while rendering remains evidence only.
Cargo is forced offline and locked; the only build artifacts are temporary.
The selected Python environment must already provide maturin and NumPy. The
disposable venv inherits system site-packages so that NumPy is available
without a network install; the wheel is installed with `--no-deps`. Python
checks are executed by the venv's interpreter with `PYTHONPATH` and
`PYTHONHOME` cleared, and the venv's NumPy import is checked before cargo gates.

On Linux, preflight fails closed unless the process is in cgroup v2 with an
effective finite `memory.max` of at most 6 GiB and `pids.max` of at most 128.
Ancestor cgroup limits count toward the effective limit. To run it safely,
use the suggested bounded scope (which also limits compiler thread counts):

```text
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env CARGO_BUILD_JOBS=1 RUST_TEST_THREADS=1 RAYON_NUM_THREADS=2 python3 scripts/preflight.py
```

Place the temporary checkout and build artifacts on disk-backed storage when
`/tmp` is a small or memory-backed mount. For example, create
`target/preflight-tmp` in the repository and prefix the command with
`TMPDIR="$PWD/target/preflight-tmp"`.

For a bounded local edit check, use `--mode quick`; it runs snapshot,
manifest, format, and check only and explicitly reports the remaining checks
as skipped. Quick mode is not a release sign-off.
