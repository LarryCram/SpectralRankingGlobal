"""
run_all_fields.py — Run the full pipeline (stages 3+4) for all fields.

For each non-skipped Run in params.csv, iterates over all OA fields (11–36),
builds edge lists (stage 3) and rankings (stage 4b).

Usage:
  .venv/bin/python pipeline/run_all_fields.py
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


def main():
    paths    = load_config()
    settings = load_settings()
    runs     = load_runs()
    working  = str(paths.working)

    fw_path = str(paths.working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    cr_path = str(paths.working / f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')

    for p in [fw_path, cr_path]:
        if not Path(p).exists():
            raise FileNotFoundError(
                f"{p} not found — run build_flat_works.py first"
            )

    with duckdb.connect() as db:
        db.execute(f"SET temp_directory = '{paths.working}/.tmp'")
        db.execute(f"SET memory_limit = '{settings.memory_limit}'")
        db.execute(f"SET preserve_insertion_order = {str(settings.preserve_insertion_order).lower()}")

        for base_run in runs:
            sc_path = base_run.sc_path(working)
            ic_path = base_run.ic_path(working)
            for p in [sc_path, ic_path]:
                if not Path(p).exists():
                    raise FileNotFoundError(
                        f"{p} not found — run build_field_candidacy.py first"
                    )

            print(f"\n{'='*72}")
            print(f"Run: {base_run.run_id}  "
                  f"τ_s={base_run.tau_s}  τ_u={base_run.tau_u}  "
                  f"m={base_run.m}  α={base_run.alpha}  ρ={base_run.rho}")
            print(f"{'='*72}")
            t_run = time.time()

            for fid in settings.all_fields:
                run = replace(base_run, field_idx=fid)
                el_path = run.el_path(working)
                rk_path = run.rankings_path(working)

                print(f"\n--- field {fid} ---")
                t_field = time.time()

                if not Path(el_path).exists():
                    t0 = time.time()
                    bloc_codes = tuple(settings.blocs.get(run.bloc, ())) if run.bloc else ()
                    n_el = build_edge_list(db, fw_path, cr_path, sc_path, ic_path, run, el_path,
                                           bloc_codes=bloc_codes)
                    print(f"  edge list: {n_el:,} rows  [{time.time()-t0:.1f}s]")
                else:
                    print(f"  edge list: cached  ({el_path})")

                df, diag = rank_field(db, run, working, rk_path, verbose=True)
                Path(run.diag_path(working)).write_text(json.dumps(diag, indent=2))
                print(f"  ranked: {len(df):,} units  [{time.time()-t_field:.1f}s]")

            print(f"\nRun {base_run.run_id} complete  [{time.time()-t_run:.0f}s total]")


if __name__ == '__main__':
    t0 = time.time()
    main()
    print(f"\nFINISHED  [{time.time()-t0:.0f}s total]")
