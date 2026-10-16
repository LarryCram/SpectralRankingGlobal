"""
analysis/plot_era2018_generic.py — Generic Seaborn facet plotter for any ANZSRC FoR 2-digit Division and its 4-digit Groups.

For any given 2-digit division (e.g., 01, 02, 03, ... 22):
  1. Identifies the 2-digit division ranking and all available 4-digit group rankings.
  2. Constructs a vertical facet figure (1 column, N rows = 1 division + M groups).
  3. Uses the 2-digit global rank as a common x-axis (x = rank_2d) across ALL facets:
     - Drops subtle vertical dashed lines down each facet at the exact x-position of each Australian HEP.
     - Compresses group rankings horizontally to align with their parent division rank.
  4. Plots log10(v) on the y-axis with ticks at (1, 0, -1, -2), rotated tick labels, and label "log(v)".
  5. Emboldens the horizontal parity line at log10(v) = 0 (v = 1.0).
  6. Highlights Australian Higher Education Providers (HEPs) with hues corresponding to official ARC ERA 2018 ratings (ERA 5, 4, 3, 2, 1, NA).
  7. Labels Australian HEPs with quarter-turn counter-clockwise (90°) rotated text and staggered callouts.
  8. Saves publication-quality PNG and PDF figures to WORKING/era2018/plots/.

Usage:
  .venv/bin/python analysis/plot_era2018_generic.py --division 01
  .venv/bin/python analysis/plot_era2018_generic.py --division 02
  .venv/bin/python analysis/plot_era2018_generic.py --all
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
from research_classification import Resolver

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


def find_division_panels(era_dir: Path, div_code: str, label: str = 'baseline') -> list[tuple[str, str, Path, str]]:
    """
    Find the 2-digit division ranking file and all matching 4-digit group files.
    Returns list of (for_code, level_type, parquet_path, label).
    """
    div_code = div_code.zfill(2)
    panels = []

    # 1. Division parquet
    div_pq = era_dir / "division" / f"rankings_div_{div_code}_{ERA_WINDOW}_{label}.parquet"
    if not div_pq.exists():
        return []

    div_diag = div_pq.with_name(f"{div_pq.stem}_diag.json")
    div_label = f"FoR {div_code}"
    if div_diag.exists():
        try:
            diag = json.loads(div_diag.read_text())
            div_label = f"FoR {div_code}: {diag.get('for_label', '').title()}"
        except Exception:
            pass
    panels.append((div_code, 'division', div_pq, div_label))

    # 2. Matching 4-digit group parquets
    grp_dir = era_dir / "group"
    if grp_dir.exists():
        grp_pqs = sorted(grp_dir.glob(f"rankings_grp_{div_code}*_{ERA_WINDOW}_{label}.parquet"))
        for pq in grp_pqs:
            grp_code = pq.stem.split('_')[2]
            diag_file = pq.with_name(f"{pq.stem}_diag.json")
            grp_label = f"FoR {grp_code}"
            if diag_file.exists():
                try:
                    diag = json.loads(diag_file.read_text())
                    grp_label = f"FoR {grp_code}: {diag.get('for_label', '')}"
                except Exception:
                    pass
            panels.append((grp_code, 'group', pq, grp_label))

    return panels


def build_facet_dataset(panels: list[tuple[str, str, Path, str]],
                        id_to_hep: dict[int, str],
                        id_to_name: dict[int, str],
                        era_map: dict[tuple[str, str], str],
                        sent_era_dir: Optional[Path] = None) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Assemble global and Australian HEP dataset aligned on common baseline 2-digit ranks."""
    con = duckdb.connect()

    # The first panel is the 2-digit division (baseline)
    div_code, _, div_pq, _ = panels[0]
    df_div = con.execute(f"""
        SELECT unit_idx, rank_v AS rank_2d, v AS v_2d
        FROM '{div_pq}'
        WHERE unit_type = 'U'
    """).df()
    inst_to_rank2d = dict(zip(df_div['unit_idx'], df_div['rank_2d']))

    all_global_records = []
    all_au_records = []

    for code, level_type, pq, label in panels:
        df = con.execute(f"""
            SELECT unit_idx, rank_v, v, pi, a_p
            FROM '{pq}'
            WHERE unit_type = 'U'
            ORDER BY rank_v ASC
        """).df()

        # If sentinel directory provided, load with-sentinel scores for this panel
        sent_map = {}
        if sent_era_dir is not None:
            if level_type == 'division':
                sent_pq = sent_era_dir / "division" / f"rankings_div_{code}_{ERA_WINDOW}_sentinel.parquet"
            else:
                sent_pq = sent_era_dir / "group" / f"rankings_grp_{code}_{ERA_WINDOW}_sentinel.parquet"

            if sent_pq.exists():
                df_sent = con.execute(f"""
                    SELECT unit_idx, v AS v_sent, rank_v AS rank_sent
                    FROM '{sent_pq}'
                    WHERE unit_type = 'U'
                """).df()
                sent_map = dict(zip(df_sent['unit_idx'], zip(df_sent['v_sent'], df_sent['rank_sent'])))

        for _, r in df.iterrows():
            uid = int(r['unit_idx'])
            rank_2d = inst_to_rank2d.get(uid, np.nan)
            if pd.isna(rank_2d):
                continue

            v_val = float(r['v'])
            log_v = float(np.log10(v_val)) if v_val > 0 else -3.0

            is_au = uid in id_to_hep
            hep_code = id_to_hep.get(uid, "")
            hep_name = id_to_name.get(uid, "")
            official_era = era_map.get((hep_code, code), "NA") if is_au else ""

            if is_au:
                if official_era == '5':
                    era_cat = 'ERA 5 (Well above world standard)'
                elif official_era == '4':
                    era_cat = 'ERA 4 (Above world standard)'
                elif official_era == '3':
                    era_cat = 'ERA 3 (At world standard)'
                elif official_era == '2':
                    era_cat = 'ERA 2 (Below world standard)'
                elif official_era == '1':
                    era_cat = 'ERA 1 (Well below world standard)'
                else:
                    era_cat = 'ERA NA (Unassessed in official ERA)'
            else:
                era_cat = 'Global Institutions'

            if uid in sent_map:
                v_s, r_s = sent_map[uid]
                v_sent = float(v_s)
                log_v_sent = float(np.log10(v_sent)) if v_sent > 0 else -3.0
                rank_sent = int(r_s)
            else:
                v_sent = np.nan
                log_v_sent = np.nan
                rank_sent = np.nan

            rec = {
                'for_code': code,
                'for_label': label,
                'unit_idx': uid,
                'rank_grp': int(r['rank_v']),
                'rank_2d': int(rank_2d),
                'v': v_val,
                'log_v': log_v,
                'v_sent': v_sent,
                'log_v_sent': log_v_sent,
                'rank_sent': rank_sent,
                'pi': float(r['pi']),
                'a_p': float(r['a_p']) if pd.notna(r['a_p']) else 0.0,
                'is_au': is_au,
                'hep_code': hep_code,
                'hep_name': hep_name,
                'official_era': official_era,
                'era_category': era_cat,
            }
            all_global_records.append(rec)
            if is_au:
                all_au_records.append(rec)

    global_df = pd.DataFrame(all_global_records)
    cols = global_df.columns if not global_df.empty else [
        'for_code', 'for_label', 'unit_idx', 'rank_grp', 'rank_2d', 'v', 'log_v',
        'v_sent', 'log_v_sent', 'rank_sent', 'pi', 'a_p', 'is_au', 'hep_code', 'hep_name', 'official_era', 'era_category'
    ]
    au_df = pd.DataFrame(all_au_records, columns=cols) if all_au_records else pd.DataFrame(columns=cols)

    # Determine recommended max_x based on Australian HEPs
    if not au_df.empty:
        max_au_rank = int(au_df['rank_2d'].max())
        calc_max = int(np.ceil((max_au_rank + 60) / 100.0) * 100)
        recommended_max_x = max(calc_max, 200)
    else:
        recommended_max_x = min(len(df_div), 500)

    return global_df, au_df, recommended_max_x


import matplotlib.lines as mlines
from matplotlib.legend import Legend


def plot_division_facet(div_code: str,
                        panels: list[tuple[str, str, Path, str]],
                        global_df: pd.DataFrame,
                        au_df: pd.DataFrame,
                        out_dir: Path,
                        max_x: Optional[int] = None,
                        run_label: str = 'baseline'):
    """Render and save the vertical facet plot for a 2-digit division and its groups."""
    out_dir.mkdir(parents=True, exist_ok=True)
    div_code = div_code.zfill(2)

    raw_name = panels[0][3]
    if ':' in raw_name:
        div_name = raw_name.split(':', 1)[-1].strip().title()
    else:
        div_name = raw_name.replace(f"FoR {div_code}", "").strip().title()

    sns.set_theme(style="whitegrid", font="sans-serif")

    n_panels = len(panels)
    fig_height = max(4.5, 2.7 * n_panels)

    fig, axes = plt.subplots(
        nrows=n_panels, ncols=1, figsize=(14, fig_height),
        sharey=True, sharex=True,
        gridspec_kw={'hspace': 0.06}
    )
    if n_panels == 1:
        axes = [axes]

    top_margin_frac = 1.0 - (0.95 / fig_height)
    bottom_margin_frac = 0.50 / fig_height
    fig.subplots_adjust(top=top_margin_frac, bottom=bottom_margin_frac)

    # Australian HEP ranks to draw vertical drop lines for
    visible_au = au_df[(au_df['rank_2d'] <= max_x) & (au_df['rank_2d'] >= 1)]
    au_ranks_to_mark = sorted(visible_au['rank_2d'].unique())

    for ax, (code, level_type, _, label) in zip(axes, panels):
        g_sub = global_df[global_df['for_code'] == code].sort_values('rank_2d')
        a_sub = au_df[au_df['for_code'] == code].sort_values('rank_2d')

        g_vis = g_sub[g_sub['rank_2d'] <= max_x]
        a_vis = a_sub[a_sub['rank_2d'] <= max_x]

        # 1. Subtle vertical drop lines down each facet for Australian institution positions
        for rx in au_ranks_to_mark:
            ax.axvline(rx, color='#cbd5e1', linestyle=':', linewidth=0.85, alpha=0.7, zorder=1)

        # 2. Global distribution baseline
        dist_label = f"Global institutions ($N = {len(g_sub):,}$)"
        if level_type == 'division':
            ax.plot(
                g_vis['rank_2d'], g_vis['log_v'],
                color='#94a3b8', linewidth=1.8, alpha=0.85, zorder=2,
                label=dist_label
            )
        else:
            ax.scatter(
                g_vis['rank_2d'], g_vis['log_v'],
                color='#94a3b8', s=16, alpha=0.45, zorder=2,
                label=dist_label
            )

        # 3. Reference parity line at log(v) = 0
        ax.axhline(
            0.0, color='#0f172a', linestyle='--', linewidth=2.0, alpha=0.95, zorder=3,
            label=r'World Parity ($\log_{10}(v) = 0$)'
        )

        # 4a. Australian HEPs with Sentinel on (ε=1): asterisk '*' along the institution's vertical drop line (explained in caption)
        sent_pts = a_vis[a_vis['log_v_sent'].notna()]
        if not sent_pts.empty:
            for cat, color in ERA_PALETTE.items():
                cat_sent = sent_pts[sent_pts['era_category'] == cat]
                if not cat_sent.empty:
                    ax.scatter(
                        cat_sent['rank_2d'], cat_sent['log_v_sent'],
                        marker='*', s=140,
                        color=color, edgecolor='#0f172a', linewidth=0.6,
                        alpha=0.92, zorder=4
                    )

        # 4b. Australian HEPs by ERA rating hue (Baseline, ε=0, main plot)
        for cat, color in ERA_PALETTE.items():
            cat_pts = a_vis[a_vis['era_category'] == cat]
            if not cat_pts.empty:
                marker = ERA_MARKERS.get(cat, 'o')
                ax.scatter(
                    cat_pts['rank_2d'], cat_pts['log_v'],
                    color=color, marker=marker, s=95,
                    edgecolor='white', linewidth=1.4, zorder=5
                )

        # 5. Label Australian HEPs with quarter-turn CCW (90 deg) at baseline position
        y_staggers = [+0.22, -0.22, +0.40, -0.40]
        for i, (_, r) in enumerate(a_vis.iterrows()):
            hep = r['hep_code']
            x = r['rank_2d']
            y = r['log_v']
            txt = f"{hep}"

            y_shift = y_staggers[i % len(y_staggers)]
            va = 'bottom' if y_shift > 0 else 'top'

            ax.annotate(
                txt,
                xy=(x, y),
                xytext=(x, y + y_shift),
                rotation=90,
                arrowprops=dict(arrowstyle="-", color='#475569', lw=0.9, alpha=0.85),
                fontsize=8.5, fontweight='bold', color='#0f172a',
                ha='center', va=va, zorder=6,
                bbox=dict(boxstyle="round,pad=0.18", facecolor='white', edgecolor='#cbd5e1', alpha=0.92, lw=0.6)
            )

        ax.set_ylim(-2.2, 1.2)
        ax.set_yticks([1.0, 0.0, -1.0, -2.0])
        ax.set_yticklabels(['1', '0', '-1', '-2'])
        ax.yaxis.set_minor_locator(ticker.FixedLocator([-1.5, -0.5, 0.5, 1.5]))
        ax.tick_params(axis='y', which='major', left=True, labelrotation=90, labelsize=9.5)
        ax.tick_params(axis='y', which='minor', left=True, length=3.5, color='#475569')

        ax.set_xlim(0, max_x * 1.02)
        step = 50 if max_x <= 350 else 100
        ax.xaxis.set_major_locator(ticker.MultipleLocator(step))
        ax.xaxis.set_minor_locator(ticker.MultipleLocator(step // 4))

        # Facet title placed inside the plot raised to clear log(v) = -2 line
        trans_inner = mtransforms.blended_transform_factory(ax.transAxes, ax.transData)
        ax.text(
            0.012, -1.90,
            f"{label} (AU Retained in Window: {len(a_vis)}/{len(a_sub)})",
            transform=trans_inner,
            fontsize=10.5, fontweight='normal', color='#0f172a',
            va='bottom', ha='left', zorder=7,
            bbox=dict(boxstyle="square,pad=0.2", facecolor='white', edgecolor='none', alpha=0.85)
        )
        ax.set_ylabel("log(v)", fontsize=11, fontweight='bold', color='#0f172a')

        # Figure legend: just the 2 top rows (Global institutions and World Parity)
        dist_handle = (
            mlines.Line2D([], [], color='#94a3b8', linewidth=1.8, alpha=0.85, label=dist_label)
            if level_type == 'division'
            else mlines.Line2D([], [], color='none', marker='o', markerfacecolor='#94a3b8',
                               markeredgecolor='none', alpha=0.45, markersize=5, label=dist_label)
        )
        parity_handle = mlines.Line2D(
            [], [], color='#0f172a', linestyle='--', linewidth=2.0, alpha=0.95,
            label=r'World Parity ($\log_{10}(v) = 0$)'
        )
        leg = ax.legend(
            [dist_handle, parity_handle],
            [dist_handle.get_label(), parity_handle.get_label()],
            loc='upper right', frameon=True, framealpha=0.92,
            facecolor='white', edgecolor='#cbd5e1', fontsize=8
        )
        ax.add_artist(leg)

    # Single line string just above and outside the top of the top facet:
    # 'x ERA 5, y ERA 4, z ERA 3, w ERA 2, v ERA 1 and u ERA NA' with relevant symbols
    top_handles = [
        mlines.Line2D([], [], color='none', marker=ERA_MARKERS['ERA 5 (Well above world standard)'],
                      markerfacecolor=ERA_PALETTE['ERA 5 (Well above world standard)'],
                      markeredgecolor='white', markeredgewidth=1.2, markersize=8.5),
        mlines.Line2D([], [], color='none', marker=ERA_MARKERS['ERA 4 (Above world standard)'],
                      markerfacecolor=ERA_PALETTE['ERA 4 (Above world standard)'],
                      markeredgecolor='white', markeredgewidth=1.2, markersize=7.5),
        mlines.Line2D([], [], color='none', marker=ERA_MARKERS['ERA 3 (At world standard)'],
                      markerfacecolor=ERA_PALETTE['ERA 3 (At world standard)'],
                      markeredgecolor='white', markeredgewidth=1.2, markersize=8.0),
        mlines.Line2D([], [], color='none', marker=ERA_MARKERS['ERA 2 (Below world standard)'],
                      markerfacecolor=ERA_PALETTE['ERA 2 (Below world standard)'],
                      markeredgecolor='white', markeredgewidth=1.2, markersize=8.0),
        mlines.Line2D([], [], color='none', marker=ERA_MARKERS['ERA 1 (Well below world standard)'],
                      markerfacecolor=ERA_PALETTE['ERA 1 (Well below world standard)'],
                      markeredgecolor='white', markeredgewidth=1.2, markersize=8.0),
        mlines.Line2D([], [], color='none', marker=ERA_MARKERS['ERA NA (Unassessed in official ERA)'],
                      markerfacecolor=ERA_PALETTE['ERA NA (Unassessed in official ERA)'],
                      markeredgecolor='white', markeredgewidth=1.2, markersize=7.5),
    ]
    top_labels = ['ERA 5', 'ERA 4', 'ERA 3', 'ERA 2', 'ERA 1', 'ERA NA']

    top_legend = Legend(
        axes[0],
        top_handles,
        top_labels,
        loc='lower center',
        bbox_to_anchor=(0.5, 1.04),
        bbox_transform=axes[0].transAxes,
        ncol=6,
        frameon=False,
        fontsize=9.5,
        handletextpad=0.25,
        columnspacing=1.0
    )
    axes[0].add_artist(top_legend)

    # X-axis label: "Ordinal Global Rank (Division XX Name)" and nothing else
    axes[-1].set_xlabel(f"Ordinal Global Rank (Division {div_code} {div_name})",
                        fontsize=11, fontweight='bold', color='#0f172a')

    # Title: "FoR XX Name" and nothing more
    title_y = 1.0 - (0.20 / fig_height)
    plt.suptitle(f"FoR {div_code} {div_name}",
                 fontsize=14.0, fontweight='bold', y=title_y, color='#0f172a')

    out_png = out_dir / f"era2018_div{div_code}_facet.png"
    out_pdf = out_dir / f"era2018_div{div_code}_facet.pdf"
    plt.savefig(out_png, dpi=300, bbox_inches='tight')
    plt.savefig(out_pdf, bbox_inches='tight')
    plt.close()

    print(f"Saved facet plot for Division {div_code}:")
    print(f"  → {out_png}")
    print(f"  → {out_pdf}")


def plot_division(div_code: str,
                  era_dir: Path,
                  sent_era_dir: Optional[Path],
                  data_dir: Path,
                  plots_dir: Path,
                  era_map: dict[tuple[str, str], str],
                  id_to_hep: dict[int, str],
                  id_to_name: dict[int, str],
                  max_x_override: Optional[int] = None,
                  run_label: str = 'baseline') -> bool:
    """Plot facet for a single division if data is available."""
    div_code = div_code.zfill(2)
    panels = find_division_panels(era_dir, div_code, label=run_label)
    if not panels:
        print(f"No ranking data found for Division {div_code} in {era_dir / 'division'}")
        return False

    print(f"\nProcessing Division {div_code}: found {len(panels)} panels (1 division, {len(panels) - 1} groups) [model='{run_label}']")
    global_df, au_df, rec_max_x = build_facet_dataset(panels, id_to_hep, id_to_name, era_map, sent_era_dir=sent_era_dir)
    max_x = max_x_override if max_x_override else rec_max_x

    n_au = len(au_df['unit_idx'].unique()) if (not au_df.empty and 'unit_idx' in au_df.columns) else 0
    print(f"  AU HEPs found: {n_au} unique institutions. Max rank x = {max_x}")
    plot_division_facet(div_code, panels, global_df, au_df, plots_dir, max_x=max_x, run_label=run_label)
    return True


def main():
    parser = argparse.ArgumentParser(description="Generic ERA 2018 Facet Plotter for ANZSRC Divisions and Groups")
    parser.add_argument("--division", type=str, help="2-digit FoR division to plot (e.g., 01, 02)")
    parser.add_argument("--all", action="store_true", help="Plot all completed 2-digit divisions")
    parser.add_argument("--max-x", type=int, default=None, help="Override maximum x-axis rank")
    parser.add_argument("--model", choices=['baseline', 'sentinel'], default='baseline',
                        help="Choose ranking dataset model (default: baseline ε=0)")
    parser.add_argument("--sentinel", action="store_true",
                        help="Plot 2-digit comparative facet contrasting baseline (ε=0) and sentinel (ε=1) models")
    args = parser.parse_args()

    if args.sentinel:
        from analysis.plot_era2018_sentinel_comparison import run_comparison
        run_comparison(division=args.division, all_divisions=args.all, max_x=args.max_x)
        return

    paths = load_config()
    run_label = args.model
    era_dir = paths.working / f"era2018_{run_label}" if run_label != 'baseline' else paths.working / "era2018"
    sent_era_dir = paths.working / "era2018_sentinel" if run_label == 'baseline' else None
    data_dir = paths.data
    plots_dir = paths.working / "era2018" / "plots"

    era_map = load_era_official_outcomes()
    id_to_hep, id_to_name = load_hep_mappings(data_dir)

    if args.division:
        plot_division(args.division, era_dir, sent_era_dir, data_dir, plots_dir, era_map, id_to_hep, id_to_name,
                      max_x_override=args.max_x, run_label=run_label)
    elif args.all:
        div_pqs = sorted((era_dir / "division").glob(f"rankings_div_*_{ERA_WINDOW}_{run_label}.parquet"))
        if not div_pqs:
            print("No completed division rankings found in", era_dir / "division")
            return
        print(f"Found {len(div_pqs)} completed divisions to plot for model '{run_label}'.")
        for pq in div_pqs:
            code = pq.stem.split('_')[2]
            plot_division(code, era_dir, sent_era_dir, data_dir, plots_dir, era_map, id_to_hep, id_to_name,
                          max_x_override=args.max_x, run_label=run_label)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()


