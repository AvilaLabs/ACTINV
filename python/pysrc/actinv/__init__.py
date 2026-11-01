"""ACTINV: open, standalone nuclide-inventory and activation solver.

The solver and its Python objects live in the native extension ``actinv._actinv``; this package
re-exports them unchanged. ``actinv.openmc`` (the OpenMC R2S adapter) is imported on request and
is the only place OpenMC is ever imported.
"""
from . import _actinv as _native

_SKIP = {"__name__", "__doc__", "__package__", "__loader__", "__spec__", "__file__", "__path__",
         "__builtins__", "__cached__"}
globals().update({k: v for k, v in vars(_native).items() if k not in _SKIP})
del _native, _SKIP
