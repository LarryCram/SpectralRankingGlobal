"""
run_field_bloc.py — OA field rankings filtered to country blocs.

Runs stages 3+4 for OA field_idx 11–36 for each entry in BLOC_RUNS:
  baseline    : all countries — no filter
  OECDG20     : OECD ∪ G20 (46 countries)
  OECDG20CIA  : OECDG20 − CIA  (43 countries)
  CIAA        : {AU, CN, IN, US}
  BASELINECIA : WORLD − CIA  (all OA countries except CN, IN, US)

Candidacy parquets are global (shared with baseline).
Both edge lists and rankings are cached by file existence.

Usage:
  .venv/bin/python pipeline/run_field_bloc.py
"""

import sys
import json
import time
from dataclasses import replace
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from util import load_config, load_settings, load_runs, load_world, BLOC_RUNS, CIA, guard
from build_edge_list_field import build_edge_list
from run_rankings import rank_field


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--yes', '-y', action='store_true',
                         help='Rebuild stale outputs without prompting')
    args = parser.parse_args()

    paths    = load_config()
    settings = load_settings()
    runs     = load_runs()
    working  = str(paths.working)

    fw_path = str(paths.working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    cr_path = str(paths.working / f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')

    for p in [fw_path, cr_path]:
        if not Path(p).exists():
            raise FileNotFoundError(f"{p} not found — run build_flat_works.py first")

    world = load_world(fw_path)
    blocs = {**settings.blocs, 'WORLD': (), 'BASELINE-CIA': tuple(sorted(world - CIA))}
    print(f"WORLD: {len(world)} countries  BASELINE-CIA: {len(blocs['BASELINE-CIA'])} countries")

    base_run = runs[0]
    sc_path  = base_run.sc_path(working)
    ic_path  = base_run.ic_path(working)

    for file_label, bloc_key in BLOC_RUNS:
        bloc_codes = blocs[bloc_key]

        print(f"\n{'='*72}")
        print(f"Bloc: {file_label}  ({bloc_key})  {len(bloc_codes)} countries")
        print(f"{'='*72}")
        t_bloc = time.time()

        with duckdb.connect() as db:
            db.execute(f"SET temp_directory = '{paths.working}/.tmp'")
            db.execute(f"SET memory_limit = '{settings.memory_limit}'")
            db.execute(f"SET preserve_insertion_order = {str(settings.preserve_insertion_order).lower()}")

            for fid in settings.all_fields:
                run     = replace(base_run, field_idx=fid, label=file_label, bloc=bloc_key)
                el_path = run.el_path(working)
                rk_path = run.rankings_path(working)

                print(f"\n--- field {fid} ---")
                t_field = time.time()

                el_inputs = [fw_path, cr_path, sc_path, ic_path]
                if guard.ensure_fresh(el_path, *el_inputs, script=__file__,
                                      auto_yes=args.yes, label='edge list'):
                    t0 = time.time()
                    n_el = build_edge_list(
                        db, fw_path, cr_path, sc_path, ic_path,
                        run, el_path, bloc_codes=bloc_codes,
                    )
                    guard.record_build(el_path, *el_inputs, script=__file__,
                                       build_seconds=time.time() - t0)
                    print(f"  edge list: {n_el:,} rows  [{time.time()-t0:.1f}s]")

                rk_inputs  = [el_path, sc_path, ic_path]
                rk_scripts = [__file__, 'pipeline/run_rankings.py',
                              'pipeline/build_csr_field.py', 'pipeline/katz_ranker.py']
                if guard.ensure_fresh(rk_path, *rk_inputs, script=rk_scripts,
                                      auto_yes=args.yes, label='ranking'):
                    t0 = time.time()
                    df, diag = rank_field(db, run, working, rk_path, verbose=True)
                    guard.record_build(rk_path, *rk_inputs, script=rk_scripts,
                                       build_seconds=time.time() - t0)
                    Path(run.diag_path(working)).write_text(json.dumps(diag, indent=2))
                    print(f"  ranked: {len(df):,} units  [{time.time()-t_field:.1f}s]")

        print(f"\nBloc {file_label} complete  [{time.time()-t_bloc:.0f}s total]")


if __name__ == '__main__':
    t0 = time.time()
    main()
    print(f"\nFINISHED  [{time.time()-t0:.0f}s total]")
