"""
Comprehensive Comparison of Committee Representation Effect on Rating Deficit:
Jul26 vs Oct26 OpenAlex Snapshots for ARC ERA 2018 Emulation.
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.stats import t as student_t, mannwhitneyu, ttest_ind, chi2_contingency, fisher_exact

ZERO_SEAT_HEPS = {'ACU', 'BON', 'CDU', 'CQU', 'CSU', 'ECU', 'FED', 'NDA', 'SWN', 'VIC'}
STEM_RECS = {'MIC', 'PCE', 'EE', 'BB', 'MHS'}
HASS_RECS = {'EC', 'EHS', 'HCA'}

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
        'p': p_vals
    }, index=names)
    r2 = 1.0 - (np.sum(residuals**2) / np.sum((y_vals - np.mean(y_vals))**2))
    return res_table, preds, r2

def enrich_data(df):
    df = df.copy()
    df['is_peer'] = df['rec'].isin(HASS_RECS).astype(int)
    df['has_rec_x_peer'] = df['has_rec'] * df['is_peer']
    df['is_zero_seat'] = df['hep'].isin(ZERO_SEAT_HEPS).astype(int)
    # Spectral expected ERA (Model: era ~ log_v)
    _, era_hat, _ = run_ols('era', ['log_v'], df)
    df['era_hat'] = era_hat
    df['residual'] = df['era'] - df['era_hat'] # positive = premium, negative = deficit
    df['rating_deficit'] = df['era_hat'] - df['era'] # positive = deficit below objective impact
    return df

def analyze_snapshot(df, label):
    df = enrich_data(df)
    n_total = len(df)
    n_has = df['has_rec'].sum()
    pct_has = df['has_rec'].mean() * 100
    
    print(f"\n{'='*80}\nSNAPSHOT: {label} (N = {n_total} Australian Units)\n{'='*80}")
    print(f"Units with REC member on panel: {n_has} ({pct_has:.1f}%)")
    print(f"STEM/Citation units: {sum(df['is_peer'] == 0)}, HASS/Peer Review units: {sum(df['is_peer'] == 1)}")
    
    # Model 1
    tab1, _, r2_1 = run_ols('era', ['log_v', 'has_rec'], df)
    print("\n--- MODEL 1: ERA ~ log10(v) + has_rec --- (R2 = {:.4f})".format(r2_1))
    print(tab1.round(4))
    
    # Model 1b (Go8 control)
    tab1b, _, r2_1b = run_ols('era', ['log_v', 'has_rec', 'is_go8'], df)
    print("\n--- MODEL 1b: ERA ~ log10(v) + has_rec + is_go8 --- (R2 = {:.4f})".format(r2_1b))
    print(tab1b.round(4))
    
    # Model 2 (Interaction)
    tab2, _, r2_2 = run_ols('era', ['log_v', 'has_rec', 'is_peer', 'has_rec_x_peer', 'is_go8'], df)
    print("\n--- MODEL 2: Interaction Model (Peer Review Latitude vs Citation Constraint) --- (R2 = {:.4f})".format(r2_2))
    print(tab2.round(4))
    
    # Model 3: Zero-seat penalty
    tab3, _, r2_3 = run_ols('era', ['log_v', 'is_go8', 'is_zero_seat'], df)
    print("\n--- MODEL 3: Zero-Seat Institutional Deficit Penalty --- (R2 = {:.4f})".format(r2_3))
    print(tab3.round(4))
    
    # Disciplinary Subgroup Regressions
    df_stem = df[df['is_peer'] == 0]
    df_hass = df[df['is_peer'] == 1]
    tab_stem, _, _ = run_ols('era', ['log_v', 'has_rec', 'is_go8'], df_stem)
    tab_hass, _, _ = run_ols('era', ['log_v', 'has_rec', 'is_go8'], df_hass)
    
    print("\n--- REGIME SUBGROUPS: STEM (N={}) vs HASS (N={}) ---".format(len(df_stem), len(df_hass)))
    print("STEM has_rec coef: {:+.4f} (t = {:.2f}, p = {:.4f}) | Go8 coef: {:+.4f} (p = {:.4f})".format(
        tab_stem.loc['has_rec', 'coef'], tab_stem.loc['has_rec', 't'], tab_stem.loc['has_rec', 'p'],
        tab_stem.loc['is_go8', 'coef'], tab_stem.loc['is_go8', 'p']
    ))
    print("HASS has_rec coef: {:+.4f} (t = {:.2f}, p = {:.4f}) | Go8 coef: {:+.4f} (p = {:.4f})".format(
        tab_hass.loc['has_rec', 'coef'], tab_hass.loc['has_rec', 't'], tab_hass.loc['has_rec', 'p'],
        tab_hass.loc['is_go8', 'coef'], tab_hass.loc['is_go8', 'p']
    ))
    
    # Residual Analysis
    res_has = df[df['has_rec'] == 1]['residual']
    res_no = df[df['has_rec'] == 0]['residual']
    premium = res_has.mean() - res_no.mean()
    t_stat, p_t = ttest_ind(res_has, res_no)
    u_stat, p_u = mannwhitneyu(res_has, res_no)
    
    # HASS residuals
    res_hass_has = df_hass[df_hass['has_rec'] == 1]['residual']
    res_hass_no = df_hass[df_hass['has_rec'] == 0]['residual']
    hass_premium = res_hass_has.mean() - res_hass_no.mean()
    t_hass, p_t_hass = ttest_ind(res_hass_has, res_hass_no)
    
    # Zero seat residuals
    res_zero = df[df['is_zero_seat'] == 1]['residual']
    res_nonzero = df[df['is_zero_seat'] == 0]['residual']
    zero_deficit = res_nonzero.mean() - res_zero.mean()
    
    print("\n--- RESIDUAL / RATING DEFICIT ANALYSIS ---")
    print(f"All Fields: With REC: {res_has.mean():+.3f} | Without REC: {res_no.mean():+.3f} | Net Premium: {premium:+.3f} pts (t={t_stat:.2f}, p={p_t:.4e})")
    print(f"HASS Only : With REC: {res_hass_has.mean():+.3f} | Without REC: {res_hass_no.mean():+.3f} | Net Premium: {hass_premium:+.3f} pts (t={t_hass:.2f}, p={p_t_hass:.4e})")
    print(f"Zero-Seat Universities (N={len(res_zero)}): Mean Residual: {res_zero.mean():+.3f} pts | Deficit vs Peers: {zero_deficit:+.3f} pts")
    
    # Downgrade Test (v >= 1.0)
    above = df[df['v'] >= 1.0].copy()
    n_above = len(above)
    rate_has = (above[above['has_rec'] == 1]['era'] <= 3).mean() * 100
    rate_no = (above[above['has_rec'] == 0]['era'] <= 3).mean() * 100
    rr = rate_no / max(rate_has, 0.001)
    # Contingency
    c_table = [
        [sum((above['has_rec'] == 1) & (above['era'] >= 4)), sum((above['has_rec'] == 1) & (above['era'] <= 3))],
        [sum((above['has_rec'] == 0) & (above['era'] >= 4)), sum((above['has_rec'] == 0) & (above['era'] <= 3))]
    ]
    _, p_chi, _, _ = chi2_contingency(c_table)
    _, p_fish = fisher_exact(c_table)
    
    print("\n--- DOWNGRADE TEST (v >= 1.0 Parity, N = {}) ---".format(n_above))
    print(f"Downgrade rate WITH REC member   : {rate_has:.1f}%")
    print(f"Downgrade rate WITHOUT REC member: {rate_no:.1f}%")
    print(f"Relative Risk of Downgrade       : {rr:.2f}x (Chi2 p = {p_chi:.4f}, Fisher p = {p_fish:.4f})")
    
    return {
        'df': df,
        'model1_coef': tab1.loc['has_rec', 'coef'],
        'model1_p': tab1.loc['has_rec', 'p'],
        'model1b_coef': tab1b.loc['has_rec', 'coef'],
        'model1b_p': tab1b.loc['has_rec', 'p'],
        'model1b_go8': tab1b.loc['is_go8', 'coef'],
        'model2_interaction': tab2.loc['has_rec_x_peer', 'coef'],
        'model2_interaction_p': tab2.loc['has_rec_x_peer', 'p'],
        'stem_coef': tab_stem.loc['has_rec', 'coef'],
        'stem_p': tab_stem.loc['has_rec', 'p'],
        'hass_coef': tab_hass.loc['has_rec', 'coef'],
        'hass_p': tab_hass.loc['has_rec', 'p'],
        'net_premium': premium,
        'p_t': p_t,
        'hass_premium': hass_premium,
        'p_t_hass': p_t_hass,
        'zero_seat_penalty': tab3.loc['is_zero_seat', 'coef'],
        'zero_seat_p': tab3.loc['is_zero_seat', 'p'],
        'rr_downgrade': rr,
        'p_fish': p_fish
    }

def main():
    jul_path = Path("/home/lc/s/SpectralRankingGlobal_WORKING_jul26/era2018/hep_reports/era2018_committee_representation_test.csv")
    oct_path = Path("/home/lc/s/SpectralRankingGlobal_WORKING_Oct26/era2018/hep_reports/era2018_committee_representation_test.csv")
    
    df_jul = pd.read_csv(jul_path)
    df_oct = pd.read_csv(oct_path)
    
    res_jul = analyze_snapshot(df_jul, "JUL26 SNAPSHOT")
    res_oct = analyze_snapshot(df_oct, "OCT26 SNAPSHOT")
    
    # Save enriched Oct26 dataframe
    res_oct['df'].to_csv(oct_path, index=False)
    print(f"\nUpdated enriched dataset saved to {oct_path}")
    
    # Side-by-side summary table
    print("\n" + "="*80)
    print("SIDE-BY-SIDE SUMMARY COMPARISON: JUL26 vs OCT26 SNAPSHOTS")
    print("="*80)
    metrics = [
        ("Evaluated Australian Units (N)", len(df_jul), len(df_oct)),
        ("Model 1: has_rec coefficient (p)", f"{res_jul['model1_coef']:+.4f} (p={res_jul['model1_p']:.4f})", f"{res_oct['model1_coef']:+.4f} (p={res_oct['model1_p']:.4f})"),
        ("Model 1b: has_rec coef controlling for Go8 (p)", f"{res_jul['model1b_coef']:+.4f} (p={res_jul['model1b_p']:.4f})", f"{res_oct['model1b_coef']:+.4f} (p={res_oct['model1b_p']:.4f})"),
        ("Model 1b: Go8 coefficient (p)", f"{res_jul['model1b_go8']:+.4f}", f"{res_oct['model1b_go8']:+.4f}"),
        ("Model 2: has_rec x Peer Review interaction (p)", f"{res_jul['model2_interaction']:+.4f} (p={res_jul['model2_interaction_p']:.4f})", f"{res_oct['model2_interaction_p']:+.4f} (p={res_oct['model2_interaction_p']:.4f})"),
        ("STEM Subgroup: has_rec coefficient (p)", f"{res_jul['stem_coef']:+.4f} (p={res_jul['stem_p']:.4f})", f"{res_oct['stem_coef']:+.4f} (p={res_oct['stem_p']:.4f})"),
        ("HASS Subgroup: has_rec coefficient (p)", f"{res_jul['hass_coef']:+.4f} (p={res_jul['hass_p']:.4f})", f"{res_oct['hass_coef']:+.4f} (p={res_oct['hass_p']:.4f})"),
        ("Overall Net Committee Premium (All Fields)", f"{res_jul['net_premium']:+.3f} pts (p={res_jul['p_t']:.2e})", f"{res_oct['net_premium']:+.3f} pts (p={res_oct['p_t']:.2e})"),
        ("HASS Net Committee Premium", f"{res_jul['hass_premium']:+.3f} pts (p={res_jul['p_t_hass']:.2e})", f"{res_oct['hass_premium']:+.3f} pts (p={res_oct['p_t_hass']:.2e})"),
        ("Zero-Seat Institutional Penalty (p)", f"{res_jul['zero_seat_penalty']:+.4f} (p={res_jul['zero_seat_p']:.4f})", f"{res_oct['zero_seat_penalty']:+.4f} (p={res_oct['zero_seat_p']:.4f})"),
        ("Above Parity Downgrade Relative Risk (p)", f"{res_jul['rr_downgrade']:.2f}x (p={res_jul['p_fish']:.4f})", f"{res_oct['rr_downgrade']:.2f}x (p={res_oct['p_fish']:.4f})"),
    ]
    
    summary_df = pd.DataFrame(metrics, columns=['Metric / Specification', 'Jul26 Snapshot', 'Oct26 Snapshot'])
    print(summary_df.to_string(index=False))

if __name__ == '__main__':
    main()
