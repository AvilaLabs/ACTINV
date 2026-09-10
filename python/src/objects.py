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
    def irradiate(self, duration, multiplier=1.0):
        self.append({"dt": str(duration), "flux": multiplier})
        return self

    def cool(self, duration):
        self.append({"dt": str(duration), "flux": 0.0})
        return self


def _paths(value, base=None):
    value = _copy.deepcopy(value)
    refs = [(value.get("library", {}), "path"), (value.get("decay", {}), "primary"),
            (value.get("decay", {}), "fallback")]
    for section, field in (("photon", "response"), ("uncertainty", "covariance"),
                           ("radiological", "table")):
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
