"""
run_area_bloc.py — AREA5 broad-area rankings (util/areas.py), baseline and country blocs.

Runs stages 3+4 for AREA5 areas 1–5 (field_idx = area id; each area is the union of
its member OA fields) for each entry in BLOC_RUNS:
  baseline    : all countries — no filter
  OECDG20     : OECD ∪ G20 (46 countries)
  OECDG20CIA  : OECDG20 − CIA  (43 countries)
  CIAA        : {AU, CN, IN, US}
  BASELINECIA : WORLD − CIA  (all OA countries except CN, IN, US)

Bloc filtering is applied at edge list build time (all-in semantics: a work
is included only if EVERY affiliated institution has a country_code in the
bloc).  Candidacy parquets are global and shared with the baseline run.

BASELINE-CIA codes are derived at runtime by querying flat_works for all
distinct country_codes, then subtracting CIA = {CN, IN, US}.

A fresh DuckDB connection is used per area to prevent memory accumulation across
the large area 3/4 edge list builds.

Outputs: WORKING/rankings_{area}_{window}_{label}.parquet (+ _diag.json, el_ edge lists).
Replaces run_leiden_bloc.py and run_area5.py (CWTS Leiden groups retired 2026-11-28).

Usage:
  .venv/bin/python pipeline/run_area_bloc.py
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
from util.areas import AREA_CODES, AREA_GROUPS, AREA_NAMES
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
    working  = paths.working
    working_s = str(working)

    fw_path = str(working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    cr_path = str(working / f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')

    for p in [fw_path, cr_path]:
        if not Path(p).exists():
            raise FileNotFoundError(f"{p} not found — run build_flat_works.py first")

    world = load_world(fw_path)
    blocs = {**settings.blocs, 'WORLD': (), 'BASELINE-CIA': tuple(sorted(world - CIA))}
    print(f"WORLD: {len(world)} countries  BASELINE-CIA: {len(blocs['BASELINE-CIA'])} countries")

    base_run = runs[0]

    def _db():
        db = duckdb.connect()
        db.execute(f"SET temp_directory = '{working}/.tmp'")
        db.execute(f"SET memory_limit = '{settings.memory_limit}'")
        db.execute(f"SET preserve_insertion_order = {str(settings.preserve_insertion_order).lower()}")
        return db

    area_base = replace(base_run, field_idx=1)
    sc_path = area_base.sc_path(working_s)
    ic_path = area_base.ic_path(working_s)

    for p in [sc_path, ic_path]:
        if not Path(p).exists():
            raise FileNotFoundError(f"{p} not found — run build_field_candidacy.py first")

    for file_label, bloc_key in BLOC_RUNS:
        bloc_codes = blocs[bloc_key]

        print(f"\n{'='*72}")
        print(f"Bloc: {file_label}  ({bloc_key})  {len(bloc_codes)} countries")
        print(f"{'='*72}")
        t_bloc = time.time()

        for aid in settings.all_area_fields:
            run = replace(base_run, field_idx=aid, label=file_label, bloc=bloc_key)

            el_path  = Path(run.el_path(working_s))
            rk_path  = run.rankings_path(working_s)

            print(f"\n--- area {aid} ({AREA_CODES[aid]}) ---")
            t_field = time.time()

            with _db() as db:
                el_inputs = [fw_path, cr_path, sc_path, ic_path]
                if guard.ensure_fresh(str(el_path), *el_inputs, script=__file__,
                                      auto_yes=args.yes, label='edge list'):
                    t0 = time.time()
                    n_el = build_edge_list(
                        db, fw_path, cr_path, sc_path, ic_path,
                        run, str(el_path),
                        bloc_codes=bloc_codes,
                    )
                    guard.record_build(str(el_path), *el_inputs, script=__file__,
                                       build_seconds=time.time() - t0)
                    print(f"  edge list: {n_el:,} rows  [{time.time()-t0:.1f}s]")

                rk_inputs  = [str(el_path), sc_path, ic_path]
                rk_scripts = [__file__, 'pipeline/run_rankings.py',
                              'pipeline/build_csr_field.py', 'pipeline/katz_ranker.py']
                if guard.ensure_fresh(rk_path, *rk_inputs, script=rk_scripts,
                                      auto_yes=args.yes, label='ranking'):
                    t0 = time.time()
                    df, diag = rank_field(db, run, working_s, rk_path, verbose=True)
                    guard.record_build(rk_path, *rk_inputs, script=rk_scripts,
                                       build_seconds=time.time() - t0)
                    diag.update(area_code=AREA_CODES[aid], area_label=AREA_NAMES[aid],
                                member_field_idx=list(AREA_GROUPS[aid]))
                    Path(run.diag_path(working_s)).write_text(json.dumps(diag, indent=2))
                    print(f"  ranked: {len(df):,} units  [{time.time()-t_field:.1f}s]")

        print(f"\nBloc {file_label} complete  [{time.time()-t_bloc:.0f}s total]")


if __name__ == '__main__':
    t0 = time.time()
    main()
    print(f"\nFINISHED  [{time.time()-t0:.0f}s total]")
