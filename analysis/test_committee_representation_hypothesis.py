"""
Test the Committee Representation Hypothesis:
Examine whether institutional representation on the ARC Research Evaluation Committees (RECs)
explains discrepancies between objective global spectral rankings (v) and official ERA 2018 outcomes.
"""

import json
import duckdb
import glob
import numpy as np
import pandas as pd
import sys
from pathlib import Path
from scipy.stats import mannwhitneyu, ttest_ind, pearsonr, spearmanr, chi2_contingency
from sklearn.linear_model import LinearRegression, LogisticRegression

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
from util import load_config

def main():
    paths = load_config()
    era_dir = paths.working / "era2018"
    div_dir = era_dir / "division"

    # 1. Load REC rosters
    rec_file = paths.data / "era2018_rec_rosters.json"
    rec_data = json.loads(rec_file.read_text())

    # Map FoR 2-digit division to its evaluating REC
    for_to_rec = {
        '01': 'MIC',  # Mathematical, Information and Computing
        '02': 'PCE',  # Physical, Chemical and Earth
        '03': 'PCE',
        '04': 'PCE',
        '05': 'EE',   # Engineering and Environmental
        '06': 'BB',   # Biological and Biotechnological
        '07': 'BB',   # Ag & Vet
        '08': 'MIC',
        '09': 'EE',
        '10': 'EE',
        '11': 'MHS',  # Medical and Health
        '12': 'HCA',  # Built Environment (Architects in HCA)
        '13': 'EHS',  # Education and Human Society
        '14': 'EC',   # Economics and Commerce
        '15': 'EC',
        '16': 'EHS',
        '17': 'MHS',  # Psychology in MHS/EHS (psychologists across both)
        '18': 'HCA',  # Law in HCA (legal scholars Charlesworth, Grantham, Richardson in HCA)
        '19': 'HCA',  # Humanities and Creative Arts
        '20': 'HCA',
        '21': 'HCA',
        '22': 'HCA',
    }

    # Load HEP concordances to standardize university names to short HEP codes
    hep_file = paths.data / "HEP_concordances.xlsx"
    hep_df = pd.read_excel(hep_file, sheet_name='Keys')
    hep_df['inst_id'] = hep_df['institution_idx'].astype(str).str.lstrip('I').astype('int64')
    id_to_hep = dict(zip(hep_df['inst_id'], hep_df['HEP']))
    hep_to_group = dict(zip(hep_df['HEP'], hep_df['Grouping'])) if 'Grouping' in hep_df.columns else {}

    # Standardize affiliation strings to HEP codes
    def match_affiliation(affil_str):
        s = affil_str.lower()
        if 'australian national university' in s or 'anu' in s: return 'ANU'
        if 'sydney' in s and 'technology' not in s and 'western' not in s: return 'SYD'
        if 'technology, sydney' in s or 'uts' in s or 'university of technology sydney' in s: return 'UTS'
        if 'western sydney' in s: return 'WSU'
        if 'melbourne' in s: return 'MEL'
        if 'monash' in s: return 'MON'
        if 'queensland' in s and 'technology' not in s and 'southern' not in s: return 'QLD'
        if 'queensland university of technology' in s or 'qut' in s: return 'QUT'
        if 'southern queensland' in s: return 'USQ'
        if 'new south wales' in s or 'unsw' in s: return 'NSW'
        if 'adelaide' in s: return 'ADE'
        if 'western australia' in s: return 'UWA'
        if 'curtin' in s: return 'CUT'
        if 'deakin' in s: return 'DKN'
        if 'rmit' in s: return 'RMT'
        if 'south australia' in s and 'western' not in s: return 'USA'
        if 'la trobe' in s: return 'LTU'
        if 'griffith' in s: return 'GRF'
        if 'flinders' in s: return 'FLN'
        if 'newcastle' in s: return 'NEW'
        if 'tasmania' in s: return 'TAS'
        if 'wollongong' in s: return 'WOL'
        if 'macquarie' in s: return 'MQU'
        if 'new england' in s: return 'UNE'
        if 'sunshine coast' in s: return 'USC'
        if 'southern cross' in s: return 'SCU'
        if 'james cook' in s: return 'JCU'
        if 'murdoch' in s: return 'MUR'
        if 'charles darwin' in s: return 'CDU'
        if 'canberra' in s: return 'CAN'
        if 'edith cowan' in s: return 'ECU'
        if 'swinburne' in s: return 'SWN'
        if 'federation' in s or 'ballarat' in s: return 'FED'
        if 'central queensland' in s or 'cqu' in s: return 'CQU'
        if 'charles sturt' in s: return 'CSU'
        if 'victoria university' in s: return 'VIC'
        if 'catholic' in s: return 'ACU'
        if 'bond' in s: return 'BON'
        if 'notre dame' in s: return 'NDA'
        if 'divinity' in s: return 'DIV'
        return 'OTHER'

    # Build committee representation counts by (rec_code, hep_code)
    rec_counts = {}
    for r_code, r_obj in rec_data.items():
        # Chair counts as 1.5 or 2? Let's keep chair flag and count
        counts = {}
        # Chair
        c_aff = match_affiliation(r_obj['chair']['affiliation'])
        counts[c_aff] = counts.get(c_aff, 0) + 1
        # Members
        for m in r_obj['members']:
            m_aff = match_affiliation(m['affiliation'])
            counts[m_aff] = counts.get(m_aff, 0) + 1
        rec_counts[r_code] = counts

    # 2. Load official ERA outcomes
    outcome_files = glob.glob("/home/lc/Dropbox/RESEARCH/ERA/data/ERA_OUTCOMES/*.pkl")
    era_map = {}
    for f in outcome_files:
        try:
            df = pd.read_pickle(f)
            for _, r in df.iterrows():
                hep = str(r['short_name']).strip()
                code_raw = str(r['FOR_code']).strip().zfill(2)
                if len(code_raw) == 3: code_raw = '0' + code_raw
                val = str(r[2018]).strip()
                if val in ['1', '2', '3', '4', '5']:
                    era_map[(hep, code_raw)] = int(val)
        except Exception:
            continue

    con = duckdb.connect()

    # 3. Assemble dataset of all evaluated Australian units across all 22 divisions
    records = []
    for div_num in range(1, 23):
        div_code = str(div_num).zfill(2)
        pq_file = div_dir / f"rankings_div_{div_code}_2011_2016_baseline.parquet"
        diag_file = div_dir / f"rankings_div_{div_code}_2011_2016_baseline_diag.json"
        if not pq_file.exists(): continue

        diag = json.loads(diag_file.read_text())
        rec_code = for_to_rec.get(div_code, '')
        rec_membership = rec_counts.get(rec_code, {})

        df_rk = con.execute(f"SELECT unit_idx, v, rank_v, pi, a_p FROM '{pq_file}' WHERE unit_type = 'U'").df()
        df_rk['hep'] = df_rk['unit_idx'].map(id_to_hep)
        df_au = df_rk.dropna(subset=['hep']).copy()

        for _, r in df_au.iterrows():
            hep = r['hep']
            era_val = era_map.get((hep, div_code), np.nan)
            if pd.isna(era_val): continue

            v_val = float(r['v'])
            log_v = float(np.log10(v_val)) if v_val > 0 else -3.0

            n_rec_members = rec_membership.get(hep, 0)
            has_rec = 1 if n_rec_members > 0 else 0

            # Mission group classification
            go8 = 1 if hep in ['ANU', 'MEL', 'SYD', 'QLD', 'NSW', 'MON', 'ADE', 'UWA'] else 0

            records.append({
                'div': div_code,
                'for_label': diag['for_label'],
                'rec': rec_code,
                'hep': hep,
                'is_go8': go8,
                'v': v_val,
                'log_v': log_v,
                'era': int(era_val),
                'n_rec_members': n_rec_members,
                'has_rec': has_rec,
            })

    df = pd.DataFrame(records)
    print(f"Total Australian evaluated units across all divisions: {len(df)}")
    print(f"Units with REC representation on their panel: {df['has_rec'].sum()} ({df['has_rec'].mean()*100:.1f}%)")

    # 4. Statistical Modeling
    from scipy.stats import t as student_t

    def run_ols(y_col, X_cols, data):
        X = np.column_stack([np.ones(len(data))] + [data[c].values for c in X_cols])
        y_vals = data[y_col].values
        beta = np.linalg.lstsq(X, y_vals, rcond=None)[0]
        preds = X @ beta
        residuals = y_vals - preds
        dof = len(data) - X.shape[1]
        sigma2 = np.sum(residuals**2) / dof
        var_beta = sigma2 * np.linalg.inv(X.T @ X)
        se = np.sqrt(np.diag(var_beta))
        t_vals = beta / se
        p_vals = [2 * (1 - student_t.cdf(np.abs(t), dof)) for t in t_vals]
        names = ['const'] + list(X_cols)
        res_table = pd.DataFrame({
            'coef': beta,
            'std_err': se,
            't': t_vals,
            'P>|t|': p_vals
        }, index=names)
        return res_table, preds

    # Model 1: ERA Rating ~ log_10(v) + has_rec
    tab1, preds1 = run_ols('era', ['log_v', 'has_rec'], df)
    print("\n" + "="*80)
    print("MODEL 1: OLS Regression: ERA Rating ~ log_10(v) + has_rec")
    print("="*80)
    print(tab1.round(4))

    # Model 1b: Controlling for Go8 status
    tab1b, preds1b = run_ols('era', ['log_v', 'has_rec', 'is_go8'], df)
    print("\n" + "="*80)
    print("MODEL 1b: OLS Controlling for Go8: ERA Rating ~ log_10(v) + has_rec + is_go8")
    print("="*80)
    print(tab1b.round(4))

    # C. Residual Analysis
    # Baseline expected ERA solely from spectral score
    tab_base, era_hat = run_ols('era', ['log_v'], df)
    df['era_hat'] = era_hat
    df['residual'] = df['era'] - df['era_hat']

    mean_res_has = df[df['has_rec'] == 1]['residual'].mean()
    mean_res_no = df[df['has_rec'] == 0]['residual'].mean()
    med_res_has = df[df['has_rec'] == 1]['residual'].median()
    med_res_no = df[df['has_rec'] == 0]['residual'].median()

    t_stat, p_t = ttest_ind(df[df['has_rec'] == 1]['residual'], df[df['has_rec'] == 0]['residual'])
    u_stat, p_u = mannwhitneyu(df[df['has_rec'] == 1]['residual'], df[df['has_rec'] == 0]['residual'])

    print("\n" + "="*80)
    print("RESIDUAL ANALYSIS: (ERA Rating - Expected ERA from Spectral v)")
    print("="*80)
    print(f"Mean residual with REC member : {mean_res_has:+.3f} rating points")
    print(f"Mean residual without REC member: {mean_res_no:+.3f} rating points")
    print(f"Net Committee Premium           : {mean_res_has - mean_res_no:+.3f} rating points")
    print(f"Two-sample t-test               : t = {t_stat:.3f}, p = {p_t:.4e}")
    print(f"Mann-Whitney U test             : U = {u_stat:.1f}, p = {p_u:.4e}")

    # D. Severe Discrepancy Breakdown: Above World Parity (v >= 1.0) but Low ERA (<= 3)
    above_parity = df[df['v'] >= 1.0].copy()
    above_low_era = above_parity[above_parity['era'] <= 3]
    pct_low_with_rec = (above_parity[above_parity['has_rec'] == 1]['era'] <= 3).mean() * 100
    pct_low_no_rec = (above_parity[above_parity['has_rec'] == 0]['era'] <= 3).mean() * 100

    print("\n" + "="*80)
    print("THE DOWNGRADE TEST: Departments Operating Above World Parity (v >= 1.0)")
    print("="*80)
    print(f"Total departments operating above world parity: {len(above_parity)}")
    print(f"Rate of low rating (ERA <= 3) WITH an REC member: {pct_low_with_rec:.1f}%")
    print(f"Rate of low rating (ERA <= 3) WITHOUT an REC member: {pct_low_no_rec:.1f}%")
    print(f"Relative Risk of downgrade without committee presence: {pct_low_no_rec / max(pct_low_with_rec, 0.01):.2f}x")

    # Save detailed CSV
    out_csv = era_dir / "hep_reports/era2018_committee_representation_test.csv"
    df.to_csv(out_csv, index=False)
    print(f"\nDetailed unit-level dataset saved to {out_csv}")

if __name__ == "__main__":
    main()
