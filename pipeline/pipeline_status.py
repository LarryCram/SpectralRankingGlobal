"""
pipeline_status.py — "Where am I up to?" report across every guarded output.

Scans WORKING/ plus the project tree (reports/plots directories included —
guard sidecars live next to whatever they guard, not only in WORKING/) for
`*.guard.json` sidecars written by util/guard.py, and re-checks each one's
freshness against its recorded inputs/scripts right now. Also flags parquet
outputs matching known pipeline naming patterns in WORKING/ that have no
guard sidecar at all — these predate the guard system (or were built by a
script that hasn't been wired up yet) and their freshness can't be verified.

Usage:
  .venv/bin/python pipeline/pipeline_status.py
"""

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, guard

PROJECT_ROOT = Path(__file__).parent.parent
EXCLUDE_DIRS = {'.venv', '.git', 'ZARCHIVE', '__pycache__'}

KNOWN_PATTERNS = [
    'flat_works_*.parquet', 'corpus_references_*.parquet',
    '*_cands_*.parquet', 'el_*.parquet', 'rankings_*.parquet',
    'work_v_*.parquet', 'filtered_works_topics.parquet', 'hcw_*.parquet',
    'hca_crossfield_*.parquet', 'enclave_*.parquet',
]


def _find_guard_files() -> list:
    paths = load_config()
    found = set(glob.glob(str(paths.working / '*.guard.json')))
    for p in PROJECT_ROOT.rglob('*.guard.json'):
        if not any(part in EXCLUDE_DIRS for part in p.parts):
            found.add(str(p))
    return sorted(found)


def main():
    paths = load_config()
    working = paths.working

    guard_files = _find_guard_files()
    fresh, stale = [], []
    guarded_outputs = set()

    for gf in guard_files:
        manifest = json.loads(Path(gf).read_text())
        output = manifest['output']
        guarded_outputs.add(Path(output).name)
        inputs = [i['path'] for i in manifest.get('inputs', [])]
        scripts = [s['path'] for s in manifest.get('scripts', [])]
        fr = guard.check_freshness(output, *inputs, script=scripts)
        (fresh if fr.fresh else stale).append((output, fr.reason))

    unmanaged = []
    for pattern in KNOWN_PATTERNS:
        for p in glob.glob(str(working / pattern)):
            # Symlinks (e.g. rho1/om1/beta1 edge lists aliasing baseline) have
            # no independent state — their freshness is the target's freshness.
            if Path(p).name not in guarded_outputs and not Path(p).is_symlink():
                unmanaged.append(p)
    unmanaged.sort()

    print(f"{len(fresh)} fresh, {len(stale)} stale  "
          f"({len(guard_files)} guarded outputs, {len(unmanaged)} unmanaged)\n")

    if stale:
        print(f"STALE ({len(stale)}) — rebuild before trusting downstream work:")
        for o, r in stale:
            print(f"  {Path(o).name}")
            print(f"      {r}")
        print()

    if unmanaged:
        print(f"UNMANAGED ({len(unmanaged)}) — no guard record, freshness unverified "
              f"(predates the guard system or was built by an unwired script):")
        for p in unmanaged:
            print(f"  {Path(p).name}")
        print()

    if not stale and not unmanaged:
        print("Everything guarded is fresh.")


if __name__ == '__main__':
    main()
