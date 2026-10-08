"""
analysis/compare_sources_vs_institutions.py — Analyze changes in sources (journals)
versus institutions between ERA 2018 (2011–2016) and ERA 2026 (2020–2025).
"""

from pathlib import Path
import sys
import json
import pandas as pd
import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
from util import load_config

def main():
    paths = load_config()
    
    records = []
    
    unique_u_18 = set()
    unique_s_18 = set()
    unique_u_26 = set()
    unique_s_26 = set()

    for div in range(1, 23):
        div_str = f"{div:02d}"
        f18 = paths.working / f"era2018/division/rankings_div_{div_str}_2011_2016_baseline.parquet"
        f26 = paths.working / f"era2026/division/rankings_div_{div_str}_2020_2025_baseline.parquet"
        diag18_f = paths.working / f"era2018/division/rankings_div_{div_str}_2011_2016_baseline_diag.json"
        
        with open(diag18_f) as fp:
            diag18 = json.load(fp)
            for_label = diag18.get('for_label', f'Division {div_str}')
        
        df18 = pd.read_parquet(f18)
        df26 = pd.read_parquet(f26)
        
        u18 = df18[df18['unit_type'] == 'U']
        s18 = df18[df18['unit_type'] == 'S']
        u26 = df26[df26['unit_type'] == 'U']
        s26 = df26[df26['unit_type'] == 'S']
        
        unique_u_18.update(u18['unit_idx'].tolist())
        unique_s_18.update(s18['unit_idx'].tolist())
        unique_u_26.update(u26['unit_idx'].tolist())
        unique_s_26.update(s26['unit_idx'].tolist())
        
        n_u18 = len(u18)
        n_u26 = len(u26)
        n_s18 = len(s18)
        n_s26 = len(s26)
        
        records.append({
            'for_code': div_str,
            'for_label': for_label,
            'n_u_18': n_u18,
            'n_u_26': n_u26,
            'growth_u_pct': (n_u26 - n_u18) / n_u18 * 100 if n_u18 > 0 else 0,
            'n_s_18': n_s18,
            'n_s_26': n_s26,
            'growth_s_pct': (n_s26 - n_s18) / n_s18 * 100 if n_s18 > 0 else 0,
            'ratio_u_to_s_18': n_u18 / n_s18 if n_s18 > 0 else np.nan,
            'ratio_u_to_s_26': n_u26 / n_s26 if n_s26 > 0 else np.nan,
            # Proportions with v >= 1
            's_v1_pct_18': (s18['v'] >= 1.0).mean() * 100,
            's_v1_pct_26': (s26['v'] >= 1.0).mean() * 100,
            'u_v1_pct_18': (u18['v'] >= 1.0).mean() * 100,
            'u_v1_pct_26': (u26['v'] >= 1.0).mean() * 100,
            # Mean activity a_p (volume per entity)
            'mean_ap_s_18': s18['a_p'].mean(),
            'mean_ap_s_26': s26['a_p'].mean(),
            'growth_ap_s_pct': (s26['a_p'].mean() - s18['a_p'].mean()) / s18['a_p'].mean() * 100,
            'mean_ap_u_18': u18['a_p'].mean(),
            'mean_ap_u_26': u26['a_p'].mean(),
            'growth_ap_u_pct': (u26['a_p'].mean() - u18['a_p'].mean()) / u18['a_p'].mean() * 100,
            # Total volume
            'total_ap_18': u18['a_p'].sum(),
            'total_ap_26': u26['a_p'].sum(),
            'growth_total_vol_pct': (u26['a_p'].sum() - u18['a_p'].sum()) / u18['a_p'].sum() * 100,
        })

    df_res = pd.DataFrame(records)
    
    # Save results
    out_csv = paths.working / "era2026/sources_vs_institutions_comparison.csv"
    df_res.to_csv(out_csv, index=False)
    
    print("=" * 80)
    print("SOURCES (JOURNALS) VS INSTITUTIONS: ERA 2018 (2011-2016) vs ERA 2026 (2020-2025)")
    print("=" * 80)
    
    print("\n1. UNIQUE GLOBAL ENTITY COUNTS (across all 22 divisions):")
    print(f"   Institutions (Unique): 2018 = {len(unique_u_18):,} -> 2026 = {len(unique_u_26):,} (+{(len(unique_u_26)-len(unique_u_18))/len(unique_u_18)*100:.1f}%)")
    print(f"   Sources (Unique):      2018 = {len(unique_s_18):,} -> 2026 = {len(unique_s_26):,} (+{(len(unique_s_26)-len(unique_s_18))/len(unique_s_18)*100:.1f}%)")
    
    tot_u18 = df_res['n_u_18'].sum()
    tot_u26 = df_res['n_u_26'].sum()
    tot_s18 = df_res['n_s_18'].sum()
    tot_s26 = df_res['n_s_26'].sum()
    
    print("\n2. AGGREGATED DIVISION-INSTANCE PAIRS:")
    print(f"   Institution-Division Pairs: 2018 = {tot_u18:,} -> 2026 = {tot_u26:,} (+{(tot_u26-tot_u18)/tot_u18*100:.1f}%)")
    print(f"   Source-Division Pairs:      2018 = {tot_s18:,} -> 2026 = {tot_s26:,} (+{(tot_s26-tot_s18)/tot_s18*100:.1f}%)")
    print(f"   Ratio (U / S pairs):        2018 = {tot_u18/tot_s18:.2f} -> 2026 = {tot_u26/tot_s26:.2f}")

    print("\n3. DIVISION BREAKDOWN:")
    cols_show = ['for_code', 'for_label', 'n_u_18', 'n_u_26', 'growth_u_pct', 'n_s_18', 'n_s_26', 'growth_s_pct']
    fmt = "{:<4} {:<32} | {:>6} {:>6} {:>7} | {:>6} {:>6} {:>7}"
    print(fmt.format("FoR", "Division Name", "U 2018", "U 2026", "U Grw%", "S 2018", "S 2026", "S Grw%"))
    print("-" * 88)
    for _, r in df_res.iterrows():
        print(fmt.format(
            r['for_code'], r['for_label'][:32],
            int(r['n_u_18']), int(r['n_u_26']), f"{r['growth_u_pct']:+.1f}%",
            int(r['n_s_18']), int(r['n_s_26']), f"{r['growth_s_pct']:+.1f}%"
        ))

    print("\n4. ACTIVITY & INTENSITY PER UNIT (MEAN a_p):")
    fmt_ap = "{:<4} {:<32} | {:>7} {:>7} {:>7} | {:>7} {:>7} {:>7}"
    print(fmt_ap.format("FoR", "Division Name", "ap_U 18", "ap_U 26", "U apGrw", "ap_S 18", "ap_S 26", "S apGrw"))
    print("-" * 92)
    for _, r in df_res.iterrows():
        print(fmt_ap.format(
            r['for_code'], r['for_label'][:32],
            f"{r['mean_ap_u_18']:.0f}", f"{r['mean_ap_u_26']:.0f}", f"{r['growth_ap_u_pct']:+.1f}%",
            f"{r['mean_ap_s_18']:.0f}", f"{r['mean_ap_s_26']:.0f}", f"{r['growth_ap_s_pct']:+.1f}%"
        ))

    print("\n5. PRESTIGE DISTRIBUTION (% >= 1.0):")
    fmt_v = "{:<4} {:<32} | {:>8} {:>8} | {:>8} {:>8}"
    print(fmt_v.format("FoR", "Division Name", "%U v>=1 18", "%U v>=1 26", "%S v>=1 18", "%S v>=1 26"))
    print("-" * 80)
    for _, r in df_res.iterrows():
        print(fmt_v.format(
            r['for_code'], r['for_label'][:32],
            f"{r['u_v1_pct_18']:.1f}%", f"{r['u_v1_pct_26']:.1f}%",
            f"{r['s_v1_pct_18']:.1f}%", f"{r['s_v1_pct_26']:.1f}%"
        ))

if __name__ == '__main__':
    main()
