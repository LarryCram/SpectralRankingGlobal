"""
analysis/plot_era2018_physics.py — Seaborn facet of full ERA 2018 Physical Sciences results.

Creates a vertical 7-panel facet (one column) for:
  - FoR 02: Physical Sciences
  - FoR 0201: Astronomical and Space Sciences
  - FoR 0202: Atomic, Molecular, Nuclear, Particle and Plasma Physics
  - FoR 0203: Classical Physics
  - FoR 0204: Condensed Matter Physics
  - FoR 0205: Optical Physics
  - FoR 0299: Other Physical Sciences

Each panel plots:
  - x-axis: The institution's ordinal rank in the 2-digit division (FoR 02)
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


def build_physics_dataset(paths) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Assemble ranking data mapped to common 2-digit (Division 02) ranks."""
    era_map = load_era_official_outcomes()

    # Load HEP concordances
    hep_file = paths.data / "HEP_concordances.xlsx"
    hep_df = pd.read_excel(hep_file, sheet_name='Keys')
    hep_df['inst_id'] = hep_df['institution_idx'].astype(str).str.lstrip('I').astype('int64')
    id_to_hep = dict(zip(hep_df['inst_id'], hep_df['HEP']))
    id_to_name = dict(zip(hep_df['inst_id'], hep_df['Organisation']))

    era_dir = paths.working / "era2018"
    con = duckdb.connect()

    # 1. Base 2-digit Division 02 ranking map: unit_idx -> rank_02
    div02_pq = era_dir / "division" / "rankings_div_02_2011_2016_baseline.parquet"
    df_02 = con.execute(f"""
        SELECT unit_idx, rank_v AS rank_02, v AS v_02
        FROM '{div02_pq}'
        WHERE unit_type = 'U'
    """).df()
    inst_to_rank02 = dict(zip(df_02['unit_idx'], df_02['rank_02']))

    panels_spec = [
        ('02', 'division', 'rankings_div_02_2011_2016_baseline.parquet', 'FoR 02: Physical Sciences'),
        ('0201', 'group', 'rankings_grp_0201_2011_2016_baseline.parquet', 'FoR 0201: Astronomical & Space Sciences'),
        ('0202', 'group', 'rankings_grp_0202_2011_2016_baseline.parquet', 'FoR 0202: Atomic, Mol., Nuclear & Plasma'),
        ('0203', 'group', 'rankings_grp_0203_2011_2016_baseline.parquet', 'FoR 0203: Classical Physics'),
        ('0204', 'group', 'rankings_grp_0204_2011_2016_baseline.parquet', 'FoR 0204: Condensed Matter Physics'),
        ('0205', 'group', 'rankings_grp_0205_2011_2016_baseline.parquet', 'FoR 0205: Optical Physics'),
        ('0299', 'group', 'rankings_grp_0299_2011_2016_baseline.parquet', 'FoR 0299: Other Physical Sciences'),
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
            rank_02 = inst_to_rank02.get(uid, np.nan)
            if pd.isna(rank_02):
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
                'rank_02': int(rank_02),
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


def plot_physics_facet(global_df: pd.DataFrame, au_df: pd.DataFrame, out_dir: Path, max_x: int = 1100):
    """Generate 7-panel vertical facet plot with common 2-digit rank x-axis and log(v) y-axis."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_png = out_dir / "era2018_physics_facet.png"
    out_pdf = out_dir / "era2018_physics_facet.pdf"

    sns.set_theme(style="whitegrid", font="sans-serif")

    codes = ['02', '0201', '0202', '0203', '0204', '0205', '0299']
    labels = {
        '02': 'FoR 02: Physical Sciences',
        '0201': 'FoR 0201: Astronomical & Space Sciences',
        '0202': 'FoR 0202: Atomic, Mol., Nuclear & Plasma',
        '0203': 'FoR 0203: Classical Physics',
        '0204': 'FoR 0204: Condensed Matter Physics',
        '0205': 'FoR 0205: Optical Physics',
        '0299': 'FoR 0299: Other Physical Sciences',
    }

    fig, axes = plt.subplots(
        nrows=7, ncols=1, figsize=(14, 25),
        sharey=True, sharex=True,
        gridspec_kw={'hspace': 0.32}
    )

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

    visible_au = au_df[(au_df['rank_02'] <= max_x) & (au_df['rank_02'] >= 1)]
    au_ranks_to_mark = sorted(visible_au['rank_02'].unique())

    for ax, code in zip(axes, codes):
        g_sub = global_df[global_df['for_code'] == code].sort_values('rank_02')
        a_sub = au_df[au_df['for_code'] == code].sort_values('rank_02')

        g_vis = g_sub[g_sub['rank_02'] <= max_x]
        a_vis = a_sub[a_sub['rank_02'] <= max_x]

        # 1. Subtle vertical drop lines down each facet for Australian institution positions
        for rx in au_ranks_to_mark:
            ax.axvline(rx, color='#cbd5e1', linestyle=':', linewidth=0.85, alpha=0.7, zorder=1)

        # 2. Global distribution baseline
        if code == '02':
            ax.plot(
                g_vis['rank_02'], g_vis['log_v'],
                color='#94a3b8', linewidth=1.8, alpha=0.85, zorder=2,
                label='Global institutions ($N = ' + f"{len(g_sub):,}" + '$)'
            )
        else:
            ax.scatter(
                g_vis['rank_02'], g_vis['log_v'],
                color='#94a3b8', s=16, alpha=0.45, zorder=2,
                label='Global institutions ($N = ' + f"{len(g_sub):,}" + '$)'
            )

        # 3. Reference parity line at log(v) = 0
        ax.axhline(
            0.0, color='#0f172a', linestyle='--', linewidth=2.0, alpha=0.95, zorder=3,
            label=r'World Parity ($\log_{10}(v) = 0$)'
        )

        # 4. Australian HEPs by ERA rating hue
        for cat, color in era_palette.items():
            cat_pts = a_vis[a_vis['era_category'] == cat]
            if not cat_pts.empty:
                marker = era_markers.get(cat, 'o')
                ax.scatter(
                    cat_pts['rank_02'], cat_pts['log_v'],
                    color=color, marker=marker, s=95,
                    edgecolor='white', linewidth=1.4, zorder=5,
                    label=f'{cat} ($n={len(cat_pts)}$)'
                )

        # 5. Label Australian HEPs with quarter-turn CCW (90 deg)
        y_staggers = [+0.26, -0.26, +0.48, -0.48]
        for i, (_, r) in enumerate(a_vis.iterrows()):
            hep = r['hep_code']
            x = r['rank_02']
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

        ax.set_ylim(-2.2, 1.1)
        ax.set_yticks([1.0, 0.0, -1.0, -2.0])
        ax.set_yticklabels(['1', '0', '-1', '-2'])
        ax.tick_params(axis='y', labelrotation=90, labelsize=9.5)

        ax.set_xlim(0, max_x * 1.02)
        ax.xaxis.set_major_locator(ticker.MultipleLocator(100))
        ax.xaxis.set_minor_locator(ticker.MultipleLocator(25))

        ax.set_title(f"{labels[code]} (AU Retained in Window: {len(a_vis)}/{len(a_sub)})",
                     fontsize=11.5, fontweight='bold', pad=8, loc='left', color='#0f172a')
        ax.set_ylabel("log(v)", fontsize=11, fontweight='bold', color='#0f172a')

        handles, leg_labels = ax.get_legend_handles_labels()
        by_label = dict(zip(leg_labels, handles))
        ax.legend(
            by_label.values(), by_label.keys(),
            loc='upper right', frameon=True, framealpha=0.92,
            facecolor='white', edgecolor='#cbd5e1', fontsize=8
        )

    axes[-1].set_xlabel("Ordinal Global Rank in FoR 02 Division ($x = \\text{rank}_{02}$, common scale $\\rightarrow$)",
                        fontsize=11, fontweight='bold', color='#0f172a')

    plt.suptitle("ERA 2018 Emulation: Physical Sciences Disciplines (Census Window 2011–2016)\nCommon Vertical Alignment ($x = \\text{rank}_{02}$) Across Facets with Official ARC Outcomes",
                 fontsize=13.5, fontweight='bold', y=0.995, color='#0f172a')

    plt.savefig(out_png, dpi=300, bbox_inches='tight')
    plt.savefig(out_pdf, bbox_inches='tight')
    plt.close()

    print(f"Saved physics facet plots successfully:")
    print(f"  → {out_png}")
    print(f"  → {out_pdf}")


def main():
    paths = load_config()
    plots_dir = paths.working / "era2018" / "plots"
    global_df, au_df = build_physics_dataset(paths)
    plot_physics_facet(global_df, au_df, plots_dir, max_x=1100)


if __name__ == '__main__':
    main()
