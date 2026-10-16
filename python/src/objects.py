"""Convenient Python objects over the unchanged ACTINV JSON contract."""
import copy as _copy
import json as _json
import os as _os
import tempfile as _tempfile
import types as _types
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
        # "bundled:NAME" names a table inside the binary, not a file under base.
        if path and not str(path).startswith("bundled:"):
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
budget_json = _budget_json = budget      # wrapper functions shadow them
optimize_json = _optimize_json = optimize


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


def budget(budget, *, base_dir=None, verify=True):
    """Run an actinv-budget-1 mapping (or file path) and return the
    actinv-budget-result-1 document. `base_spec` and `limits` paths inside
    the budget resolve against `base_dir`, which for a file path defaults to
    that file's directory, and for a mapping defaults to the current
    directory.

    A file is passed through verbatim, so `budget_sha256` is the hash of its
    bytes, as on the command line; for a mapping it is the hash of the
    `json.dumps` text sent to the solver.

    A failed verification is not an exception: the document is still
    returned and its `verification` block records the failure (the `actinv
    budget` CLI exits 3 in that case; there is no Python equivalent)."""
    if isinstance(budget, (_os.PathLike, str)):
        budget = _Path(budget)
        if base_dir is None:
            base_dir = str(budget.resolve().parent)
        text = budget.read_bytes().decode("utf-8")
    else:
        text = _json.dumps(dict(budget), allow_nan=False)
    return _json.loads(_budget_json(
        text, None if base_dir is None else _os.fspath(base_dir), verify))


def optimize(optspec_path, *, outdir=None, resume=False):
    """Run an actinv-optimize-1 design search by path; returns the result
    summary dict. The ledger lands in `outdir` (default: `optimize_out`
    beside the spec)."""
    return _json.loads(_optimize_json(_os.fspath(optspec_path),
                                      None if outdir is None else _os.fspath(outdir),
                                      resume))


def _waste_run(command, spec, base_dir, text):
    """Run one native waste command (or the twin) on a path or a mapping.

    A path goes to the native binding unchanged, so the command resolves the
    paths inside the spec against that file's directory, as on the command
    line. A mapping is written to a uniquely named file inside `base_dir`
    (default: the current directory) for the duration of the call and removed
    afterwards, also on error, so its relative paths resolve against
    `base_dir`."""
    if isinstance(spec, (_os.PathLike, str)):
        out = _waste(command, _os.fspath(spec))
    else:
        body = _json.dumps(dict(spec), allow_nan=False)
        fd, name = _tempfile.mkstemp(
            prefix=".actinv-waste-", suffix=".json",
            dir=_os.getcwd() if base_dir is None else _os.fspath(base_dir))
        try:
            with _os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
            out = _waste(command, name)
        finally:
            try:
                _os.unlink(name)
            except FileNotFoundError:
                pass
    return out if text else _json.loads(out)


def _waste_operation(command, summary):
    def operation(spec, *, base_dir=None, text=False):
        return _waste_run(command, spec, base_dir, text)
    operation.__name__ = command
    operation.__qualname__ = "waste." + command
    operation.__doc__ = (
        summary + """

    `spec` is a path, or a mapping together with `base_dir` (the directory its
    relative paths resolve against; default: the current directory). Returns
    the result as a dict, or with text=True the exact JSON text the command
    line writes to an output file. A refusal raises ValueError carrying the
    command's message. Nothing is printed.""")
    return operation


def _waste_summary(result):
    """Flatten an actinv-waste-result-1 classification result to one dict per
    component and target step.

    Accepts the result itself (`waste.classify`), the `classification` member
    of an intrusion-screen result, or a twin's `waste_classification`. Only
    fields already in the result are read; the governing table, column and
    margin are those of the first entry of the target's
    `evaluation.binding_constraints` and `binding_nuclides` lists the
    contributors of all its binding constraints. Fields the result lacks are
    None."""
    if isinstance(result, dict) and result.get("schema") != "actinv-waste-result-1":
        for key in ("classification", "waste_classification"):
            if isinstance(result.get(key), dict):
                result = result[key]
                break
    if not isinstance(result, dict) or result.get("schema") != "actinv-waste-result-1":
        raise ValueError("summary needs an actinv-waste-result-1 classification result")
    rows = []
    for component in result.get("components") or []:
        for target in component.get("targets") or []:
            binding = (target.get("evaluation") or {}).get("binding_constraints") or []
            governing = binding[0] if binding else {}
            nuclides = []
            for constraint in binding:
                for name in constraint.get("contributors") or []:
                    if name not in nuclides:
                        nuclides.append(name)
            rows.append({
                "component": component.get("id"),
                "waste_type": component.get("waste_type"),
                "component_status": component.get("status"),
                "step": target.get("step"),
                "t_s": target.get("t_s"),
                "class": target.get("class"),
                "calculated_only_class": target.get("calculated_only_class"),
                "status": target.get("status"),
                "coverage": target.get("coverage"),
                "unknown_reason": target.get("unknown_reason"),
                "external_tritium_status": target.get("external_tritium_status"),
                "binding_target_class": governing.get("target_class"),
                "binding_table": governing.get("table"),
                "binding_column": governing.get("column"),
                "binding_normalized_sum": governing.get("normalized_sum"),
                "binding_normalized_margin": governing.get("normalized_margin"),
                "binding_strict": governing.get("strict"),
                "binding_passes": governing.get("passes"),
                "binding_constraint_count": len(binding),
                "binding_nuclides": nuclides,
            })
    return rows


waste = _types.SimpleNamespace(
    classify=_waste_operation(
        "classify", "Nominal Part 61 classification (`actinv waste SPEC`)."),
    budget=_waste_operation(
        "budget", "Impurity limits for a requested class (`actinv waste budget SPEC`)."),
    bounds=_waste_operation(
        "bounds", "Classes admitted by declared activity intervals (`actinv waste bounds SPEC`)."),
    composition=_waste_operation(
        "composition",
        "Classes over declared composition bounds (`actinv waste composition SPEC`)."),
    composition_solve=_waste_operation(
        "composition_solve",
        "Composition bounds from native witness runs (`actinv waste composition solve SPEC`)."),
    intrusion_screen=_waste_operation(
        "intrusion_screen",
        "Draft intrusion screen of a whole container (`actinv waste intrusion-screen SPEC`)."),
    summary=_waste_summary,
)
twin = _waste_operation("twin", "Facility twin (`actinv twin SPEC`), with its optional waste block.")
twin.__qualname__ = "twin"


# Maturin's top-level package re-exports the extension using __all__. PyO3
# registers native functions there, but dynamically defined classes need exports too.
__all__ = ["__version__", "_cli", "cram_step", "broaden", "run", "run_json",
           "reverse", "reverse_json", "validate", "Material", "Spectrum",
           "Schedule", "Problem", "Result", "solve", "decide", "optimize",
           "decide_json", "optimize_json", "budget", "budget_json", "waste", "twin"]
