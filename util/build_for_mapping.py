"""
build_for_mapping.py — one-off generator for the two FOR/AREA5 reference tables.

Not part of the guarded pipeline rerun order: this data only changes when the
research-classification package itself is updated (a new ANZSRC/OpenAlex audit),
not on every snapshot refresh. Run manually and commit the resulting CSVs:

  .venv/bin/python util/build_for_mapping.py

Writes:
  data/oax_field_to_area5.csv       26 rows (+ 1 unreachable Indigenous Studies row)
  data/oax_subfield_to_for2020.csv  one row per subfield_idx actually present in
                                     this project's candidacy data (~252 rows)

Neither output touches flat_works — both are small static lookup tables joined
ad hoc, at query time, by whatever script needs FOR/AREA5 codes (same pattern as
data/bloc.xlsx / data/HEP_concordances.xlsx).
"""

import csv
import sys
from pathlib import Path

import duckdb
from research_classification import Resolver

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, FIELD_NAMES

# Indigenous Studies is unreachable from OpenAlex's topic hierarchy (confirmed
# exhaustively — no OA field/subfield in this project's data resolves there; see
# CLAUDE.md/REFERENCE.md). Carried explicitly as a recognized, always-listed,
# zero-populated category rather than silently omitted.
_INDIGENOUS_AREA5 = {'area5_code': 'IND', 'area5_label': 'Indigenous Studies'}


def build_area5_table(out_path: Path) -> int:
    r = Resolver()
    rows = []
    for field_idx in sorted(FIELD_NAMES):
        area5 = r.resolve(str(field_idx), 'OAX', 'FOR2020_AREA5')
        rows.append({
            'field_idx':  field_idx,
            'field_name': FIELD_NAMES[field_idx],
            'area5_code': area5.code,
            'area5_label': area5.label,
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['field_idx', 'field_name', 'area5_code', 'area5_label'])
        w.writeheader()
        w.writerows(rows)
        # Indigenous Studies: explicit, always-listed, unreachable-by-construction
        # placeholder row (no field_idx populates it) so consumers iterating "all
        # AREA5 categories" see it rather than silently omitting it.
        w.writerow({'field_idx': '', 'field_name': '',
                    'area5_code': _INDIGENOUS_AREA5['area5_code'],
                    'area5_label': _INDIGENOUS_AREA5['area5_label']})
    return len(rows)


def build_for2020_table(out_path: Path, subfield_ids: list[int]) -> int:
    r = Resolver()
    rows = []
    for subfield_idx in subfield_ids:
        res = r.resolve(str(subfield_idx), 'OAX', 'FOR2020')
        division_code = res.code[:2]
        # Division label isn't directly on the group-level CanonicalResult; look
        # it up via a fresh division-precision resolve of the same code.
        division_label = r.resolve(division_code, 'FOR2020', 'FOR2020').label
        rows.append({
            'subfield_idx':       subfield_idx,
            'for_group_code':     res.code,
            'for_group_label':    res.label,
            'for_division_code':  division_code,
            'for_division_label': division_label,
            'confidence':         res.confidence,
            'match_method':       res.match_method,
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=[
            'subfield_idx', 'for_group_code', 'for_group_label',
            'for_division_code', 'for_division_label', 'confidence', 'match_method',
        ])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def _distinct_subfield_ids(working_dir: Path, window: str) -> list[int]:
    path = working_dir / f'subfield_source_cands_{window}.parquet'
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run pipeline/build_field_candidacy.py first "
            f"(need the real subfield_idx domain this project actually uses)."
        )
    with duckdb.connect() as db:
        rows = db.execute(f"SELECT DISTINCT field_idx FROM '{path}' ORDER BY field_idx").fetchall()
    return [r[0] for r in rows]


def main():
    paths = load_config()
    window = '2020_2024'  # candidacy is window-scoped but the subfield_idx domain itself is not
    subfield_ids = _distinct_subfield_ids(paths.working, window)

    area5_path = paths.data / 'oax_field_to_area5.csv'
    for2020_path = paths.data / 'oax_subfield_to_for2020.csv'

    n_area5 = build_area5_table(area5_path)
    print(f"Wrote {n_area5} rows (+ 1 Indigenous Studies placeholder) → {area5_path}")

    n_for = build_for2020_table(for2020_path, subfield_ids)
    print(f"Wrote {n_for} rows → {for2020_path}")


if __name__ == '__main__':
    main()
    print("FINISHED!")
