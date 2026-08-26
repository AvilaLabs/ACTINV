"""ACTINV shared ENDF-6 primitives (own code; ACT-P0 lineage)."""
import re
import numpy as np
_F = re.compile(r"^\s*([+-]?\d*\.?\d*)([+-]\d+)\s*$")
def endf_float(s):
    s = s.strip()
    if not s: return 0.0
    try: return float(s)
    except ValueError:
        m = _F.match(s)
        if m: return float(m.group(1) + "e" + m.group(2))
        raise ValueError(repr(s))
def fields(line): return [line[i*11:(i+1)*11] for i in range(6)]
def ints(line, lo=2, hi=6): return [int(x) for x in fields(line)[lo:hi]]
def read_tab1(lines, i):
    """TAB1 at lines[i]: returns (c1,c2,l1,l2,nr,np, [(nbt,int)...], x, y), next index."""
    f = fields(lines[i]); c1, c2 = endf_float(f[0]), endf_float(f[1]); l1, l2, nr, np_ = (int(x) for x in f[2:6]); i += 1
    nbt = []
    while len(nbt) < nr:
        ff = fields(lines[i]); i += 1
        for k in range(0, 6, 2):
            if len(nbt) < nr: nbt.append((int(ff[k]), int(ff[k+1])))
    x, y = [], []
    while len(x) < np_:
        ff = fields(lines[i]); i += 1
        for k in range(0, 6, 2):
            if len(x) < np_: x.append(endf_float(ff[k])); y.append(endf_float(ff[k+1]))
    return (c1, c2, l1, l2, nr, np_, nbt, x, y), i
def read_list(lines, i):
    f = fields(lines[i]); c1, c2 = endf_float(f[0]), endf_float(f[1]); l1, l2, n1, n2 = (int(x) for x in f[2:6]); i += 1
    vals = []
    while len(vals) < n1:
        vals += [endf_float(x) for x in fields(lines[i])[:min(6, n1 - len(vals))]]; i += 1
    return (c1, c2, l1, l2, n1, n2, vals), i
def sections(path):
    """Yield ((mat, mf, mt), [lines]) for every section of an ENDF-6 file (single or multi-material)."""
    cur, buf = None, []
    with open(path, errors="replace") as fh:
        for line in fh:
            if len(line) < 75: continue
            try: mat, mf, mt = int(line[66:70]), int(line[70:72]), int(line[72:75])
            except ValueError: continue
            if mf == 0 or mt == 0 or mat <= 0:
                if cur is not None and buf: yield cur, buf
                cur, buf = None, []; continue
            key = (mat, mf, mt)
            if key != cur:
                if cur is not None and buf: yield cur, buf
                cur, buf = key, []
            buf.append(line.rstrip("\n"))
    if cur is not None and buf: yield cur, buf


def interp_eval(x, y, nbt, xs):
    """Evaluate a TAB1 (x,y, interpolation regions) at points xs (numpy), honoring INT 1-5."""
    x = np.asarray(x); y = np.asarray(y); out = np.zeros_like(xs, float)
    idx = np.searchsorted(x, xs, side="right") - 1; idx = np.clip(idx, 0, len(x) - 2)
    x1, x2, y1, y2 = x[idx], x[idx + 1], y[idx], y[idx + 1]
    # interpolation law per point: region r covers points up to NBT_r (1-based) -> segment i uses law of first region with NBT > i+1
    laws = np.ones(len(x) - 1, int)
    start = 0
    for nb, law in nbt:
        laws[start:nb - 1] = law; start = nb - 1
    law = laws[idx]
    with np.errstate(divide="ignore", invalid="ignore"):
        t_lin = np.where(x2 != x1, (xs - x1) / (x2 - x1), 0.0)
        t_log = np.where((x2 > 0) & (x1 > 0) & (x2 != x1), np.log(xs / x1) / np.log(x2 / x1), 0.0)
        out = np.where(law == 1, y1, out)
        out = np.where(law == 2, y1 + t_lin * (y2 - y1), out)
        out = np.where(law == 3, y1 + t_log * (y2 - y1), out)
        out = np.where(law == 4, np.where((y1 > 0) & (y2 > 0), y1 * (y2 / y1) ** t_lin, y1 + t_lin * (y2 - y1)), out)
        out = np.where(law == 5, np.where((y1 > 0) & (y2 > 0), y1 * (y2 / y1) ** t_log, y1 + t_log * (y2 - y1)), out)
    out[(xs < x[0]) | (xs > x[-1])] = 0.0
    return out
