#!/usr/bin/env python3
"""P140 gate H4 (protocols/ACTINV-P140_PROTOCOL.md): the handbook walk-through.

  H4  Every complete spec in docs/guide/ validates; every fragment for a parity feature, merged into
      examples/fns_fe_5min.json, validates; every CLI command shown for `import-fispact`, `report` and `inspect`
      runs on the shipped examples and exits as documented.

What is extracted, and how each item is classified (the rules are recorded in the output JSON):

  Fenced JSON blocks. The handbook labels fragments as fragments (docs/maintainers/DOCUMENTATION.md: "label JSON
  fragments as fragments"; docs/guide/specification.md: "JSON fragments below belong inside a complete problem; they
  are not standalone runnable inputs"). A block is therefore a COMPLETE SPEC iff, parsed as written, it is a top-level
  object whose `spec` key is `actinv-spec-1` (the schema string `actinv validate` accepts); it is checked with
  `validate FILE`, and with `--files` when every path in it resolves. Any other block is a FRAGMENT when it is bare
  `"key": value` text (wrapped in braces to parse) or an object without a document identifier, whose keys are problem
  keys (or a key with a recorded home, such as `dilution_overrides`); a block that carries its own `spec`/`schema`/
  `format` identifier (a mesh spec, a waste document, a radiological table, a spallation table, a twin spec) is
  ANOTHER DOCUMENT: parsed and recorded, not validated (`actinv validate` is for `actinv-spec-1`, and the other
  documents need inputs this gate does not supply).

  Fragments. Each is merged into examples/fns_fe_5min.json: its top-level keys replace or deep-merge into the
  problem (objects merge, lists and scalars replace, `material.composition` is replaced rather than merged so that
  `Fe` does not collide with the base `FE`); a key with a recorded home is placed there (`dilution_overrides` under
  `self_shielding`, which is first given the table block of examples/shielding_demo.json because dilution overrides
  are table mode only); a `robustness` block belongs to a study and is merged into examples/study_fe_smoke.json. The
  rule used is recorded per fragment. Placeholders are normalized: a `sha256` that is not 64 hex digits becomes 64
  zeros, and a `path` whose basename exists in the local data trees is pointed at that file with its real SHA-256
  (`--files` is then also run when every referenced path resolves). Fragments that name files that are not shipped
  are validated without `--files`.

  Derived items. The bundled regulatory table names in the handbook table, the summary `depletion` field, and the
  minimal radiological table are turned into fragments by the same merge.

  Commands. Every `actinv new/import-fispact/report/inspect` line in a code block is classified as a synopsis
  (placeholders such as DECK.i, brackets, bars) or a literal command. Synopses are expanded into concrete commands on
  shipped inputs (an FNS deck and its files from ~/nuclear-data/conderc-fns, the shipped library, decay data and
  covariance, a result produced by running examples/fns_fe_5min.json, a result with a summary and a bundled table,
  fission-yield files); literal commands are rewritten placeholder by placeholder and run in order. The exit status
  must be what the handbook says: 0, except that `report` fails when no requested table can be built or a named
  nuclide is in no selected step, and `import-fispact --strict` fails exactly when the import report lists an
  unsupported keyword.

Every binary call is serial, without a shell, from the repository root, under a timeout. Overall PASS iff every
gating item passes and nothing in the handbook went unclassified. A feature with no fenced fragment is listed as
uncovered; that is information, not a failure.

Usage:
  check_p140_docs.py --candidate BIN [--workdir target/p140-docs] [--out results/p140_docs.json] [--timeout 600]
                     [--fns ~/nuclear-data/conderc-fns/fns]
  check_p140_docs.py --self-check [--workdir target/p140-docs]
      extracts and classifies everything, writes the extracted and merged files, prints the counts and the
      classification, and runs no binary.

Run inside the required systemd cgroup. Exit 0 on PASS, 1 on FAIL, 2 on a missing input.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols" / "ACTINV-P140_PROTOCOL.md"
GUIDE = ROOT / "docs" / "guide"
CONVENTION_DOC = ROOT / "docs" / "maintainers" / "DOCUMENTATION.md"
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
STUDY_SPEC = ROOT / "examples" / "study_fe_smoke.json"
SHIELD_SPEC = ROOT / "examples" / "shielding_demo.json"
DEFAULT_FNS = Path("~/nuclear-data/conderc-fns/fns").expanduser()
NUCLEAR_DATA = Path("~/nuclear-data").expanduser()
NFY_DIR = NUCLEAR_DATA / "endfb-viii.0-nfpy"
DECK_REL = "Fe/TENDL-2017_1996exp_5min.i"
COV_REL = "actinv-data/v1.1.0/uncertainty/tendl-2025-neutron-709g.cov.npz"
SPEC_ID = "actinv-spec-1"
ZERO_SHA = "0" * 64
TAIL = 400

# Keys of an `actinv-spec-1` problem (a fragment may carry any of them at its top level).
SPEC_KEYS = {"title", "projectile", "library", "decay", "material", "spectrum", "schedule", "options",
             "fission_yields", "uncertainty", "radiological", "damage", "kerma", "self_shielding", "photon",
             "summary", "pathways", "overrides", "outputs", "flux", "gas"}
# A key that is not a problem key but has a home inside one, found from the section heading and prose.
HOME_RULES = {"dilution_overrides": ["self_shielding"]}
# Keys that belong to a study spec rather than a problem.
STUDY_KEYS = {"robustness"}
# Identifier keys that mark a block as a document of another type.
DOC_ID_KEYS = ("spec", "schema", "format", "study")
WASTE_FRAGMENT_KEYS = {"status", "source", "excludes_activation", "activity_bounds_bq", "activity_bq"}
REPLACE_KEYS = {"composition"}

COMPLETE_RULE = ("complete spec iff the block, parsed as written, is a top-level object with spec == 'actinv-spec-1'; "
                 "the handbook labels every other JSON block a fragment (docs/maintainers/DOCUMENTATION.md; "
                 "docs/guide/specification.md line 23)")

# Parity features and how to recognize a fragment that covers one: a dotted key path present in the fragment
# (a list element's keys are written `name[].key`).
FEATURES = OrderedDict([
    ("summary", ("summary",)),
    ("summary.depletion", ("summary.depletion",)),
    ("photon.point_source", ("photon.point_source",)),
    ("photon.bremsstrahlung", ("photon.bremsstrahlung",)),
    ("photon.group_structure", ("photon.group_structure",)),
    ("schedule[].power_W_cm3", ("schedule[].power_W_cm3",)),
    ("schedule[].power_W_per_g", ("schedule[].power_W_per_g",)),
    ("kerma", ("kerma",)),
    ("schedule[].wall_loading_MW_m2", ("schedule[].wall_loading_MW_m2",)),
    ("schedule[].partition", ("schedule[].partition",)),
    ("overrides", ("overrides",)),
    ("pathways", ("pathways",)),
    ("self_shielding.geometry", ("self_shielding.geometry",)),
    ("self_shielding.dilution_overrides", ("self_shielding.dilution_overrides",)),
    ("spectrum.rebin", ("spectrum.rebin",)),
    ("fission_yields.spontaneous", ("fission_yields.spontaneous",)),
    ("options.fission", ("options.fission",)),
    ("schedule[].library", ("schedule[].library",)),
    ("schedule[].projectile", ("schedule[].projectile",)),
    ("radiological.bundled", ("radiological.bundled",)),
    ("robustness.statistics", ("robustness.statistics",)),
])
# Where the prose describes a feature (first hits are listed for a feature with no fenced fragment).
FEATURE_PROSE = {
    "summary": r"`summary`", "summary.depletion": r"`depletion`", "photon.point_source": r"photon\.point_source",
    "photon.bremsstrahlung": r"photon\.bremsstrahlung", "photon.group_structure": r"`group_structure`",
    "schedule[].power_W_cm3": r"power_W_cm3", "schedule[].power_W_per_g": r"power_W_per_g", "kerma": r"`kerma`",
    "schedule[].wall_loading_MW_m2": r"wall_loading_MW_m2", "schedule[].partition": r"`partition`",
    "overrides": r"`overrides`", "pathways": r"`pathways`", "self_shielding.geometry": r"self_shielding\.geometry",
    "self_shielding.dilution_overrides": r"dilution_overrides", "spectrum.rebin": r"spectrum\.rebin",
    "fission_yields.spontaneous": r"fission_yields\.spontaneous", "options.fission": r"options\.fission",
    "schedule[].library": r"schedule `library`|`library` override", "schedule[].projectile": r"`projectile` without",
    "radiological.bundled": r"bundled:", "robustness.statistics": r"robustness\.statistics",
}


# ------------------------------------------------------------------------------ helpers


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def record_file(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def rel(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def tail(data, n=TAIL):
    text = data.decode(errors="replace") if isinstance(data, bytes) else str(data)
    return text[-n:]


def is_hex64(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def dump(obj, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    return path


def key_paths(obj, prefix=""):
    """Dotted key paths of a JSON value; the keys of a list's object elements are written `name[].key`."""
    out = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.add(prefix + key)
            if isinstance(value, dict):
                out |= key_paths(value, prefix + key + ".")
            elif isinstance(value, list):
                for element in value:
                    if isinstance(element, dict):
                        out |= key_paths(element, prefix + key + "[].")
    return out


def deep_merge(base, fragment, rules, path=""):
    """Objects merge, everything else replaces; `composition` objects replace."""
    for key, value in fragment.items():
        here = f"{path}.{key}" if path else key
        if (isinstance(value, dict) and isinstance(base.get(key), dict) and key not in REPLACE_KEYS):
            deep_merge(base[key], value, rules, here)
        else:
            if key in base:
                rules.append(f"{here}: replaced")
            else:
                rules.append(f"{here}: added")
            base[key] = copy.deepcopy(value)


# ------------------------------------------------------------------------------ markdown


class Block:
    def __init__(self, file, start, end, lang, text, heading, prev, prev_line):
        self.file, self.start, self.end, self.lang = file, start, end, lang
        self.text, self.heading, self.prev, self.prev_line = text, heading, prev, prev_line

    @property
    def source(self):
        return f"docs/guide/{self.file}:{self.start}"

    @property
    def span(self):
        return f"docs/guide/{self.file}:{self.start}-{self.end}"


FENCE_OPEN = re.compile(r"^(\s*)(`{3,})\s*([\w+-]*)\s*$")


def parse_markdown(path):
    """Fenced blocks with their heading and the prose line before them; prose lines for later searches."""
    lines = path.read_text(encoding="utf-8").split("\n")
    blocks, heading, prev, prev_line, i = [], "", "", 0, 0
    while i < len(lines):
        m = FENCE_OPEN.match(lines[i])
        if m:
            indent, fence, lang = len(m.group(1)), m.group(2), m.group(3)
            j = i + 1
            while j < len(lines) and not re.match(r"^\s*" + fence + r"\s*$", lines[j]):
                j += 1
            body = [(line[indent:] if not line[:indent].strip() else line.lstrip()) for line in lines[i + 1:j]]
            blocks.append(Block(path.name, i + 1, j + 1, lang, "\n".join(body), heading, prev, prev_line))
            i = j + 1
            continue
        if lines[i].startswith("#"):
            heading = lines[i].lstrip("#").strip()
        if lines[i].strip():
            prev, prev_line = lines[i].strip(), i + 1
        i += 1
    return blocks, lines


def parse_json_block(text):
    """(object, mode) where mode is 'as-is' or 'wrapped' (bare `"key": value` text), else (None, error)."""
    try:
        return json.loads(text), "as-is"
    except ValueError as first:
        try:
            return json.loads("{" + text + "}"), "wrapped"
        except ValueError:
            return None, f"unparsable: {first}"


def classify_json(block):
    """Returns a dict: kind in complete_spec, fragment, other_document, other_document_fragment, unclassified."""
    obj, mode = parse_json_block(block.text)
    info = {"parse": mode, "label": None, "kind": "unclassified", "reason": ""}
    label = []
    if re.search(r"fragment", block.prev, re.I):
        label.append(f"prose before the block says fragment ({block.file}:{block.prev_line})")
    if block.file == "specification.md":
        label.append("docs/guide/specification.md:23 says JSON fragments below are not standalone")
    info["label"] = label
    if obj is None:
        info["reason"] = mode
        return info, None
    if not isinstance(obj, dict):
        info["reason"] = "top level is not an object"
        return info, obj
    ident = next(((k, obj[k]) for k in DOC_ID_KEYS if isinstance(obj.get(k), str)), None)
    if ident is not None:
        key, value = ident
        if key == "spec" and value == SPEC_ID and mode == "as-is":
            info.update(kind="complete_spec", reason=f"top-level spec == {SPEC_ID}")
        else:
            info.update(kind="other_document", schema=value, reason=f"carries its own identifier {key}={value}")
        return info, obj
    keys = set(obj)
    if keys <= WASTE_FRAGMENT_KEYS and "status" in keys:
        info.update(kind="other_document_fragment", reason="waste external_tritium object (waste.md)")
    elif keys <= (SPEC_KEYS | set(HOME_RULES) | STUDY_KEYS):
        info.update(kind="fragment", reason=f"problem keys {sorted(keys)} without a document identifier")
    else:
        info["reason"] = f"unknown keys {sorted(keys - SPEC_KEYS - set(HOME_RULES) - STUDY_KEYS)}"
    return info, obj


# ------------------------------------------------------------------------------ data placeholders


_FILE_INDEX = {}


def file_index():
    """basename -> path for the local data trees a handbook placeholder may stand for."""
    if _FILE_INDEX:
        return _FILE_INDEX
    roots = [(NFY_DIR, False), (NUCLEAR_DATA / "endfb-viii.0-decay", False), (ROOT / "actinv-data" / "v1.1.0", True),
             (ROOT / "examples", False)]
    for root, recursive in roots:
        if not root.is_dir():
            continue
        for path in (root.rglob("*") if recursive else root.glob("*")):
            if path.is_file():
                _FILE_INDEX.setdefault(path.name, path)
    return _FILE_INDEX


def resolve_local(value):
    path = Path(value)
    path = path if path.is_absolute() else ROOT / path
    return path if path.is_file() else None


def normalize(obj, subs, pointer=""):
    """Point placeholder paths at local files and make placeholder hashes well-formed; edits in place."""
    if isinstance(obj, dict):
        if isinstance(obj.get("path"), str) and not obj["path"].startswith(("bundled:", "catalog:")):
            path = obj["path"]
            found = resolve_local(path)
            if found is None:
                candidate = file_index().get(Path(path).name)
                if candidate is not None:
                    obj["path"] = rel(candidate) if str(candidate).startswith(str(ROOT)) else str(candidate)
                    subs.append({"at": pointer or "/", "what": "path", "from": path, "to": obj["path"]})
                    found = candidate
            if "sha256" in obj:
                if found is not None:
                    real = sha256_file(found)
                    if obj["sha256"] != real:
                        subs.append({"at": pointer or "/", "what": "sha256", "from": obj["sha256"], "to": real})
                        obj["sha256"] = real
                elif not is_hex64(obj["sha256"]):
                    subs.append({"at": pointer or "/", "what": "sha256 placeholder", "from": obj["sha256"],
                                 "to": ZERO_SHA})
                    obj["sha256"] = ZERO_SHA
        for key, value in obj.items():
            normalize(value, subs, f"{pointer}/{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            normalize(value, subs, f"{pointer}/{index}")


def file_refs(obj, in_decay=False):
    """Every file path a spec names (the objects with `path`, and decay.primary/fallback)."""
    refs = []
    if isinstance(obj, dict):
        if isinstance(obj.get("path"), str):
            refs.append(obj["path"])
        if in_decay:
            refs += [obj[k] for k in ("primary", "fallback") if isinstance(obj.get(k), str)]
        for key, value in obj.items():
            refs += file_refs(value, key == "decay")
    elif isinstance(obj, list):
        for value in obj:
            refs += file_refs(value, in_decay)
    return refs


def unresolved_refs(spec):
    out = []
    for ref in file_refs(spec):
        if ref.startswith(("bundled:", "catalog:")):
            continue
        if resolve_local(ref) is None:
            out.append(ref)
    return out


# ------------------------------------------------------------------------------ plan


class Plan:
    """Everything extracted without a binary: items to run, items that are only classified, and the files written."""

    def __init__(self, workdir, fns_root):
        self.workdir, self.fns_root = workdir, fns_root
        self.items = []          # dict with `run` closure for executable items
        self.blocks = []         # classification record of every fenced block that holds JSON
        self.commands = []       # classification record of every actinv command line
        self.unclassified = []
        self.fragments_seen = []  # (id, key paths) of the fragments used for the feature coverage
        self.prose_lines = {}

    def add(self, **item):
        item.setdefault("gating", True)
        item.setdefault("run", None)
        self.items.append(item)
        return item


def make_item_id(prefix, block, extra=""):
    return f"{prefix}:{block.file[:-3]}:{block.start}{extra}"


def fragment_to_spec(obj, rules, notes):
    """Merge a fragment into a copy of the gate spec (or the study example); returns (target, document)."""
    if set(obj) & STUDY_KEYS:
        doc = json.loads(STUDY_SPEC.read_text(encoding="utf-8"))
        rules.append("target: examples/study_fe_smoke.json (a `robustness` block belongs to a study spec)")
        for key, value in obj.items():
            rules.append(f"study key {key}: set")
            doc[key] = copy.deepcopy(value)
        return "study", doc
    doc = json.loads(GATE_SPEC.read_text(encoding="utf-8"))
    rules.append("target: examples/fns_fe_5min.json")
    top = {k: v for k, v in obj.items() if k in SPEC_KEYS}
    deep_merge(doc, top, rules)
    for key, value in obj.items():
        if key in SPEC_KEYS:
            continue
        home = HOME_RULES.get(key)
        if home is None:
            notes.append(f"no home for key {key}")
            continue
        parent = doc
        for step in home:
            if step not in parent:
                shield = json.loads(SHIELD_SPEC.read_text(encoding="utf-8"))
                parent[step] = copy.deepcopy(shield[step])
                rules.append(f"{step}: table block copied from examples/shielding_demo.json (prerequisite: "
                             f"{key} is table mode only)")
            parent = parent[step]
        parent[key] = copy.deepcopy(value)
        rules.append(f"{'.'.join(home)}.{key}: placed under {'.'.join(home)} (home inferred from the heading/prose)")
    return "spec", doc


def add_fragment_item(plan, item_id, source, obj, extra_rules=None, category="fragment", note="", tag=None):
    rules, notes, subs = list(extra_rules or []), [], []
    target, doc = fragment_to_spec(obj, rules, notes)
    if notes:
        plan.unclassified.append({"id": item_id, "source": source, "why": "; ".join(notes)})
    if target == "study":
        covariance = doc.get("robustness", {}).get("covariance")
        if isinstance(covariance, dict) and resolve_local(covariance.get("path", "")) is None:
            doc["robustness"]["covariance"] = {"path": COV_REL, "sha256": sha256_file(ROOT / COV_REL)}
            rules.append(f"robustness.covariance: placeholder replaced by the shipped covariance {COV_REL}")
        if isinstance(doc.get("cases", {}).get("spectra"), list):
            out_dir = plan.workdir / "merged"
            for spectrum in doc["cases"]["spectra"]:
                if "spec_ref" in spectrum:
                    spectrum["spec_ref"] = os.path.relpath(GATE_SPEC, out_dir)
            rules.append("cases.spectra[].spec_ref: rewritten relative to the merged study file")
    normalize(doc, subs)
    path = dump(doc, plan.workdir / "merged" / (re.sub(r"[^A-Za-z0-9_.-]", "_", item_id) + ".json"))
    missing = unresolved_refs(doc) if target == "spec" else []
    paths = key_paths(obj)
    for key in obj:
        if key in HOME_RULES:
            paths.add(".".join(HOME_RULES[key] + [key]))
    if tag:
        paths.add(tag)
    plan.fragments_seen.append((item_id, source, sorted(paths)))
    item = plan.add(id=item_id, category=category, source=[source], classification="fragment", target=target,
                    merge_rules=rules, substitutions=subs, file=rel(path), unresolved_paths=missing,
                    action=("study validate" if target == "study" else
                            "validate" + (" + validate --files" if not missing else " (no --files: files not shipped)")),
                    note=note)
    item["run"] = lambda ctx, item=item, path=path, target=target, missing=missing: run_validate(
        ctx, path, study=(target == "study"), files=(not missing and target == "spec"))
    return item


def run_validate(ctx, path, study=False, files=True):
    checks = []
    if study:
        checks.append(ctx.call([ctx.candidate, "study", "validate", path], expect=0))
    else:
        checks.append(ctx.call([ctx.candidate, "validate", path], expect=0))
        if files:
            checks.append(ctx.call([ctx.candidate, "validate", path, "--files"], expect=0))
    return checks


def extract_json_blocks(plan, blocks_by_file):
    for name, blocks in blocks_by_file.items():
        for block in blocks:
            if block.lang != "json":
                continue
            info, obj = classify_json(block)
            record = {"source": block.span, "heading": block.heading, "kind": info["kind"], "parse": info["parse"],
                      "label": info["label"], "reason": info["reason"], "schema": info.get("schema")}
            plan.blocks.append(record)
            item_id = make_item_id("json", block)
            if info["kind"] == "unclassified":
                plan.unclassified.append({"id": item_id, "source": block.span, "why": info["reason"]})
                continue
            raw_path = dump(obj, plan.workdir / "blocks" / f"{block.file[:-3]}_L{block.start}.json")
            record["file"] = rel(raw_path)
            if info["kind"] == "complete_spec":
                doc = copy.deepcopy(obj)
                subs = []
                normalize(doc, subs)
                path = dump(doc, plan.workdir / "complete" / f"{block.file[:-3]}_L{block.start}.json")
                missing = unresolved_refs(doc)
                item = plan.add(id=item_id, category="complete_spec", source=[block.span],
                                classification="complete_spec", rule=COMPLETE_RULE, substitutions=subs,
                                file=rel(path), unresolved_paths=missing,
                                action="validate" + (" + validate --files" if not missing else ""))
                item["run"] = lambda ctx, path=path, missing=missing: run_validate(ctx, path, files=not missing)
            elif info["kind"] == "fragment":
                add_fragment_item(plan, item_id, block.span, obj)
            else:
                plan.add(id=item_id, category="other_document", source=[block.span], gating=False,
                         classification=info["kind"], schema=info.get("schema"), file=rel(raw_path),
                         action="parsed only", outcome="info",
                         note=(f"{info.get('schema') or 'document fragment'}: not an actinv-spec-1 problem; "
                               "`actinv validate` does not apply and the document needs inputs outside H4"))


def add_derived_items(plan, blocks_by_file, lines_by_file):
    spec_lines = lines_by_file["specification.md"]
    # the bundled regulatory tables, from the handbook's own table
    in_table_names = []
    for number, line in enumerate(spec_lines, 1):
        if re.match(r"^\| `(us|euratom)-", line):
            first_cell = line.split("|")[1]
            for name in re.findall(r"`((?:us|euratom)-[a-z0-9-]+)`", first_cell):
                in_table_names.append((name, number))
    for name, number in in_table_names:
        obj = {"radiological": {"table": {"path": f"bundled:{name}"}}}
        add_fragment_item(plan, f"derived:bundled:{name}", f"docs/guide/specification.md:{number}", obj,
                          extra_rules=["derived from the 'Bundled regulatory tables' table: the bundled name as "
                                       "radiological.table.path with no responses (an empty selector chooses all)"],
                          category="derived_fragment", tag="radiological.bundled")
    # summary.depletion, from the field table
    dep_line = next((n for n, line in enumerate(spec_lines, 1) if line.startswith("| `depletion` | `false`")), None)
    summary_block = next((b for b in blocks_by_file["specification.md"]
                          if b.lang == "json" and '"summary"' in b.text and '"dominant_top"' in b.text), None)
    if dep_line and summary_block:
        obj, _ = parse_json_block(summary_block.text)
        obj = copy.deepcopy(obj)
        obj["summary"]["depletion"] = True
        add_fragment_item(plan, "derived:summary.depletion", f"docs/guide/specification.md:{dep_line}", obj,
                          extra_rules=[f"the summary fragment of {summary_block.span} plus `depletion: true`, "
                                       "from the field table"], category="derived_fragment")
    # the minimal radiological table: written, hash-pinned and used by a problem
    table_block = next((b for b in blocks_by_file["specification.md"]
                        if b.lang == "json" and "actinv-radiological-table-1" in b.text), None)
    if table_block:
        table, _ = parse_json_block(table_block.text)
        path = dump(table, plan.workdir / "docs" / "minimal_radiological_table.json")
        obj = {"radiological": {"table": {"path": rel(path), "sha256": sha256_file(path)},
                                "responses": [table["responses"][0]["id"]]}}
        add_fragment_item(plan, "derived:radiological-minimal-table", table_block.span, obj,
                          extra_rules=["the 'minimal table' block written to the workdir, hash-pinned, and "
                                       "selected by a problem's radiological section"],
                          category="derived_fragment")


# ------------------------------------------------------------------------------ command extraction


PLACEHOLDER = re.compile(r"^[A-Z][A-Z0-9_]+(\.[A-Za-z]{1,4})?(\.\.\.)?$")
IN_SCOPE = ("import-fispact", "report", "inspect", "new")


class Command:
    def __init__(self, block, start, end, text):
        self.block, self.start, self.end, self.text = block, start, end, text
        try:
            self.tokens = shlex.split(text)
        except ValueError:
            self.tokens = text.split()
        self.cmd = self.tokens[1] if len(self.tokens) > 1 else ""
        self.sub = self.tokens[2] if self.cmd == "inspect" and len(self.tokens) > 2 else ""
        self.synopsis = any(re.search(r"[\[\]|]", t) or PLACEHOLDER.match(t) for t in self.tokens[2:])

    @property
    def source(self):
        span = f"{self.start}" if self.start == self.end else f"{self.start}-{self.end}"
        return f"docs/guide/{self.block.file}:{span}"

    @property
    def flags(self):
        return sorted(set(re.findall(r"--[a-z][a-z-]*", self.text)))


def extract_commands(blocks_by_file):
    out = []
    for name, blocks in blocks_by_file.items():
        for block in blocks:
            if block.lang not in ("text", "bash", "sh", ""):
                continue
            lines = block.text.split("\n")
            i = 0
            while i < len(lines):
                if lines[i].lstrip().startswith("actinv "):
                    start, text = i, lines[i].strip()
                    while True:
                        if text.endswith("\\") and i + 1 < len(lines):
                            i += 1
                            text = text[:-1].rstrip() + " " + lines[i].strip()
                        elif (block.lang == "text" and i + 1 < len(lines) and lines[i + 1][:1] in (" ", "\t")
                              and lines[i + 1].strip()):
                            i += 1
                            text += " " + lines[i].strip()
                        else:
                            break
                    out.append(Command(block, block.start + 1 + start, block.start + 1 + i, text))
                i += 1
    return out


# ------------------------------------------------------------------------------ the binary


class Context:
    """The candidate, the paths of the shipped inputs, and the state later items depend on."""

    def __init__(self, candidate, timeout, plan):
        self.candidate, self.timeout, self.plan = candidate, timeout, plan
        self.workdir, self.state = plan.workdir, {}
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("ACTINV_LIBRARY", "ACTINV_ENDF_DECAY", "ACTINV_COVARIANCE")}

    def call(self, argv, expect=0, check=None, quiet_stdout=False):
        """Run one command; `expect` is an exit code, 'nonzero' or None (any). Returns the check record."""
        argv = [str(a) for a in argv]
        for a in argv[1:]:
            # output paths inside the workdir: the CLI writes single files without creating parents
            path = Path(a) if os.path.isabs(a) else ROOT / a
            if path.suffix and str(path.resolve()).startswith(str(Path(self.workdir).resolve())):
                path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            proc = subprocess.run(argv, cwd=str(ROOT), env=self.env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=self.timeout, check=False)
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            code, out, err = None, b"", f"timed out after {self.timeout} s".encode()
        except OSError as error:
            code, out, err = None, b"", f"{type(error).__name__}: {error}".encode()
        ok = code is not None and (expect is None or (code != 0 if expect == "nonzero" else code == expect))
        record = {"argv": ["CAND"] + [rel(a) if os.path.isabs(a) else a for a in argv[1:]],
                  "expected_exit": expect, "exit_code": code, "wall_s": round(time.monotonic() - started, 3),
                  "stdout_bytes": len(out), "stdout_sha256": hashlib.sha256(out).hexdigest(),
                  "stdout_tail": tail(out), "stderr_tail": tail(err)}
        if check is not None and ok:
            problem = check(out, err)
            if problem:
                ok = False
                record["check_failed"] = problem
        record["ok"] = ok
        return record


def passed(checks):
    return bool(checks) and all(c["ok"] for c in checks)


# ------------------------------------------------------------------------------ inputs for the command items


def deck_inputs(fns_root):
    deck = fns_root / DECK_REL
    stem = deck.with_suffix("")
    files = Path(str(stem) + "_files")
    fluxes = None
    if files.is_file():
        for line in files.read_text(errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "fluxes":
                fluxes = deck.parent / parts[1]
    return deck, files, fluxes


def unsupported_in(report_path):
    report = json.loads(Path(report_path).read_text())
    return [e["keyword"] for e in report.get("entries", []) if e.get("status") == "unsupported"]


def documented_unsupported_keywords(lines):
    """The all-caps keywords the handbook lists under 'Not translated'."""
    out, inside = set(), False
    for line in lines:
        if line.startswith("## "):
            inside = line.strip() == "## Not translated"
            continue
        if inside:
            out |= {k for k in re.findall(r"`([A-Z][A-Z0-9]{3,})`", line)}
    # these are unsupported only in some positions or forms (after the first TIME; for a non-standard structure;
    # for an isomer daughter), so a keyword scan of the decks cannot tell
    return out - {"GETXS", "TIME", "GRPCONVERT", "ACROSS"}


def find_unsupported_deck(fns_root, keywords):
    """First FNS deck (sorted) other than the gate deck that has a documented-unsupported keyword at a line start."""
    pattern = re.compile(r"^\s*(" + "|".join(sorted(keywords)) + r")\b", re.M) if keywords else None
    if pattern is None or not fns_root.is_dir():
        return None, None
    for deck in sorted(fns_root.glob("*/*.i")):
        if str(deck).endswith(DECK_REL):
            continue
        try:
            text = deck.read_text(errors="replace")
        except OSError:
            continue
        m = pattern.search(text)
        if m:
            return deck, m.group(1)
    return None, None


def result_info(path):
    """What a result can supply, for the exit status `report` must give."""
    result = json.loads(Path(path).read_text())
    steps = result.get("steps", [])

    def names(step):
        found = set(step.get("activity_Bq_per_g", {}))
        inventory = step.get("inventory", [])
        if isinstance(inventory, list):
            found |= {e.get("nuclide") for e in inventory if isinstance(e, dict)}
        elif isinstance(inventory, dict):
            found |= set(inventory)
        return found

    return {"n_steps": len(steps), "summary": any("summary" in s for s in steps),
            "radiological": any(str(r.get("unit", "")).startswith("Sv")
                                for s in steps for r in (s.get("radiological") or {}).get("responses", [])),
            "photon_source": any(s.get("photon_source") for s in steps),
            "names": [names(s) for s in steps],
            "top": [n for n, _ in sorted(steps[-1].get("activity_Bq_per_g", {}).items(), key=lambda kv: -kv[1])][:2]
            if steps else []}


TABLE_NEEDS = {"tab1": None, "tab2": None, "totals": None, "timeseries": None, "tab3": "radiological",
               "tab4": "photon_source", "dominant": "summary", "elements": "summary"}


def report_expectation(info, tables, step, nuclides):
    """0 if some requested table can be built and every nuclide is in a selected step, else 'nonzero'."""
    selected = range(info["n_steps"]) if step in (None, "all") else [int(step) - 1]
    for nuclide in nuclides:
        if not any(nuclide in info["names"][i] for i in selected if i < len(info["names"])):
            return "nonzero"
    wanted = tables or list(TABLE_NEEDS)
    return 0 if any(TABLE_NEEDS[t] is None or info[TABLE_NEEDS[t]] for t in wanted) else "nonzero"


def build_command_items(plan, commands, lines_by_file, args):
    """Classify every actinv command line; add the executable items for the in-scope ones."""
    fns_root = plan.fns_root
    deck, files, fluxes = deck_inputs(fns_root)
    lib = GATE_SPEC_DOC["library"]["path"]
    decay = GATE_SPEC_DOC["decay"]["primary"]
    W = plan.workdir
    group_json = dump({"structure": "p140-custom", "boundaries_eV": [1e-5, 1.0, 1e3, 1e6, 2e7]},
                      W / "docs" / "custom_groups.json")
    spectrum_json = dump(GATE_SPEC_DOC["spectrum"], W / "docs" / "spectrum_object.json")
    nfy = [NFY_DIR / "nfy-092_U_235.endf", NFY_DIR / "nfy-094_Pu_239.endf"]
    group_names = []
    for number, line in enumerate(lines_by_file["specification.md"], 1):
        m = re.match(r"^\| `(fispact-\d+)`", line)
        if m:
            group_names.append((m.group(1), number))
    unsupported_keywords = documented_unsupported_keywords(lines_by_file["fispact-migration.md"])
    bad_deck, bad_keyword = find_unsupported_deck(fns_root, unsupported_keywords)
    bad_origin = "FNS corpus deck"
    if bad_deck is None and "PROBTABLE" in unsupported_keywords and deck.is_file():
        # no corpus deck carries a keyword the handbook lists as unsupported: add one to a copy of the gate deck
        text = deck.read_text(errors="replace")
        lines_ = text.split("\n")
        at = next((n for n, line in enumerate(lines_) if line.strip().upper().startswith("FISPACT")), 0) + 1
        lines_.insert(at, "PROBTABLE")
        bad_deck = W / "docs" / "fe_5min_probtable.i"
        bad_deck.parent.mkdir(parents=True, exist_ok=True)
        bad_deck.write_text("\n".join(lines_), encoding="utf-8")
        bad_keyword, bad_origin = "PROBTABLE", "copy of the gate deck with a PROBTABLE line added after FISPACT"

    by_key = {}
    for command in commands:
        key = (command.cmd, command.sub)
        record = {"source": command.source, "text": command.text, "command": command.cmd, "sub": command.sub,
                  "flags": command.flags, "form": "synopsis" if command.synopsis else "literal"}
        plan.commands.append(record)
        if command.cmd not in IN_SCOPE:
            record["scope"] = "out of H4 scope"
            continue
        record["scope"] = "H4 (generated example)" if command.cmd == "new" else "H4"
        by_key.setdefault(key, []).append((command, record))

    def sources(key):
        return [c.source for c, _ in by_key.get(key, [])]

    def add_cmd_item(item_id, category, srcs, label, build, expect=0, check=None, post=None, extra=None):
        item = plan.add(id=item_id, category=category, source=srcs, classification="synopsis expansion",
                        action=label, **(extra or {}))

        def run(ctx):
            argv, exp = build(ctx)
            if argv is None:
                return [{"argv": [], "ok": False, "check_failed": exp}]
            exp = expect if exp is None else exp
            checks = [ctx.call(argv, expect=exp, check=check)]
            if post and checks[0]["ok"]:
                checks += post(ctx)
            return checks
        item["run"] = run
        return item

    # ---- new (a complete generated example the handbook names)
    new_path = W / "new" / "problem.json"
    if ("new", "") in by_key:
        def run_new(ctx):
            if new_path.exists():
                new_path.unlink()
            new_path.parent.mkdir(parents=True, exist_ok=True)
            checks = [ctx.call([ctx.candidate, "new", new_path], expect=0)]
            if checks[0]["ok"]:
                for level in ([], ["--files"], ["--hashes"]):
                    checks.append(ctx.call([ctx.candidate, "validate", new_path] + level, expect=0))
            return checks
        item = plan.add(id="cmd:new", category="generated_example", source=sources(("new", "")),
                        classification="literal sequence", action="new, then validate / --files / --hashes "
                        "(quick-start.md and specification.md show new, validate and validate --hashes)")
        item["run"] = run_new

    # ---- the result the report items read, and the same problem with a summary and a bundled table
    base_result = W / "run" / "base_result.json"
    rich_spec_path = None
    rich_result = W / "run" / "rich_result.json"

    def run_base(ctx):
        base_result.parent.mkdir(parents=True, exist_ok=True)
        checks = [ctx.call([ctx.candidate, "run", GATE_SPEC, base_result], expect=0)]
        if checks[0]["ok"]:
            ctx.state["base_info"] = result_info(base_result)
        return checks
    item = plan.add(id="support:run-gate-spec", category="support", source=["examples/fns_fe_5min.json"],
                    classification="support", action="run examples/fns_fe_5min.json into the workdir "
                    "(the RESULT.json the report items read)")
    item["run"] = run_base

    rich_doc = copy.deepcopy(GATE_SPEC_DOC)
    rich_rules = []
    for fid, keys in (("derived:summary.depletion", ("material", "summary")),
                      ("derived:bundled:us-49cfr173-a2", ("radiological",))):
        found = next((i for i in plan.items if i["id"] == fid), None)
        if found is None:
            continue
        merged = json.loads((ROOT / found["file"]).read_text())
        for key in keys:
            rich_doc[key] = merged[key]
            rich_rules.append(f"{key}: taken from the merged {fid}")
    rich_spec_path = dump(rich_doc, W / "merged" / "rich_spec.json")

    def run_rich(ctx):
        rich_result.parent.mkdir(parents=True, exist_ok=True)
        checks = [ctx.call([ctx.candidate, "run", rich_spec_path, rich_result], expect=0)]
        if checks[0]["ok"]:
            ctx.state["rich_info"] = result_info(rich_result)
        return checks
    item = plan.add(id="support:run-rich-spec", category="support", source=[rel(rich_spec_path)],
                    classification="support", merge_rules=rich_rules,
                    action="run the gate spec with the handbook's summary (with depletion) and bundled A2 table "
                           "(a result that has the sections `report` tables need)")
    item["run"] = run_rich

    # ---- import-fispact
    imp_dir = W / "import"
    key = ("import-fispact", "")
    imp_sources = sources(key)
    if key in by_key:
        def imp_post(path):
            def post(ctx):
                return [ctx.call([ctx.candidate, "validate", path], expect=0),
                        ctx.call([ctx.candidate, "validate", path, "--files"], expect=0)]
            return post

        def imp_check(path):
            def check(out, err):
                if not Path(path).is_file():
                    return f"{rel(path)} was not written"
                if not Path(str(path)[:-5] + ".import.json").is_file():
                    return "the .import.json report was not written"
                return None
            return check

        out1 = imp_dir / "default.json"
        add_cmd_item("cmd:import-fispact:default", "cli_import_fispact", imp_sources,
                     "import-fispact DECK.i OUT.json (no options), then validate and validate --files",
                     lambda ctx: ([ctx.candidate, "import-fispact", deck, out1], 0),
                     check=imp_check(out1), post=imp_post(out1))
        out2 = imp_dir / "explicit.json"
        add_cmd_item("cmd:import-fispact:explicit-inputs", "cli_import_fispact", imp_sources,
                     "import-fispact with --fluxes --files --library --decay, then validate --files",
                     lambda ctx: ([ctx.candidate, "import-fispact", deck, out2, "--fluxes", fluxes, "--files",
                                   files, "--library", ROOT / lib, "--decay", ROOT / decay], 0),
                     check=imp_check(out2), post=imp_post(out2))
        out3 = imp_dir / "covariance.json"
        add_cmd_item("cmd:import-fispact:covariance", "cli_import_fispact", imp_sources,
                     "import-fispact with --covariance (paired with --library), then validate --files",
                     lambda ctx: ([ctx.candidate, "import-fispact", deck, out3, "--library", ROOT / lib, "--decay",
                                   ROOT / decay, "--covariance", ROOT / COV_REL], 0),
                     check=imp_check(out3), post=imp_post(out3))

        def strict_builder(target_deck, out_path, baseline):
            def build(ctx):
                if not Path(baseline).is_file():
                    return None, f"baseline import {rel(baseline)} missing (earlier item failed)"
                count = len(unsupported_in(baseline))
                ctx.state[f"strict:{out_path.name}"] = count
                return [ctx.candidate, "import-fispact", target_deck, out_path, "--strict"], \
                    ("nonzero" if count else 0)
            return build
        out4 = imp_dir / "strict.json"
        add_cmd_item("cmd:import-fispact:strict", "cli_import_fispact", imp_sources,
                     "import-fispact --strict: fails exactly when the (non-strict) report lists an unsupported "
                     "keyword", strict_builder(deck, out4, out1.with_suffix(".import.json")))
        if bad_deck is not None:
            bad_dir = imp_dir / "unsupported"
            out5, out6 = bad_dir / "plain.json", bad_dir / "strict.json"

            def run_bad(ctx):
                bad_args = ["--files", files, "--fluxes", fluxes]
                checks = [ctx.call([ctx.candidate, "import-fispact", bad_deck, out5] + bad_args, expect=0)]
                if not checks[0]["ok"]:
                    return checks
                found = unsupported_in(out5.with_suffix(".import.json"))
                checks.append({"argv": ["(report check)"], "ok": bool(found),
                               "stdout_tail": f"unsupported keywords in the report: {found}",
                               "check_failed": None if found else
                               f"the handbook lists {bad_keyword} under 'Not translated' but the report has no "
                               "unsupported entry"})
                checks.append(ctx.call([ctx.candidate, "import-fispact", bad_deck, out6, "--strict"] + bad_args,
                                       expect="nonzero" if found else 0))
                return checks
            item = plan.add(id="cmd:import-fispact:strict-unsupported", category="cli_import_fispact",
                            source=imp_sources, classification="synopsis expansion",
                            action=f"{bad_origin} ({bad_deck.name}) has {bad_keyword}, which the handbook lists as "
                                   "unsupported: the report must say so and --strict must fail")
            item["run"] = run_bad
        else:
            plan.add(id="cmd:import-fispact:strict-unsupported", category="cli_import_fispact", gating=False,
                     source=imp_sources, classification="synopsis expansion", outcome="info",
                     action="no FNS deck carries a keyword the handbook lists as unsupported; not exercised")

    # ---- literal walkthrough (import, validate, run)
    walk_dir = W / "walkthrough"
    for command, record in by_key.get(key, []):
        if command.synopsis:
            continue
        block_commands = [c for c in commands if c.block is command.block]
        record["scope"] = "H4 (literal walkthrough)"

        def rewrite(c):
            out = []
            for token in c.tokens[1:]:
                if token.endswith(".i"):
                    out.append(deck)
                elif token.endswith(".json"):
                    out.append(walk_dir / Path(token).name)
                else:
                    out.append(token)
            return out
        steps = [rewrite(c) for c in block_commands]
        for c in block_commands:
            for r in plan.commands:
                if r["source"] == c.source and r["form"] == "literal":
                    r["scope"] = "H4 (literal walkthrough)"

        def run_walk(ctx, steps=steps):
            walk_dir.mkdir(parents=True, exist_ok=True)
            checks = []
            for argv in steps:
                checks.append(ctx.call([ctx.candidate] + argv, expect=0))
                if not checks[-1]["ok"]:
                    break
            return checks
        item = plan.add(id=f"cmd:walkthrough:{command.block.file[:-3]}:{command.start}", category="walkthrough",
                        source=[c.source for c in block_commands], classification="literal sequence",
                        action="run the shown sequence with DECK -> the FNS Fe 5-minute deck and each .json "
                               "-> the workdir: " + "; ".join(" ".join(c.tokens[1:3]) for c in block_commands))
        item["run"] = run_walk

    # ---- report
    key = ("report", "")
    rep_sources = sources(key)
    if key in by_key:
        variants = [
            ("all", [], None, None, []),
            ("tsv-tab1-tab2-step1", ["--tables", "tab1,tab2", "--format", "tsv", "--step", "1"], ["tab1", "tab2"],
             "1", []),
            ("markdown-totals-tab2", ["--tables", "totals,tab2", "--format", "markdown"], ["totals", "tab2"],
             None, []),
            ("text-totals", ["--tables", "totals", "--format", "text", "--step", "all"], ["totals"], "all", []),
            ("timeseries-doc-nuclides", ["--tables", "timeseries", "--nuclides", "Co60,Mn54"], ["timeseries"],
             None, ["Co60", "Mn54"]),
            ("timeseries-present-nuclides", ["--tables", "timeseries,tab1,tab2", "--nuclides", "@TOP"],
             ["timeseries", "tab1", "tab2"], None, ["@TOP"]),
            ("dominant-elements", ["--tables", "dominant,elements"], ["dominant", "elements"], None, []),
            ("tab3", ["--tables", "tab3"], ["tab3"], None, []),
            ("tab4", ["--tables", "tab4"], ["tab4"], None, []),
        ]
        for which, rpath, skey in (("base", base_result, "base_info"), ("rich", rich_result, "rich_info")):
            for label, extra, tables, step, nuclides in variants:
                def build(ctx, rpath=rpath, skey=skey, extra=extra, tables=tables, step=step, nuclides=nuclides):
                    info = ctx.state.get(skey)
                    if info is None:
                        return None, f"the result {rel(rpath)} was not produced (earlier item failed)"
                    top = ",".join(info["top"])
                    names = list(info["top"]) if nuclides == ["@TOP"] else list(nuclides)
                    argv = [ctx.candidate, "report", rpath] + [top if a == "@TOP" else a for a in extra]
                    return argv, report_expectation(info, tables, step, names)
                add_cmd_item(f"cmd:report:{which}:{label}", "cli_report", rep_sources,
                             f"report on the {which} result ({label}); exit 0 iff a requested table can be built "
                             "and every named nuclide is in a selected step", build)
            out_dir = W / "report" / which

            def build_out(ctx, rpath=rpath, skey=skey, out_dir=out_dir):
                info = ctx.state.get(skey)
                if info is None:
                    return None, f"the result {rel(rpath)} was not produced (earlier item failed)"
                return [ctx.candidate, "report", rpath, "--format", "csv", "--out", out_dir], 0

            def check_out(out, err, out_dir=out_dir):
                written = sorted(p.name for p in out_dir.glob("*")) if out_dir.is_dir() else []
                return None if written else f"--out wrote no file under {rel(out_dir)}"
            add_cmd_item(f"cmd:report:{which}:out-dir", "cli_report", rep_sources,
                         f"report on the {which} result with --out DIR (one file per table)", build_out,
                         check=check_out)

    # ---- inspect
    def expand_inspect(sub, specs):
        key = ("inspect", sub)
        if key not in by_key:
            return
        for label, argv_tail, check_path in specs:
            def build(ctx, argv_tail=argv_tail):
                return [ctx.candidate, "inspect", sub] + list(argv_tail), 0

            def check(out, err, check_path=check_path, argv_tail=argv_tail):
                if "--table" in argv_tail:
                    return None if out.strip() else "empty table"
                if check_path and not Path(check_path).is_file():
                    return f"--out wrote no file {rel(check_path)}"
                text = Path(check_path).read_text() if check_path else out.decode(errors="replace")
                try:
                    json.loads(text)
                except ValueError as error:
                    return f"output is not JSON: {error}"
                return None
            add_cmd_item(f"cmd:inspect:{sub}:{label}", "cli_inspect", sources(key),
                         f"inspect {sub} {' '.join(rel(a) for a in argv_tail)}", build, check=check)

    ins = W / "inspect"
    dec = ROOT / decay
    libp = ROOT / lib
    expand_inspect("decay", [
        ("nuclides", [dec, "Co60", "Mn54"], None),
        ("isomer-table", [dec, "Ta182m1", "--table"], None),
        ("all-to-file", [dec, "--out", ins / "decay_all.json"], ins / "decay_all.json"),
    ])
    expand_inspect("xs", [
        ("library", [libp], None),
        ("target", [libp, "--target", "Fe56"], None),
        ("target-mt", [libp, "--target", "Fe56", "--mt", "16"], None),
        ("target-mt-table", [libp, "--target", "Fe56", "--mt", "16", "--table"], None),
        ("mt-to-file", [libp, "--mt", "102", "--out", ins / "xs_mt102.json"], ins / "xs_mt102.json"),
        ("spectrum-run-spec", [libp, "--target", "Fe56", "--spectrum", GATE_SPEC], None),
        ("spectrum-object", [libp, "--target", "Fe56", "--mt", "16", "--spectrum", spectrum_json], None),
    ])
    expand_inspect("yields", [
        ("one-evaluation", [nfy[0]], None),
        ("parent", [nfy[0], nfy[1], "--parent", "Pu239"], None),
        ("energy", [nfy[0], "--energy", "14000000"], None),
        ("energy-table", [nfy[0], "--energy", "0.0253", "--table"], None),
        ("cumulative", [nfy[0], "--cumulative"], None),
    ])
    expand_inspect("library", [
        ("json", [libp], None),
        ("table", [libp, "--table"], None),
    ])
    groups = [(f"{name}", [name], None) for name, _ in group_names]
    groups += [("fispact-709x4", ["fispact-709x4"], None), ("fispact-1102x2", ["fispact-1102x2"], None),
               ("json-file", [group_json], None), ("table", ["fispact-162", "--table"], None),
               ("to-file", ["fispact-709", "--out", ins / "groups_709.json"], ins / "groups_709.json")]
    expand_inspect("groups", groups)

    # every in-scope synopsis must be fully understood: a subcommand or flag the expansions do not use is reported
    used_flags = {("inspect", "decay"): {"--table", "--out"},
                  ("inspect", "xs"): {"--target", "--mt", "--spectrum", "--table", "--out"},
                  ("inspect", "yields"): {"--parent", "--energy", "--cumulative", "--table", "--out"},
                  ("inspect", "library"): {"--table", "--out"}, ("inspect", "groups"): {"--table", "--out"},
                  ("report", ""): {"--tables", "--format", "--out", "--step", "--nuclides"},
                  ("import-fispact", ""): {"--fluxes", "--files", "--library", "--decay", "--covariance",
                                           "--strict"},
                  ("new", ""): {"--data-dir"}}
    for (cmd, sub), entries in by_key.items():
        known = used_flags.get((cmd, sub))
        if known is None:
            for command, _ in entries:
                plan.unclassified.append({"id": f"cmd:{cmd}:{sub}", "source": command.source,
                                          "why": f"no expansion for `{cmd} {sub}`"})
            continue
        for command, _ in entries:
            extra = [f for f in command.flags if f not in known]
            if command.synopsis and extra:
                plan.unclassified.append({"id": f"cmd:{cmd}:{sub}", "source": command.source,
                                          "why": f"flags not exercised by any expansion: {extra}"})
            if command.synopsis and cmd == "new" and "--data-dir" in command.flags:
                pass


# ------------------------------------------------------------------------------ features


def feature_coverage(plan, lines_by_file):
    out = OrderedDict()
    for feature, needles in FEATURES.items():
        hits = []
        for item_id, source, paths in plan.fragments_seen:
            if any(n in paths for n in needles):
                hits.append({"item": item_id, "source": source})
        entry = {"covered": bool(hits), "fragments": hits}
        if not hits:
            pattern = re.compile(FEATURE_PROSE[feature])
            prose = []
            for name in sorted(lines_by_file):
                for number, line in enumerate(lines_by_file[name], 1):
                    if pattern.search(line) and len(prose) < 3:
                        prose.append(f"docs/guide/{name}:{number}")
            entry["prose_only_at"] = prose
        out[feature] = entry
    return out


# ------------------------------------------------------------------------------ driver


GATE_SPEC_DOC = None


def build_plan(workdir, fns_root):
    global GATE_SPEC_DOC
    GATE_SPEC_DOC = json.loads(GATE_SPEC.read_text(encoding="utf-8"))
    plan = Plan(workdir, fns_root)
    blocks_by_file, lines_by_file = OrderedDict(), OrderedDict()
    for path in sorted(GUIDE.glob("*.md")):
        blocks, lines = parse_markdown(path)
        blocks_by_file[path.name], lines_by_file[path.name] = blocks, lines
    extract_json_blocks(plan, blocks_by_file)
    add_derived_items(plan, blocks_by_file, lines_by_file)
    commands = extract_commands(blocks_by_file)
    build_command_items(plan, commands, lines_by_file, None)
    plan.features = feature_coverage(plan, lines_by_file)
    plan.blocks_by_file, plan.lines_by_file = blocks_by_file, lines_by_file
    return plan


def required_inputs(plan):
    deck, files, fluxes = deck_inputs(plan.fns_root)
    needed = [("FNS deck", deck), ("FNS files file", files), ("FNS fluxes file", fluxes),
              ("gate spec", GATE_SPEC), ("study example", STUDY_SPEC), ("shielding example", SHIELD_SPEC),
              ("library", ROOT / GATE_SPEC_DOC["library"]["path"]),
              ("decay data", ROOT / GATE_SPEC_DOC["decay"]["primary"]), ("covariance", ROOT / COV_REL),
              ("fission yields (U-235)", NFY_DIR / "nfy-092_U_235.endf"),
              ("fission yields (Pu-239)", NFY_DIR / "nfy-094_Pu_239.endf"), ("protocol", PROTOCOL),
              ("conventions", CONVENTION_DOC), ("handbook", GUIDE)]
    return [f"{label}: {path}" for label, path in needed if path is None or not Path(path).exists()]


def print_classification(plan):
    print("== JSON blocks")
    for b in plan.blocks:
        print(f"  {b['kind']:<24} {b['source']:<44} {b['parse']:<7} {b['reason'][:70]}")
    print("== commands")
    for c in plan.commands:
        print(f"  {c['form']:<8} {c['scope']:<28} {c['source']:<40} {c['text'][:70]}")
    print("== items")
    for i in plan.items:
        print(f"  {i['category']:<20} {i['id']:<56} {'gating' if i.get('gating', True) else 'info':<6} "
              f"{i.get('action', '')[:70]}")
    print("== feature coverage")
    for feature, entry in plan.features.items():
        where = ", ".join(f["source"] for f in entry["fragments"]) if entry["covered"] else \
            "NO FENCED FRAGMENT (prose at " + ", ".join(entry.get("prose_only_at", [])) + ")"
        print(f"  {feature:<36} {where}")
    if plan.unclassified:
        print("== UNCLASSIFIED")
        for u in plan.unclassified:
            print(f"  {u['source']}: {u['why']}")


def summarize(plan):
    counts = Counter()
    for i in plan.items:
        counts[i["category"]] += 1
    block_kinds = Counter(b["kind"] for b in plan.blocks)
    command_forms = Counter((c["scope"], c["form"]) for c in plan.commands)
    return {"items_by_category": dict(sorted(counts.items())), "json_blocks": len(plan.blocks),
            "json_blocks_by_kind": dict(sorted(block_kinds.items())),
            "commands": len(plan.commands),
            "commands_by_scope_form": {f"{k[0]} / {k[1]}": v for k, v in sorted(command_forms.items())},
            "unclassified": len(plan.unclassified),
            "features_covered": [f for f, e in plan.features.items() if e["covered"]],
            "features_without_fenced_fragment": [f for f, e in plan.features.items() if not e["covered"]]}


def self_check(plan):
    print_classification(plan)
    summary = summarize(plan)
    print("== counts")
    print(json.dumps(summary, indent=1))
    print(f"files written under {rel(plan.workdir)} (blocks/, complete/, merged/, docs/)")
    return 1 if plan.unclassified else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate", help="release binary under test")
    ap.add_argument("--workdir", default="target/p140-docs")
    ap.add_argument("--out", default="results/p140_docs.json")
    ap.add_argument("--timeout", type=int, default=600, help="seconds allowed per command")
    ap.add_argument("--fns", default=str(DEFAULT_FNS), help="root of the FNS deck corpus")
    ap.add_argument("--self-check", action="store_true", help="extract and classify only; run no binary")
    args = ap.parse_args(argv)
    workdir = Path(args.workdir)
    workdir = workdir if workdir.is_absolute() else ROOT / workdir
    fns_root = Path(os.path.expanduser(args.fns)).resolve()
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else ROOT / out_path
    if not args.self_check and not args.candidate:
        ap.error("--candidate is required unless --self-check is given")
    workdir.mkdir(parents=True, exist_ok=True)
    plan = build_plan(workdir, fns_root)
    missing = required_inputs(plan)
    if args.self_check:
        if missing:
            print("note: inputs a real run needs are missing:\n  " + "\n  ".join(missing))
        return self_check(plan)
    candidate = Path(args.candidate).resolve()
    if not candidate.is_file():
        missing.insert(0, f"candidate binary: {candidate}")
    if missing:
        print("missing inputs:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2

    ctx = Context(candidate, args.timeout, plan)
    record = {"protocol": "ACTINV-P140", "gate": "H4", "checker": record_file(Path(__file__).resolve()),
              "protocol_file": record_file(PROTOCOL), "candidate_binary": record_file(candidate),
              "gate_spec": record_file(GATE_SPEC), "conventions_doc": record_file(CONVENTION_DOC),
              "handbook_files": {p.name: sha256_file(p) for p in sorted(GUIDE.glob("*.md"))},
              "complete_spec_rule": COMPLETE_RULE, "fns_root": str(fns_root), "cwd": str(ROOT),
              "timeout_s": args.timeout, "classification": {"json_blocks": plan.blocks, "commands": plan.commands,
                                                            "unclassified": plan.unclassified},
              "features": plan.features, "items": []}
    started = time.monotonic()
    # the support runs go first; every other item runs in document order
    ordered = sorted(plan.items, key=lambda i: 0 if i["category"] == "support" else 1)
    width = max(len(i["id"]) for i in plan.items)
    for item in ordered:
        entry = {k: v for k, v in item.items() if k != "run"}
        if item["run"] is None:
            entry.setdefault("outcome", "info")
        else:
            try:
                checks = item["run"](ctx)
            except Exception as error:  # a checker defect must not hide the other items
                checks = [{"argv": [], "ok": False, "check_failed": f"checker error: {type(error).__name__}: {error}"}]
            entry["checks"] = checks
            entry["outcome"] = "pass" if passed(checks) else "fail"
        record["items"].append(entry)
        tag = {"pass": "PASS", "fail": "FAIL", "info": "INFO"}[entry["outcome"]]
        detail = ""
        if entry["outcome"] == "fail":
            bad = next((c for c in entry["checks"] if not c["ok"]), {})
            detail = (f" exit {bad.get('exit_code')} (expected {bad.get('expected_exit')}) "
                      f"{bad.get('check_failed') or bad.get('stderr_tail', '')[-160:]}").replace("\n", " ")
        print(f"{tag:<4} {entry['id']:<{width}} {entry['source'][0] if entry['source'] else ''} "
              f"{entry.get('action', '')[:60]}{detail}")
    gating = [e for e in record["items"] if e.get("gating", True)]
    counts = Counter(e["outcome"] for e in gating)
    per_category = {}
    for e in record["items"]:
        c = per_category.setdefault(e["category"], Counter())
        c[e["outcome"]] += 1
    failures = [e["id"] for e in gating if e["outcome"] != "pass"]
    verdict = "PASS" if not failures and not plan.unclassified else "FAIL"
    record["counts"] = {"gating_items": len(gating), "pass": counts.get("pass", 0), "fail": counts.get("fail", 0),
                        "info_items": sum(1 for e in record["items"] if not e.get("gating", True)),
                        "by_category": {k: dict(v) for k, v in sorted(per_category.items())},
                        "plan": summarize(plan)}
    record["failures"] = failures
    record["unclassified"] = plan.unclassified
    record["wall_s"] = round(time.monotonic() - started, 1)
    record["verdict"] = verdict
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"H4 {verdict}: {counts.get('pass', 0)}/{len(gating)} gating items pass, {len(failures)} fail, "
          f"{len(plan.unclassified)} unclassified; record {rel(out_path)}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
