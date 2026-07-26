"""
run_for_division.py — spectral ranking at the ANZSRC FOR2020 division level.

Standalone and decoupled from Run's field_idx-range dispatch (is_leiden/is_subfield)
by design — see data/oax_subfield_to_for2020.csv (built by util/build_for_mapping.py)
for the subfield_idx -> for_division_code grouping (resolved at subfield precision,
the higher-fidelity/fully-audited tier — see REFERENCE.md). Only 16-22 of FOR2020's
23 divisions are actually reached from this project's OpenAlex data; most reached
divisions contain just one OA field's worth of subfields (near-duplicates of the
existing field-level ranking) — see REFERENCE.md for the full breakdown.

No new intermediate files: FOR-division candidacy is a cheap re-aggregation of the
existing subfield_source_cands_{window}.parquet/subfield_inst_cands_{window}.parquet
(already the expensive GROUP BY over all of flat_works), and the edge list has no
sensitivity-suite reuse case here (baseline only), so both are built as scratch temp
parquet files under WORKING/.tmp/ and discarded — only the ranking table +
diagnostics are kept, under WORKING/for_division/, using the bare ANZSRC division
code (e.g. 32) directly, disambiguated from every other scheme by directory alone.

Outputs (per populated division, baseline label only):
  WORKING/for_division/rankings_{division_code}_{window}_baseline.parquet
  WORKING/for_division/rankings_{division_code}_{window}_baseline_diag.json

Usage:
  .venv/bin/python pipeline/run_for_division.py
"""

import csv
import json
import sys
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from util import load_config, load_settings, load_runs

from build_edge_list_field import build_edge_list
from run_rankings import rank_field, show_top


def load_for_division_groups(data_dir: Path) -> tuple[dict[int, tuple[int, ...]], dict[int, str]]:
    """Returns (division_code -> tuple of member subfield_idx, division_code -> label)."""
    groups: dict[int, list[int]] = defaultdict(list)
    labels: dict[int, str] = {}
    with open(data_dir / 'oax_subfield_to_for2020.csv', newline='') as f:
        for row in csv.DictReader(f):
            division_code = int(row['for_division_code'])
            groups[division_code].append(int(row['subfield_idx']))
            labels[division_code] = row['for_division_label']
    return {k: tuple(sorted(v)) for k, v in groups.items()}, labels


def build_scratch_candidacy(db: duckdb.DuckDBPyConnection, working_dir: Path,
                            window: str, division_code: int,
                            member_subfields: tuple[int, ...]) -> tuple[str, str]:
    """
    Re-aggregate the existing (already-cheap) subfield-level candidacy tables into
    one FOR division. No corpus rescan: subfield_source_cands/subfield_inst_cands
    are already the output of the one expensive GROUP BY over flat_works.
    """
    subfield_sc = working_dir / f'subfield_source_cands_{window}.parquet'
    subfield_ic = working_dir / f'subfield_inst_cands_{window}.parquet'
    members_sql = ', '.join(str(s) for s in member_subfields)

    out_sc = working_dir / '.tmp' / f'_fordiv_{division_code}_source_cands.parquet'
    out_ic = working_dir / '.tmp' / f'_fordiv_{division_code}_inst_cands.parquet'
    out_sc.parent.mkdir(parents=True, exist_ok=True)

    db.execute(f"""
        COPY (
            SELECT {division_code} AS field_idx, source_idx, SUM(weighted_works) AS weighted_works
            FROM '{subfield_sc}'
            WHERE field_idx IN ({members_sql})
            GROUP BY source_idx
        ) TO '{out_sc}' (FORMAT PARQUET)
    """)
    db.execute(f"""
        COPY (
            SELECT {division_code} AS field_idx, institution_idx, SUM(weighted_works) AS weighted_works
            FROM '{subfield_ic}'
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
    out_dir = working / 'for_division'
    out_dir.mkdir(exist_ok=True)
    tmp_dir = working / '.tmp'
    tmp_dir.mkdir(exist_ok=True)

    groups, labels = load_for_division_groups(paths.data)

    fw_path = str(working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    cr_path = str(working / f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')

    with duckdb.connect() as db:
        db.execute(f"SET temp_directory = '{tmp_dir}'")
        db.execute(f"SET memory_limit = '{settings.memory_limit}'")
        db.execute(f"SET preserve_insertion_order = {str(settings.preserve_insertion_order).lower()}")

        for division_code in sorted(groups):
            members = groups[division_code]
            print(f"\n=== FOR division {division_code} ({labels[division_code]}) — "
                  f"{len(members)} member subfield(s) ===")

            run = replace(baseline, field_idx=division_code)
            sc_path, ic_path = build_scratch_candidacy(db, working, window, division_code, members)
            el_path = str(tmp_dir / f'_fordiv_{division_code}_el.parquet')

            t0 = time.time()
            n_el = build_edge_list(
                db, fw_path, cr_path, sc_path, ic_path, run, el_path,
                member_ids=members, filter_col_override='subfield_idx',
            )
            print(f"  Edge list: {n_el:,} rows  [{time.time()-t0:.1f}s]")

            out_path = str(out_dir / f'rankings_{division_code}_{window}_baseline.parquet')
            df, diag = rank_field(db, run, str(working), out_path,
                                  el_path=el_path, sc_path=sc_path, ic_path=ic_path)
            diag.update(for_division_code=division_code, for_division_label=labels[division_code],
                       member_subfield_idx=list(members))
            diag_path = str(out_dir / f'rankings_{division_code}_{window}_baseline_diag.json')
            Path(diag_path).write_text(json.dumps(diag, indent=2))
            print(f"  → {out_path}")

            show_top(df, 'S', label=f'FORDIV-{division_code}')
            show_top(df, 'U', label=f'FORDIV-{division_code}')

            for p in (sc_path, ic_path, el_path):
                Path(p).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
    print("\nFINISHED!")
