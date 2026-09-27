"""Convenient Python objects over the unchanged ACTINV JSON contract."""
import copy as _copy
import json as _json
import os as _os
from pathlib import Path as _Path


class Material(dict):
    """Composition with explicit basis; values are never silently converted."""
    def __init__(self, composition, *, mass_g=1.0, basis="wt_percent"):
        super().__init__(composition=dict(composition), mass_g=mass_g, basis=basis)


class Spectrum(dict):
    """Group-integrated values, with explicit ordering and optional total flux."""
    def __init__(self, values, *, structure="fispact-709", total=None,
                 descending=False, boundaries_eV=None):
        super().__init__(structure=structure, flux_per_group=list(values), descending=descending)
        if total is not None:
            self["total"] = total
        if boundaries_eV is not None:
            self["boundaries_eV"] = list(boundaries_eV)


class Schedule(list):
    """Ordered duration/multiplier segments. Methods return self for chaining."""
    def irradiate(self, duration, multiplier=1.0, *, feed=None, removal=None):
        step = {"dt": str(duration), "flux": multiplier}
        self._terms(step, feed, removal)
        self.append(step)
        return self

    def cool(self, duration, *, feed=None, removal=None):
        step = {"dt": str(duration), "flux": 0.0}
        self._terms(step, feed, removal)
        self.append(step)
        return self

    @staticmethod
    def _terms(step, feed, removal):
        if feed:
            step["feed"] = {str(k): float(v) for k, v in dict(feed).items()}
        if removal:
            step["removal"] = {str(k): float(v) for k, v in dict(removal).items()}

    def feed(self, rates):
        """Constant feed on the last segment: nuclide -> atoms s^-1 g^-1."""
        if not self:
            raise ValueError("feed requires an existing segment")
        self[-1].setdefault("feed", {}).update(
            {str(k): float(v) for k, v in dict(rates).items()})
        return self

    def remove(self, rates):
        """First-order removal on the last segment: nuclide or element -> s^-1."""
        if not self:
            raise ValueError("removal requires an existing segment")
        self[-1].setdefault("removal", {}).update(
            {str(k): float(v) for k, v in dict(rates).items()})
        return self


def _paths(value, base=None):
    value = _copy.deepcopy(value)
    refs = [(value.get("library", {}), "path"), (value.get("decay", {}), "primary"),
            (value.get("decay", {}), "fallback")]
    for section, field in (("photon", "response"), ("uncertainty", "covariance"),
                           ("radiological", "table"), ("damage", "table")):
        refs.append(((value.get(section) or {}).get(field) or {}, "path"))
    refs.extend((ref, "path") for ref in (value.get("fission_yields") or {}).get("files", []))
    for ref, key in refs:
        path = ref.get(key)
        if path:
            path = _Path(path)
            if base is not None and not path.is_absolute():
                path = _Path(base) / path
            ref[key] = str(path)
    return value


class Problem(dict):
    """Editable mapping; use from_file to resolve references against a project folder."""
    def __init__(self, value=None, **fields):
        super().__init__(value or {}, **fields)
        self.setdefault("spec", "actinv-spec-1")

    @classmethod
    def from_file(cls, path, *, base=None):
        path = _Path(path)
        return cls(_paths(_json.loads(path.read_text(encoding="utf-8")),
                          path.resolve().parent if base is None else _Path(base).resolve()))

    @classmethod
    def example(cls, data_dir="actinv-data"):
        return cls(_json.loads(_example_json(_os.fspath(data_dir))))

    def to_json(self):
        return _json.dumps(_paths(self), allow_nan=False, indent=2)

    def save(self, path):
        # Pin relative references to the caller's current directory before relocating.
        _Path(path).write_text(_json.dumps(_paths(self, _Path.cwd()), allow_nan=False, indent=2)+"\n", encoding="utf-8")

    def validate(self):
        return validate(self.to_json())

    def run(self):
        return solve(self)


class Result(dict):
    """Complete result mapping plus common analysis helpers; units stay explicit."""
    @property
    def steps(self):
        return self["steps"]

    @property
    def ledger(self):
        return self["ledger"]

    @property
    def certificate(self):
        return self["certificate"]

    def activity(self, nuclide=None):
        return [(s["t_s"], sum(s["activity_Bq_per_g"].values()) if nuclide is None
                 else s["activity_Bq_per_g"].get(nuclide, 0.0)) for s in self.steps]

    def heat(self):
        return [(s["t_s"], s["heat_W_per_g"]["total"]) for s in self.steps]

    def save(self, path):
        _Path(path).write_text(_json.dumps(self, allow_nan=False, indent=2)+"\n", encoding="utf-8")


def solve(problem):
    """Solve a mapping or problem Path; return Result. Legacy run(str)->str remains available."""
    if isinstance(problem, (_os.PathLike, str)):
        problem = Problem.from_file(problem)
    elif not isinstance(problem, Problem):
        problem = Problem(problem)
    return Result(_json.loads(run_json(problem.to_json())))


run_json = run
reverse_json = reverse


def reverse(problem, measurements, *, segments=False):
    """Estimate flux multipliers from measured activities (trace regime).

    `measurements` is a mapping or a path to an actinv-reverse-input-1 file.
    With segments=True each irradiation step gets an independent multiplier.
    """
    if isinstance(problem, (_os.PathLike, str)):
        problem = Problem.from_file(problem)
    elif not isinstance(problem, Problem):
        problem = Problem(problem)
    if isinstance(measurements, (_os.PathLike, str)):
        measurements = _json.loads(_Path(measurements).read_text(encoding="utf-8"))
    return Result(_json.loads(reverse_json(
        problem.to_json(), _json.dumps(measurements, allow_nan=False), segments)))


decide_json = _decide_json = decide      # native bindings, before the
optimize_json = _optimize_json = optimize  # wrapper functions shadow them


def decide(decision, *, base_dir=None):
    """Run an actinv-decide-1 mapping (or file path) and return the decision
    document. `run_spec` may itself be an embedded spec mapping or a path
    resolved against `base_dir` (default: current directory)."""
    if isinstance(decision, (_os.PathLike, str)):
        decision = _Path(decision)
        if base_dir is None:
            base_dir = str(decision.resolve().parent)
        decision = _json.loads(decision.read_text(encoding="utf-8"))
    return _json.loads(_decide_json(
        _json.dumps(dict(decision), allow_nan=False),
        None if base_dir is None else _os.fspath(base_dir)))


def optimize(optspec_path, *, outdir=None, resume=False):
    """Run an actinv-optimize-1 design search by path; returns the result
    summary dict. The ledger lands in `outdir` (default: `optimize_out`
    beside the spec)."""
    return _json.loads(_optimize_json(_os.fspath(optspec_path),
                                      None if outdir is None else _os.fspath(outdir),
                                      resume))


# Maturin's top-level package re-exports the extension using __all__. PyO3
# registers native functions there, but dynamically defined classes need exports too.
__all__ = ["__version__", "_cli", "cram_step", "broaden", "run", "run_json",
           "reverse", "reverse_json", "validate", "Material", "Spectrum",
           "Schedule", "Problem", "Result", "solve", "decide", "optimize",
           "decide_json", "optimize_json"]
