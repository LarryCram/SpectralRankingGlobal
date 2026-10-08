"""
Compute reliability indicators across all 22 2-digit ANZSRC 2008 Divisions:
1. Graph-theoretic integrity: Giant SCC institutional retention rate (%)
2. Spectral gap: Delta lambda = lambda_1 - lambda_2
3. Citation flow density: pairs / work
4. National coverage: AU HEPs ranked
5. Threshold discriminability: ROC AUC for predicting ERA >= 4 (Above World Standard)
6. Ordinal association: Somers' D (handling ties properly)
7. Parity calibration: Median log10(v) for ERA 3 ("At world standard") vs ERA 4 vs ERA 5
8. Reliability verdict
"""

import duckdb
import glob
import json
import numpy as np
import pandas as pd
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from scipy.stats import somersd
from sklearn.metrics import roc_auc_score
from util import load_config

def main():
    paths = load_config()
    era_dir = paths.working / "era2018"
    div_dir = era_dir / "division"

    # 1. Load official ERA outcomes
    outcome_files = glob.glob("/home/lc/Dropbox/RESEARCH/ERA/data/ERA_OUTCOMES/*.pkl")
    era_map = {}
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
                    era_map[(hep, code_raw)] = int(val)
        except Exception:
            continue

    # 2. Load AU HEP concordances
    hep_file = paths.data / "HEP_concordances.xlsx"
    hep_df = pd.read_excel(hep_file, sheet_name='Keys')
    hep_df['inst_id'] = hep_df['institution_idx'].astype(str).str.lstrip('I').astype('int64')
    id_to_hep = dict(zip(hep_df['inst_id'], hep_df['HEP']))

    con = duckdb.connect()

    era_methods = {
        '01': 'Citation', '02': 'Citation', '03': 'Citation', '04': 'Citation',
        '05': 'Citation', '06': 'Citation', '07': 'Citation', '08': 'Citation',
        '09': 'Citation', '10': 'Citation', '11': 'Citation', '17': 'Citation',
        '12': 'Peer Review', '13': 'Peer Review', '14': 'Citation',
        '15': 'Peer Review', '16': 'Peer Review', '18': 'Peer Review',
        '19': 'Peer Review', '20': 'Peer Review', '21': 'Peer Review',
        '22': 'Peer Review',
    }

    # Citation pairs per work from task log
    pairs_per_work_map = {
        '01': 1.76, '02': 4.31, '03': 6.02, '04': 4.91, '05': 3.35, '06': 5.96,
        '07': 1.82, '08': 2.31, '09': 4.91, '10': 0.96, '11': 6.01, '12': 1.78,
        '13': 1.20, '14': 1.76, '15': 2.43, '16': 1.73, '17': 3.59, '18': 0.12,
        '19': 0.13, '20': 0.85, '21': 1.20, '22': 0.56
    }

    rows = []
    for div_num in range(1, 23):
        div_code = str(div_num).zfill(2)
        diag_file = div_dir / f"rankings_div_{div_code}_2011_2016_baseline_diag.json"
        pq_file = div_dir / f"rankings_div_{div_code}_2011_2016_baseline.parquet"

        if not diag_file.exists() or not pq_file.exists():
            continue

        diag = json.loads(diag_file.read_text())

        df_rk = con.execute(f"SELECT unit_idx, v FROM '{pq_file}' WHERE unit_type = 'U'").df()
        df_rk['hep'] = df_rk['unit_idx'].map(id_to_hep)
        df_au = df_rk.dropna(subset=['hep']).copy()
        df_au['era'] = df_au['hep'].apply(lambda h: era_map.get((h, div_code), np.nan))
        df_eval = df_au.dropna(subset=['era']).copy()
        df_eval['log_v'] = np.log10(df_eval['v'])

        u_ret_pct = (diag['n_u_ranked'] / max(diag['n_u_cands'], 1)) * 100.0
        sp_gap = diag['spectral_gap']
        n_au = len(df_au)
        n_eval = len(df_eval)

        # Somers' D between continuous v and ordinal ERA rating Y
        if n_eval >= 5 and len(df_eval['era'].unique()) > 1:
            try:
                res_sd = somersd(df_eval['v'], df_eval['era'])
                sd_val = res_sd.statistic
            except Exception:
                sd_val = np.nan
        else:
            sd_val = np.nan

        # ROC AUC for predicting ERA >= 4 (Above World Standard)
        n_high = (df_eval['era'] >= 4).sum()
        n_low = (df_eval['era'] <= 3).sum()
        if n_high >= 2 and n_low >= 2:
            try:
                auc_val = roc_auc_score((df_eval['era'] >= 4).astype(int), df_eval['v'])
            except Exception:
                auc_val = np.nan
        else:
            auc_val = np.nan

        # Median log10(v) for key rating bands
        med_3 = df_eval[df_eval['era'] == 3]['log_v'].median() if (df_eval['era'] == 3).any() else np.nan
        med_4 = df_eval[df_eval['era'] == 4]['log_v'].median() if (df_eval['era'] == 4).any() else np.nan
        med_5 = df_eval[df_eval['era'] == 5]['log_v'].median() if (df_eval['era'] == 5).any() else np.nan

        # Reliability verdict synthesis
        pairs_w = pairs_per_work_map.get(div_code, 1.0)
        if u_ret_pct < 50.0 or pairs_w < 0.20 or n_au <= 2:
            verdict = "Collapsed (Non-viable)"
        elif sp_gap < 0.10 or n_au < 15:
            verdict = "Fragile / Low coverage"
        elif auc_val is not None and not np.isnan(auc_val) and auc_val >= 0.75 and abs(med_3) <= 0.15:
            verdict = "Highly Reliable"
        elif u_ret_pct >= 90.0 and sp_gap >= 0.20:
            verdict = "Structurally Sound"
        else:
            verdict = "Moderate"

        rows.append({
            'div': div_code,
            'label': diag['for_label'],
            'mode': era_methods.get(div_code, ''),
            'u_ret': u_ret_pct,
            'gap': sp_gap,
            'pairs_w': pairs_w,
            'n_au': n_au,
            'auc_ge4': auc_val,
            'somers_d': sd_val,
            'med_3': med_3,
            'med_4': med_4,
            'med_5': med_5,
            'verdict': verdict
        })

    out_df = pd.DataFrame(rows)
    # Save CSV
    out_csv = era_dir / "hep_reports/era2018_2d_division_reliability_indicators.csv"
    out_df.to_csv(out_csv, index=False)
    print(f"Saved indicator report to {out_csv}")

    # Print formatted markdown table
    print("\n" + "=" * 110)
    print("ALL 22 ANZSRC 2008 DIVISIONS: SPECTRAL RANKING RELIABILITY INDICATORS")
    print("=" * 110)
    for _, r in out_df.iterrows():
        auc_str = f"{r['auc_ge4']:.2f}" if pd.notna(r['auc_ge4']) else "—"
        sd_str = f"{r['somers_d']:.2f}" if pd.notna(r['somers_d']) else "—"
        m3_str = f"{r['med_3']:+.2f}" if pd.notna(r['med_3']) else "—"
        m4_str = f"{r['med_4']:+.2f}" if pd.notna(r['med_4']) else "—"
        m5_str = f"{r['med_5']:+.2f}" if pd.notna(r['med_5']) else "—"
        print(f"FoR {r['div']} | {r['label'][:22]:<22} | {r['mode'][:7]:<7} | Ret: {r['u_ret']:5.1f}% | Gap: {r['gap']:.3f} | AU: {r['n_au']:2d} | AUC>=4: {auc_str:>4} | Somers'D: {sd_str:>4} | Med3: {m3_str:>5} | Med4: {m4_str:>5} | {r['verdict']}")

if __name__ == "__main__":
    main()
