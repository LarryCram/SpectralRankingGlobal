"""
compare_era2018_snapshots.py — Comprehensive comparison of ERA 2018 emulation results between Jul26 and Oct26 snapshots.
"""

import sys
from pathlib import Path
import pandas as pd
import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

JUL_REPORT = Path("/home/lc/s/SpectralRankingGlobal_WORKING_jul26/era2018/hep_reports/era2018_au_hep_spectral_rankings.parquet")
OCT_REPORT = Path("/home/lc/s/SpectralRankingGlobal_WORKING_Oct26/era2018/hep_reports/era2018_au_hep_spectral_rankings.parquet")
OUTCOMES_FILE = Path(__file__).parent.parent / "data/era2018_outcomes.xlsx"
KEYS_FILE = Path(__file__).parent.parent / "data/HEP_concordances.xlsx"

con = duckdb.connect()

def main():
    df_j = pd.read_parquet(JUL_REPORT)
    df_o = pd.read_parquet(OCT_REPORT)

    print("=" * 80)
    print("1. SUMMARY OF EVALUATIONS (Jul26 vs Oct26)")
    print("=" * 80)
    print(f"Total Jul26 evaluations: {len(df_j):,}")
    print(f"Total Oct26 evaluations: {len(df_o):,}")
    
    div_j = df_j[df_j['for_level'] == 'division']
    div_o = df_o[df_o['for_level'] == 'division']
    grp_j = df_j[df_j['for_level'] == 'group']
    grp_o = df_o[df_o['for_level'] == 'group']

    print(f"  2-digit Divisions: Jul26 = {len(div_j):,}, Oct26 = {len(div_o):,}")
    print(f"  4-digit Groups   : Jul26 = {len(grp_j):,}, Oct26 = {len(grp_o):,}")
    print()

    # Merge on (for_level, for_code, institution_idx)
    merged = pd.merge(
        df_j, df_o,
        on=['for_level', 'for_code', 'institution_idx', 'hep_short_name'],
        suffixes=('_jul', '_oct')
    )
    print(f"Matched evaluations in both snapshots: {len(merged):,}")

    r_v_all = merged['v_jul'].corr(merged['v_oct'], method='pearson')
    rho_v_all = merged['v_jul'].corr(merged['v_oct'], method='spearman')
    r_pi_all = merged['pi_jul'].corr(merged['pi_oct'], method='pearson')
    rho_pi_all = merged['pi_jul'].corr(merged['pi_oct'], method='spearman')

    print(f"Overall Influence score (v)  : Pearson r = {r_v_all:.4f}, Spearman rho = {rho_v_all:.4f}")
    print(f"Overall Volume score (pi)    : Pearson r = {r_pi_all:.4f}, Spearman rho = {rho_pi_all:.4f}")
    print()

    # By level
    m_div = merged[merged['for_level'] == 'division']
    m_grp = merged[merged['for_level'] == 'group']

    print("Correlations by Level:")
    print(f"  Divisions (2-digit, n={len(m_div):,}): Pearson r = {m_div['v_jul'].corr(m_div['v_oct']):.4f}, Spearman rho = {m_div['v_jul'].corr(m_div['v_oct'], method='spearman'):.4f}")
    print(f"  Groups    (4-digit, n={len(m_grp):,}): Pearson r = {m_grp['v_jul'].corr(m_grp['v_oct']):.4f}, Spearman rho = {m_grp['v_jul'].corr(m_grp['v_oct'], method='spearman'):.4f}")
    print()

    # 2. Division-level breakdown
    print("=" * 80)
    print("2. DIVISION-BY-DIVISION INFLUENCE CORRELATION (Jul26 vs Oct26)")
    print("=" * 80)
    div_records = []
    for for_code, group in m_div.groupby('for_code'):
        label = group['for_label_jul'].iloc[0]
        n_evals = len(group)
        r_v = group['v_jul'].corr(group['v_oct'])
        sp_v = group['v_jul'].corr(group['v_oct'], method='spearman')
        mean_v_j = group['v_jul'].mean()
        mean_v_o = group['v_oct'].mean()
        div_records.append({
            'FoR': for_code,
            'Label': label,
            'N': n_evals,
            'Mean_v_Jul': mean_v_j,
            'Mean_v_Oct': mean_v_o,
            'Pearson_r': r_v,
            'Spearman_rho': sp_v,
        })
    div_df = pd.DataFrame(div_records).sort_values('FoR')
    print(div_df.to_string(index=False, formatters={
        'Mean_v_Jul': '{:.2f}'.format,
        'Mean_v_Oct': '{:.2f}'.format,
        'Pearson_r': '{:.4f}'.format,
        'Spearman_rho': '{:.4f}'.format,
    }))
    print()

    # 3. Agreement with Official ARC ERA 2018 Ratings
    import glob
    outcome_files = glob.glob("/home/lc/Dropbox/RESEARCH/ERA/data/ERA_OUTCOMES/*.pkl")
    if outcome_files:
        print("=" * 80)
        print("3. CONCORDANCE WITH OFFICIAL ARC ERA 2018 RATINGS (1–5)")
        print("=" * 80)
        era_records = []
        for f in outcome_files:
            try:
                df = pd.read_pickle(f)
                for _, r in df.iterrows():
                    hep = str(r['short_name']).strip()
                    code_raw = str(r['FOR_code']).strip().zfill(2)
                    if len(code_raw) == 3:
                        code_raw = '0' + code_raw
                    val = str(r[2018]).strip()
                    if val in ['1', '2', '3', '4', '5']:
                        era_records.append({
                            'hep_short_name': hep,
                            'for_code': code_raw,
                            'rating': int(val),
                        })
            except Exception:
                continue

        era_outcomes_df = pd.DataFrame(era_records)
        m_arc = pd.merge(
            merged, era_outcomes_df,
            on=['hep_short_name', 'for_code']
        )
        if not m_arc.empty:
            valid_arc = m_arc.dropna(subset=['rating', 'v_jul', 'v_oct'])
            print(f"Matched official ARC evaluations: {len(valid_arc):,}")
            
            rho_j = valid_arc['v_jul'].corr(valid_arc['rating'], method='spearman')
            rho_o = valid_arc['v_oct'].corr(valid_arc['rating'], method='spearman')
            r_j = valid_arc['v_jul'].corr(valid_arc['rating'], method='pearson')
            r_o = valid_arc['v_oct'].corr(valid_arc['rating'], method='pearson')

            print(f"Jul26 vs Official ERA Rating: Pearson r = {r_j:.4f}, Spearman rho = {rho_j:.4f}")
            print(f"Oct26 vs Official ERA Rating: Pearson r = {r_o:.4f}, Spearman rho = {rho_o:.4f}")
            print()

            # Breakdown by level
            for level in ['division', 'group']:
                sub = valid_arc[valid_arc['for_level'] == level]
                r_j_sub = sub['v_jul'].corr(sub['rating'])
                r_o_sub = sub['v_oct'].corr(sub['rating'])
                rho_j_sub = sub['v_jul'].corr(sub['rating'], method='spearman')
                rho_o_sub = sub['v_oct'].corr(sub['rating'], method='spearman')
                print(f"  Level {level:<8} (n={len(sub):<4}): Jul26 rho = {rho_j_sub:.4f} | Oct26 rho = {rho_o_sub:.4f}")
            print()

    # 4. Institutional Top 25 Ranking
    print("=" * 80)
    print("4. TOP AUSTRALIAN UNIVERSITIES BY MEAN DIVISION INFLUENCE (v_I)")
    print("=" * 80)
    hep_div_j = div_j.groupby('hep_short_name')['v'].agg(['mean', 'count']).rename(columns={'mean': 'mean_v_jul', 'count': 'n_div_jul'})
    hep_div_o = div_o.groupby('hep_short_name')['v'].agg(['mean', 'count']).rename(columns={'mean': 'mean_v_oct', 'count': 'n_div_oct'})
    
    hep_comp = pd.concat([hep_div_j, hep_div_o], axis=1).dropna()
    hep_comp['rank_jul'] = hep_comp['mean_v_jul'].rank(ascending=False).astype(int)
    hep_comp['rank_oct'] = hep_comp['mean_v_oct'].rank(ascending=False).astype(int)
    hep_comp['dRank'] = hep_comp['rank_jul'] - hep_comp['rank_oct']
    hep_comp = hep_comp.sort_values('mean_v_oct', ascending=False)

    keys = pd.read_excel(KEYS_FILE, sheet_name='Keys')
    name_map = dict(zip(keys['HEP'], keys['Organisation']))
    group_map = dict(zip(keys['HEP'], keys['Group']))
    hep_comp['Name'] = hep_comp.index.map(name_map)
    hep_comp['Group'] = hep_comp.index.map(group_map)

    top25 = hep_comp.head(25)
    print(top25[['Group', 'mean_v_jul', 'rank_jul', 'mean_v_oct', 'rank_oct', 'dRank', 'Name']].to_string(formatters={
        'mean_v_jul': '{:.2f}'.format,
        'mean_v_oct': '{:.2f}'.format,
        'dRank': '{:+d}'.format,
    }))
    print()

if __name__ == '__main__':
    main()
