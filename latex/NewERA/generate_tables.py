"""
generate_tables.py — Generates publication-grade LaTeX tables for the NewERA paper.
"""

import sys
import json
from pathlib import Path
import pandas as pd
import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from util import load_config
from util.areas import AREAS, ALL_AREAS, INDIGENOUS_AREA
from util.runs import FIELD_NAMES

def main():
    paths = load_config()
    out_dir = Path(__file__).parent / 'tables'
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    # ──────────────────────────────────────────────────────────────────────────
    # Table 1: AREA5 Concordance
    # ──────────────────────────────────────────────────────────────────────────
    rows1 = []
    for a in AREAS:
        oax_str = ", ".join(f"{fid} {FIELD_NAMES[fid].replace('&', r'\&')}" for fid in a.oax_fields)
        for_str = ", ".join(str(d) for d in a.for_divisions)
        name_esc = a.name.replace('&', r'\&')
        rows1.append(f"{a.code} & {name_esc} & {for_str} & {oax_str} \\\\")

    for_str_ind = ", ".join(str(d) for d in INDIGENOUS_AREA.for_divisions)
    rows1.append(f"{INDIGENOUS_AREA.code} & {INDIGENOUS_AREA.name} & {for_str_ind} & \\textit{{Recognized national priority, zero-populated}} \\\\")

    tab1 = r"""\begin{table}[htbp]
\centering
\small
\caption{Concordance of the Five Broad Research Areas (AREA5) with Australian ANZSRC 2020 Divisions and OpenAlex Fields.}
\label{tab:area5_concordance}
\begin{tabularx}{\textwidth}{l >{\raggedright\arraybackslash}p{3.8cm} >{\raggedright\arraybackslash}p{2.6cm} >{\raggedright\arraybackslash}X}
\toprule
\textbf{Code} & \textbf{Broad Discipline} & \textbf{ANZSRC FOR 2020 (2-digit)} & \textbf{OpenAlex Fields} \\
\midrule
""" + "\n".join(rows1) + r"""
\bottomrule
\end{tabularx}
\end{table}
"""
    (out_dir / "tab1_area5_concordance.tex").write_text(tab1)
    print("Generated tab1_area5_concordance.tex")

    # ──────────────────────────────────────────────────────────────────────────
    # Table 2: Corpus Scale and Spectral Properties
    # ──────────────────────────────────────────────────────────────────────────
    rows2 = []
    for a in AREAS:
        diag_path = paths.working / f"area5/rankings_{a.id}_2020_2024_baseline_diag.json"
        with open(diag_path) as f:
            d = json.load(f)
        s_count = f"{d.get('n_s_ranked', 0):,d}"
        u_count = f"{d.get('n_u_ranked', 0):,d}"
        l1 = f"{d.get('lam1', 0.0):.4f}"
        l2 = f"{d.get('lam2', 0.0):.4f}"
        gap = f"{d.get('spectral_gap', 0.0):.4f}"
        sname_esc = a.short_name.replace('&', r'\&')
        rows2.append(f"{a.code} & {sname_esc} & {s_count} & {u_count} & {l1} & {l2} & {gap} \\\\")

    tab2 = r"""\begin{table}[htbp]
\centering
\small
\caption{Corpus Scale and Spectral Properties across the Five Broad Disciplines (2020--2024 Census Window).}
\label{tab:corpus_summary}
\begin{tabularx}{\textwidth}{l >{\raggedright\arraybackslash}X r r ccc}
\toprule
\textbf{Code} & \textbf{Broad Discipline} & \textbf{Sources ($N_S$)} & \textbf{Institutions ($N_U$)} & $\lambda_1$ & $\lambda_2$ & \textbf{Spectral Gap} \\
\midrule
""" + "\n".join(rows2) + r"""
\bottomrule
\end{tabularx}
\end{table}
"""
    (out_dir / "tab2_corpus_summary.tex").write_text(tab2)
    print("Generated tab2_corpus_summary.tex")


    # ──────────────────────────────────────────────────────────────────────────
    # Table 3: Australian Higher Education Providers Summary (Top 25)
    # ──────────────────────────────────────────────────────────────────────────
    keys = pd.read_excel(paths.data / 'HEP_concordances.xlsx', sheet_name='Keys')
    keys['inst_id'] = keys['institution_idx'].str.lstrip('I').astype('int64')

    hep_scores = {}
    for a in AREAS:
        pq = str(paths.working / f"area5/rankings_{a.id}_2020_2024_baseline.parquet")
        u_df = con.execute(f"SELECT unit_idx, v FROM '{pq}' WHERE unit_type = 'U'").df()
        m = keys.merge(u_df, left_on='inst_id', right_on='unit_idx', how='left')
        hep_scores[a.code] = m.set_index('HEP')['v']

    hep_df = pd.DataFrame(hep_scores)
    hep_df['Mean'] = hep_df.mean(axis=1)
    
    # Merge institution name and group
    name_map = keys.set_index('HEP')['Organisation'].to_dict()
    group_map = keys.set_index('HEP')['Group'].to_dict()
    hep_df['Name'] = hep_df.index.map(name_map)
    hep_df['Group'] = hep_df.index.map(group_map)
    hep_df = hep_df.sort_values('Mean', ascending=False)

    rows3 = []
    for hep, r in hep_df.head(25).iterrows():
        mcs = f"{r['MCS']:.2f}" if pd.notna(r['MCS']) else "--"
        pse = f"{r['PSE']:.2f}" if pd.notna(r['PSE']) else "--"
        les = f"{r['LES']:.2f}" if pd.notna(r['LES']) else "--"
        bhs = f"{r['BHS']:.2f}" if pd.notna(r['BHS']) else "--"
        ssh = f"{r['SSH']:.2f}" if pd.notna(r['SSH']) else "--"
        mean_v = f"{r['Mean']:.2f}"
        name = r['Name']
        grp = r['Group'] if pd.notna(r['Group']) else ""
        rows3.append(f"{hep} & {name} & {grp} & {mcs} & {pse} & {les} & {bhs} & {ssh} & \\textbf{{{mean_v}}} \\\\")

    tab3 = r"""\begin{table}[htbp]
\centering
\small
\caption{Institutional Influence Scores ($v_I$) for Leading Australian Universities across the Five Broad Disciplines (World Average = 1.0).}
\label{tab:hep_summary}
\begin{tabularx}{\textwidth}{l >{\raggedright\arraybackslash}X c cccccc}
\toprule
\textbf{Code} & \textbf{Institution} & \textbf{Group} & \textbf{MCS} & \textbf{PSE} & \textbf{LES} & \textbf{BHS} & \textbf{SSH} & \textbf{Mean $v_I$} \\
\midrule
""" + "\n".join(rows3) + r"""
\bottomrule
\end{tabularx}
\end{table}
"""
    (out_dir / "tab3_hep_summary.tex").write_text(tab3)
    print("Generated tab3_hep_summary.tex")

    # ──────────────────────────────────────────────────────────────────────────
    # Table 4: Sensitivity & Geopolitical Robustness Correlations (Leiden / AREA)
    # ──────────────────────────────────────────────────────────────────────────
    # Compute Spearman rank correlations for institutions between baseline and variants across Area 1..5
    variants = [
        ('OECDG20', 'OECD+G20 Bloc'),
        ('OECDG20CIA', 'OECD+G20 excl. CN/IN/US'),
        ('CIAA', 'AU, CN, IN, US Only'),
        ('tau20', r'Retention Threshold ($\tau=20$)'),
        ('rho1', r'Full Citation Count ($\rho=1$)'),
        ('eps1', r'Sentinel Model ($\epsilon=1$)'),
    ]

    corr_records = []
    for var_label, var_desc in variants:
        row_corrs = []
        for i in range(1, 6):
            base_pq = str(paths.working / f"rankings_{i}_2020_2024_baseline.parquet")
            var_pq = str(paths.working / f"rankings_{i}_2020_2024_{var_label}.parquet")
            try:
                sql = f"""
                SELECT b.v AS v_base, c.v AS v_var
                FROM '{base_pq}' b
                JOIN '{var_pq}' c ON b.unit_idx = c.unit_idx AND b.unit_type = c.unit_type
                WHERE b.unit_type = 'U' AND b.v IS NOT NULL AND c.v IS NOT NULL
                """
                pair_df = con.execute(sql).df()
                sp_corr = pair_df['v_base'].corr(pair_df['v_var'], method='spearman')
                row_corrs.append(f"{sp_corr:.3f}")
            except Exception:
                row_corrs.append("--")
        corr_records.append(f"{var_desc} & " + " & ".join(row_corrs) + r" \\")

    tab4 = r"""\begin{table}[htbp]
\centering
\small
\caption{Algorithmic and Geopolitical Stability: Spearman Rank Correlation ($\rho_s$) of Institutional Influence ($v_I$) between Baseline and Variants across the Five Broad Disciplines.}
\label{tab:sensitivity_correlations}
\begin{tabularx}{\textwidth}{>{\raggedright\arraybackslash}X ccccc}
\toprule
\textbf{Model / Bloc Variant} & \textbf{MCS} & \textbf{PSE} & \textbf{LES} & \textbf{BHS} & \textbf{SSH} \\
\midrule
""" + "\n".join(corr_records) + r"""
\bottomrule
\end{tabularx}
\end{table}
"""
    (out_dir / "tab4_sensitivity_correlations.tex").write_text(tab4)
    print("Generated tab4_sensitivity_correlations.tex")

if __name__ == '__main__':
    main()
