"""
compare_snapshots_newera.py — Comprehensive comparison of NewERA results between Jul26 and Oct26 snapshots.
"""

import sys
import json
from pathlib import Path
import pandas as pd
import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from util.areas import AREAS, ALL_AREAS

JUL_DIR = Path("/home/lc/s/SpectralRankingGlobal_WORKING_jul26")
OCT_DIR = Path("/home/lc/s/SpectralRankingGlobal_WORKING_Oct26")
DATA_DIR = Path(__file__).parent.parent / "data"

con = duckdb.connect()

def compare_corpus_and_spectral():
    print("=" * 80)
    print("1. AREA5 CORPUS SCALE & SPECTRAL CONVERGENCE (Jul26 vs Oct26)")
    print("=" * 80)
    rows = []
    for a in AREAS:
        jul_diag_p = JUL_DIR / f"area5/rankings_{a.id}_2020_2024_baseline_diag.json"
        oct_diag_p = OCT_DIR / f"area5/rankings_{a.id}_2020_2024_baseline_diag.json"
        
        with open(jul_diag_p) as f:
            dj = json.load(f)
        with open(oct_diag_p) as f:
            do = json.load(f)
            
        ns_j, ns_o = dj.get('n_s_ranked', 0), do.get('n_s_ranked', 0)
        nu_j, nu_o = dj.get('n_u_ranked', 0), do.get('n_u_ranked', 0)
        gap_j, gap_o = dj.get('spectral_gap', 0.0), do.get('spectral_gap', 0.0)
        l2_j, l2_o = dj.get('lam2', 0.0), do.get('lam2', 0.0)
        
        rows.append({
            'Code': a.code,
            'Discipline': a.short_name,
            'Ns_Jul': ns_j,
            'Ns_Oct': ns_o,
            'dNs_%': (ns_o - ns_j) / ns_j * 100 if ns_j else 0,
            'Nu_Jul': nu_j,
            'Nu_Oct': nu_o,
            'dNu_%': (nu_o - nu_j) / nu_j * 100 if nu_j else 0,
            'Gap_Jul': gap_j,
            'Gap_Oct': gap_o,
            'dGap': gap_o - gap_j,
        })
    df = pd.DataFrame(rows)
    print(df.to_string(index=False, formatters={
        'Ns_Jul': '{:,}'.format, 'Ns_Oct': '{:,}'.format,
        'Nu_Jul': '{:,}'.format, 'Nu_Oct': '{:,}'.format,
        'dNs_%': '{:+.1f}%'.format, 'dNu_%': '{:+.1f}%'.format,
        'Gap_Jul': '{:.4f}'.format, 'Gap_Oct': '{:.4f}'.format,
        'dGap': '{:+.4f}'.format,
    }))
    print()
    return df

def compare_hep():
    print("=" * 80)
    print("2. AUSTRALIAN HEP INSTITUTIONAL INFLUENCE (v_I) (Jul26 vs Oct26)")
    print("=" * 80)
    keys = pd.read_excel(DATA_DIR / 'HEP_concordances.xlsx', sheet_name='Keys')
    keys['inst_id'] = keys['institution_idx'].str.lstrip('I').astype('int64')

    scores_j = {}
    scores_o = {}

    for a in AREAS:
        pq_j = str(JUL_DIR / f"area5/rankings_{a.id}_2020_2024_baseline.parquet")
        pq_o = str(OCT_DIR / f"area5/rankings_{a.id}_2020_2024_baseline.parquet")

        df_j = con.execute(f"SELECT unit_idx, v FROM '{pq_j}' WHERE unit_type = 'U'").df()
        df_o = con.execute(f"SELECT unit_idx, v FROM '{pq_o}' WHERE unit_type = 'U'").df()

        mj = keys.merge(df_j, left_on='inst_id', right_on='unit_idx', how='left')
        mo = keys.merge(df_o, left_on='inst_id', right_on='unit_idx', how='left')

        scores_j[a.code] = mj.set_index('HEP')['v']
        scores_o[a.code] = mo.set_index('HEP')['v']

    df_j = pd.DataFrame(scores_j)
    df_o = pd.DataFrame(scores_o)

    df_j['Mean_Jul'] = df_j.mean(axis=1)
    df_o['Mean_Oct'] = df_o.mean(axis=1)

    combined = pd.DataFrame({
        'HEP': df_j.index,
        'Name': df_j.index.map(keys.set_index('HEP')['Organisation'].to_dict()),
        'Group': df_j.index.map(keys.set_index('HEP')['Group'].to_dict()),
        'Mean_Jul': df_j['Mean_Jul'].values,
        'Mean_Oct': df_o['Mean_Oct'].values,
    }).dropna(subset=['Mean_Jul', 'Mean_Oct'])

    combined['dMean'] = combined['Mean_Oct'] - combined['Mean_Jul']
    combined['Rank_Jul'] = combined['Mean_Jul'].rank(ascending=False).astype(int)
    combined['Rank_Oct'] = combined['Mean_Oct'].rank(ascending=False).astype(int)
    combined['dRank'] = combined['Rank_Jul'] - combined['Rank_Oct']  # positive = improved rank

    spearman_rho = combined['Mean_Jul'].corr(combined['Mean_Oct'], method='spearman')
    pearson_r = combined['Mean_Jul'].corr(combined['Mean_Oct'], method='pearson')

    print(f"Total Australian HEPs evaluated in both: {len(combined)}")
    print(f"Overall Pearson correlation (r)        : {pearson_r:.4f}")
    print(f"Overall Spearman rank correlation (rho): {spearman_rho:.4f}")
    print()

    # Per-discipline correlations
    print("Discipline-level Australian HEP correlation:")
    for a in AREAS:
        s_j = df_j[a.code]
        s_o = df_o[a.code]
        valid = pd.DataFrame({'j': s_j, 'o': s_o}).dropna()
        sp = valid['j'].corr(valid['o'], method='spearman')
        pe = valid['j'].corr(valid['o'], method='pearson')
        print(f"  {a.code} ({a.short_name:<24}): n={len(valid):<2}  Pearson r={pe:.4f}  Spearman rho={sp:.4f}")

    print("\nTop 25 Australian HEPs (Ranked by Oct26 Mean v_I):")
    top25 = combined.sort_values('Mean_Oct', ascending=False).head(25)
    print(top25[['HEP', 'Group', 'Mean_Jul', 'Rank_Jul', 'Mean_Oct', 'Rank_Oct', 'dRank', 'Name']].to_string(index=False, formatters={
        'Mean_Jul': '{:.2f}'.format,
        'Mean_Oct': '{:.2f}'.format,
        'dRank': '{:+d}'.format,
    }))
    print()
    return combined

def compare_robustness():
    print("=" * 80)
    print("3. GEOPOLITICAL & SENSITIVITY ROBUSTNESS SPEARMAN RHO (Table 4 Comparison)")
    print("=" * 80)
    variants = [
        ('OECDG20', 'OECD+G20 Bloc'),
        ('OECDG20CIA', 'OECD+G20 excl. CN/IN/US'),
        ('CIAA', 'AU, CN, IN, US Only'),
        ('tau20', 'Retention Threshold (tau=20)'),
        ('rho1', 'Full Citation Count (rho=1)'),
        ('eps1', 'Sentinel Model (eps=1)'),
    ]

    for var_label, var_desc in variants:
        print(f"\nVariant: {var_desc} ({var_label})")
        print(f"{'Area':<6} | {'Jul26 rho_s':<12} | {'Oct26 rho_s':<12} | {'Delta':<8}")
        print("-" * 45)
        for i, a in enumerate(AREAS, start=1):
            def get_rho(work_dir):
                base_pq = str(work_dir / f"rankings_{i}_2020_2024_baseline.parquet")
                var_pq = str(work_dir / f"rankings_{i}_2020_2024_{var_label}.parquet")
                sql = f"""
                SELECT b.v AS v_base, c.v AS v_var
                FROM '{base_pq}' b
                JOIN '{var_pq}' c ON b.unit_idx = c.unit_idx AND b.unit_type = c.unit_type
                WHERE b.unit_type = 'U' AND b.v IS NOT NULL AND c.v IS NOT NULL
                """
                pair_df = con.execute(sql).df()
                return pair_df['v_base'].corr(pair_df['v_var'], method='spearman')

            try:
                rj = get_rho(JUL_DIR)
                ro = get_rho(OCT_DIR)
                print(f"{a.code:<6} | {rj:.4f}       | {ro:.4f}       | {ro-rj:+.4f}")
            except Exception as e:
                print(f"{a.code:<6} | ERROR: {e}")

if __name__ == "__main__":
    compare_corpus_and_spectral()
    compare_hep()
    compare_robustness()
