"""Default data files and ``actinv`` command for ``actinv.openmc``.

Kept apart from ``actinv/openmc.py`` so it imports, and is testable, without OpenMC.
"""
import json
import os
import sys
import sysconfig
from pathlib import Path


def data_root(env=None, cwd=None):
    """Installed-data root, as the CLI resolves it: ``$ACTINV_DATA_DIR``, else ``./actinv-data``."""
    env = os.environ if env is None else env
    value = env.get("ACTINV_DATA_DIR")
    root = Path(value) if value else Path("actinv-data")
    return root if root.is_absolute() else Path(cwd if cwd is not None else os.getcwd()) / root


def embedded_catalog():
    """The data catalog compiled into this ``actinv`` build, as a dict."""
    from . import _catalog_json
    return json.loads(_catalog_json())


def _default_artifact(catalog, role):
    bundle = next(b for b in catalog["bundles"] if b["id"] == catalog["default_bundle"])
    by_id = {a["id"]: a for a in catalog["artifacts"]}
    for artifact_id in bundle["artifacts"]:
        if by_id[artifact_id]["role"] == role:
            return by_id[artifact_id]
    raise ValueError(f"default data bundle {bundle['id']!r} has no {role} artifact")


def default_data(roles=("activation-library", "decay-primary", "decay-fallback"), catalog=None, root=None):
    """Paths of the default bundle's artifacts for ``roles``, as ``{role: str path}``.

    ``catalog`` and ``root`` default to the embedded catalog and ``data_root()``. Raises
    ``FileNotFoundError`` naming ``actinv data fetch`` when a file is not installed.
    """
    catalog = embedded_catalog() if catalog is None else catalog
    base = (data_root() if root is None else Path(root)) / f"v{catalog['catalog_version']}"
    paths = {role: base / _default_artifact(catalog, role)["path"] for role in roles}
    missing = [str(p) for p in paths.values() if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            f"ACTINV data catalog v{catalog['catalog_version']} is not installed: missing "
            + ", ".join(missing)
            + ". Run `actinv data fetch` in the folder that should hold actinv-data/ (or set "
            "ACTINV_DATA_DIR), or pass library=, decay_primary= and decay_fallback= explicitly.")
    return {role: str(p) for role, p in paths.items()}


def default_command(scripts_dir=None, executable=None):
    """Command list that runs the ``actinv`` CLI shipped with this installed package.

    Prefers the console script the wheel installed beside the running interpreter; otherwise runs
    the package's own entry point through that interpreter, so it is always this package's build.
    """
    interpreter = Path(sys.executable if executable is None else executable)
    scripts = Path(sysconfig.get_path("scripts") if scripts_dir is None else scripts_dir)
    for folder in (scripts, interpreter.parent):
        for name in ("actinv.exe", "actinv"):
            if (folder / name).is_file():
                return [str(folder / name)]
    return [str(interpreter), "-c", "import actinv; actinv._cli()"]
