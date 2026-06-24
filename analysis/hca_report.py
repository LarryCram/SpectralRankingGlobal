"""
hca_report.py — Compare our HCA with Clarivate Highly Cited Researchers 2025.

For each OA field with a close Clarivate category match, reports:
  n_hca    : our HCA count in this field
  n_hcr    : Clarivate HCR count in matched category (Cross-Field excluded)
  overlap  : last-name matches (ASCII-normalised, lower-cased)

Also runs a preliminary first+last name match across all 7,131 HCR (including
Cross-Field) using nameparser + unidecode, irrespective of field.

Inputs:
  WORKING/hca_{window}_{label}.parquet
  data/2025_HCR.xlsx

Usage:
  .venv/bin/python analysis/hca_report.py
  .venv/bin/python analysis/hca_report.py --window 2020_2024 --label baseline
"""

import sys
import argparse
import unicodedata
from collections import defaultdict
from pathlib import Path

import pandas as pd
from nameparser import HumanName
from unidecode import unidecode

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, FIELD_NAMES

# ── concordance ───────────────────────────────────────────────────────────────
# (field_idx, clarivate_categories, note)
# Only fields with genuine disciplinary alignment are included.
# Categories are from the Clarivate HCR 2025 file (excluding Cross-Field).

CONCORDANCE = [
    (11, ['Agricultural Sciences', 'Plant and Animal Science'],
         'Ag+plant/animal covers the ag+bio scope; misses pure cell biology'),
    (13, ['Biology and Biochemistry', 'Molecular Biology and Genetics'],
         'Good match — both categories together span biochem/genetics/molbio'),
    (14, ['Economics and Business'],
         'Good match; note: same Clarivate category used for field 20'),
    (16, ['Chemistry'],
         'Good match'),
    (17, ['Computer Science'],
         'Good match'),
    (19, ['Geosciences'],
         'Good match; Space Science excluded (planetary astro is minor in field 19)'),
    (20, ['Economics and Business'],
         'Good match; note: same Clarivate category used for field 14'),
    (22, ['Engineering'],
         'Good match'),
    (23, ['Environment and Ecology'],
         'Good match'),
    (24, ['Immunology', 'Microbiology'],
         'Good match — two Clarivate categories combine cleanly'),
    (25, ['Materials Science'],
         'Good match'),
    (26, ['Mathematics'],
         'Good match'),
    (27, ['Clinical Medicine'],
         'Good match'),
    (28, ['Neuroscience and Behavior'],
         'Good match'),
    (30, ['Pharmacology and Toxicology'],
         'Good match'),
    (31, ['Physics', 'Space Science'],
         'Good match — Physics+Space Science spans field 31 well'),
    (32, ['Psychiatry and Psychology'],
         'Partial — Clarivate combines psychiatry with psychology'),
    (33, ['Social Sciences'],
         'Good match'),
    (34, ['Plant and Animal Science'],
         'Partial — animal science part overlaps; no dedicated veterinary category'),
]

NO_MATCH = [
    (12, 'Arts and Humanities',
         'no Clarivate category'),
    (15, 'Chemical Engineering',
         'Engineering is too broad; Chemistry excludes process engineering'),
    (18, 'Decision Sciences',
         'no Clarivate category'),
    (21, 'Energy',
         'no Clarivate category'),
    (29, 'Nursing',
         'Clinical Medicine is too broad to be meaningful'),
    (35, 'Dentistry',
         'Clinical Medicine is too broad to be meaningful'),
    (36, 'Health Professions',
         'no Clarivate category'),
]


# ── name utilities ────────────────────────────────────────────────────────────

def norm_last(name: str) -> str | None:
    """Extract and normalise last name: ASCII, lowercase, stripped."""
    if not name or str(name).lower() == 'nan':
        return None
    parts = str(name).strip().split()
    if not parts:
        return None
    raw = parts[-1]
    ascii_name = unicodedata.normalize('NFKD', raw).encode('ascii', 'ignore').decode('ascii')
    return ascii_name.lower().strip('.,')


def extract_hca_last(hca: pd.DataFrame, field_idx: int) -> pd.Series:
    """Normalised last names of our HCA in a field (Series of strings, no NaN)."""
    sub = hca[hca['field_idx'] == field_idx]['display_name'].dropna()
    return sub.map(norm_last).dropna()


def hcr_last_names(hcr: pd.DataFrame, categories: list[str]) -> pd.Series:
    """Normalised last names of HCR in the given Clarivate categories."""
    sub = hcr[hcr['Category'].isin(categories)]['Last Name']
    return sub.map(lambda n: norm_last(n) if pd.notna(n) else None).dropna()


# ── report ────────────────────────────────────────────────────────────────────

def run_report(hca: pd.DataFrame, hcr: pd.DataFrame) -> None:
    sep  = '─' * 120
    sep2 = '═' * 120

    # ── preamble: concordance ────────────────────────────────────────────────
    print(sep2)
    print('HCA vs Clarivate HCR 2025 — field concordance')
    print(sep2)
    print(f'\n{"fid":>4}  {"OA Field":<44}  {"Clarivate Category":<45}  Note')
    print(sep)
    for fid, cats, note in CONCORDANCE:
        fname  = FIELD_NAMES.get(fid, str(fid))
        catstr = ' + '.join(cats)
        print(f'{fid:>4}  {fname:<44}  {catstr:<45}  {note}')
    print()
    print('No Clarivate match:')
    for fid, fname, reason in NO_MATCH:
        print(f'  {fid:>3}  {fname:<44}  {reason}')

    # ── overlap table ────────────────────────────────────────────────────────
    print(f'\n{sep2}')
    print('Overlap by last name (ASCII-normalised)')
    print('  %hca = overlap / n_hca  |  %hcr = overlap / n_hcr')
    print(sep2)
    hdr = (f'{"fid":>4}  {"OA Field":<30}  {"Clarivate Cat":<38}'
           f'  {"n_hca":>6}  {"n_hcr":>6}  {"overlap":>8}  {"% hca":>7}  {"% hcr":>7}')
    print(hdr)
    print(sep)

    detail_rows = []   # (fid, fname, sorted overlap names)

    for fid, cats, _ in CONCORDANCE:
        fname     = FIELD_NAMES.get(fid, str(fid))
        catstr    = ' + '.join(c.split()[-1] for c in cats)   # abbreviated for table

        hca_names = set(extract_hca_last(hca, fid))
        hcr_names = set(hcr_last_names(hcr, cats))

        n_hca    = len(hca[hca['field_idx'] == fid])
        n_hcr    = hcr[hcr['Category'].isin(cats)].shape[0]
        overlap  = hca_names & hcr_names
        n_ov     = len(overlap)
        pct_hca  = n_ov / n_hca * 100 if n_hca else 0
        pct_hcr  = n_ov / n_hcr * 100 if n_hcr else 0

        print(f'{fid:>4}  {fname:<30}  {catstr:<38}'
              f'  {n_hca:>6}  {n_hcr:>6}  {n_ov:>8}  {pct_hca:>6.1f}%  {pct_hcr:>6.1f}%')

        detail_rows.append((fid, fname, cats, n_hca, n_hcr, sorted(overlap)))

    # ── per-field overlap names ───────────────────────────────────────────────
    print(f'\n{sep2}')
    print('Overlapping last names by field  (sorted; common surnames may be false positives)')
    print(sep2)

    for fid, fname, cats, n_hca, n_hcr, names in detail_rows:
        catstr = ' + '.join(cats)
        print(f'\n  [{fid}] {fname}  →  {catstr}')
        print(f'       n_hca={n_hca}  n_hcr={n_hcr}  overlap={len(names)}')
        if names:
            # wrap at 100 chars
            line = '       '
            for nm in names:
                if len(line) + len(nm) + 2 > 110:
                    print(line)
                    line = '       '
                line += nm + '  '
            if line.strip():
                print(line)
        else:
            print('       (none)')

    print(f'\n{sep2}')


# ── name-match utilities ──────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """unidecode + lowercase + strip."""
    return unidecode(str(s)).lower().strip()


def _parse_name(full_name: str) -> tuple[str, str] | tuple[None, None]:
    """
    Parse any name string via nameparser + unidecode.
    Returns (first_norm, last_norm) using the nameparser first/last fields.
    Middle names/initials are ignored.  Returns (None, None) if either is empty.
    """
    raw = str(full_name or '').strip()
    if not raw or raw.lower() == 'nan':
        return None, None
    hn = HumanName(_norm(raw))
    first = hn.first.strip(' .,')
    last  = hn.last.strip(' .,')
    if not first or not last:
        return None, None
    return first, last


def _parse_hca(display_name) -> tuple[str, str] | tuple[None, None]:
    """Return (first_norm, last_norm) from an OA display_name."""
    return _parse_name(display_name)


def _parse_hcr(first_name, last_name) -> tuple[str, str] | tuple[None, None]:
    """Return (first_norm, last_norm) from Clarivate separate First/Last columns."""
    return _parse_name(f'{first_name} {last_name}')


def name_match_report(hca: pd.DataFrame, hcr: pd.DataFrame, sample_n: int = 20) -> None:
    """
    For each HCR (all 7,131 including Cross-Field), look for a full first + last
    name match in our HCA.  Match key: (first_norm, last_norm) via nameparser +
    unidecode.  Middle names/initials are stripped before comparison.

    Prints match/no-match counts and a sample of `sample_n` matched rows.
    """
    sep  = '─' * 110
    sep2 = '═' * 110

    # ── build HCA lookup: (first_norm, last_norm) → [(display_name, field_idx), …] ──
    hca_lookup: dict[tuple, list] = defaultdict(list)
    for _, row in hca.iterrows():
        key = _parse_hca(row['display_name'])
        if key[0]:
            hca_lookup[key].append((row['display_name'], int(row['field_idx'])))

    print(f'\n{sep2}')
    print('Name match: HCR → HCA  (full first + last name, nameparser + unidecode)')
    print(f'HCA lookup keys: {len(hca_lookup):,}  '
          f'(distinct normalised first × last across all fields)')
    print(sep2)

    matched_rows   = []   # dicts for matched HCR
    unmatched_rows = []

    for _, hcr_row in hcr.iterrows():
        fi, ln = _parse_hcr(hcr_row['First Name'], hcr_row['Last Name'])
        if not fi or not ln:
            unmatched_rows.append(hcr_row)
            continue
        hits = hca_lookup.get((fi, ln))
        if hits:
            matched_rows.append({
                'hcr_name':     f'{hcr_row["First Name"]} {hcr_row["Last Name"]}',
                'hcr_category': hcr_row['Category'],
                'hcr_affil':    str(hcr_row['Primary Affiliation'] or ''),
                'hca_matches':  hits,     # [(display_name, field_idx), …]
            })
        else:
            unmatched_rows.append(hcr_row)

    n_total    = len(hcr)
    n_matched  = len(matched_rows)
    n_unmatched = n_total - n_matched

    print(f'Total HCR        : {n_total:,}')
    print(f'Matched (≥1 HCA) : {n_matched:,}  ({n_matched/n_total*100:.1f}%)')
    print(f'Unmatched        : {n_unmatched:,}  ({n_unmatched/n_total*100:.1f}%)')

    # ── per-category breakdown ────────────────────────────────────────────────
    from collections import Counter
    cat_matched   = Counter(m['hcr_category'] for m in matched_rows)
    cat_total     = hcr['Category'].value_counts()
    all_cats      = sorted(cat_total.index)

    print(f'\n{"Category":<36}  {"n_hcr":>6}  {"matched":>8}  {"unmatched":>10}  {"match%":>7}')
    print(sep)
    for cat in sorted(all_cats, key=lambda c: -(cat_matched.get(c, 0) / cat_total[c])):
        n   = int(cat_total[cat])
        nm  = cat_matched.get(cat, 0)
        pct = nm / n * 100
        print(f'  {cat:<34}  {n:>6}  {nm:>8}  {n-nm:>10}  {pct:>6.1f}%')
    print(sep)
    print(f'  {"TOTAL":<34}  {n_total:>6}  {n_matched:>8}  {n_unmatched:>10}  '
          f'{n_matched/n_total*100:>6.1f}%')

    # ── sample of matched rows ────────────────────────────────────────────────
    print(f'\nSample of {sample_n} matched HCR (ordered by appearance):')
    print(sep)
    hdr = f'  {"HCR name":<30}  {"Category":<32}  {"HCA fields matched"}'
    print(hdr)
    print(sep)

    for m in matched_rows[:sample_n]:
        field_counts = Counter(fid for _, fid in m['hca_matches'])
        parts = []
        for fid, cnt in sorted(field_counts.items()):
            tag = f'{fid}:{FIELD_NAMES.get(fid, str(fid))}'
            parts.append(f'{tag}×{cnt}' if cnt > 1 else tag)
        fields_str = ', '.join(parts)
        print(f'  {m["hcr_name"]:<30}  {m["hcr_category"]:<32}  {fields_str}')

    print(sep)


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window',   default='2020_2024')
    parser.add_argument('--label',    default='baseline')
    parser.add_argument('--hca-path', default='',
                        help='explicit HCA parquet path; default hca_{window}_{label}.parquet')
    args = parser.parse_args()

    paths = load_config()
    hca_path = (Path(args.hca_path) if args.hca_path
                else paths.working / f'hca_{args.window}_{args.label}.parquet')
    hcr_path = Path(__file__).parent.parent / 'data' / '2025_HCR.xlsx'

    for p in (hca_path, hcr_path):
        if not p.exists():
            print(f'ERROR: {p} not found'); sys.exit(1)

    print('Loading HCA...')
    hca = pd.read_parquet(hca_path, columns=['field_idx', 'display_name'])
    print(f'  {len(hca):,} HCA rows  |  {hca["field_idx"].nunique()} fields')

    print('Loading HCR...')
    hcr = pd.read_excel(hcr_path)
    named_hcr = hcr[hcr['Category'] != 'Cross-Field']
    print(f'  {len(hcr):,} total HCR  |  {len(named_hcr):,} named-category  '
          f'|  {hcr["Category"].nunique()} categories')
    print()

    run_report(hca, hcr)
    name_match_report(hca, hcr)


if __name__ == '__main__':
    main()
