"""
analysis/compute_hass_share_by_year_and_country.py — Compute HASS publication share
of total national publication volume by year and country (2011–2025).
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
    fw_path = paths.working / "flat_works_2000_2025.parquet"
    db = duckdb.connect()

    key_countries = ['AU', 'SG', 'GB', 'US', 'CA', 'NZ', 'NL', 'DE', 'CN', 'IN', 'BR', 'RU', 'ZA', 'IR', 'TR']
    c_sql = ', '.join(f"'{c}'" for c in key_countries)

    print("Querying flat_works for HASS vs Total publications by country and year (2011–2025)...")
    
    # Query using leiden_idx == 5 (Social Sciences and Humanities)
    q = f"""
    WITH base AS (
        SELECT 
            publication_year as year,
            country_code,
            COUNT(DISTINCT work_idx) as n_total,
            COUNT(DISTINCT CASE WHEN leiden_idx = 5 THEN work_idx END) as n_hass
        FROM '{fw_path}'
        WHERE publication_year BETWEEN 2011 AND 2025
          AND country_code IN ({c_sql})
        GROUP BY publication_year, country_code
    ),
    global_tot AS (
        SELECT 
            publication_year as year,
            'GLOBAL' as country_code,
            COUNT(DISTINCT work_idx) as n_total,
            COUNT(DISTINCT CASE WHEN leiden_idx = 5 THEN work_idx END) as n_hass
        FROM '{fw_path}'
        WHERE publication_year BETWEEN 2011 AND 2025
          AND country_code IS NOT NULL
        GROUP BY publication_year
    )
    SELECT * FROM base
    UNION ALL
    SELECT * FROM global_tot
    ORDER BY country_code, year
    """
    
    df = db.execute(q).df()
    df['hass_share_pct'] = df['n_hass'] * 100.0 / df['n_total']
    
    # Pivot table: Rows = Country, Columns = Year, Values = hass_share_pct
    pivot_share = df.pivot_table(index='country_code', columns='year', values='hass_share_pct')
    
    # Also calculate 2011-2016 average vs 2020-2025 average
    df_18 = df[df['year'].between(2011, 2016)].groupby('country_code')[['n_total', 'n_hass']].sum()
    df_18['share_2011_2016'] = df_18['n_hass'] * 100.0 / df_18['n_total']

    df_26 = df[df['year'].between(2020, 2025)].groupby('country_code')[['n_total', 'n_hass']].sum()
    df_26['share_2020_2025'] = df_26['n_hass'] * 100.0 / df_26['n_total']
    df_26['hass_growth_pct'] = (df_26['n_hass'] - df_18['n_hass']) * 100.0 / df_18['n_hass']
    df_26['total_growth_pct'] = (df_26['n_total'] - df_18['n_total']) * 100.0 / df_18['n_total']

    summary = pd.DataFrame({
        'Share_2011_2016': df_18['share_2011_2016'],
        'Share_2020_2025': df_26['share_2020_2025'],
        'Delta_Share_pp': df_26['share_2020_2025'] - df_18['share_2011_2016'],
        'HASS_Vol_Grw_%': df_26['hass_growth_pct'],
        'Total_Vol_Grw_%': df_26['total_growth_pct']
    }).sort_values('Share_2020_2025', ascending=False)

    out_csv = paths.working / "era2026/hass_share_by_year_and_country.csv"
    pivot_share.to_csv(out_csv)
    
    out_summary = paths.working / "era2026/hass_growth_summary.csv"
    summary.to_csv(out_summary)

    print("\n" + "=" * 115)
    print("TABLE: HASS (SSH) SHARE OF TOTAL PUBLICATION VOLUME (%) BY YEAR AND COUNTRY")
    print("=" * 115)
    pd.set_option('display.max_columns', 20)
    pd.set_option('display.width', 130)
    pd.set_option('display.precision', 2)
    
    # Sort pivot by 2025 share
    pivot_share_sorted = pivot_share.loc[summary.index]
    print(pivot_share_sorted.round(2))

    print("\n" + "=" * 115)
    print("ERA 2018 (2011–2016) vs ERA 2026 (2020–2025): HASS VOLUME & SHARE EXPANSION")
    print("=" * 115)
    print(summary.round(2).to_string())

if __name__ == '__main__':
    main()
