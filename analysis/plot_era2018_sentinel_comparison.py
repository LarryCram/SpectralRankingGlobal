"""
analysis/plot_era2018_sentinel_comparison.py — Seaborn facet comparison of ERA 2018 2-digit Divisions with and without Sentinels.

Compares the baseline ERA 2018 emulation (epsilon=0, label='baseline') against the
sentinel ERA 2018 emulation (epsilon=1, label='sentinel', exogenous citations included)
for any ANZSRC FoR 2-digit Division (01 to 22).

Visual Architecture (based on plot_era2018_generic.py):
  1. Two vertical facets per division (sharex=True, sharey=True):
     - Top Panel: Baseline Model (Without Sentinels, epsilon=0)
     - Bottom Panel: Sentinel Model (With Exogenous Sentinels, epsilon=1)
  2. Uses the baseline 2-digit global rank as a common x-axis (x = rank_base) across both facets:
     - Drops subtle vertical dashed lines down both facets at the exact x-position of each Australian HEP.
     - Bottom panel aligns institutions on the same drop line, showing directional movement in v and rank.
  3. Plots log10(v) on the y-axis with ticks at [1, 0, -1, -2], rotated tick labels, and label "log(v)".
  4. Emboldens the horizontal parity line at log10(v) = 0 (v = 1.0).
  5. Highlights Australian Higher Education Providers (HEPs) with hues corresponding to official ARC ERA 2018 ratings (ERA 5, 4, 3, 2, 1, NA).
  6. In the bottom panel, displays vertical displacement vectors (green for gain, red for drop) and annotated rank deltas.
  7. Labels Australian HEPs with quarter-turn counter-clockwise (90°) rotated text and staggered callouts.
  8. Saves publication-quality PNG (300 DPI) and PDF figures to WORKING/era2018_sentinel/plots/.

Usage:
  .venv/bin/python analysis/plot_era2018_sentinel_comparison.py --division 01
  .venv/bin/python analysis/plot_era2018_sentinel_comparison.py --division 02
  .venv/bin/python analysis/plot_era2018_sentinel_comparison.py --all
"""

import argparse
import glob
import json
from pathlib import Path
import sys
from typing import Optional

import duckdb
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import matplotlib.transforms as mtransforms
import numpy as np
import pandas as pd
import seaborn as sns

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config

ERA_WINDOW = "2011_2016"

ERA_PALETTE = {
    'ERA 5 (Well above world standard)': '#1b7837',    # Rich dark green
    'ERA 4 (Above world standard)':      '#2166ac',    # Deep blue
    'ERA 3 (At world standard)':         '#d95f02',    # Warm amber / orange
    'ERA 2 (Below world standard)':      '#7570b3',    # Purple
    'ERA 1 (Well below world standard)': '#b2182b',    # Crimson
    'ERA NA (Unassessed in official ERA)': '#64748b',  # Slate gray
}

ERA_MARKERS = {
    'ERA 5 (Well above world standard)': 'o',
    'ERA 4 (Above world standard)':      's',
    'ERA 3 (At world standard)':         '^',
    'ERA 2 (Below world standard)':      'v',
    'ERA 1 (Well below world standard)': '<',
    'ERA NA (Unassessed in official ERA)': 'D',
}


def load_era_official_outcomes() -> dict[tuple[str, str], str]:
    """Load official ERA 2018 outcomes for all HEPs and FoR codes."""
    outcome_files = glob.glob('/home/lc/Dropbox/RESEARCH/ERA/data/ERA_OUTCOMES/*.pkl')
    era_map = {}
    for f in outcome_files:
        try:
            df = pd.read_pickle(f)
            for _, r in df.iterrows():
                hep = str(r['short_name']).strip()
                code_raw = str(r['FOR_code']).strip()
                code = code_raw.zfill(2)
                if len(code) == 3:
                    code = '0' + code
                val = str(r[2018]).strip()
                era_map[(hep, code)] = val
        except Exception:
            continue
    return era_map


def load_hep_mappings(data_dir: Path) -> tuple[dict[int, str], dict[int, str]]:
    """Load Australian HEP institution ID mappings."""
    hep_file = data_dir / "HEP_concordances.xlsx"
    if not hep_file.exists():
        return {}, {}
    hep_df = pd.read_excel(hep_file, sheet_name='Keys')
    hep_df['inst_id'] = hep_df['institution_idx'].astype(str).str.lstrip('I').astype('int64')
    id_to_hep = dict(zip(hep_df['inst_id'], hep_df['HEP']))
    id_to_name = dict(zip(hep_df['inst_id'], hep_df['Organisation']))
    return id_to_hep, id_to_name


def load_division_comparison_data(
    div_code: str,
    era_base_dir: Path,
    era_sent_dir: Path,
    id_to_hep: dict[int, str],
    id_to_name: dict[int, str],
    era_map: dict[tuple[str, str], str],
) -> tuple[pd.DataFrame, pd.DataFrame, str, int]:
    """
    Load and join 2-digit division data for baseline and sentinel runs.
    Returns: (global_df, au_df, div_label, recommended_max_x)
    """
    div_code = div_code.zfill(2)
    base_pq = era_base_dir / "division" / f"rankings_div_{div_code}_{ERA_WINDOW}_baseline.parquet"
    sent_pq = era_sent_dir / "division" / f"rankings_div_{div_code}_{ERA_WINDOW}_sentinel.parquet"

    if not base_pq.exists() or not sent_pq.exists():
        raise FileNotFoundError(f"Missing parquet for Division {div_code} in baseline ({base_pq.exists()}) or sentinel ({sent_pq.exists()})")

    # Read diagnostic label
    div_label = f"FoR {div_code}"
    diag_file = base_pq.with_name(f"{base_pq.stem}_diag.json")
    if diag_file.exists():
        try:
            diag = json.loads(diag_file.read_text())
            div_label = f"FoR {div_code}: {diag.get('for_label', '')}"
        except Exception:
            pass

    con = duckdb.connect()
    # Join baseline and sentinel runs
    df = con.execute(f"""
        SELECT 
            b.unit_idx,
            b.rank_v AS rank_base,
            b.v AS v_base,
            b.pi AS pi_base,
            b.a_p AS ap_base,
            s.rank_v AS rank_sent,
            s.v AS v_sent,
            s.pi AS pi_sent,
            s.a_p AS ap_sent
        FROM '{base_pq}' b
        JOIN '{sent_pq}' s ON b.unit_idx = s.unit_idx
        WHERE b.unit_type = 'U' AND s.unit_type = 'U'
        ORDER BY b.rank_v ASC
    """).df()

    df['log_v_base'] = np.where(df['v_base'] > 0, np.log10(df['v_base']), -3.0)
    df['log_v_sent'] = np.where(df['v_sent'] > 0, np.log10(df['v_sent']), -3.0)
    df['delta_v'] = df['v_sent'] - df['v_base']
    df['delta_log_v'] = df['log_v_sent'] - df['log_v_base']
    df['delta_rank'] = df['rank_base'] - df['rank_sent']  # positive = improved rank

    df['is_au'] = df['unit_idx'].isin(id_to_hep)
    df['hep_code'] = df['unit_idx'].map(id_to_hep).fillna("")
    df['hep_name'] = df['unit_idx'].map(id_to_name).fillna("")

    # Map official ERA category
    def get_era_cat(row):
        if not row['is_au']:
            return 'Global Institutions'
        off = era_map.get((row['hep_code'], div_code), "NA")
        if off == '5':
            return 'ERA 5 (Well above world standard)'
        elif off == '4':
            return 'ERA 4 (Above world standard)'
        elif off == '3':
            return 'ERA 3 (At world standard)'
        elif off == '2':
            return 'ERA 2 (Below world standard)'
        elif off == '1':
            return 'ERA 1 (Well below world standard)'
        else:
            return 'ERA NA (Unassessed in official ERA)'

    df['era_category'] = df.apply(get_era_cat, axis=1)

    au_df = df[df['is_au']].copy()

    # Determine recommended max_x based on Australian HEPs with comfortable right-hand breathing room
    if not au_df.empty:
        max_au_rank = int(max(au_df['rank_base'].max(), au_df['rank_sent'].max()))
        calc_max = int(np.ceil((max_au_rank + 150) / 100.0) * 100)
        recommended_max_x = max(calc_max, 250)
    else:
        recommended_max_x = min(len(df), 500)

    return df, au_df, div_label, recommended_max_x


def plot_division_comparison(
    div_code: str,
    global_df: pd.DataFrame,
    au_df: pd.DataFrame,
    div_label: str,
    out_dir: Path,
    max_x: Optional[int] = None,
):
    """
    Render publication-quality 2-panel comparison facet plot for a 2-digit division.
    Panel 1: Baseline (Without Sentinels, epsilon=0)
    Panel 2: Sentinel Model (With Exogenous Sentinels, epsilon=1)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    div_code = div_code.zfill(2)

    out_png = out_dir / f"era2018_div{div_code}_sentinel_comparison.png"
    out_pdf = out_dir / f"era2018_div{div_code}_sentinel_comparison.pdf"

    sns.set_theme(style="whitegrid", font="sans-serif")

    fig, (ax1, ax2) = plt.subplots(
        nrows=2, ncols=1, figsize=(14, 9.8),
        sharey=True, sharex=True,
        gridspec_kw={'hspace': 0.08}
    )

    # Filter to visible window
    visible_au = au_df[(au_df['rank_base'] <= max_x) & (au_df['rank_base'] >= 1)]
    au_ranks_to_mark = sorted(visible_au['rank_base'].unique())

    g_vis = global_df[global_df['rank_base'] <= max_x].sort_values('rank_base')
    a_vis = visible_au.sort_values('rank_base')

    # Subtle drop lines down BOTH facets at Australian baseline rank positions
    for rx in au_ranks_to_mark:
        ax1.axvline(rx, color='#cbd5e1', linestyle=':', linewidth=0.85, alpha=0.75, zorder=1)
        ax2.axvline(rx, color='#cbd5e1', linestyle=':', linewidth=0.85, alpha=0.75, zorder=1)

    # Reference parity lines at log10(v) = 0
    ax1.axhline(0.0, color='#0f172a', linestyle='--', linewidth=2.0, alpha=0.95, zorder=3,
                label=r'World Parity ($\log_{10}(v) = 0$)')
    ax2.axhline(0.0, color='#0f172a', linestyle='--', linewidth=2.0, alpha=0.95, zorder=3,
                label=r'World Parity ($\log_{10}(v) = 0$)')

    # =========================================================================
    # PANEL 1: BASELINE (WITHOUT SENTINELS)
    # =========================================================================
    # Global institutions baseline curve
    ax1.plot(
        g_vis['rank_base'], g_vis['log_v_base'],
        color='#94a3b8', linewidth=1.8, alpha=0.85, zorder=2,
        label=f'Global baseline ($N = {len(global_df):,}$)'
    )

    # Australian HEP points by ERA hue
    for cat, color in ERA_PALETTE.items():
        cat_pts = a_vis[a_vis['era_category'] == cat]
        if not cat_pts.empty:
            marker = ERA_MARKERS.get(cat, 'o')
            ax1.scatter(
                cat_pts['rank_base'], cat_pts['log_v_base'],
                color=color, marker=marker, s=100,
                edgecolor='white', linewidth=1.4, zorder=5,
                label=f'{cat} ($n={len(cat_pts)}$)'
            )

    # Adaptive staggering: left half alternates up/down; right half points down away from upper-right legend
    def get_stagger(i: int, x_val: float) -> tuple[float, str]:
        if x_val > 0.52 * max_x:
            shift = -0.28 if (i % 2 == 0) else -0.50
            return shift, 'top'
        else:
            shifts = [+0.28, -0.28, +0.50, -0.50]
            s = shifts[i % len(shifts)]
            return s, ('bottom' if s > 0 else 'top')

    for i, (_, r) in enumerate(a_vis.iterrows()):
        hep = r['hep_code']
        x = r['rank_base']
        y = r['log_v_base']
        txt = f"{hep}"

        y_shift, va = get_stagger(i, x)

        ax1.annotate(
            txt,
            xy=(x, y),
            xytext=(x, y + y_shift),
            rotation=90,
            arrowprops=dict(arrowstyle="-", color='#475569', lw=0.9, alpha=0.85),
            fontsize=8.5, fontweight='bold', color='#0f172a',
            ha='center', va=va, zorder=6,
            bbox=dict(boxstyle="round,pad=0.18", facecolor='white', edgecolor='#cbd5e1', alpha=0.92, lw=0.6)
        )

    # Inner facet label
    trans1 = mtransforms.blended_transform_factory(ax1.transAxes, ax1.transData)
    ax1.text(
        0.012, -2.0,
        f"{div_label} — Baseline Model (Without Sentinels, ε=0) [AU: {len(a_vis)}/{len(au_df)}]",
        transform=trans1,
        fontsize=10.5, fontweight='normal', color='#0f172a',
        va='bottom', ha='left', zorder=7,
        bbox=dict(boxstyle="square,pad=0.2", facecolor='white', edgecolor='none', alpha=0.88)
    )
    ax1.set_ylabel("log(v)", fontsize=11, fontweight='bold', color='#0f172a')

    # Legend for Panel 1 (compact 2 columns)
    handles1, labels1 = ax1.get_legend_handles_labels()
    by_label1 = dict(zip(labels1, handles1))
    ax1.legend(
        by_label1.values(), by_label1.keys(),
        loc='upper right', ncol=2, frameon=True, framealpha=0.94,
        facecolor='white', edgecolor='#cbd5e1', fontsize=7.8, columnspacing=1.0
    )

    # =========================================================================
    # PANEL 2: SENTINEL MODEL (WITH EXOGENOUS SENTINELS)
    # =========================================================================
    # Global institutions in sentinel model (plotted against common baseline rank)
    ax2.scatter(
        g_vis['rank_base'], g_vis['log_v_sent'],
        color='#94a3b8', s=14, alpha=0.40, zorder=2,
        label=f'Global under sentinel ($N = {len(global_df):,}$)'
    )

    # Faint baseline reference curve in panel 2 for comparison
    ax2.plot(
        g_vis['rank_base'], g_vis['log_v_base'],
        color='#cbd5e1', linestyle=':', linewidth=1.2, alpha=0.75, zorder=2,
        label='Baseline curve (ref)'
    )

    # Vertical displacement arrows and ghosted baseline points for Australian HEPs
    for _, r in a_vis.iterrows():
        x = r['rank_base']
        y_old = r['log_v_base']
        y_new = r['log_v_sent']

        # Ghosted original baseline marker
        ax2.scatter(x, y_old, color='#94a3b8', marker='o', s=45, alpha=0.45, zorder=3)

        # Directional shift vector
        vec_color = '#15803d' if y_new >= y_old else '#b91c1c'
        ax2.annotate(
            '', xy=(x, y_new), xytext=(x, y_old),
            arrowprops=dict(arrowstyle="->", color=vec_color, lw=1.2, alpha=0.75),
            zorder=4
        )

    # Australian HEP points by ERA hue in sentinel model
    for cat, color in ERA_PALETTE.items():
        cat_pts = a_vis[a_vis['era_category'] == cat]
        if not cat_pts.empty:
            marker = ERA_MARKERS.get(cat, 'o')
            ax2.scatter(
                cat_pts['rank_base'], cat_pts['log_v_sent'],
                color=color, marker=marker, s=100,
                edgecolor='white', linewidth=1.4, zorder=5,
                label=f'{cat} ($n={len(cat_pts)}$)'
            )

    # Australian HEP labels in sentinel model (showing short name, new rank, and rank change)
    for i, (_, r) in enumerate(a_vis.iterrows()):
        hep = r['hep_code']
        x = r['rank_base']
        y = r['log_v_sent']
        r_new = int(r['rank_sent'])
        dr = int(r['delta_rank'])  # positive = improved rank
        dr_str = f"▲{dr}" if dr > 0 else (f"▼{-dr}" if dr < 0 else "=")
        txt = f"{hep}\n#{r_new} ({dr_str})"

        y_shift, va = get_stagger(i, x)

        ax2.annotate(
            txt,
            xy=(x, y),
            xytext=(x, y + y_shift),
            rotation=90,
            arrowprops=dict(arrowstyle="-", color='#475569', lw=0.9, alpha=0.85),
            fontsize=7.8, fontweight='bold', color='#0f172a',
            ha='center', va=va, zorder=6,
            bbox=dict(boxstyle="round,pad=0.18", facecolor='white', edgecolor='#cbd5e1', alpha=0.92, lw=0.6)
        )

    # Inner facet label
    trans2 = mtransforms.blended_transform_factory(ax2.transAxes, ax2.transData)
    ax2.text(
        0.012, -2.0,
        f"{div_label} — Sentinel Model (With Exogenous Sentinels, ε=1) [Aligned to Baseline Ranks]",
        transform=trans2,
        fontsize=10.5, fontweight='normal', color='#0f172a',
        va='bottom', ha='left', zorder=7,
        bbox=dict(boxstyle="square,pad=0.2", facecolor='white', edgecolor='none', alpha=0.88)
    )
    ax2.set_ylabel("log(v)", fontsize=11, fontweight='bold', color='#0f172a')

    # Legend for Panel 2 (compact 2 columns)
    handles2, labels2 = ax2.get_legend_handles_labels()
    by_label2 = dict(zip(labels2, handles2))
    ax2.legend(
        by_label2.values(), by_label2.keys(),
        loc='upper right', ncol=2, frameon=True, framealpha=0.94,
        facecolor='white', edgecolor='#cbd5e1', fontsize=7.8, columnspacing=1.0
    )

    # Axes limits, ticks, and rotation
    for ax in (ax1, ax2):
        ax.set_ylim(-2.2, 1.1)
        ax.set_yticks([1.0, 0.0, -1.0, -2.0])
        ax.set_yticklabels(['1', '0', '-1', '-2'])
        ax.tick_params(axis='y', labelrotation=90, labelsize=9.5)

        ax.set_xlim(0, max_x)
        step = 50 if max_x <= 350 else 100
        ax.xaxis.set_major_locator(ticker.MultipleLocator(step))
        ax.xaxis.set_minor_locator(ticker.MultipleLocator(step // 4))

    ax2.set_xlabel(
        f"Ordinal Global Rank in Baseline FoR {div_code} Division ($x = \\text{{rank}}_{{\\text{{baseline}}}}$, common scale $\\rightarrow$)",
        fontsize=11, fontweight='bold', color='#0f172a'
    )

    plt.suptitle(
        f"ERA 2018 Emulation: {div_label} — Impact of Exogenous Sentinels\n"
        f"Direct Facet Comparison: Baseline (ε=0) vs. Sentinel (ε=1) Across Australian HEPs",
        fontsize=13.0, fontweight='bold', y=0.995, color='#0f172a'
    )

    plt.savefig(out_png, dpi=300, bbox_inches='tight')
    plt.savefig(out_pdf, bbox_inches='tight')
    plt.close()

    print(f"Saved sentinel comparison facet plot for Division {div_code}:")
    print(f"  → {out_png}")
    print(f"  → {out_pdf}")


def plot_national_summary(
    all_au_df: pd.DataFrame,
    out_dir: Path
):
    """
    Generate national summary scatter & boxplot comparing Baseline vs. Sentinel
    across all Australian institutions across all 22 divisions.
    """
    out_png = out_dir / "era2018_all_divisions_sentinel_comparison.png"
    out_pdf = out_dir / "era2018_all_divisions_sentinel_comparison.pdf"

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

    # Left plot: Rank Baseline vs. Rank Sentinel
    max_rank = max(all_au_df['rank_base'].max(), all_au_df['rank_sent'].max()) * 1.05
    ax1.plot([1, max_rank], [1, max_rank], color='#0f172a', linestyle='--', linewidth=1.5, alpha=0.7, label='Identity (Rank Unchanged)')
    
    # Color points by official ERA rating
    for cat, color in ERA_PALETTE.items():
        cat_pts = all_au_df[all_au_df['era_category'] == cat]
        if not cat_pts.empty:
            marker = ERA_MARKERS.get(cat, 'o')
            ax1.scatter(
                cat_pts['rank_base'], cat_pts['rank_sent'],
                color=color, marker=marker, s=55, alpha=0.85,
                edgecolor='white', linewidth=0.8,
                label=f'{cat} ($n={len(cat_pts)}$)'
            )

    ax1.set_xscale('log')
    ax1.set_yscale('log')
    ax1.set_xlabel('Global Rank in Baseline (ε=0, log scale)', fontsize=11, fontweight='bold')
    ax1.set_ylabel('Global Rank in Sentinel Model (ε=1, log scale)', fontsize=11, fontweight='bold')
    ax1.set_title('Global Rank Comparison: Baseline vs. Sentinel Across All 22 Divisions', fontsize=12, fontweight='bold')
    ax1.legend(loc='lower right', frameon=True, fontsize=8.5)

    # Right plot: Delta log10(v) by FoR Division
    div_order = sorted(all_au_df['for_code'].unique())
    sns.boxplot(
        data=all_au_df, x='for_code', y='delta_log_v',
        order=div_order, ax=ax2, color='#94a3b8',
        fliersize=3, boxprops=dict(alpha=0.6)
    )
    ax2.axhline(0.0, color='#0f172a', linestyle='--', linewidth=1.5, alpha=0.8)
    ax2.set_xlabel('ANZSRC FoR 2-digit Division', fontsize=11, fontweight='bold')
    ax2.set_ylabel(r'$\Delta \log_{10}(v) = \log_{10}(v_{\mathrm{sentinel}}) - \log_{10}(v_{\mathrm{baseline}})$', fontsize=11, fontweight='bold')
    ax2.set_title(r'Spectral Impact ($\Delta \log_{10}(v)$) Across FoR Divisions', fontsize=12, fontweight='bold')
    ax2.tick_params(axis='x', rotation=45)

    plt.suptitle('ERA 2018 Emulation: National Impact of Exogenous Sentinels Across Australian HEPs (All 22 Divisions)',
                 fontsize=14, fontweight='bold', y=0.99)
    plt.tight_layout()
    plt.savefig(out_png, dpi=300, bbox_inches='tight')
    plt.savefig(out_pdf, bbox_inches='tight')
    plt.close()

    print(f"\nSaved national summary comparison:")
    print(f"  → {out_png}")
    print(f"  → {out_pdf}")


def run_comparison(division: Optional[str] = None, all_divisions: bool = False, max_x: Optional[int] = None):
    """Entry point for executing sentinel comparison plots."""
    paths = load_config()
    era_base_dir = paths.working / "era2018"
    era_sent_dir = paths.working / "era2018_sentinel"
    plots_dir = era_sent_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    data_dir = paths.data
    era_map = load_era_official_outcomes()
    id_to_hep, id_to_name = load_hep_mappings(data_dir)

    if division:
        div_code = division.zfill(2)
        try:
            global_df, au_df, div_label, rec_max_x = load_division_comparison_data(
                div_code, era_base_dir, era_sent_dir, id_to_hep, id_to_name, era_map
            )
            m_x = max_x if max_x else rec_max_x
            plot_division_comparison(div_code, global_df, au_df, div_label, plots_dir, max_x=m_x)
        except Exception as exc:
            print(f"Error plotting Division {div_code}: {exc}")

    elif all_divisions:
        base_pqs = sorted((era_base_dir / "division").glob(f"rankings_div_*_{ERA_WINDOW}_baseline.parquet"))
        if not base_pqs:
            print("No baseline division parquets found.")
            return

        all_au_records = []
        print(f"Found {len(base_pqs)} divisions to compare...")
        for pq in base_pqs:
            code = pq.stem.split('_')[2]
            try:
                global_df, au_df, div_label, rec_max_x = load_division_comparison_data(
                    code, era_base_dir, era_sent_dir, id_to_hep, id_to_name, era_map
                )
                m_x = max_x if max_x else rec_max_x
                plot_division_comparison(code, global_df, au_df, div_label, plots_dir, max_x=m_x)
                if not au_df.empty:
                    au_df['for_code'] = code
                    all_au_records.append(au_df)
            except Exception as exc:
                print(f"Skipping Division {code}: {exc}")

        if all_au_records:
            full_au_df = pd.concat(all_au_records, ignore_index=True)
            plot_national_summary(full_au_df, plots_dir)


def main():
    parser = argparse.ArgumentParser(description="ERA 2018 2-digit Division Sentinel Comparison Plotter")
    parser.add_argument("--division", type=str, help="2-digit FoR division to plot (e.g., 01, 02)")
    parser.add_argument("--all", action="store_true", help="Plot all 22 2-digit divisions and national summary")
    parser.add_argument("--max-x", type=int, default=None, help="Override maximum x-axis rank")
    args = parser.parse_args()

    if args.division or args.all:
        run_comparison(division=args.division, all_divisions=args.all, max_x=args.max_x)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
