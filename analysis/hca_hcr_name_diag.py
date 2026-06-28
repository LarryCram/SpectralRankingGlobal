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
import json
import time
import argparse
from pathlib import Path
from collections import defaultdict

import duckdb
import pandas as pd
from nameparser import HumanName
from nameparser.config import CONSTANTS as _NP_CONSTANTS
from unidecode import unidecode

# Academic and honours postnominals not in nameparser's default suffix list.
# Add lowercase; nameparser normalises to lowercase before matching.
for _pn in [
    'fba', 'ffasl', 'faa', 'frs', 'frse', 'frsc', 'frsb', 'fmedsci',
    'cbe', 'obe', 'mbe', 'dbe', 'kbe', 'ao', 'ac', 'am',
    'frcpe', 'frcp', 'frcs', 'fracp', 'fracs', 'frcpa',
    'facss', 'faas', 'faps', 'fassa',
]:
    _NP_CONSTANTS.suffix_acronyms.add(_pn)

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
    authors_path: str,
) -> tuple[dict[str, list], dict[int, set[int]]]:
    """
    Scan OA authorships parquet: identify authors with >= works_min distinct works,
    fetch their display_name from authors.parquet, and build institution membership.

    Returns:
      name_idx       : {last_norm: [(author_idx, first_norm, first_initial), ...]}
      inst_to_authors: {institution_idx: set(author_idx)}
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
        SELECT a.author_idx, a.display_name, a.works_count, a.cited_by_count
        FROM parquet_scan('{authors_path}') a
        JOIN _wc w ON a.author_idx = w.author_idx
        WHERE a.display_name IS NOT NULL
          AND length(trim(a.display_name)) > 2
    """).df()
    print(f'  {len(df):,} (author_idx, name) pairs  [{time.time()-t1:.1f}s]')

    # institution membership: which qualified authors have published from each institution?
    t2 = time.time()
    inst_df = con.execute(f"""
        SELECT DISTINCT a.institution_idx, a.author_idx
        FROM read_parquet({file_list}) a
        JOIN _wc w ON a.author_idx = w.author_idx
        WHERE a.institution_idx IS NOT NULL
    """).df()
    con.close()
    print(f'  {len(inst_df):,} (institution_idx, author_idx) pairs  [{time.time()-t2:.1f}s]')

    print('Building indexes...')
    t0 = time.time()
    idx: dict[str, list] = defaultdict(list)
    author_stats: dict[int, tuple[int, int]] = {}   # author_idx → (works_count, cited_by_count)
    n_skipped = 0
    for row in df.itertuples(index=False):
        parsed = _parse_oax_display(row.display_name)
        if parsed is None:
            n_skipped += 1
            continue
        last_n, first_n, fi = parsed
        aid = int(row.author_idx)
        idx[last_n].append((aid, first_n, fi))
        author_stats[aid] = (
            int(row.works_count)     if row.works_count     is not None else 0,
            int(row.cited_by_count)  if row.cited_by_count  is not None else 0,
        )

    inst_to_authors: dict[int, set[int]] = defaultdict(set)
    for row in inst_df.itertuples(index=False):
        inst_to_authors[int(row.institution_idx)].add(int(row.author_idx))

    print(f'  name_idx: {len(idx):,} last_norms  | {n_skipped:,} skipped  '
          f'| inst_to_authors: {len(inst_to_authors):,} institutions  [{time.time()-t0:.1f}s]')
    return dict(idx), dict(inst_to_authors), author_stats


def load_hcr_inst_map(data_dir: Path) -> dict[str, list[int]]:
    """
    Load per-person institution resolutions from hcr_person_inst.json.
    Key: 'FirstName|||LastName|||Category', value: [institution_idx, ...].
    Written by test_hcr_inst_oax.py.
    """
    path = data_dir / 'hcr_person_inst.json'
    if not path.exists():
        print(f'  WARNING: {path.name} not found — run test_hcr_inst_oax.py first')
        return {}
    data = json.loads(path.read_text())
    print(f'  Loaded {path.name}: {len(data):,} persons')
    return data


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

    paths        = load_config()
    au_glob      = str(paths.openalex / 'parquet' / 'authorships' / '*.parquet')
    authors_path = str(paths.openalex / 'parquet' / 'authors'     / '*.parquet')
    hcr_path     = Path(__file__).parent.parent / 'data' / '2025_HCR.xlsx'

    if not hcr_path.exists():
        print(f'ERROR: {hcr_path} not found'); sys.exit(1)

    # ── load HCR ─────────────────────────────────────────────────────────────
    print('Loading HCR...')
    hcr = pd.read_excel(hcr_path)
    print(f'  {len(hcr):,} HCR entries')

    # ── build OA index ────────────────────────────────────────────────────────
    oax_idx, inst_to_authors, author_stats = build_oax_index(au_glob, args.works_min, authors_path)

    # ── load HCR institution resolutions ─────────────────────────────────────
    data_dir = Path(__file__).parent.parent / 'data'
    print('\nLoading HCR institution resolutions...')
    hcr_inst_map = load_hcr_inst_map(data_dir)

    # ── lookup each HCR ───────────────────────────────────────────────────────
    print('\nLooking up each HCR...')
    results = []
    for _, row in hcr.iterrows():
        first_raw = str(row.get('First Name', '') or '').strip()
        last_raw  = str(row.get('Last Name',  '') or '').strip()
        cat_raw   = str(row.get('Category',   '') or '').strip()
        last_n, first_n, fi = _parse_hcr_name(first_raw, last_raw)

        candidates = find_candidates(last_n, first_n, fi, oax_idx) if fi else []

        # institution filter: narrow candidates to those who have published from one
        # of this person's resolved OA institutions.  Institution is used as a soft
        # signal only — if the filter produces zero results (OA parent/child idx
        # mismatch between institutions.parquet and authorships.parquet) we fall
        # back to the full name-only candidate list rather than falsely eliminating
        # world-leading researchers who are obviously in OA.
        inst_key   = f'{first_raw}|||{last_raw}|||{cat_raw}'
        inst_idxs  = hcr_inst_map.get(inst_key, [])
        inst_filtered: list = []
        inst_fallback = False
        if inst_idxs and candidates:
            inst_author_set: set[int] = set()
            for iidx in inst_idxs:
                inst_author_set.update(inst_to_authors.get(iidx, set()))
            inst_filtered = [(aid, fn, fii) for aid, fn, fii in candidates
                             if int(aid) in inst_author_set]
            if not inst_filtered:
                inst_filtered  = candidates   # fall back — institution mismatch in OA data
                inst_fallback  = True
        inst_candidates = inst_filtered

        results.append({
            'hcr_first':       first_raw,
            'hcr_last':        last_raw,
            'category':        cat_raw,
            'affil':           str(row.get('Primary Affiliation', '') or ''),
            'last_norm':       last_n,
            'first_norm':      first_n,
            'first_initial':   fi,
            'inst_idxs':       inst_idxs,
            'inst_fallback':   inst_fallback,
            'n_candidates':    len(candidates),
            'candidates':      candidates,
            'n_inst_cands':    len(inst_candidates),
            'inst_candidates': inst_candidates,
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

    # ── institution disambiguation summary ───────────────────────────────────
    print(f'\n{sep2}')
    print('Institution disambiguation  (name candidates filtered by resolved OA institution)')
    print(sep2)
    has_inst   = [r for r in results if r['inst_idxs']]
    no_inst    = [r for r in results if not r['inst_idxs']]
    has_cands  = [r for r in has_inst if r['n_candidates'] > 0]

    n_unique   = sum(1 for r in has_cands if r['n_inst_cands'] == 1)
    n_narrowed = sum(1 for r in has_cands if 1 < r['n_inst_cands'] < r['n_candidates'])
    n_unchanged= sum(1 for r in has_cands if r['n_inst_cands'] == r['n_candidates']
                     and r['n_candidates'] > 0 and not r['inst_fallback'])
    n_fallback = sum(1 for r in has_cands if r['inst_fallback'])
    n_no_cands = sum(1 for r in has_inst  if r['n_candidates'] == 0)

    print(f'  HCR with ≥1 resolved institution : {len(has_inst):,}')
    print(f'    of which ≥1 name candidate      : {len(has_cands):,}')
    print(f'      → uniquely disambiguated (n=1) : {n_unique:,}  ({n_unique/len(has_cands)*100:.1f}%)')
    print(f'      → narrowed (>1 → fewer)        : {n_narrowed:,}')
    print(f'      → unchanged (inst didn\'t help)  : {n_unchanged:,}')
    print(f'      → inst mismatch / fallback      : {n_fallback:,}  '
          f'(OA parent/child idx differs between institutions.parquet and authorships)')
    print(f'    of which zero name candidates    : {n_no_cands:,}  (A1 failures)')
    print(f'  HCR with NO resolved institution   : {len(no_inst):,}')

    # ── inst-mismatch / fallback: confidence stratification ──────────────────
    # Institution resolver gave a wrong institution_idx (T3 fuzzy false positive,
    # e.g. "MD Anderson" → Baptist MD Anderson; affil → Swiss Federal Office of Energy).
    # We fell back to name-only candidates.  For world-leading researchers, the
    # correct OA record has far more citations than any homonym, so we stratify by
    # how confidently cited_by_count identifies the top candidate.
    #
    # Confidence tiers:
    #   CLEAR  : top cited ≥ 1000  AND  (n=1  OR  top/2nd ≥ 5×)
    #   LIKELY : top cited ≥ 1000  AND  top/2nd ∈ [2×, 5×)
    #   WEAK   : top cited ≥ 1000  AND  top/2nd < 2×   (two plausible candidates)
    #   LOW    : top cited < 1000                        (sparse record; uncertain)

    fallback_rows = [r for r in has_cands if r['inst_fallback']]

    def _fb_stats(r):
        cands_sorted = sorted(
            r['candidates'],
            key=lambda c: author_stats.get(int(c[0]), (0, 0))[1],
            reverse=True,
        )
        top   = author_stats.get(int(cands_sorted[0][0]), (0, 0)) if cands_sorted else (0, 0)
        top2  = author_stats.get(int(cands_sorted[1][0]), (0, 0)) if len(cands_sorted) > 1 else (0, 0)
        ratio = (top[1] / top2[1]) if top2[1] > 0 else float('inf')
        n     = len(cands_sorted)
        if top[1] >= 1000 and (n == 1 or ratio >= 5):
            tier = 'CLEAR'
        elif top[1] >= 1000 and ratio >= 2:
            tier = 'LIKELY'
        elif top[1] >= 1000:
            tier = 'WEAK'
        else:
            tier = 'LOW'
        return cands_sorted, top, top2, ratio, tier

    if fallback_rows:
        tier_counts = {'CLEAR': 0, 'LIKELY': 0, 'WEAK': 0, 'LOW': 0}
        for r in fallback_rows:
            _, _, _, _, tier = _fb_stats(r)
            tier_counts[tier] += 1

        print(f'\n{"─"*100}')
        print(f'Institution-mismatch fallback ({len(fallback_rows)}) — name candidates, ranked by cited_by_count.')
        print('Institution resolver gave wrong OA institution_idx (T3 fuzzy false positive).')
        print(f'{"─"*100}')
        print(f'  CLEAR  (top cited ≥1000, ratio ≥5× or unique) : {tier_counts["CLEAR"]:>4}')
        print(f'  LIKELY (top cited ≥1000, ratio 2–5×)          : {tier_counts["LIKELY"]:>4}')
        print(f'  WEAK   (top cited ≥1000, ratio <2×)           : {tier_counts["WEAK"]:>4}')
        print(f'  LOW    (top cited <1000)                       : {tier_counts["LOW"]:>4}')

        for label, rows in [
            ('CLEAR',  [r for r in fallback_rows if _fb_stats(r)[4] == 'CLEAR']),
            ('LIKELY', [r for r in fallback_rows if _fb_stats(r)[4] == 'LIKELY']),
            ('WEAK',   [r for r in fallback_rows if _fb_stats(r)[4] == 'WEAK']),
            ('LOW',    [r for r in fallback_rows if _fb_stats(r)[4] == 'LOW']),
        ]:
            if not rows:
                continue
            print(f'\n  ── {label} ({len(rows)}) ──')
            print(f'  {"HCR name":<30}  {"Cat":<10}   {"#":>2}  {"author_idx":>12}  {"works":>7}  {"cited":>9}  ratio')
            print(f'  {"─"*28}  {"─"*10}   {"─"*2}  {"─"*12}  {"─"*7}  {"─"*9}  ─────')
            for r in sorted(rows, key=lambda x: x['hcr_last']):
                name = f'{r["hcr_first"]} {r["hcr_last"]}'
                cands_sorted, top, top2, ratio, _ = _fb_stats(r)
                ratio_s = f'{ratio:5.1f}×' if ratio != float('inf') else ' only1'
                for rank, (aid, fn, fii) in enumerate(cands_sorted[:3]):
                    wc, cc = author_stats.get(int(aid), (0, 0))
                    prefix = f'  {name:<30}  {r["category"][:10]:<10}' if rank == 0 else f'  {"":30}  {"":10}'
                    rs = f'  {ratio_s}' if rank == 0 else ''
                    print(f'{prefix}   {rank+1:>2}  {aid:>12}  {wc:>7,}  {cc:>9,}{rs}')

    # ── uniquely disambiguated sample ─────────────────────────────────────────
    unique_rows = [r for r in has_cands if r['n_inst_cands'] == 1]
    if unique_rows:
        print(f'\n{"─"*100}')
        print(f'Uniquely disambiguated ({len(unique_rows)}) — name + institution → exactly 1 OA author:')
        print(f'{"─"*100}')
        print(f'  {"HCR name":<30}  {"Category":<26}  {"author_idx":>12}  {"works":>7}  {"cited":>9}')
        print(f'  {"─"*28}  {"─"*26}  {"─"*12}  {"─"*7}  {"─"*9}')
        for r in unique_rows[:40]:
            aid = int(r['inst_candidates'][0][0])
            wc, cc = author_stats.get(aid, (0, 0))
            print(f'  {r["hcr_first"]+" "+r["hcr_last"]:<30}  {r["category"]:<26}'
                  f'  {aid:>12}  {wc:>7,}  {cc:>9,}')

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

    # ── A2: search display_name_alternatives for A1 failures ─────────────────
    if failures:
        import duckdb as _duckdb
        print(f'\n{sep2}')
        print(f'A2 fallback — searching display_name_alternatives for {len(failures)} A1 failures:')
        print(sep2)
        con2 = _duckdb.connect()
        for r in sorted(failures, key=lambda x: (x['last_norm'], x['first_norm'])):
            fn = r['hcr_first'].replace("'", "''")
            ln = r['hcr_last'].replace("'", "''")
            hits = con2.execute(f"""
                SELECT author_idx, display_name, display_name_alternatives
                FROM parquet_scan('{authors_path}')
                WHERE list_contains(
                    list_transform(display_name_alternatives, x -> lower(trim(x))),
                    lower(trim('{fn} {ln}'))
                ) OR list_contains(
                    list_transform(display_name_alternatives, x -> lower(trim(x))),
                    lower(trim('{ln}, {fn}'))
                )
            """).fetchall()
            if hits:
                for h in hits:
                    print(f'  FOUND  {r["hcr_last"]}, {r["hcr_first"]}  →'
                          f'  author_idx={h[0]}  display_name={h[1]}')
                    print(f'         alternatives: {h[2]}')
            else:
                # Digraph-normalised fallback: unidecode + ae/oe/ue collapse on
                # both the HCR name and OA display_name (OA alternatives stay
                # in unicode so exact SQL can't match digraph spellings).
                def _col(s: str) -> str:
                    from unidecode import unidecode as _u
                    s = _u(s.lower())
                    return s.replace('ae', 'a').replace('oe', 'o').replace('ue', 'u').strip()

                fn_c = _col(r['hcr_first'])
                ln_c = _col(r['hcr_last'].split()[-1])  # last word, mirroring A1
                cands = con2.execute(f"""
                    SELECT author_idx, display_name, display_name_alternatives
                    FROM parquet_scan('{authors_path}')
                    WHERE works_count >= {args.works_min}
                """).df()
                cands['_ln'] = cands['display_name'].apply(
                    lambda dn: _col(dn.split()[-1]) if dn and dn.split() else '')
                cands['_fn'] = cands['display_name'].apply(
                    lambda dn: _col(dn.split()[0]) if dn and len(dn.split()) > 1 else '')
                matched = cands[
                    (cands['_ln'] == ln_c) &
                    (cands['_fn'].str.startswith(fn_c[:1]) if fn_c else True)
                ]
                if not matched.empty:
                    for _, mrow in matched.iterrows():
                        print(f'  FOUND* {r["hcr_last"]}, {r["hcr_first"]}  →'
                              f'  author_idx={mrow.author_idx}  display_name={mrow.display_name}'
                              f'  (digraph match)')
                else:
                    print(f'  MISS   {r["hcr_last"]}, {r["hcr_first"]}')
        con2.close()

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
