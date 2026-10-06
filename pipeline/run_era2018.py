"""
pipeline/run_era2018.py — ERA 2018 emulation using Spectral Ranking bipartite engine.

Emulates ARC ERA 2018 evaluation:
- Classification standard: ANZSRC FoR 2008 (2-digit Division and 4-digit Group)
  via research_classification.Resolver()
- Census window: 2011–2016 (6-year official ERA 2018 evaluation window)
- Population: All 42 Australian Higher Education Providers (HEPs)
- Baseline settings:
    m = (0, 1, 1, 0)       -- bipartite (source <-> institution)
    alpha = 1.0            -- pure Perron leading eigenvector
    rho = 0                -- fixed-count degree adjustment (R_bar / R_i)
    omega = 0              -- author-fractional institutional weighting
    chi = 0.5              -- equal source/institution mixing
    epsilon = 0, beta = 0, bloc = ''
    tau_u = 50 / 6         -- ~8.333/yr (50.0 weighted works over 6 yrs, matches ERA LVT)
    tau_s = 10.0           -- 60.0 weighted works over 6 yrs
    whitelist = data/md_journal_whitelist.parquet with threshold >= 1.0 weighted work
- Output isolation: WORKING/era2018/
    candidacy/   -- 2011_2016 subfield candidacy master tables
    division/    -- rankings_div_{code}_2011_2016_baseline.parquet + diag.json
    group/       -- rankings_grp_{code}_2011_2016_baseline.parquet + diag.json
    hep_reports/ -- aggregated Australian HEP evaluation & ERA rating concordance

Usage:
    .venv/bin/python pipeline/run_era2018.py --dry-run
    .venv/bin/python pipeline/run_era2018.py --candidacy-only
    .venv/bin/python pipeline/run_era2018.py --division 01
    .venv/bin/python pipeline/run_era2018.py --group 0101
    .venv/bin/python pipeline/run_era2018.py --divisions
    .venv/bin/python pipeline/run_era2018.py --groups
    .venv/bin/python pipeline/run_era2018.py --report
"""

import argparse
from collections import defaultdict
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

import duckdb
import pandas as pd
from research_classification import Resolver

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from util import load_config, load_settings, load_runs, Run
from build_field_candidacy import build_subfield_candidacy
from build_edge_list_field import build_edge_list
from run_rankings import rank_field, show_top


ERA_WINDOW = "2011_2016"
ERA_YEAR_MIN = 2011
ERA_YEAR_MAX = 2011 + 5  # 2016 (6 years inclusive)
ERA_TAU_U = 50.0 / 6.0    # 50 weighted works over 6 years (ERA Low Volume Threshold)
ERA_TAU_S = 10.0         # 60 weighted works over 6 years (baseline source threshold)
ERA_WHITELIST_TAU = 1.0  # Multidisciplinary journal threshold


def get_era_run() -> Run:
    """Create Run specification matching ERA 2018 evaluation baseline."""
    return Run(
        tc0=ERA_YEAR_MIN,
        tc1=ERA_YEAR_MAX,
        tau_s=ERA_TAU_S,
        tau_u=ERA_TAU_U,
        m=(0, 1, 1, 0),
        alpha=1.0,
        rho=0,
        chi=0.5,
        mu_type='',
        label='baseline',
        tt0=ERA_YEAR_MIN,
        tt1=ERA_YEAR_MAX,
        epsilon=0,
        omega=0,
        beta=0,
        bloc='',
    )


def ensure_candidacy(db: duckdb.DuckDBPyConnection, fw_path: str, cands_dir: Path) -> tuple[str, str]:
    """Build or verify 2011-2016 subfield candidacy master tables."""
    cands_dir.mkdir(parents=True, exist_ok=True)
    out_s = str(cands_dir / f"subfield_source_cands_{ERA_WINDOW}.parquet")
    out_u = str(cands_dir / f"subfield_inst_cands_{ERA_WINDOW}.parquet")

    if Path(out_s).exists() and Path(out_u).exists():
        print(f"Candidacy tables already exist:\n  → {out_s}\n  → {out_u}")
        return out_s, out_u

    print(f"Building 2011–2016 subfield candidacy from {fw_path} ...")
    t0 = time.time()
    n_s, n_u = build_subfield_candidacy(db, fw_path, ERA_WINDOW, out_s, out_u)
    print(f"  Built candidacy: {n_s:,} source rows, {n_u:,} inst rows [{time.time() - t0:.1f}s]")
    return out_s, out_u


def get_for2008_mappings(db: duckdb.DuckDBPyConnection, subfield_sc_path: str) -> tuple[
    dict[str, tuple[int, ...]], dict[str, str],
    dict[str, tuple[int, ...]], dict[str, str]
]:
    """
    Resolve active subfields in 2011-2016 to FoR 2008 Groups (4-digit)
    and Divisions (2-digit) using research_classification.Resolver.
    """
    r = Resolver()
    subfield_rows = db.execute(f"""
        SELECT DISTINCT field_idx 
        FROM '{subfield_sc_path}'
        WHERE field_idx IS NOT NULL
    """).fetchall()
    active_subfields = [row[0] for row in subfield_rows]

    div_members: dict[str, list[int]] = defaultdict(list)
    div_labels: dict[str, str] = {}
    grp_members: dict[str, list[int]] = defaultdict(list)
    grp_labels: dict[str, str] = {}

    for sf in active_subfields:
        res = r.resolve(str(sf), "OAX", "FOR2008")
        grp_code = res.code
        grp_members[grp_code].append(sf)
        grp_labels[grp_code] = res.label

        div_code = grp_code[:2]
        div_members[div_code].append(sf)

    for div_code in sorted(div_members.keys()):
        row = r._con.execute("SELECT label FROM for_2008 WHERE code = ?", [div_code]).fetchone()
        div_labels[div_code] = row[0] if row else f"Division {div_code}"

    div_map = {k: tuple(sorted(v)) for k, v in div_members.items()}
    grp_map = {k: tuple(sorted(v)) for k, v in grp_members.items()}
    return div_map, div_labels, grp_map, grp_labels


def build_scratch_unit_candidacy(db: duckdb.DuckDBPyConnection,
                                 subfield_sc: str, subfield_ic: str,
                                 tmp_dir: Path, unit_code: str,
                                 member_subfields: tuple[int, ...]) -> tuple[str, str]:
    """Re-aggregate subfield candidacy into scratch tables for one FoR unit."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_sc = str(tmp_dir / f"_era_{unit_code}_source_cands.parquet")
    out_ic = str(tmp_dir / f"_era_{unit_code}_inst_cands.parquet")
    members_sql = ', '.join(str(s) for s in member_subfields)

    # Use a dummy integer index (int(unit_code)) for field_idx in scratch tables
    dummy_field_idx = int(unit_code)

    db.execute(f"""
        COPY (
            SELECT {dummy_field_idx} AS field_idx, source_idx, SUM(weighted_works) AS weighted_works
            FROM '{subfield_sc}'
            WHERE field_idx IN ({members_sql})
            GROUP BY source_idx
        ) TO '{out_sc}' (FORMAT PARQUET)
    """)
    db.execute(f"""
        COPY (
            SELECT {dummy_field_idx} AS field_idx, institution_idx, SUM(weighted_works) AS weighted_works
            FROM '{subfield_ic}'
            WHERE field_idx IN ({members_sql})
            GROUP BY institution_idx
        ) TO '{out_ic}' (FORMAT PARQUET)
    """)
    return out_sc, out_ic


def run_unit(db: duckdb.DuckDBPyConnection,
             fw_path: str, cr_path: str,
             subfield_sc: str, subfield_ic: str,
             whitelist_pq: str,
             unit_code: str, unit_label: str, unit_level: str,
             members: tuple[int, ...],
             out_dir: Path, tmp_dir: Path):
    """Execute complete edge-building, ranking, and diagnostics for one FoR unit."""
    prefix = "div" if unit_level == "division" else "grp"
    out_pq = out_dir / f"rankings_{prefix}_{unit_code}_{ERA_WINDOW}_baseline.parquet"
    out_diag = out_dir / f"rankings_{prefix}_{unit_code}_{ERA_WINDOW}_baseline_diag.json"

    print(f"\n[{unit_level.upper()}] FoR {unit_code} — {unit_label} ({len(members)} subfields)")
    dummy_idx = int(unit_code)
    run = replace(get_era_run(), field_idx=dummy_idx)

    sc_path, ic_path = build_scratch_unit_candidacy(db, subfield_sc, subfield_ic, tmp_dir, unit_code, members)
    el_path = str(tmp_dir / f"_era_{unit_code}_el.parquet")

    t0 = time.time()
    n_el = build_edge_list(
        db, fw_path, cr_path, sc_path, ic_path, run, el_path,
        member_ids=members, filter_col_override='subfield_idx',
        whitelist_s_path=whitelist_pq, whitelist_tau_abs=ERA_WHITELIST_TAU
    )
    print(f"  Edge list: {n_el:,} edges [{time.time() - t0:.1f}s]")

    if n_el == 0:
        print("  WARNING: empty edge list; skipping ranking")
        for p in (sc_path, ic_path, el_path):
            Path(p).unlink(missing_ok=True)
        return

    df, diag = rank_field(db, run, str(tmp_dir), str(out_pq),
                          el_path=el_path, sc_path=sc_path, ic_path=ic_path)
    diag.update({
        "for_code": unit_code,
        "for_label": unit_label,
        "for_level": unit_level,
        "member_subfield_idx": list(members),
        "tau_u_abs": ERA_TAU_U * run.window_years,
        "tau_s_abs": ERA_TAU_S * run.window_years,
        "whitelist_tau_abs": ERA_WHITELIST_TAU,
    })
    out_diag.write_text(json.dumps(diag, indent=2))
    print(f"  Ranked: {len(df):,} units → {out_pq}")
    show_top(df, 'U', label=f"FOR-{unit_code}")

    for p in (sc_path, ic_path, el_path):
        Path(p).unlink(missing_ok=True)


def generate_hep_report(era_dir: Path, data_dir: Path):
    """Aggregate rankings across all completed FoRs for the 42 Australian HEPs."""
    reports_dir = era_dir / "hep_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    hep_file = data_dir / "HEP_concordances.xlsx"
    if not hep_file.exists():
        print(f"HEP concordances file not found at {hep_file}")
        return

    hep_df = pd.read_excel(hep_file, sheet_name='Keys')
    hep_df['inst_id'] = hep_df['institution_idx'].astype(str).str.lstrip('I').astype('int64')
    hep_map = dict(zip(hep_df['inst_id'], hep_df['HEP']))
    hep_names = dict(zip(hep_df['inst_id'], hep_df['Organisation']))
    hep_ids = set(hep_map.keys())
    print(f"Loaded {len(hep_ids)} Australian HEPs from concordances (Keys sheet).")

    records = []
    for level_dir, prefix, level_name in [
        (era_dir / "division", "div", "division"),
        (era_dir / "group", "grp", "group")
    ]:
        if not level_dir.exists():
            continue
        for pq in sorted(level_dir.glob(f"rankings_{prefix}_*_{ERA_WINDOW}_baseline.parquet")):
            code = pq.stem.split('_')[2]
            diag_file = pq.with_name(f"{pq.stem}_diag.json")
            label = ""
            if diag_file.exists():
                diag = json.loads(diag_file.read_text())
                label = diag.get("for_label", "")

            con = duckdb.connect()
            df = con.execute(f"SELECT * FROM '{pq}' WHERE unit_type = 'U'").df()
            if df.empty:
                continue

            # Compute Australian-only rank
            au_subset = df[df['unit_idx'].isin(hep_ids)].copy()
            if au_subset.empty:
                continue

            au_subset['au_rank_v'] = au_subset['v'].rank(ascending=False, method='min').astype(int)
            for _, r in au_subset.iterrows():
                inst_idx = int(r['unit_idx'])
                records.append({
                    "for_level": level_name,
                    "for_code": code,
                    "for_label": label,
                    "institution_idx": inst_idx,
                    "hep_short_name": hep_map.get(inst_idx, ""),
                    "v": r['v'],
                    "pi": r['pi'],
                    "rank_v_global": int(r['rank_v']),
                    "rank_v_au": int(r['au_rank_v']),
                    "rank_pi_global": int(r['rank_pi']),
                    "a_p": float(r['a_p']) if 'a_p' in r and pd.notna(r['a_p']) else None,
                })

    if not records:
        print("No Australian HEP rankings found to summarize.")
        return

    report_df = pd.DataFrame(records)
    out_pq = reports_dir / "era2018_au_hep_spectral_rankings.parquet"
    out_csv = reports_dir / "era2018_au_hep_spectral_rankings.csv"
    report_df.to_parquet(out_pq)
    report_df.to_csv(out_csv, index=False)
    print(f"\nGenerated Australian HEP summary report ({len(report_df):,} records):")
    print(f"  → {out_pq}")
    print(f"  → {out_csv}")


def main():
    parser = argparse.ArgumentParser(description="Run ERA 2018 Spectral Ranking Emulation")
    parser.add_argument("--dry-run", action="store_true", help="Inspect configuration without execution")
    parser.add_argument("--candidacy-only", action="store_true", help="Generate 2011-2016 candidacy tables only")
    parser.add_argument("--division", type=str, help="Run single 2-digit FoR division (e.g. 01)")
    parser.add_argument("--group", type=str, help="Run single 4-digit FoR group (e.g. 0101)")
    parser.add_argument("--divisions", action="store_true", help="Run all 2-digit FoR divisions")
    parser.add_argument("--groups", action="store_true", help="Run all 4-digit FoR groups")
    parser.add_argument("--report", action="store_true", help="Generate Australian HEP report")
    args = parser.parse_args()

    paths = load_config()
    settings = load_settings()
    era_dir = paths.working / "era2018"
    cands_dir = era_dir / "candidacy"
    div_dir = era_dir / "division"
    grp_dir = era_dir / "group"
    tmp_dir = era_dir / ".tmp"
    data_dir = paths.data
    whitelist_pq = str(data_dir / "md_journal_whitelist.parquet")

    fw_path = str(paths.working / f"flat_works_{settings.year_min}_{settings.year_max}.parquet")
    cr_path = str(paths.working / f"corpus_references_{settings.year_min}_{settings.year_max}.parquet")

    for p in (fw_path, cr_path, whitelist_pq):
        if not Path(p).exists():
            raise FileNotFoundError(f"Required input not found: {p}")

    div_dir.mkdir(parents=True, exist_ok=True)
    grp_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    with duckdb.connect() as db:
        db.execute(f"SET temp_directory = '{tmp_dir}'")
        db.execute(f"SET memory_limit = '{settings.memory_limit}'")
        db.execute(f"SET preserve_insertion_order = {str(settings.preserve_insertion_order).lower()}")

        sub_sc, sub_ic = ensure_candidacy(db, fw_path, cands_dir)
        div_map, div_labels, grp_map, grp_labels = get_for2008_mappings(db, sub_sc)

        print("\n==================================================================")
        print("ERA 2018 Spectral Ranking Pipeline Configuration")
        print("==================================================================")
        print(f"Window         : {ERA_WINDOW} ({ERA_YEAR_MIN}–{ERA_YEAR_MAX}, 6 years)")
        print(f"Tau_U          : {ERA_TAU_U:.4f}/yr  (total = {ERA_TAU_U * 6:.1f} works, ERA LVT)")
        print(f"Tau_S          : {ERA_TAU_S:.1f}/yr  (total = {ERA_TAU_S * 6:.1f} works)")
        print(f"MD Whitelist   : {whitelist_pq} (threshold >= {ERA_WHITELIST_TAU:.1f} work)")
        print(f"Active Divs    : {len(div_map)} Divisions (01–22)")
        print(f"Active Groups  : {len(grp_map)} Groups")
        print(f"Output Root    : {era_dir}")
        print("==================================================================\n")

        if args.dry_run:
            print("DRY-RUN completed successfully. No computation performed.")
            return

        if args.candidacy_only:
            print("Candidacy tables verified. Exiting as requested.")
            return

        if args.division:
            div_code = args.division.zfill(2)
            if div_code not in div_map:
                raise ValueError(f"Division {div_code} not in active divisions: {sorted(div_map.keys())}")
            run_unit(db, fw_path, cr_path, sub_sc, sub_ic, whitelist_pq,
                     div_code, div_labels[div_code], "division", div_map[div_code], div_dir, tmp_dir)

        if args.group:
            grp_code = args.group.zfill(4)
            if grp_code not in grp_map:
                raise ValueError(f"Group {grp_code} not in active groups: {sorted(grp_map.keys())}")
            run_unit(db, fw_path, cr_path, sub_sc, sub_ic, whitelist_pq,
                     grp_code, grp_labels[grp_code], "group", grp_map[grp_code], grp_dir, tmp_dir)

        if args.divisions:
            print(f"Starting run across all {len(div_map)} Divisions ...")
            for div_code in sorted(div_map.keys()):
                run_unit(db, fw_path, cr_path, sub_sc, sub_ic, whitelist_pq,
                         div_code, div_labels[div_code], "division", div_map[div_code], div_dir, tmp_dir)

        if args.groups:
            print(f"Starting run across all {len(grp_map)} Groups ...")
            for grp_code in sorted(grp_map.keys()):
                run_unit(db, fw_path, cr_path, sub_sc, sub_ic, whitelist_pq,
                         grp_code, grp_labels[grp_code], "group", grp_map[grp_code], grp_dir, tmp_dir)

        if args.report:
            generate_hep_report(era_dir, data_dir)


if __name__ == '__main__':
    main()
