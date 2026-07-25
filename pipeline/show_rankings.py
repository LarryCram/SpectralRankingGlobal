"""
show_rankings.py — Display field rankings with OA names and run diagnostics.

Reads  : WORKING/rankings_{field_idx}_{window}.parquet
         WORKING/rankings_{field_idx}_{window}_diag.json
         OPENALEX/parquet_converted/sources.parquet
         OPENALEX/parquet_converted/institutions.parquet

Usage:
  .venv/bin/python pipeline/show_rankings.py --field 14
  .venv/bin/python pipeline/show_rankings.py --field 14 --n 30 --window 2020_2024
"""

import sys
import json
import argparse
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, load_settings


def show_rankings(field_idx: int, window: str, n: int = 25) -> None:
    paths   = load_config()
    working = paths.working

    rk_path   = working / f'rankings_{field_idx}_{window}.parquet'
    diag_path = working / f'rankings_{field_idx}_{window}_diag.json'
    src_path  = f'{paths.openalex}/sources.parquet'
    inst_path = f'{paths.openalex}/institutions.parquet'

    if not rk_path.exists():
        raise FileNotFoundError(
            f"{rk_path} not found — run pipeline/run_rankings.py first."
        )

    # ── Diagnostics header ────────────────────────────────────────────────────
    diag = json.loads(diag_path.read_text()) if diag_path.exists() else {}
    if diag:
        n_s_drop = diag['n_s_cands'] - diag['n_s_ranked']
        n_u_drop = diag['n_u_cands'] - diag['n_u_ranked']
        print(f"Field {diag['field_idx']}  window {diag['window']}"
              f"  label={diag.get('label','')}  m={diag['m']}"
              f"  α={diag['alpha']}  ρ={diag['rho']}"
              f"  τ_s={diag['tau_s']}/yr  τ_u={diag['tau_u']}/yr")
        print(f"Algorithm : {diag['algo']}")
        if diag.get('lam1'):
            print(f"Eigenvalues: λ₁={diag['lam1']:.6f}  λ₂={diag['lam2']:.6f}"
                  f"  gap={diag['spectral_gap']:.2e}")
        print(f"Sources   : {diag['n_s_cands']:,} passed τ  →  "
              f"{diag['n_s_ranked']:,} in edge list  ({n_s_drop:,} dropped, "
              f"no intra-corpus citations)")
        print(f"Institutions: {diag['n_u_cands']:,} passed τ  →  "
              f"{diag['n_u_ranked']:,} in edge list  ({n_u_drop:,} dropped)")
        print()

    settings = load_settings()
    tc0, tc1 = window.split('_')
    fw_path = str(paths.working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')

    with duckdb.connect() as db:

        # ── Sources ───────────────────────────────────────────────────────────
        src_df = db.execute(f"""
            WITH work_counts AS (
                SELECT source_idx,
                       COUNT(DISTINCT work_idx) AS total_works
                FROM '{fw_path}'
                WHERE field_idx = {field_idx}
                  AND publication_year BETWEEN {tc0} AND {tc1}
                GROUP BY source_idx
            )
            SELECT
                rk.rank_v,
                rk.rank_pi,
                s.display_name                                AS name,
                s.host_organization_name                      AS publisher,
                s.country_code                                AS country,
                ROUND(rk.v,  3)                               AS v,
                ROUND(rk.pi, 6)                               AS pi,
                wc.total_works,
                ROUND(rk.a_p, 1)                              AS weighted_works
            FROM '{rk_path}' rk
            JOIN '{src_path}' s
              ON rk.unit_idx = CAST(
                     REGEXP_REPLACE(s.id, 'https://openalex.org/S', '') AS BIGINT)
            LEFT JOIN work_counts wc ON rk.unit_idx = wc.source_idx
            WHERE rk.unit_type = 'S'
            ORDER BY rk.rank_v
            LIMIT {n}
        """).df()

        # ── Institutions ──────────────────────────────────────────────────────
        inst_df = db.execute(f"""
            WITH work_counts AS (
                SELECT institution_idx,
                       COUNT(DISTINCT work_idx) AS total_works
                FROM '{fw_path}'
                WHERE field_idx = {field_idx}
                  AND publication_year BETWEEN {tc0} AND {tc1}
                GROUP BY institution_idx
            )
            SELECT
                rk.rank_v,
                rk.rank_pi,
                i.display_name                                AS name,
                i.geo.country                                 AS country,
                i.type,
                ROUND(rk.v,  3)                               AS v,
                ROUND(rk.pi, 6)                               AS pi,
                wc.total_works,
                ROUND(rk.a_p, 1)                              AS weighted_works
            FROM '{rk_path}' rk
            JOIN '{inst_path}' i
              ON rk.unit_idx = CAST(
                     REGEXP_REPLACE(i.id, 'https://openalex.org/I', '') AS BIGINT)
            LEFT JOIN work_counts wc ON rk.unit_idx = wc.institution_idx
            WHERE rk.unit_type = 'U'
            ORDER BY rk.rank_v
            LIMIT {n}
        """).df()

    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 200)
    pd.set_option('display.max_colwidth', 60)
    pd.set_option('display.float_format', '{:.3f}'.format)

    print(f"Top {n} sources (ranked by v = size-adjusted prestige):")
    print(src_df.to_string(index=False))
    print()
    print(f"Top {n} institutions (ranked by v):")
    print(inst_df.to_string(index=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--field',  type=int, default=14)
    parser.add_argument('--window', type=str, default='2020_2024')
    parser.add_argument('--n',      type=int, default=25)
    args = parser.parse_args()
    show_rankings(args.field, args.window, args.n)


if __name__ == '__main__':
    main()
