"""
run_field_bloc.py — OA field rankings filtered to country blocs.

Runs stages 3+4 for OA field_idx 11–36, for two country blocs:
  OECDG20CIA : OECDG20 minus {CN, IN, US}  (label: OECDG20CIA)
  CIAA       : {AU, CN, IN, US}            (label: CIAA)

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

from util import load_config, load_settings, load_runs
from build_edge_list_field import build_edge_list
from run_rankings import rank_field

BLOCS = [
    ('OECDG20CIA', 'OECDG20-CIA'),
    ('CIAA',       'CIAA'),
]


def main():
    paths    = load_config()
    settings = load_settings()
    runs     = load_runs()
    working  = str(paths.working)

    fw_path = str(paths.working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    cr_path = str(paths.working / f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')

    for p in [fw_path, cr_path]:
        if not Path(p).exists():
            raise FileNotFoundError(f"{p} not found — run build_flat_works.py first")

    base_run = runs[0]
    sc_path  = base_run.sc_path(working)
    ic_path  = base_run.ic_path(working)

    for file_label, bloc_key in BLOCS:
        bloc_codes = settings.blocs[bloc_key]

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

                if not Path(el_path).exists():
                    t0 = time.time()
                    n_el = build_edge_list(
                        db, fw_path, cr_path, sc_path, ic_path,
                        run, el_path, bloc_codes=bloc_codes,
                    )
                    print(f"  edge list: {n_el:,} rows  [{time.time()-t0:.1f}s]")
                else:
                    print(f"  edge list: cached")

                if Path(rk_path).exists():
                    print(f"  ranked: cached")
                else:
                    df, diag = rank_field(db, run, working, rk_path, verbose=True)
                    Path(run.diag_path(working)).write_text(json.dumps(diag, indent=2))
                    print(f"  ranked: {len(df):,} units  [{time.time()-t_field:.1f}s]")

        print(f"\nBloc {file_label} complete  [{time.time()-t_bloc:.0f}s total]")


if __name__ == '__main__':
    t0 = time.time()
    main()
    print(f"\nFINISHED  [{time.time()-t0:.0f}s total]")
