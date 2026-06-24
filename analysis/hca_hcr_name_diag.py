"""
hca_hcr_name_diag.py — A1 diagnostic: which HCR names have zero OA author matches?

For each of the 7,131 HCR (2025 list), builds a candidate list of OA author_ids
whose display_name is plausibly the same person, using name-matching logic that
strongly rejects impossible matches.

Matching logic (both conditions must hold):
  1. last_norm matches: unidecode+lower of last word of display_name
  2. first compatible:
       - first_initial must match (first alpha char)
       - if BOTH sides have ≥4 alpha chars in first name → require first-3-char prefix match
         (strong rejection: "Alice" vs "Andrew" both start with 'a' but "ali" ≠ "and")

OA scan: works_count >= 10 (excludes stubs; all HCR-level researchers exceed this)

Output:
  - Candidate-count histogram
  - Summary: n_found vs n_empty (A1 failures)
  - Printed list of A1-failure HCR names + their parsed components

Usage:
  .venv/bin/python analysis/hca_hcr_name_diag.py
  .venv/bin/python analysis/hca_hcr_name_diag.py --works-min 5
"""

from __future__ import annotations

import sys
import time
import argparse
from pathlib import Path
from collections import defaultdict

import duckdb
import pandas as pd
from nameparser import HumanName
from unidecode import unidecode

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from util import load_config

from name_match import _norm, _first_alpha


# ── name processing ───────────────────────────────────────────────────────────

def _alpha_len(s: str) -> int:
    return sum(c.isalpha() for c in s)


def _de_umlaut(s: str) -> str:
    """Collapse German umlaut digraphs: ae→a, oe→o, ue→u."""
    for dig, rep in [('ae', 'a'), ('oe', 'o'), ('ue', 'u')]:
        s = s.replace(dig, rep)
    return s


def _parse_hcr_name(first: str, last: str) -> tuple[str, str, str]:
    """
    Returns (last_norm, first_norm, first_initial) for a HCR entry.
    Passes the full name to nameparser so that Jr./III land in suffix and
    parenthetical nicknames land in nickname — both stripped from last/first.
    Takes the last WORD of hn.last so particles ("van der", "de la") are dropped,
    matching OA's last-token indexing convention.
    """
    hn = HumanName(_norm(f'{first} {last}'))
    first_n = hn.first.strip(' .,')
    last_words = hn.last.strip(' .,').split()
    last_n = last_words[-1].strip('.,') if last_words else ''
    return last_n, first_n, _first_alpha(first_n)


def _parse_oax_display(display_name: str) -> tuple[str, str, str] | None:
    """
    Fast extraction for OA display_names — no nameparser, simple split.
    Returns (last_norm, first_norm, first_initial) or None if unparseable.
    Space-split: first token = first name proxy, last token = last name proxy.
    Both sides unidecode+lower'd.
    """
    raw = _norm(display_name)
    tokens = raw.split()
    if len(tokens) < 2:
        return None
    first = tokens[0].strip('.,')
    last  = tokens[-1].strip('.,')
    if not first or not last:
        return None
    fi = _first_alpha(first)
    if not fi:
        return None
    return last, first, fi


def names_compatible(hcr_first: str, hcr_fi: str,
                     oax_first: str, oax_fi: str) -> bool:
    """
    True unless the match is clearly impossible.

    Rejects when:
      - first initials differ (different first letter)
      - BOTH sides are full names (≥4 alpha chars) but first-3-char prefix disagrees
        after Germanic umlaut normalisation (ae→a, oe→o, ue→u)
    """
    if hcr_fi != oax_fi:
        return False
    hcr_full = _alpha_len(hcr_first) >= 4
    oax_full = _alpha_len(oax_first) >= 4
    if hcr_full and oax_full:
        if hcr_first[:3] == oax_first[:3]:
            return True
        return _de_umlaut(hcr_first)[:3] == _de_umlaut(oax_first)[:3]
    return True


# ── OA author index ───────────────────────────────────────────────────────────

def build_oax_index(
    au_glob: str,
    works_min: int,
) -> dict[str, list[tuple[str, str, str]]]:
    """
    Scan OA authorships parquet: get distinct (author_idx, author_name) pairs
    for authors with >= works_min distinct works.

    Returns: {last_norm: [(author_idx, first_norm, first_initial), ...]}
    """
    print(f'Loading OA authorships (works_count >= {works_min})...')
    t0 = time.time()
    import glob as _glob
    files = sorted(_glob.glob(au_glob))
    # skip any parquet files that contain invalid UTF-8 strings
    bad_names = {'updated_date=2025-11-06_part_0070.parquet'}
    files = [f for f in files if Path(f).name not in bad_names]
    file_list = str(files).replace("'", '"')  # DuckDB needs double-quoted strings in list
    con = duckdb.connect()
    con.execute(f"""
        CREATE TEMP TABLE _wc AS
        SELECT author_idx, COUNT(DISTINCT work_idx) AS n_works
        FROM read_parquet({file_list})
        WHERE author_idx IS NOT NULL
        GROUP BY author_idx
        HAVING COUNT(DISTINCT work_idx) >= {works_min}
    """)
    print(f'  work-count table built  [{time.time()-t0:.1f}s]')
    t1 = time.time()
    df = con.execute(f"""
        SELECT DISTINCT a.author_idx, a.author_name AS display_name
        FROM read_parquet({file_list}) a
        JOIN _wc w ON a.author_idx = w.author_idx
        WHERE a.author_name IS NOT NULL
          AND length(trim(a.author_name)) > 2
    """).df()
    con.close()
    print(f'  {len(df):,} (author_idx, name) pairs  [{time.time()-t1:.1f}s]')

    print('Building name index...')
    t0 = time.time()
    idx: dict[str, list] = defaultdict(list)
    n_skipped = 0
    for row in df.itertuples(index=False):
        parsed = _parse_oax_display(row.display_name)
        if parsed is None:
            n_skipped += 1
            continue
        last_n, first_n, fi = parsed
        idx[last_n].append((row.author_idx, first_n, fi))
    print(f'  indexed {len(idx):,} distinct last_norms  '
          f'| {n_skipped:,} skipped  [{time.time()-t0:.1f}s]')
    return dict(idx)


# ── per-HCR candidate lookup ──────────────────────────────────────────────────

def find_candidates(
    hcr_last: str, hcr_first: str, hcr_fi: str,
    oax_idx: dict[str, list],
) -> list[tuple[str, str, str]]:
    """
    Return all OA author entries (id, first_norm, first_initial) for this HCR
    that pass the name compatibility filter.
    """
    pool = oax_idx.get(hcr_last, [])
    return [
        (aid, oax_first, oax_fi)
        for aid, oax_first, oax_fi in pool
        if names_compatible(hcr_first, hcr_fi, oax_first, oax_fi)
    ]


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--works-min', type=int, default=10)
    args = parser.parse_args()

    paths   = load_config()
    au_glob = str(paths.openalex / 'parquet' / 'authorships' / '*.parquet')
    hcr_path = Path(__file__).parent.parent / 'data' / '2025_HCR.xlsx'

    if not hcr_path.exists():
        print(f'ERROR: {hcr_path} not found'); sys.exit(1)

    # ── load HCR ─────────────────────────────────────────────────────────────
    print('Loading HCR...')
    hcr = pd.read_excel(hcr_path)
    print(f'  {len(hcr):,} HCR entries')

    # ── build OA index ────────────────────────────────────────────────────────
    oax_idx = build_oax_index(au_glob, args.works_min)

    # ── lookup each HCR ───────────────────────────────────────────────────────
    print('\nLooking up each HCR...')
    results = []
    for _, row in hcr.iterrows():
        last_n, first_n, fi = _parse_hcr_name(
            str(row.get('First Name', '') or ''),
            str(row.get('Last Name',  '') or ''),
        )
        candidates = find_candidates(last_n, first_n, fi, oax_idx) if fi else []
        results.append({
            'hcr_first':  str(row.get('First Name', '')),
            'hcr_last':   str(row.get('Last Name',  '')),
            'category':   str(row.get('Category',   '')),
            'affil':      str(row.get('Primary Affiliation', '') or ''),
            'last_norm':  last_n,
            'first_norm': first_n,
            'first_initial': fi,
            'n_candidates':  len(candidates),
            'candidates':    candidates,
        })

    # ── summary ───────────────────────────────────────────────────────────────
    sep  = '─' * 100
    sep2 = '═' * 100
    n_total  = len(results)
    n_empty  = sum(1 for r in results if r['n_candidates'] == 0)
    n_found  = n_total - n_empty

    print(f'\n{sep2}')
    print(f'A1 diagnostic: HCR name-logic matches in OA authors (works_count >= {args.works_min})')
    print(sep2)
    print(f'Total HCR          : {n_total:,}')
    print(f'≥1 OA candidate    : {n_found:,}  ({n_found/n_total*100:.1f}%)  [A1 success]')
    print(f'Zero OA candidates : {n_empty:,}  ({n_empty/n_total*100:.1f}%)  [A1 failure — name logic cannot find them]')

    # candidate count histogram
    from collections import Counter
    hist = Counter(min(r['n_candidates'], 20) for r in results)
    print(f'\nCandidate-count distribution:')
    print(f'  {"n_candidates":>14}  {"n_HCR":>8}')
    print(sep)
    for k in sorted(hist):
        label = str(k) if k < 20 else '20+'
        print(f'  {label:>14}  {hist[k]:>8}')

    # ── A1 failures: printed list ─────────────────────────────────────────────
    failures = [r for r in results if r['n_candidates'] == 0]
    print(f'\n{sep2}')
    print(f'A1 failures ({len(failures):,}) — HCR names with zero OA matches:')
    print(sep2)
    print(f'  {"First Name":<22}  {"Last Name":<22}  {"last_norm":<18}  {"first_norm":<16}  fi  Category')
    print(sep)
    for r in sorted(failures, key=lambda x: (x['last_norm'], x['first_norm'])):
        print(f'  {r["hcr_first"]:<22}  {r["hcr_last"]:<22}  '
              f'{r["last_norm"]:<18}  {r["first_norm"]:<16}  '
              f'{r["first_initial"] or "?":>2}  {r["category"]}')

    # ── A1 successes with very high ambiguity ─────────────────────────────────
    high_amb = sorted(
        [r for r in results if r['n_candidates'] >= 100],
        key=lambda x: -x['n_candidates'],
    )[:20]
    if high_amb:
        print(f'\n{sep2}')
        print('High-ambiguity matches (n_candidates >= 100) — top 20:')
        print(sep2)
        print(f'  {"First Name":<22}  {"Last Name":<22}  {"n_candidates":>13}  Category')
        print(sep)
        for r in high_amb:
            print(f'  {r["hcr_first"]:<22}  {r["hcr_last"]:<22}  '
                  f'{r["n_candidates"]:>13,}  {r["category"]}')


if __name__ == '__main__':
    main()
