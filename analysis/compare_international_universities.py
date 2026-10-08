"""
analysis/compare_international_universities.py — Longitudinal comparison of university
spectral rankings between ERA 2018 (2011–2016) and ERA 2026 (2020–2025) across
OECD countries + China + India + Brazil.

Supports both:
1. CWTS Leiden Ranking curated university cohort (1,506 globally standardized universities)
2. OpenAlex type = 'education' institutional cohort
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
    
    oecd_plus = [
        'AU', 'AT', 'BE', 'CA', 'CL', 'CO', 'CR', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 
        'GR', 'HU', 'IS', 'IE', 'IL', 'IT', 'JP', 'KR', 'LV', 'LT', 'LU', 'MX', 'NL', 
        'NZ', 'NO', 'PL', 'PT', 'SK', 'SI', 'ES', 'SE', 'CH', 'TR', 'GB', 'US',
        'CN', 'IN', 'BR'
    ]
    oecd_sql = ', '.join(f"'{c}'" for c in oecd_plus)
    
    leiden_tsv = "/home/lc/m/LeidenOpen/raw/cwts_leiden_ranking_open_edition_2024/university.tsv"
    inst_parquet = "/home/lc/m/openalex_jul26/parquet_converted/institutions.parquet"
    rk_18 = "/home/lc/s/SpectralRankingGlobal_WORKING_jul26/era2018/division/rankings_div_*.parquet"
    rk_26 = "/home/lc/s/SpectralRankingGlobal_WORKING_jul26/era2026/division/rankings_div_*.parquet"

    db = duckdb.connect()

    # Query 1: Curated CWTS Leiden Ranking Universities
    q_leiden = f"""
    WITH leiden_univs AS (
        SELECT l.university_id, l.university, l.country_code, inst.institution_idx
        FROM '{leiden_tsv}' l
        JOIN '{inst_parquet}' inst
          ON 'https://ror.org/' || l.ror_id = inst.ror
        WHERE l.country_code IN ({oecd_sql})
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
               count(distinct u.institution_idx) as n_univ_18,
               count(*) as n_evals_18,
               round(mean(d.v), 3) as mean_v_18,
               round(median(d.v), 3) as med_v_18,
               round(count(CASE WHEN d.v >= 1.0 THEN 1 END) * 100.0 / count(*), 1) as pct_ge_1_18
        FROM div_18 d
        JOIN leiden_univs u ON d.unit_idx = u.institution_idx
        GROUP BY u.country_code
    ),
    agg_26 AS (
        SELECT u.country_code,
               count(distinct u.institution_idx) as n_univ_26,
               count(*) as n_evals_26,
               round(mean(d.v), 3) as mean_v_26,
               round(median(d.v), 3) as med_v_26,
               round(count(CASE WHEN d.v >= 1.0 THEN 1 END) * 100.0 / count(*), 1) as pct_ge_1_26
        FROM div_26 d
        JOIN leiden_univs u ON d.unit_idx = u.institution_idx
        GROUP BY u.country_code
    )
    SELECT a18.country_code,
           a18.n_univ_18, a26.n_univ_26,
           a18.n_evals_18, a26.n_evals_26,
           a18.mean_v_18, a26.mean_v_26,
           round(a26.mean_v_26 - a18.mean_v_18, 3) as delta_v,
           a18.pct_ge_1_18, a26.pct_ge_1_26,
           round(a26.pct_ge_1_26 - a18.pct_ge_1_18, 1) as delta_pct_ge_1
    FROM agg_18 a18
    JOIN agg_26 a26 ON a18.country_code = a26.country_code
    ORDER BY a26.n_evals_26 DESC
    """

    df_leiden = db.execute(q_leiden).df()
    out_leiden = paths.working / "era2026/leiden_universities_era_comparison.csv"
    df_leiden.to_csv(out_leiden, index=False)

    print("=" * 85)
    print("ERA 2018 vs. ERA 2026: CURATED CWTS LEIDEN UNIVERSITIES BENCHMARK")
    print("=" * 85)
    print(df_leiden.to_string())

if __name__ == '__main__':
    main()
