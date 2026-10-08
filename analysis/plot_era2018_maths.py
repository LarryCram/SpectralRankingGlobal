"""
analysis/plot_era2018_maths.py — Seaborn facet of ERA 2018 Mathematical Sciences results.

Creates a vertical 6-panel facet (one column) for:
  - FoR 01: Mathematical Sciences
  - FoR 0101: Pure Mathematics
  - FoR 0102: Applied Mathematics
  - FoR 0103: Numerical and Computational Mathematics
  - FoR 0104: Statistics
  - FoR 0105: Mathematical Physics

Each panel plots:
  - x-axis: The institution's ordinal rank in the 2-digit division (FoR 01)
  - y-axis: log10(v), with ticks at (1, 0, -1, -2), rotated tick labels, and caption "log(v)"
  - Emboldened horizontal parity line at log10(v) = 0 (v = 1.0)
  - Faint vertical drop lines at Australian institution positions
  - Australian HEPs highlighted with distinct hues for ERA 2018 ratings (ERA 5, 4, 3, etc.)
  - Quarter-turn counter-clockwise (90 deg) labels for Australian HEPs
"""

import glob
import json
from pathlib import Path
import duckdb
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
import seaborn as sns

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config


def load_era_official_outcomes() -> dict[tuple[str, str], str]:
    """Load official ERA 2018 outcomes for all HEPs."""
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


def build_maths_dataset(paths) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Assemble ranking data mapped to common 2-digit (Division 01) ranks."""
    era_map = load_era_official_outcomes()

    # Load HEP concordances
    hep_file = paths.data / "HEP_concordances.xlsx"
    hep_df = pd.read_excel(hep_file, sheet_name='Keys')
    hep_df['inst_id'] = hep_df['institution_idx'].astype(str).str.lstrip('I').astype('int64')
    id_to_hep = dict(zip(hep_df['inst_id'], hep_df['HEP']))
    id_to_name = dict(zip(hep_df['inst_id'], hep_df['Organisation']))

    era_dir = paths.working / "era2018"
    con = duckdb.connect()

    # 1. Base 2-digit Division 01 ranking map: unit_idx -> rank_01
    div01_pq = era_dir / "division" / "rankings_div_01_2011_2016_baseline.parquet"
    df_01 = con.execute(f"""
        SELECT unit_idx, rank_v AS rank_01, v AS v_01
        FROM '{div01_pq}'
        WHERE unit_type = 'U'
    """).df()
    inst_to_rank01 = dict(zip(df_01['unit_idx'], df_01['rank_01']))
    inst_to_v01 = dict(zip(df_01['unit_idx'], df_01['v_01']))

    panels_spec = [
        ('01', 'division', 'rankings_div_01_2011_2016_baseline.parquet', 'FoR 01: Mathematical Sciences'),
        ('0101', 'group', 'rankings_grp_0101_2011_2016_baseline.parquet', 'FoR 0101: Pure Mathematics'),
        ('0102', 'group', 'rankings_grp_0102_2011_2016_baseline.parquet', 'FoR 0102: Applied Mathematics'),
        ('0103', 'group', 'rankings_grp_0103_2011_2016_baseline.parquet', 'FoR 0103: Numerical & Computational Mathematics'),
        ('0104', 'group', 'rankings_grp_0104_2011_2016_baseline.parquet', 'FoR 0104: Statistics'),
        ('0105', 'group', 'rankings_grp_0105_2011_2016_baseline.parquet', 'FoR 0105: Mathematical Physics'),
    ]

    all_global_records = []
    all_au_records = []

    for code, subdir, fname, label in panels_spec:
        pq = era_dir / subdir / fname
        if not pq.exists():
            print(f"Warning: {pq} does not exist, skipping.")
            continue

        df = con.execute(f"""
            SELECT unit_idx, rank_v, v, pi, a_p
            FROM '{pq}'
            WHERE unit_type = 'U'
            ORDER BY rank_v ASC
        """).df()

        for _, r in df.iterrows():
            uid = int(r['unit_idx'])
            rank_01 = inst_to_rank01.get(uid, np.nan)
            if pd.isna(rank_01):
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
                elif official_era in ('2', '1'):
                    era_cat = f'ERA {official_era} (Below world standard)'
                else:
                    era_cat = 'ERA NA (Unassessed in official ERA)'
            else:
                era_cat = 'Global Institutions'

            rec = {
                'for_code': code,
                'for_label': label,
                'unit_idx': uid,
                'rank_grp': int(r['rank_v']),
                'rank_01': int(rank_01),
                'v': v_val,
                'log_v': log_v,
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
    au_df = pd.DataFrame(all_au_records)
    return global_df, au_df


def plot_maths_facet(global_df: pd.DataFrame, au_df: pd.DataFrame, out_dir: Path, max_x: int = 200, filename_suffix: str = ""):
    """Generate 6-panel vertical facet plot with common 2-digit rank x-axis and log(v) y-axis."""
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{filename_suffix}" if filename_suffix else ""
    out_png = out_dir / f"era2018_maths_facet{suffix}.png"
    out_pdf = out_dir / f"era2018_maths_facet{suffix}.pdf"

    sns.set_theme(style="whitegrid", font="sans-serif")

    codes = ['01', '0101', '0102', '0103', '0104', '0105']
    labels = {
        '01': 'FoR 01: Mathematical Sciences',
        '0101': 'FoR 0101: Pure Mathematics',
        '0102': 'FoR 0102: Applied Mathematics',
        '0103': 'FoR 0103: Numerical & Computational Mathematics',
        '0104': 'FoR 0104: Statistics',
        '0105': 'FoR 0105: Mathematical Physics',
    }

    fig, axes = plt.subplots(
        nrows=6, ncols=1, figsize=(14, 22),
        sharey=True, sharex=True,
        gridspec_kw={'hspace': 0.32}
    )

    # Color palette for ERA ratings
    era_palette = {
        'ERA 5 (Well above world standard)': '#1b7837',    # Rich dark green
        'ERA 4 (Above world standard)':      '#2166ac',    # Deep blue
        'ERA 3 (At world standard)':         '#d95f02',    # Warm amber / orange
        'ERA 2 (Below world standard)':      '#7570b3',    # Purple
        'ERA NA (Unassessed in official ERA)': '#64748b',  # Slate gray
    }

    era_markers = {
        'ERA 5 (Well above world standard)': 'o',
        'ERA 4 (Above world standard)':      's',
        'ERA 3 (At world standard)':         '^',
        'ERA 2 (Below world standard)':      'v',
        'ERA NA (Unassessed in official ERA)': 'D',
    }

    # Distinct Australian institutions in the visible window
    visible_au = au_df[(au_df['rank_01'] <= max_x) & (au_df['rank_01'] >= 1)]
    au_ranks_to_mark = sorted(visible_au['rank_01'].unique())

    for ax, code in zip(axes, codes):
        g_sub = global_df[global_df['for_code'] == code].sort_values('rank_01')
        a_sub = au_df[au_df['for_code'] == code].sort_values('rank_01')

        # Filter to visible window
        g_vis = g_sub[g_sub['rank_01'] <= max_x]
        a_vis = a_sub[a_sub['rank_01'] <= max_x]

        # 1. Subtle vertical drop lines down each facet for Australian institution positions
        for rx in au_ranks_to_mark:
            ax.axvline(rx, color='#cbd5e1', linestyle=':', linewidth=0.85, alpha=0.7, zorder=1)

        # 2. Global institutions background distribution (x = rank_01, y = log_v)
        if code == '01':
            # In Division 01, rank_01 is monotonic, so draw line + points
            ax.plot(
                g_vis['rank_01'], g_vis['log_v'],
                color='#94a3b8', linewidth=1.8, alpha=0.85, zorder=2,
                label='Global institutions ($N = ' + f"{len(g_sub):,}" + '$)'
            )
        else:
            # In 4-digit groups, plot scatter of global institutions
            ax.scatter(
                g_vis['rank_01'], g_vis['log_v'],
                color='#94a3b8', s=16, alpha=0.45, zorder=2,
                label='Global institutions ($N = ' + f"{len(g_sub):,}" + '$)'
            )

        # 3. Emboldened reference parity line at log(v) = 0 (v = 1.0)
        ax.axhline(
            0.0, color='#0f172a', linestyle='--', linewidth=2.0, alpha=0.95, zorder=3,
            label=r'World Parity ($\log_{10}(v) = 0$)'
        )

        # 4. Plot Australian HEPs by ERA rating hue
        for cat, color in era_palette.items():
            cat_pts = a_vis[a_vis['era_category'] == cat]
            if not cat_pts.empty:
                marker = era_markers.get(cat, 'o')
                ax.scatter(
                    cat_pts['rank_01'], cat_pts['log_v'],
                    color=color, marker=marker, s=95,
                    edgecolor='white', linewidth=1.4, zorder=5,
                    label=f'{cat} ($n={len(cat_pts)}$)'
                )

        # 5. Label Australian HEPs with quarter-turn counter-clockwise rotation (90 deg)
        # 4-tier vertical stagger cycle in log-space
        y_staggers = [+0.26, -0.26, +0.48, -0.48]
        for i, (_, r) in enumerate(a_vis.iterrows()):
            hep = r['hep_code']
            x = r['rank_01']
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

        # Axes formatting
        ax.set_ylim(-2.2, 1.1)
        ax.set_yticks([1.0, 0.0, -1.0, -2.0])
        ax.set_yticklabels(['1', '0', '-1', '-2'])
        # Rotate y-axis tick numbers
        ax.tick_params(axis='y', labelrotation=90, labelsize=9.5)

        ax.set_xlim(0, max_x * 1.02)
        ax.xaxis.set_major_locator(ticker.MultipleLocator(50 if max_x <= 250 else 100))
        ax.xaxis.set_minor_locator(ticker.MultipleLocator(10 if max_x <= 250 else 25))

        ax.set_title(f"{labels[code]} (AU Retained in Window: {len(a_vis)}/{len(a_sub)})",
                     fontsize=11.5, fontweight='bold', pad=8, loc='left', color='#0f172a')
        ax.set_ylabel("log(v)", fontsize=11, fontweight='bold', color='#0f172a')

        # Subplot legend
        handles, leg_labels = ax.get_legend_handles_labels()
        by_label = dict(zip(leg_labels, handles))
        ax.legend(
            by_label.values(), by_label.keys(),
            loc='upper right', frameon=True, framealpha=0.92,
            facecolor='white', edgecolor='#cbd5e1', fontsize=8
        )

    axes[-1].set_xlabel("Ordinal Global Rank in FoR 01 Division ($x = \\text{rank}_{01}$, common scale $\\rightarrow$)",
                        fontsize=11, fontweight='bold', color='#0f172a')

    plt.suptitle("ERA 2018 Emulation: Mathematical Sciences Disciplines (Census Window 2011–2016)\nCommon Vertical Alignment ($x = \\text{rank}_{01}$) Across Facets with Official ARC Outcomes",
                 fontsize=13.5, fontweight='bold', y=0.995, color='#0f172a')

    plt.savefig(out_png, dpi=300, bbox_inches='tight')
    plt.savefig(out_pdf, bbox_inches='tight')
    plt.close()

    print(f"Saved facet plot (max_rank={max_x}):")
    print(f"  → {out_png}")
    print(f"  → {out_pdf}")


def main():
    paths = load_config()
    plots_dir = paths.working / "era2018" / "plots"
    global_df, au_df = build_maths_dataset(paths)

    # Full span covering all Australian HEPs (up to RMT at rank 962)
    plot_maths_facet(global_df, au_df, plots_dir, max_x=1000, filename_suffix="")


if __name__ == '__main__':
    main()
