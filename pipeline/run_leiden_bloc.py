"""
run_leiden_bloc.py — Leiden rankings filtered to country blocs.

Runs stages 3+4 for leiden_idx 1–5 for each entry in BLOC_RUNS:
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

A fresh DuckDB connection is used per leiden group to prevent memory
accumulation across the large leiden 3/4 edge list builds.

Usage:
  .venv/bin/python pipeline/run_leiden_bloc.py
"""

import sys
import json
import time
from dataclasses import replace
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from util import load_config, load_settings, load_runs, load_world, BLOC_RUNS, CIA
from build_edge_list_field import build_edge_list
from run_rankings import rank_field


def main():
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

    leiden_base = replace(base_run, field_idx=1)
    sc_path = leiden_base.sc_path(working_s)
    ic_path = leiden_base.ic_path(working_s)

    for p in [sc_path, ic_path]:
        if not Path(p).exists():
            raise FileNotFoundError(f"{p} not found — run build_field_candidacy.py first")

    for file_label, bloc_key in BLOC_RUNS:
        bloc_codes = blocs[bloc_key]

        print(f"\n{'='*72}")
        print(f"Bloc: {file_label}  ({bloc_key})  {len(bloc_codes)} countries")
        print(f"{'='*72}")
        t_bloc = time.time()

        for lid in settings.all_leiden_fields:
            run = replace(base_run, field_idx=lid, label=file_label, bloc=bloc_key)

            el_path  = Path(run.el_path(working_s))
            rk_path  = run.rankings_path(working_s)

            print(f"\n--- leiden {lid} ---")
            t_field = time.time()

            with _db() as db:
                if not el_path.exists():
                    t0 = time.time()
                    n_el = build_edge_list(
                        db, fw_path, cr_path, sc_path, ic_path,
                        run, str(el_path),
                        bloc_codes=bloc_codes,
                    )
                    print(f"  edge list: {n_el:,} rows  [{time.time()-t0:.1f}s]")
                else:
                    print(f"  edge list: cached")

                if Path(rk_path).exists():
                    print(f"  ranked: cached")
                else:
                    df, diag = rank_field(db, run, working_s, rk_path, verbose=True)
                    Path(run.diag_path(working_s)).write_text(json.dumps(diag, indent=2))
                    print(f"  ranked: {len(df):,} units  [{time.time()-t_field:.1f}s]")

        print(f"\nBloc {file_label} complete  [{time.time()-t_bloc:.0f}s total]")


if __name__ == '__main__':
    t0 = time.time()
    main()
    print(f"\nFINISHED  [{time.time()-t0:.0f}s total]")
