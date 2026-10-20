"""
Estimate how much of the ERA - Spectral Ranking difference is attributable to committee membership,
incorporating both Categorical (1-5 discrete scale) and Quasi-continuous (continuous rating & percentile ranks) frameworks.
"""

import itertools
import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats

def get_ols_stats(y, X_mat):
    beta = np.linalg.lstsq(X_mat, y, rcond=None)[0]
    preds = X_mat @ beta
    res = y - preds
    n, p = X_mat.shape
    dof = n - p
    sigma2 = np.sum(res**2) / dof
    var_beta = sigma2 * np.linalg.inv(X_mat.T @ X_mat)
    se = np.sqrt(np.diag(var_beta))
    t_vals = beta / se
    p_vals = [2 * (1 - stats.t.cdf(np.abs(t), dof)) for t in t_vals]
    r2 = 1.0 - np.sum(res**2) / np.sum((y - np.mean(y))**2)
    return beta, se, t_vals, p_vals, r2, preds, res

def shapley_lmg(y_col, x_cols, data):
    perms = list(itertools.permutations(x_cols))
    lmg = {var: 0.0 for var in x_cols}
    y_vals = data[y_col].values
    
    def r2_for_subset(sub):
        if len(sub) == 0: return 0.0
        X = np.column_stack([np.ones(len(data))] + [data[c].values for c in sub])
        b = np.linalg.lstsq(X, y_vals, rcond=None)[0]
        res = y_vals - X @ b
        return 1.0 - np.sum(res**2) / np.sum((y_vals - np.mean(y_vals))**2)

    for perm in perms:
        curr = []
        r2_curr = 0.0
        for v in perm:
            nxt = curr + [v]
            r2_nxt = r2_for_subset(nxt)
            lmg[v] += (r2_nxt - r2_curr) / len(perms)
            curr = nxt
            r2_curr = r2_nxt
    tot_r2 = r2_for_subset(x_cols)
    return lmg, tot_r2

def main():
    csv_path = Path("/home/lc/s/SpectralRankingGlobal_WORKING_Oct26/era2018/hep_reports/era2018_committee_representation_test.csv")
    df = pd.read_csv(csv_path)

    # -------------------------------------------------------------
    # 1. QUASI-CONTINUOUS WITHIN-DIVISION PERCENTILE RANK DIFFERENCE
    # -------------------------------------------------------------
    # For each 2-digit division, rank institutions by v and by ERA rating
    pct_records = []
    for div, g in df.groupby('div'):
        n = len(g)
        rk_v = g['v'].rank(method='average', ascending=True) / n
        rk_era = g['era'].rank(method='average', ascending=True) / n
        for idx in g.index:
            pct_records.append({
                'idx': idx,
                'pct_v': rk_v.loc[idx],
                'pct_era': rk_era.loc[idx],
                'delta_pct': rk_era.loc[idx] - rk_v.loc[idx]
            })
    df_pct = pd.DataFrame(pct_records).set_index('idx')
    df['pct_v'] = df_pct['pct_v']
    df['pct_era'] = df_pct['pct_era']
    df['delta_pct'] = df_pct['delta_pct']

    # -------------------------------------------------------------
    # 2. CATEGORICAL DISCRETIZATION (World Standard Benchmark & Quantiles)
    # -------------------------------------------------------------
    # Method A: Standard World Parity calibration (v=1.0 is parity):
    # ERA 5: v >= 1.40 (well above)
    # ERA 4: 1.10 <= v < 1.40 (above)
    # ERA 3: 0.80 <= v < 1.10 (world standard)
    # ERA 2: 0.50 <= v < 0.80 (below)
    # ERA 1: v < 0.50 (well below)
    def v_to_cat(v_val):
        if v_val >= 1.40: return 5
        elif v_val >= 1.10: return 4
        elif v_val >= 0.80: return 3
        elif v_val >= 0.50: return 2
        else: return 1

    df['cat_v'] = df['v'].apply(v_to_cat)
    df['cat_diff'] = df['era'] - df['cat_v'] # >0 => ERA rated higher than spectral; <0 => ERA downgraded

    # Also Empirical Quantile Mapping (matching ERA national distribution)
    # Cutoffs at percentiles matching cumulative proportions of ERA 1,2,3,4,5
    era_props = df['era'].value_counts(normalize=True).sort_index().cumsum()
    q_cuts = [df['v'].quantile(p) for p in era_props.iloc[:-1]]
    def v_to_qcat(v_val):
        for i, cut in enumerate(q_cuts):
            if v_val <= cut: return i + 1
        return len(q_cuts) + 1
    df['cat_v_quant'] = df['v'].apply(v_to_qcat)
    df['cat_diff_quant'] = df['era'] - df['cat_v_quant']

    # -------------------------------------------------------------
    # EXECUTE ESTIMATIONS FOR (A) ALL FIELDS, (B) HASS, (C) STEM
    # -------------------------------------------------------------
    regimes = {
        'ALL DISCIPLINES (N=506)': df,
        'HASS / PEER REVIEW DISCIPLINES (N=201)': df[df['is_peer'] == 1],
        'STEM / CITATION DISCIPLINES (N=305)': df[df['is_peer'] == 0]
    }

    print("=" * 90)
    print("ESTIMATING THE EFFECT OF COMMITTEE MEMBERSHIP ON ERA-SPECTRAL RANKING DIFFERENCES")
    print("=" * 90)

    results_table = []

    for name, sub in regimes.items():
        n_sub = len(sub)
        n_has = sub['has_rec'].sum()
        pct_has = sub['has_rec'].mean() * 100

        # --- A. Quasi-Continuous Rating Discrepancy: delta_R = ERA - ERA_hat(log_v) ---
        diff_r = sub['residual'] # this is ERA - hat{ERA}(log_v)
        var_diff = np.var(diff_r)

        # Regress diff_r on has_rec
        X_rec = np.column_stack([np.ones(n_sub), sub['has_rec'].values])
        b_r, se_r, t_r, p_r, r2_diff_rec, _, _ = get_ols_stats(diff_r, X_rec)

        # Regress diff_r on has_rec + is_go8
        X_rec_go8 = np.column_stack([np.ones(n_sub), sub['has_rec'].values, sub['is_go8'].values])
        b_rg, se_rg, t_rg, p_rg, r2_diff_full, _, _ = get_ols_stats(diff_r, X_rec_go8)

        # Mean discrepancy with and without REC
        mean_diff_with = diff_r[sub['has_rec'] == 1].mean()
        mean_diff_no = diff_r[sub['has_rec'] == 0].mean()
        rating_gap = mean_diff_with - mean_diff_no

        # --- B. Quasi-Continuous Percentile Rank Discrepancy: delta_pct = pct_ERA - pct_v ---
        d_pct = sub['delta_pct']
        X_pct = np.column_stack([np.ones(n_sub), sub['has_rec'].values])
        b_p, se_p, t_p, p_p, r2_pct_rec, _, _ = get_ols_stats(d_pct, X_pct)
        mean_pct_with = d_pct[sub['has_rec'] == 1].mean()
        mean_pct_no = d_pct[sub['has_rec'] == 0].mean()
        pct_rank_gap = (mean_pct_with - mean_pct_no) * 100 # in percentile points

        # --- C. Variance Decomposition of ERA Rating (Shapley / LMG) ---
        lmg, r2_tot = shapley_lmg('era', ['log_v', 'has_rec', 'is_go8'], sub)
        pct_lmg_rec = (lmg['has_rec'] / r2_tot) * 100
        pct_lmg_v = (lmg['log_v'] / r2_tot) * 100
        pct_lmg_go8 = (lmg['is_go8'] / r2_tot) * 100

        # --- D. Categorical Classification Discrepancies ---
        # Net categorical difference (ERA - cat_v)
        mean_cat_with = sub[sub['has_rec'] == 1]['cat_diff'].mean()
        mean_cat_no = sub[sub['has_rec'] == 0]['cat_diff'].mean()
        cat_gap = mean_cat_with - mean_cat_no

        # Directional discrepancies: Upward Upgrade (ERA > cat_v) vs Downward Penalty (ERA < cat_v)
        p_upgrade_with = (sub[sub['has_rec'] == 1]['cat_diff'] > 0).mean() * 100
        p_upgrade_no = (sub[sub['has_rec'] == 0]['cat_diff'] > 0).mean() * 100

        p_downgrade_with = (sub[sub['has_rec'] == 1]['cat_diff'] < 0).mean() * 100
        p_downgrade_no = (sub[sub['has_rec'] == 0]['cat_diff'] < 0).mean() * 100

        # Attributable Risk (Excess Downgrade Risk due to lack of REC)
        ar_downgrade = p_downgrade_no - p_downgrade_with
        # Population Attributable Fraction (PAF) among unrepresented
        af_downgrade = (ar_downgrade / max(p_downgrade_no, 0.001)) * 100 if p_downgrade_no > 0 else 0

        # High Parity Downgrade Risk (v >= 1.0)
        above = sub[sub['v'] >= 1.0]
        if len(above) > 0 and (above['has_rec'] == 0).sum() > 0:
            dg_high_with = (above[above['has_rec'] == 1]['era'] <= 3).mean() * 100
            dg_high_no = (above[above['has_rec'] == 0]['era'] <= 3).mean() * 100
            rr_high = dg_high_no / max(dg_high_with, 0.01)
        else:
            dg_high_with, dg_high_no, rr_high = np.nan, np.nan, np.nan

        results_table.append({
            'regime': name,
            'N': n_sub,
            'rating_gap': rating_gap,
            'p_rating_gap': p_r[1],
            'r2_diff_rec': r2_diff_rec * 100,
            'r2_diff_full': r2_diff_full * 100,
            'pct_rank_gap': pct_rank_gap,
            'p_pct_rank': p_p[1],
            'r2_pct_rec': r2_pct_rec * 100,
            'lmg_rec_pct': pct_lmg_rec,
            'lmg_v_pct': pct_lmg_v,
            'lmg_go8_pct': pct_lmg_go8,
            'cat_gap': cat_gap,
            'p_upgrade_with': p_upgrade_with,
            'p_upgrade_no': p_upgrade_no,
            'p_downgrade_with': p_downgrade_with,
            'p_downgrade_no': p_downgrade_no,
            'af_downgrade': af_downgrade,
            'dg_high_with': dg_high_with,
            'dg_high_no': dg_high_no,
            'rr_high': rr_high
        })

        print(f"\n>>> {name}")
        print("-" * 75)
        print("1. QUASI-CONTINUOUS RATING DIFFERENCE (Delta R = ERA - Expected ERA):")
        print(f"   - Rating gap (With REC - Without REC): {rating_gap:+.3f} rating points (t = {t_r[1]:.2f}, p = {p_r[1]:.4e})")
        print(f"   - Variance of Delta R explained by REC alone : {r2_diff_rec*100:.2f}% (R2 = {r2_diff_rec:.4f})")
        print(f"   - Variance of Delta R explained by REC + Go8 : {r2_diff_full*100:.2f}% (R2 = {r2_diff_full:.4f})")
        print(f"   - Mean Delta R with REC: {mean_diff_with:+.3f} | without REC: {mean_diff_no:+.3f}")
        print("\n2. QUASI-CONTINUOUS WITHIN-DIVISION PERCENTILE RANK SHIFT:")
        print(f"   - Shift in division percentile rank : {pct_rank_gap:+.1f} percentile points (t = {t_p[1]:.2f}, p = {p_p[1]:.4e})")
        print(f"   - Variance of percentile shift explained: {r2_pct_rec*100:.2f}%")
        print(f"   - Mean percentile with REC: {mean_pct_with*100:+.1f}th pct | without REC: {mean_pct_no*100:+.1f}th pct")
        print("\n3. SHAPLEY / LMG DECOMPOSITION OF EXPLAINED ERA VARIANCE (Total R2 = {:.1f}%):".format(r2_tot*100))
        print(f"   - Spectral Citation Impact (log v) : {pct_lmg_v:.1f}% of explained variance (LMG = {lmg['log_v']:.4f})")
        print(f"   - Committee Representation (has_rec): {pct_lmg_rec:.1f}% of explained variance (LMG = {lmg['has_rec']:.4f})")
        print(f"   - Institutional Prestige (is_go8)  : {pct_lmg_go8:.1f}% of explained variance (LMG = {lmg['is_go8']:.4f})")
        print("\n4. CATEGORICAL RATING SHIFT & ATTRIBUTABLE DOWNGRADE RISK:")
        print(f"   - Net categorical tier shift (With - Without): {cat_gap:+.3f} discrete ERA tiers")
        print(f"   - Rate of Upward Upgrade (ERA > Spectral Tier): With REC = {p_upgrade_with:.1f}% vs Without REC = {p_upgrade_no:.1f}%")
        print(f"   - Rate of Downward Penalty (ERA < Spectral Tier): With REC = {p_downgrade_with:.1f}% vs Without REC = {p_downgrade_no:.1f}%")
        print(f"   - Attributable Fraction of Downgrades: {af_downgrade:.1f}% of downgrades among unrepresented units are attributable to lack of committee presence")
        if not np.isnan(rr_high):
            print(f"   - Above-Parity (v >= 1.0) Downgrade Rate: With REC = {dg_high_with:.1f}% vs Without REC = {dg_high_no:.1f}% (Relative Risk = {rr_high:.2f}x)")

    # Save summary dataframe
    df_res = pd.DataFrame(results_table)
    out_summary = Path("/home/lc/s/SpectralRankingGlobal_WORKING_Oct26/era2018/hep_reports/era2018_committee_attribution_summary.csv")
    df_res.to_csv(out_summary, index=False)
    print(f"\nSummary table saved to {out_summary}")

if __name__ == '__main__':
    main()
