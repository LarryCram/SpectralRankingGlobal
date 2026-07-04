"""
guard.py — Freshness tracking for pipeline outputs ("guard files").

Problem this solves: every build stage in this project caches by bare file
existence (`if out_path.exists(): skip`). When an upstream input (or the
producing script itself) changes but the output file is never deleted, the
stage silently skips rebuilding and downstream work proceeds on stale data
with no visible symptom — this happened twice in one session on 2026-07-04
(baseline/bloc rankings left stale under freshly-rebuilt edge lists; a
leiden-1 tau20 ranking left stale the same way).

Fix: every guarded output gets a `<output>.guard.json` sidecar recording the
exact (path, mtime, size) of each input and the producing script's own mtime
at build time. `ensure_fresh()` replaces the bare existence check: it compares
the sidecar against current state and only reports "fresh" if nothing has
moved. If no sidecar exists yet (output predates this system), it falls back
to a plain mtime comparison against the declared inputs/script — weaker (an
unrelated snapshot refresh wouldn't be caught), but self-healing: the first
rebuild after that writes an authoritative sidecar.

Usage pattern at a cache checkpoint:

    if guard.ensure_fresh(out_path, *input_paths, script=__file__,
                          auto_yes=args.yes, label='rankings field 14'):
        t0 = time.time()
        ... do the build ...
        guard.record_build(out_path, *input_paths, script=__file__,
                           build_seconds=time.time() - t0)
    else:
        ... use the existing output ...

`ensure_fresh` only prompts (blocking on stdin) when the output is stale AND
the previously recorded build took longer than `prompt_after_seconds` (default
180s) — or there's no timing history at all, in which case cost is unknown
and it prompts to be safe. Pass `auto_yes=True` (wire to a --yes/-y CLI flag)
for unattended/background runs.
"""

import glob as _glob
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Union


def _guard_path(output_path) -> Path:
    return Path(str(output_path) + '.guard.json')


def _expand(paths: Sequence[Union[str, Path]]) -> list:
    """Expand glob patterns (e.g. 'works/*.parquet') into concrete file lists."""
    out = []
    for p in paths:
        s = str(p)
        if any(ch in s for ch in '*?['):
            out.extend(sorted(_glob.glob(s)))
        else:
            out.append(s)
    return out


def _as_list(x) -> list:
    if x is None:
        return []
    if isinstance(x, (str, Path)):
        return [str(x)]
    return [str(p) for p in x]


def _mtime(p) -> float:
    return Path(p).stat().st_mtime


@dataclass
class Freshness:
    fresh: bool
    reason: str
    last_build_seconds: Optional[float] = None


def check_freshness(output_path, *input_paths, script=None) -> Freshness:
    """
    Compare an output's guard sidecar (or, failing that, plain mtimes) against
    the current state of its declared inputs and producing script(s).
    """
    out = Path(output_path)
    if not out.exists():
        return Freshness(False, "output does not exist")

    inputs = _expand(input_paths)
    scripts = _as_list(script)
    gpath = _guard_path(out)
    out_mtime = out.stat().st_mtime

    if not gpath.exists():
        newer = [str(p) for p in inputs if Path(p).exists() and _mtime(p) > out_mtime]
        newer += [f"{s} (producing script)" for s in scripts
                  if Path(s).exists() and _mtime(s) > out_mtime]
        if newer:
            return Freshness(False, "no guard record; newer than output: " + ", ".join(newer))
        return Freshness(True, "no guard record; mtime check passed (unverified)")

    manifest = json.loads(gpath.read_text())
    reasons = []

    rec_scripts = {s['path']: s for s in manifest.get('scripts', [])}
    for s in scripts:
        rec = rec_scripts.get(str(s))
        if rec is None:
            reasons.append(f"script {s} not recorded in guard (new dependency)")
        elif Path(s).exists() and _mtime(s) > rec['mtime'] + 1e-6:
            reasons.append(f"{s} modified since build")

    rec_inputs = {i['path']: i for i in manifest.get('inputs', [])}
    for p in inputs:
        if not Path(p).exists():
            reasons.append(f"input {p} missing")
            continue
        rec = rec_inputs.get(p)
        if rec is None:
            reasons.append(f"input {p} not recorded in guard (new dependency)")
        elif _mtime(p) > rec['mtime'] + 1e-6 or Path(p).stat().st_size != rec['size']:
            reasons.append(f"input {p} changed since build")

    if reasons:
        return Freshness(False, "; ".join(reasons), manifest.get('build_seconds'))
    return Freshness(True, "guard record matches current inputs", manifest.get('build_seconds'))


def record_build(output_path, *input_paths, script=None,
                  build_seconds: float = None, **extra) -> None:
    """Write the guard sidecar after a successful build."""
    out = Path(output_path)
    inputs = _expand(input_paths)
    scripts = _as_list(script)
    manifest = {
        'output': str(out),
        'built_at': time.time(),
        'build_seconds': build_seconds,
        'scripts': [
            {'path': s, 'mtime': _mtime(s)} for s in scripts if Path(s).exists()
        ],
        'inputs': [
            {'path': p, 'mtime': _mtime(p), 'size': Path(p).stat().st_size}
            for p in inputs if Path(p).exists()
        ],
    }
    manifest.update(extra)
    _guard_path(out).write_text(json.dumps(manifest, indent=2))


def ensure_fresh(output_path, *input_paths, script=None,
                  prompt_after_seconds: float = 180, auto_yes: bool = False,
                  label: str = None) -> bool:
    """
    Returns True  -> caller should (re)build now (and should call record_build
                     on success).
    Returns False -> existing output is fresh (or the user declined a stale
                     rebuild); caller should use it as-is.
    """
    name = label or Path(output_path).name
    fr = check_freshness(output_path, *input_paths, script=script)

    if fr.fresh:
        print(f"  {name}: cached (fresh) — {fr.reason}")
        return False

    print(f"  {name}: STALE — {fr.reason}")

    if not Path(output_path).exists():
        return True  # nothing to lose; no need to prompt

    expensive = fr.last_build_seconds is None or fr.last_build_seconds > prompt_after_seconds
    if expensive and not auto_yes:
        est = (f"~{fr.last_build_seconds/60:.1f} min last time"
               if fr.last_build_seconds else "unknown, no history — could be slow")
        try:
            resp = input(f"    Rebuild {name}? ({est}) [y/N] ").strip().lower()
        except EOFError:
            resp = 'n'
        if resp not in ('y', 'yes'):
            print(f"    Skipping rebuild of {name} — using stale output at caller's request.")
            return False
    return True
