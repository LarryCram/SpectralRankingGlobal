"""
analysis/compare_all_leiden_countries.py — Global comparison of university spectral rankings
between ERA 2018 (2011–2016) and ERA 2026 (2020–2025) across ALL CWTS Leiden Ranking universities
(all 1,506 universities across all countries: Russia, Iran, Singapore, Saudi Arabia, etc.).
"""

from pathlib import Path
import sys
import duckdb
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
from util import load_config

def main():
    paths = load_config()
    
    leiden_tsv = "/home/lc/m/LeidenOpen/raw/cwts_leiden_ranking_open_edition_2024/university.tsv"
    inst_parquet = "/home/lc/m/openalex_jul26/parquet_converted/institutions.parquet"
    country_parquet = "/home/lc/m/openalex_jul26/parquet_converted/countries.parquet"
    rk_18 = "/home/lc/s/SpectralRankingGlobal_WORKING_jul26/era2018/division/rankings_div_*.parquet"
    rk_26 = "/home/lc/s/SpectralRankingGlobal_WORKING_jul26/era2026/division/rankings_div_*.parquet"

    db = duckdb.connect()

    # Query all Leiden universities without country restrictions
    q = f"""
    WITH countries AS (
        SELECT CAST(REGEXP_REPLACE(id, 'https://openalex.org/countries/', '') AS VARCHAR) as code,
               display_name as country_name
        FROM '{country_parquet}'
    ),
    leiden_univs AS (
        SELECT l.university_id, l.university, l.country_code, inst.institution_idx,
               COALESCE(c.country_name, l.country_code) as country_name
        FROM '{leiden_tsv}' l
        JOIN '{inst_parquet}' inst
          ON 'https://ror.org/' || l.ror_id = inst.ror
        LEFT JOIN countries c
          ON l.country_code = c.code
    ),
    div_18 AS (
        SELECT field_idx, unit_idx, v, pi, a_p
        FROM '{rk_18}'
        WHERE unit_type = 'U'
    ),
    div_26 AS (
        SELECT field_idx, unit_idx, v, pi, a_p
        FROM '{rk_26}'
        WHERE unit_type = 'U'
    ),
    agg_18 AS (
        SELECT u.country_code,
               u.country_name,
               count(distinct u.institution_idx) as n_univ_18,
               count(*) as n_evals_18,
               round(mean(d.v), 3) as mean_v_18,
               round(median(d.v), 3) as med_v_18,
               round(count(CASE WHEN d.v >= 1.0 THEN 1 END) * 100.0 / count(*), 1) as pct_ge_1_18,
               round(sum(d.a_p), 0) as vol_18
        FROM div_18 d
        JOIN leiden_univs u ON d.unit_idx = u.institution_idx
        GROUP BY u.country_code, u.country_name
    ),
    agg_26 AS (
        SELECT u.country_code,
               u.country_name,
               count(distinct u.institution_idx) as n_univ_26,
               count(*) as n_evals_26,
               round(mean(d.v), 3) as mean_v_26,
               round(median(d.v), 3) as med_v_26,
               round(count(CASE WHEN d.v >= 1.0 THEN 1 END) * 100.0 / count(*), 1) as pct_ge_1_26,
               round(sum(d.a_p), 0) as vol_26
        FROM div_26 d
        JOIN leiden_univs u ON d.unit_idx = u.institution_idx
        GROUP BY u.country_code, u.country_name
    )
    SELECT a18.country_code,
           a18.country_name,
           a18.n_univ_18, a26.n_univ_26,
           a18.n_evals_18, a26.n_evals_26,
           round((a26.n_evals_26 - a18.n_evals_18) * 100.0 / a18.n_evals_18, 1) as eval_growth_pct,
           a18.mean_v_18, a26.mean_v_26,
           round(a26.mean_v_26 - a18.mean_v_18, 3) as delta_v,
           a18.pct_ge_1_18, a26.pct_ge_1_26,
           round(a26.pct_ge_1_26 - a18.pct_ge_1_18, 1) as delta_pct_ge_1,
           round((a26.vol_26 - a18.vol_18) * 100.0 / a18.vol_18, 1) as vol_growth_pct
    FROM agg_18 a18
    JOIN agg_26 a26 ON a18.country_code = a26.country_code
    ORDER BY a26.n_evals_26 DESC
    """

    df = db.execute(q).df()
    
    # Also calculate global Leiden aggregates
    q_global = f"""
    WITH leiden_univs AS (
        SELECT l.university_id, inst.institution_idx
        FROM '{leiden_tsv}' l
        JOIN '{inst_parquet}' inst
          ON 'https://ror.org/' || l.ror_id = inst.ror
    ),
    div_18 AS (
        SELECT d.field_idx, d.unit_idx, d.v, d.a_p
        FROM '{rk_18}' d
        JOIN leiden_univs u ON d.unit_idx = u.institution_idx
        WHERE d.unit_type = 'U'
    ),
    div_26 AS (
        SELECT d.field_idx, d.unit_idx, d.v, d.a_p
        FROM '{rk_26}' d
        JOIN leiden_univs u ON d.unit_idx = u.institution_idx
        WHERE d.unit_type = 'U'
    )
    SELECT 
        (SELECT count(distinct institution_idx) FROM leiden_univs) as total_leiden_univs,
        (SELECT count(*) FROM div_18) as n_evals_18,
        (SELECT count(*) FROM div_26) as n_evals_26,
        (SELECT round(mean(v), 3) FROM div_18) as mean_v_18,
        (SELECT round(mean(v), 2) FROM div_26) as mean_v_26,
        (SELECT round(count(CASE WHEN v >= 1.0 THEN 1 END) * 100.0 / count(*), 1) FROM div_18) as pct_ge_1_18,
        (SELECT round(count(CASE WHEN v >= 1.0 THEN 1 END) * 100.0 / count(*), 1) FROM div_26) as pct_ge_1_26
    """
    df_global = db.execute(q_global).df()

    out_csv = paths.working / "era2026/all_leiden_countries_era_comparison.csv"
    df.to_csv(out_csv, index=False)

    print("=" * 110)
    print("GLOBAL CWTS LEIDEN UNIVERSITIES: ERA 2018 (2011–2016) vs. ERA 2026 (2020–2025)")
    print("=" * 110)
    print(f"Total Countries Represented: {len(df)}")
    print(f"Total Leiden Universities:   {df_global['total_leiden_univs'].iloc[0]}")
    print(f"Global Evaluations:          2018 = {df_global['n_evals_18'].iloc[0]:,} -> 2026 = {df_global['n_evals_26'].iloc[0]:,} (+{(df_global['n_evals_26'].iloc[0]-df_global['n_evals_18'].iloc[0])/df_global['n_evals_18'].iloc[0]*100:.1f}%)")
    print(f"Global Leiden Mean v:        2018 = {df_global['mean_v_18'].iloc[0]:.3f} -> 2026 = {df_global['mean_v_26'].iloc[0]:.3f}")
    print(f"Global Leiden % >= 1.0:      2018 = {df_global['pct_ge_1_18'].iloc[0]:.1f}% -> 2026 = {df_global['pct_ge_1_26'].iloc[0]:.1f}%")
    print("=" * 110)
    print("\nALL COUNTRIES (Ranked by 2026 Evaluated Discipline Units):")
    print(df.to_string())

if __name__ == '__main__':
    main()
