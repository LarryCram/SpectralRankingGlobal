"""
analysis/compare_era2018_era2026.py — Longitudinal comparison of Australian university
spectral performance between ERA 2018 (2011–2016) and ERA 2026 (2020–2025).
"""

from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
from util import load_config

def main():
    paths = load_config()
    p18 = paths.working / "era2018/hep_reports/era2018_au_hep_spectral_rankings.parquet"
    p26 = paths.working / "era2026/hep_reports/era2026_au_hep_spectral_rankings.parquet"

    df18 = pd.read_parquet(p18)
    df26 = pd.read_parquet(p26)

    d18 = df18[df18['for_level'] == 'division'].copy().rename(columns={'hep_short_name': 'hep'})
    d26 = df26[df26['level'] == 'division'].copy()

    # Load groupings from concordances
    hep_file = paths.data / "HEP_concordances.xlsx"
    hep_df = pd.read_excel(hep_file, sheet_name='Keys')
    hep_df['inst_id'] = hep_df['institution_idx'].astype(str).str.lstrip('I').astype('int64')
    id_to_group = dict(zip(hep_df['HEP'], hep_df['Grouping'])) if 'Grouping' in hep_df.columns else {}

    d18['grouping'] = d18['hep'].map(id_to_group).fillna('Other')
    d26['grouping'] = d26['hep'].map(id_to_group).fillna('Other')

    # Merge matched units
    merged = pd.merge(d18, d26, on=['for_code', 'hep'], suffixes=('_2018', '_2026'))
    merged['delta_v'] = merged['v_2026'] - merged['v_2018']
    merged['pct_change_v'] = (merged['v_2026'] - merged['v_2018']) / merged['v_2018'] * 100

    out_csv = paths.working / "era2026/hep_reports/era2018_vs_era2026_matched_comparison.csv"
    merged.to_csv(out_csv, index=False)

    print("==================================================================")
    print("ERA 2018 (2011–2016) vs. ERA 2026 (2020–2025) SPECTRAL BENCHMARK")
    print("==================================================================")
    print(f"Total Australian evaluated units in 2018: {len(d18)}")
    print(f"Total Australian evaluated units in 2026: {len(d26)}")
    print(f"Units matched across both evaluation periods: {len(merged)}")
    print(f"2018 National Mean v: {d18['v'].mean():.3f} (Median: {d18['v'].median():.3f}, % >= 1.0: {(d18['v'] >= 1.0).mean()*100:.1f}%)")
    print(f"2026 National Mean v: {d26['v'].mean():.3f} (Median: {d26['v'].median():.3f}, % >= 1.0: {(d26['v'] >= 1.0).mean()*100:.1f}%)")
    print(f"Mean Delta v across matched units: {merged['delta_v'].mean():+.3f} ({merged['pct_change_v'].mean():+.1f}%)")

    print("\n--- PERFORMANCE BY MISSION GROUP ---")
    grp_res = merged.groupby('grouping_2026').agg(
        n=('hep', 'count'),
        mean_v_2018=('v_2018', 'mean'),
        mean_v_2026=('v_2026', 'mean'),
        mean_delta=('delta_v', 'mean'),
        pct_gain=('pct_change_v', 'mean')
    ).sort_values('mean_delta', ascending=False)
    print(grp_res.to_string())

    print("\n--- SHIFTS BY 2-DIGIT DIVISION ---")
    div_res = merged.groupby(['for_code', 'for_label_2026']).agg(
        n=('hep', 'count'),
        mean_v_2018=('v_2018', 'mean'),
        mean_v_2026=('v_2026', 'mean'),
        mean_delta=('delta_v', 'mean')
    ).reset_index().sort_values('mean_delta', ascending=False)
    for _, r in div_res.iterrows():
        print(f"Div {r['for_code']:2s} ({r['for_label_2026'][:30]:30s}): N={r['n']:2d} | 2018={r['mean_v_2018']:.2f} -> 2026={r['mean_v_2026']:.2f} (Delta={r['mean_delta']:+.2f})")

    print("\n--- TOP INSTITUTIONAL GAINERS & DECLINERS (MIN 10 DIVISIONS) ---")
    inst_res = merged.groupby('hep').agg(
        n_divs=('for_code', 'count'),
        mean_v_2018=('v_2018', 'mean'),
        mean_v_2026=('v_2026', 'mean'),
        mean_delta=('delta_v', 'mean')
    )
    inst_10 = inst_res[inst_res['n_divs'] >= 10].sort_values('mean_delta', ascending=False)
    print("\nTop 5 Gainers:")
    print(inst_10.head(5).to_string())
    print("\nTop 5 Decliners:")
    print(inst_10.tail(5).to_string())

if __name__ == '__main__':
    main()
