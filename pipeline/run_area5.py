"""
run_area5.py — spectral ranking at the FOR2020_AREA5 level (5 areas, + Indigenous
Studies carried as a recognized but currently-unpopulated category).

Standalone and decoupled from Run's field_idx-range dispatch (is_leiden/is_subfield)
by design — see data/oax_field_to_area5.csv (built by util/build_for_mapping.py) for
the field_idx -> area5_code grouping. AREA5 sits alongside the existing Leiden 5-group
scheme, not replacing it; nothing in util/runs.py or the existing Leiden pipeline is
touched by this script.

No new intermediate files: AREA5 candidacy is a cheap re-aggregation of the existing
field_source_cands_{window}.parquet/field_inst_cands_{window}.parquet (already the
expensive GROUP BY over all of flat_works), and the edge list has no sensitivity-suite
reuse case here (baseline only), so both are built as scratch temp parquet files under
WORKING/.tmp/ and discarded — only the ranking table + diagnostics are kept, under
WORKING/area5/, so the bare area5-scoped identity used internally (a small int 1–5,
matching Leiden's own numbering by construction, see AREA5_IDX below) never collides
with anything in WORKING/ (different directory) even though the numbers overlap with
Leiden's own group ids.

Outputs (per populated area, baseline label only):
  WORKING/area5/rankings_{area5_idx}_{window}_baseline.parquet
  WORKING/area5/rankings_{area5_idx}_{window}_baseline_diag.json

Usage:
  .venv/bin/python pipeline/run_area5.py
"""

import csv
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from util import load_config, load_settings, load_runs

from build_edge_list_field import build_edge_list
from run_rankings import rank_field, show_top

# area5_code -> small int identity, matching Leiden's own group numbering (not
# reused *through* Leiden's dispatch — these scripts never call Run.is_leiden/
# sc_path/ic_path — just a legible, familiar id for this script's own output).
AREA5_IDX = {'MCS': 1, 'PSE': 2, 'LES': 3, 'BHS': 4, 'SSH': 5, 'IND': 6}


def load_area5_groups(data_dir: Path) -> tuple[dict[str, tuple[int, ...]], dict[str, str]]:
    """Returns (area5_code -> tuple of member field_idx, area5_code -> label)."""
    groups: dict[str, list[int]] = {}
    labels: dict[str, str] = {}
    with open(data_dir / 'oax_field_to_area5.csv', newline='') as f:
        for row in csv.DictReader(f):
            code = row['area5_code']
            labels[code] = row['area5_label']
            if row['field_idx']:
                groups.setdefault(code, []).append(int(row['field_idx']))
    return {k: tuple(sorted(v)) for k, v in groups.items()}, labels


def build_scratch_candidacy(db: duckdb.DuckDBPyConnection, working_dir: Path,
                            window: str, area5_idx: int,
                            member_fields: tuple[int, ...]) -> tuple[str, str]:
    """
    Re-aggregate the existing (already-cheap) field-level candidacy tables into one
    AREA5 group. No corpus rescan: field_source_cands/field_inst_cands are already
    the output of the one expensive GROUP BY over flat_works.
    """
    field_sc = working_dir / f'field_source_cands_{window}.parquet'
    field_ic = working_dir / f'field_inst_cands_{window}.parquet'
    members_sql = ', '.join(str(f) for f in member_fields)

    out_sc = working_dir / '.tmp' / f'_area5_{area5_idx}_source_cands.parquet'
    out_ic = working_dir / '.tmp' / f'_area5_{area5_idx}_inst_cands.parquet'
    out_sc.parent.mkdir(parents=True, exist_ok=True)

    db.execute(f"""
        COPY (
            SELECT {area5_idx} AS field_idx, source_idx, SUM(weighted_works) AS weighted_works
            FROM '{field_sc}'
            WHERE field_idx IN ({members_sql})
            GROUP BY source_idx
        ) TO '{out_sc}' (FORMAT PARQUET)
    """)
    db.execute(f"""
        COPY (
            SELECT {area5_idx} AS field_idx, institution_idx, SUM(weighted_works) AS weighted_works
            FROM '{field_ic}'
            WHERE field_idx IN ({members_sql})
            GROUP BY institution_idx
        ) TO '{out_ic}' (FORMAT PARQUET)
    """)
    return str(out_sc), str(out_ic)


def main():
    paths    = load_config()
    settings = load_settings()
    runs     = load_runs()
    baseline = runs[0]
    assert baseline.label == 'baseline', f"expected params.csv row 0 to be 'baseline', got {baseline.label!r}"

    window = baseline.window
    working = paths.working
    out_dir = working / 'area5'
    out_dir.mkdir(exist_ok=True)
    tmp_dir = working / '.tmp'
    tmp_dir.mkdir(exist_ok=True)

    groups, labels = load_area5_groups(paths.data)

    fw_path = str(working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    cr_path = str(working / f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')

    with duckdb.connect() as db:
        db.execute(f"SET temp_directory = '{tmp_dir}'")
        db.execute(f"SET memory_limit = '{settings.memory_limit}'")
        db.execute(f"SET preserve_insertion_order = {str(settings.preserve_insertion_order).lower()}")

        for code in sorted(AREA5_IDX, key=lambda c: AREA5_IDX[c]):
            area5_idx = AREA5_IDX[code]
            members = groups.get(code, ())
            print(f"\n=== AREA5 {code} ({labels[code]}) — idx {area5_idx}, "
                  f"{len(members)} member field(s) {members} ===")
            if not members:
                print("  No OA field resolves here — no ranking possible from OpenAlex "
                      "data (see CLAUDE.md/REFERENCE.md). Skipping.")
                continue

            run = replace(baseline, field_idx=area5_idx)
            sc_path, ic_path = build_scratch_candidacy(db, working, window, area5_idx, members)
            el_path = str(tmp_dir / f'_area5_{area5_idx}_el.parquet')

            t0 = time.time()
            n_el = build_edge_list(
                db, fw_path, cr_path, sc_path, ic_path, run, el_path,
                member_ids=members, filter_col_override='field_idx',
            )
            print(f"  Edge list: {n_el:,} rows  [{time.time()-t0:.1f}s]")

            out_path = str(out_dir / f'rankings_{area5_idx}_{window}_baseline.parquet')
            df, diag = rank_field(db, run, str(working), out_path,
                                  el_path=el_path, sc_path=sc_path, ic_path=ic_path)
            diag.update(area5_code=code, area5_label=labels[code], member_field_idx=list(members))
            diag_path = str(out_dir / f'rankings_{area5_idx}_{window}_baseline_diag.json')
            Path(diag_path).write_text(json.dumps(diag, indent=2))
            print(f"  → {out_path}")

            show_top(df, 'S', label=f'AREA5-{code}')
            show_top(df, 'U', label=f'AREA5-{code}')

            for p in (sc_path, ic_path, el_path):
                Path(p).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
    print("\nFINISHED!")
