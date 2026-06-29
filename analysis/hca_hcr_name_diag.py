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

OA scan: authors of top-1% cited works per publication_year (2014–2024) in raw OA references.

Output:
  - Candidate-count histogram
  - Summary: n_found vs n_empty (A1 failures)
  - Printed list of A1-failure HCR names + their parsed components

Usage:
  .venv/bin/python analysis/hca_hcr_name_diag.py
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
from util import load_config, FIELD_NAMES

from name_match import _norm, _first_alpha

HCW_YEAR_LO = 2014
HCW_YEAR_HI = 2024


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
    first_norm = first + middle (if any) so middle initials can be compared.
    Passes the full name to nameparser so that Jr./III land in suffix and
    parenthetical nicknames land in nickname — both stripped from last/first.
    Takes the last WORD of hn.last so particles ("van der", "de la") are dropped,
    matching OA's last-token indexing convention.
    """
    hn = HumanName(_norm(f'{first} {last}'))
    first_part  = hn.first.strip(' .,')
    middle_part = hn.middle.strip(' .,')
    first_n = (first_part + (' ' + middle_part if middle_part else '')).strip()
    last_words = hn.last.strip(' .,').split()
    last_n = last_words[-1].strip('.,') if last_words else ''
    return last_n, first_n, _first_alpha(first_part)


def _parse_oax_display(display_name: str) -> tuple[str, str, str] | None:
    """
    Fast extraction for OA display_names — no nameparser, simple split.
    Returns (last_norm, first_norm, first_initial) or None if unparseable.
    first_norm = all tokens except the last (first + middle), unidecode+lower'd.
    fi = first alpha char of the first token only.
    """
    raw = _norm(display_name)
    tokens = raw.split()
    if len(tokens) < 2:
        return None
    last  = tokens[-1].strip('.,')
    first = ' '.join(t.strip('.,') for t in tokens[:-1])
    if not first or not last:
        return None
    fi = _first_alpha(tokens[0].strip('.,'))
    if not fi:
        return None
    return last, first, fi


def names_compatible(hcr_first: str, hcr_fi: str,
                     oax_first: str, oax_fi: str) -> bool:
    """
    True unless the match is clearly impossible.

    Rejects when:
      - first initials differ
      - both first words are full (≥4 alpha) but 3-char prefix disagrees
        (after Germanic umlaut normalisation)
      - both sides supply a middle component and their middle initials differ
    """
    if hcr_fi != oax_fi:
        return False
    hcr_words = hcr_first.split()
    oax_words = oax_first.split()
    hcr_w0 = hcr_words[0]
    oax_w0 = oax_words[0]
    hcr_full = _alpha_len(hcr_w0) >= 4
    oax_full = _alpha_len(oax_w0) >= 4
    if hcr_full and oax_full:
        if hcr_w0[:3] != oax_w0[:3]:
            if _de_umlaut(hcr_w0)[:3] != _de_umlaut(oax_w0)[:3]:
                return False
        if len(hcr_words) >= 2 and len(oax_words) >= 2:
            hcr_mid = _first_alpha(hcr_words[1])
            oax_mid = _first_alpha(oax_words[1])
            if hcr_mid and oax_mid and hcr_mid != oax_mid:
                return False
    return True


# ── OA author index ───────────────────────────────────────────────────────────

def build_oax_index(
    oa_root: Path,
    authors_path: str,
) -> tuple[dict[str, list], dict[int, set[int]], dict[int, tuple[int, int]]]:
    """
    Build OA author indexes from scratch using raw OA references + works + authorships.

    Pipeline:
      1. Count incoming citations per work (UNNEST cited_list from references parquet).
      2. Join with works parquet for publication_year; keep years 2014–2024.
      3. Per publication_year, retain top-1% most-cited works.
      4. Load all authorships for those works.
      5. Fetch display_name + cited_by_count from authors parquet for those author_idx values.

    Returns:
      name_idx       : {last_norm: [(author_idx, first_norm, first_initial), ...]}
      inst_to_authors: {institution_idx: set(author_idx)}
      author_stats   : {author_idx: (works_count, cited_by_count)}
    """
    import glob as _glob
    ref_glob  = str(oa_root / 'parquet' / 'references'  / '*.parquet')
    wk_glob   = str(oa_root / 'parquet' / 'works'       / '*.parquet')
    au_files  = sorted(_glob.glob(str(oa_root / 'parquet' / 'authorships' / '*.parquet')))
    bad_names = {'updated_date=2025-11-06_part_0070.parquet'}
    au_files  = [f for f in au_files if Path(f).name not in bad_names]
    au_list   = str(au_files).replace("'", '"')

    con = duckdb.connect()

    # ── step 1+2: citation counts per work, filtered to 2014–2024 ────────────
    print(f'Counting incoming citations per work ({HCW_YEAR_LO}–{HCW_YEAR_HI})...')
    t0 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _cited AS
        SELECT w.work_idx,
               w.publication_year,
               COUNT(*) AS n_ref
        FROM (
            SELECT UNNEST(cited_list) AS cited_idx
            FROM read_parquet('{ref_glob}')
        ) r
        JOIN read_parquet('{wk_glob}') w ON r.cited_idx = w.work_idx
        WHERE w.publication_year BETWEEN {HCW_YEAR_LO} AND {HCW_YEAR_HI}
        GROUP BY w.work_idx, w.publication_year
    """)
    n_cited = con.execute("SELECT COUNT(*) FROM _cited").fetchone()[0]  # type: ignore[index]
    print(f'  {n_cited:,} cited works in {HCW_YEAR_LO}–{HCW_YEAR_HI}  [{time.time()-t0:.1f}s]')

    # ── step 3: top 1% per publication_year ──────────────────────────────────
    print('Selecting top 1% per year...')
    t1 = time.time()
    con.execute("""
        CREATE TEMP TABLE _hcw AS
        SELECT work_idx
        FROM (
            SELECT work_idx,
                   publication_year,
                   n_ref,
                   QUANTILE_CONT(n_ref, 0.99)
                       OVER (PARTITION BY publication_year) AS p99
            FROM _cited
        )
        WHERE n_ref >= p99
    """)
    n_hcw = con.execute("SELECT COUNT(*) FROM _hcw").fetchone()[0]  # type: ignore[index]
    print(f'  {n_hcw:,} top-1% cited works  [{time.time()-t1:.1f}s]')

    # ── step 4: authorships for those works ───────────────────────────────────
    print('Loading authorships for top-1% works (≥2 distinct HCW per author)...')
    t2 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _qual_authors AS
        SELECT a.author_idx
        FROM read_parquet({au_list}) a
        JOIN _hcw h ON a.work_idx = h.work_idx
        WHERE a.author_idx IS NOT NULL
        GROUP BY a.author_idx
        HAVING COUNT(DISTINCT a.work_idx) >= 2
    """)
    n_authors = con.execute("SELECT COUNT(*) FROM _qual_authors").fetchone()[0]  # type: ignore[index]
    au_df = con.execute(f"""
        SELECT DISTINCT a.author_idx, a.institution_idx
        FROM read_parquet({au_list}) a
        JOIN _qual_authors q ON a.author_idx = q.author_idx
        WHERE a.institution_idx IS NOT NULL
    """).df()
    print(f'  {n_authors:,} distinct authors with ≥2 HCW  '
          f'| {len(au_df):,} (author_idx, institution_idx) pairs  [{time.time()-t2:.1f}s]')

    # ── step 5: names + stats from authors parquet ────────────────────────────
    print('Fetching display names and citation stats...')
    t3 = time.time()
    name_df = con.execute(f"""
        SELECT a.author_idx, a.display_name, a.works_count, a.cited_by_count
        FROM parquet_scan('{authors_path}') a
        JOIN _qual_authors q ON a.author_idx = q.author_idx
        WHERE a.display_name IS NOT NULL
          AND length(trim(a.display_name)) > 2
    """).df()
    con.close()
    print(f'  {len(name_df):,} (author_idx, name) pairs  [{time.time()-t3:.1f}s]')

    # ── build Python indexes ──────────────────────────────────────────────────
    print('Building indexes...')
    t0 = time.time()
    idx: dict[str, list]          = defaultdict(list)
    author_stats: dict[int, tuple] = {}
    n_skipped = 0
    for row in name_df.itertuples(index=False):
        parsed = _parse_oax_display(row.display_name)
        if parsed is None:
            n_skipped += 1
            continue
        last_n, first_n, fi = parsed
        aid = int(row.author_idx)
        idx[last_n].append((aid, first_n, fi))
        author_stats[aid] = (
            int(row.works_count)    if row.works_count    is not None else 0,
            int(row.cited_by_count) if row.cited_by_count is not None else 0,
        )

    inst_to_authors: dict[int, set[int]] = defaultdict(set)
    for row in au_df.itertuples(index=False):
        if pd.notna(row.institution_idx) and pd.notna(row.author_idx):
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
    argparse.ArgumentParser().parse_args()

    paths        = load_config()
    authors_path = str(paths.openalex / 'parquet' / 'authors' / '*.parquet')
    hcr_path     = Path(__file__).parent.parent / 'data' / '2025_HCR.xlsx'

    if not hcr_path.exists():
        print(f'ERROR: {hcr_path} not found'); sys.exit(1)

    # ── load HCR ─────────────────────────────────────────────────────────────
    print('Loading HCR...')
    hcr = pd.read_excel(hcr_path)
    # Data corrections for spreadsheet errors
    mask = (hcr['First Name'] == 'Ann C. Mc') & (hcr['Last Name'] == 'Kee')
    hcr.loc[mask, 'First Name'] = 'Ann C.'
    hcr.loc[mask, 'Last Name']  = 'McKee'
    # George R. Thompson III: suffix landed in Last Name column
    mask = (hcr['First Name'] == 'George R. Thompson') & (hcr['Last Name'] == 'III')
    hcr.loc[mask, 'First Name'] = 'George R.'
    hcr.loc[mask, 'Last Name']  = 'Thompson'
    # Ryan Teuling: OA display_name "Adriaan J. Teuling" (legal first name)
    mask = (hcr['First Name'] == 'Ryan') & (hcr['Last Name'] == 'Teuling')
    hcr.loc[mask, 'First Name'] = 'Adriaan'
    # First-name corrections: spreadsheet stores initials before preferred name,
    # causing first-initial mismatch with OA display_name.
    _FIRST_FIXES = {
        'F. D. Richard':  'Richard',   # Hobbs: OA "Richard Hobbs", fi r not f
        'R. M. Pasco':    'Pasco',     # Fearon: OA "Pasco Fearon", fi p not r
        'Konstantin S.':  'Kostya S.', # Novoselov: OA display_name "Kostya S. Novoselov"
        'Fraser':         'J. Fraser', # Stoddart: OA "J. Fraser Stoddart", fi j not f
        'Geoffrey Qiping':'Qiping',    # Shen: OA "Qiping Shen", fi q not g
        'Ai-Guo':         'Aiguo',     # Wu: OA "Aiguo Wu"; hyphen at pos 2 breaks prefix check
    }
    hcr['First Name'] = hcr['First Name'].replace(_FIRST_FIXES)
    # German umlaut digraph corrections: HCR uses oe/ae/ue, OA stores the actual umlaut.
    # Only the specific names confirmed to have umlaut-form OA display_names.
    # Compound surname corrections: HCR splits what OA stores as one word.
    _SURNAME_FIXES = {
        'Vander Weele': 'VanderWeele',  # OA "Tyler J. VanderWeele"
    }
    hcr['Last Name'] = hcr['Last Name'].replace(_SURNAME_FIXES)
    _UMLAUT_FIXES = {
        'Tuereci':          'Türeci',
        'Poertner':         'Pörtner',
        'Baernighausen':    'Bärnighausen',
        'Bruening':         'Brüning',
        'Humpenoeder':      'Humpenöder',
        'Floerke':          'Flörke',
        'Mueller-Buschbaum':'Müller-Buschbaum',
        'Goetschi':         'Götschi',
    }
    hcr['Last Name'] = hcr['Last Name'].replace(_UMLAUT_FIXES)
    print(f'  {len(hcr):,} HCR entries')

    # ── build OA index ────────────────────────────────────────────────────────
    oax_idx, inst_to_authors, author_stats = build_oax_index(paths.openalex, authors_path)

    # ── load HCR institution resolutions ─────────────────────────────────────
    data_dir = Path(__file__).parent.parent / 'data'
    print('\nLoading HCR institution resolutions...')
    hcr_inst_map = load_hcr_inst_map(data_dir)

    # ── lookup each HCR ───────────────────────────────────────────────────────
    # Disambiguation rule (applied uniformly to every HCR person):
    #   1. Name match → candidates
    #   2. Institution soft filter: if any candidate has published from a resolved
    #      institution, restrict pool to those; otherwise keep all name candidates.
    #      Institution is a soft signal only — it never eliminates the whole pool.
    #   3. Pick best = top of pool by cited_by_count.
    #   Confidence = ratio of cited[best] / cited[2nd]; for HCR researchers the
    #   correct OA record dominates by citations.
    print('\nLooking up each HCR...')
    results = []
    for _, row in hcr.iterrows():
        first_raw = str(row.get('First Name', '') or '').strip()
        last_raw  = str(row.get('Last Name',  '') or '').strip()
        cat_raw   = str(row.get('Category',   '') or '').strip()
        last_n, first_n, fi = _parse_hcr_name(first_raw, last_raw)

        candidates = find_candidates(last_n, first_n, fi, oax_idx) if fi else []

        # soft institution filter
        inst_key  = f'{first_raw}|||{last_raw}|||{cat_raw}'
        inst_idxs = hcr_inst_map.get(inst_key, [])
        inst_hit  = False
        if inst_idxs and candidates:
            inst_author_set: set[int] = set()
            for iidx in inst_idxs:
                inst_author_set.update(inst_to_authors.get(iidx, set()))
            inst_pool = [(aid, fn, fii) for aid, fn, fii in candidates
                         if int(aid) in inst_author_set]
            if inst_pool:
                candidates = inst_pool
                inst_hit   = True

        # pick best by cited_by_count
        pool_sorted = sorted(
            candidates,
            key=lambda c: author_stats.get(int(c[0]), (0, 0))[1],
            reverse=True,
        )
        best_aid  = int(pool_sorted[0][0]) if pool_sorted else None
        top_cc    = author_stats.get(best_aid, (0, 0))[1] if best_aid else 0
        top_wc    = author_stats.get(best_aid, (0, 0))[0] if best_aid else 0
        sec_cc    = author_stats.get(int(pool_sorted[1][0]), (0, 0))[1] if len(pool_sorted) > 1 else 0
        ratio     = (top_cc / sec_cc) if sec_cc > 0 else float('inf')

        results.append({
            'hcr_first':   first_raw,
            'hcr_last':    last_raw,
            'category':    cat_raw,
            'affil':       str(row.get('Primary Affiliation', '') or ''),
            'last_norm':   last_n,
            'first_norm':  first_n,
            'first_initial': fi,
            'inst_idxs':   inst_idxs,
            'inst_hit':    inst_hit,
            'n_candidates': len(pool_sorted),
            'pool':        pool_sorted,
            'best_aid':    best_aid,
            'top_cc':      top_cc,
            'top_wc':      top_wc,
            'sec_cc':      sec_cc,
            'ratio':       ratio,
            'a2_hit':      False,
        })

    # ── summary ───────────────────────────────────────────────────────────────
    sep  = '─' * 100
    sep2 = '═' * 100
    n_total  = len(results)
    n_empty  = sum(1 for r in results if r['n_candidates'] == 0)
    n_found  = n_total - n_empty


    # ── A2: recover A1 failures via display_name_alternatives ───────────────
    # Run silently; update best_aid in-place. Only unrecovered cases appear
    # in the final MISS list.
    def _col(s: str) -> str:
        s = unidecode(s.lower())
        return s.replace('ae', 'a').replace('oe', 'o').replace('ue', 'u').strip()

    failures = [r for r in results if r['n_candidates'] == 0]
    if failures:
        import duckdb as _duckdb
        con2 = _duckdb.connect()
        # pre-load all qualified authors for digraph fallback (done once)
        # restrict digraph fallback to same pool as main index (top-1% cited work authors)
        _pool_aids = list(author_stats.keys())
        con2.execute("CREATE TEMP TABLE _pool AS SELECT UNNEST($1) AS author_idx", [_pool_aids])
        _digraph_df = con2.execute(f"""
            SELECT a.author_idx, a.display_name, a.works_count, a.cited_by_count
            FROM parquet_scan('{authors_path}') a
            JOIN _pool p ON a.author_idx = p.author_idx
            WHERE a.display_name IS NOT NULL
        """).df()
        _digraph_df['_ln'] = _digraph_df['display_name'].apply(
            lambda dn: _col(dn.split()[-1]) if dn and dn.split() else '')
        _digraph_df['_fn'] = _digraph_df['display_name'].apply(
            lambda dn: _col(dn.split()[0]) if dn and len(dn.split()) > 1 else '')

        for r in failures:
            fn = r['hcr_first'].replace("'", "''")
            ln = r['hcr_last'].replace("'", "''")
            hits = con2.execute(f"""
                SELECT author_idx, works_count, cited_by_count
                FROM parquet_scan('{authors_path}')
                WHERE list_contains(
                    list_transform(display_name_alternatives, x -> lower(trim(x))),
                    lower(trim('{fn} {ln}'))
                ) OR list_contains(
                    list_transform(display_name_alternatives, x -> lower(trim(x))),
                    lower(trim('{ln}, {fn}'))
                )
            """).fetchall()
            if not hits:
                # digraph fallback
                fn_c = _col(r['hcr_first'])
                ln_c = _col(r['hcr_last'].split()[-1])
                matched = _digraph_df[
                    (_digraph_df['_ln'] == ln_c) &
                    (_digraph_df['_fn'].str.startswith(fn_c[:1]) if fn_c else True)
                ]
                if not matched.empty:
                    hits = list(matched[['author_idx', 'works_count', 'cited_by_count']].itertuples(index=False))
            if hits:
                best = max(hits, key=lambda h: (h[2] or 0))
                r['best_aid'] = int(best[0])
                r['top_wc']   = int(best[1] or 0)
                r['top_cc']   = int(best[2] or 0)
                r['sec_cc']   = sorted((h[2] or 0) for h in hits)[-2] if len(hits) > 1 else 0
                r['ratio']    = (r['top_cc'] / r['sec_cc']) if r['sec_cc'] > 0 else float('inf')
                r['n_candidates'] = len(hits)
                r['a2_hit']   = True
        con2.close()

    # ── final confidence assignment ───────────────────────────────────────────
    def _conf(r) -> str:
        if r['best_aid'] is None:
            return 'MISS'
        cc, ratio = r['top_cc'], r['ratio']
        if cc >= 1000 and (r['n_candidates'] == 1 or ratio >= 5):
            return 'CLEAR'
        if cc >= 1000 and ratio >= 2:
            return 'LIKELY'
        if cc >= 1000:
            return 'WEAK'
        return 'LOW'

    from collections import Counter
    conf_counts  = Counter(_conf(r) for r in results)
    n_inst_hit   = sum(1 for r in results if r['inst_hit'])
    n_a2_hit     = sum(1 for r in results if r.get('a2_hit'))

    # ── summary ───────────────────────────────────────────────────────────────
    print(f'\n{sep2}')
    print('Disambiguation summary')
    print(sep2)
    print(f'  Total HCR                                 : {n_total:,}')
    print(f'  Institution filter hit (soft filter)      : {n_inst_hit:,}  ({n_inst_hit/n_total*100:.1f}%)')
    print(f'  A2 recovery (display_name_alternatives)   : {n_a2_hit:,}')
    print()
    print(f'  CLEAR  (cited ≥1000, ratio ≥5× or unique) : {conf_counts["CLEAR"]:>5,}  ({conf_counts["CLEAR"]/n_total*100:.1f}%)')
    print(f'  LIKELY (cited ≥1000, ratio 2–5×)          : {conf_counts["LIKELY"]:>5,}  ({conf_counts["LIKELY"]/n_total*100:.1f}%)')
    print(f'  WEAK   (cited ≥1000, ratio <2×)           : {conf_counts["WEAK"]:>5,}  ({conf_counts["WEAK"]/n_total*100:.1f}%)')
    print(f'  LOW    (cited <1000)                      : {conf_counts["LOW"]:>5,}  ({conf_counts["LOW"]/n_total*100:.1f}%)')
    print(f'  MISS   (no OA match found)                : {conf_counts["MISS"]:>5,}  ({conf_counts["MISS"]/n_total*100:.1f}%)')

    # ── WEAK bottom 8 (lowest cited_by_count) ────────────────────────────────
    weak_rows = [r for r in results if _conf(r) == 'WEAK']
    weak_bottom = sorted(weak_rows, key=lambda x: x['top_cc'])[:8]
    print(f'\n{sep2}')
    print(f'WEAK bottom 8 by cited_by_count (of {len(weak_rows):,} total):')
    print(sep2)
    print(f'  {"HCR name":<30}  {"Category":<28}  {"works":>7}  {"cited":>9}  ratio')
    print(sep)
    for r in weak_bottom:
        ratio_s = f'{r["ratio"]:4.1f}×' if r["ratio"] != float('inf') else 'only1'
        print(f'  {r["hcr_first"]+" "+r["hcr_last"]:<30}  {r["category"]:<28}'
              f'  {r["top_wc"]:>7,}  {r["top_cc"]:>9,}  {ratio_s}')

    # ── LOW list ─────────────────────────────────────────────────────────────
    low_rows = [r for r in results if _conf(r) == 'LOW']
    if low_rows:
        print(f'\n{sep2}')
        print(f'LOW ({len(low_rows)}) — best candidate has cited_by_count < 1000 (likely wrong match or sparse OA record):')
        print(sep2)
        print(f'  {"HCR name":<30}  {"Category":<28}  {"author_idx":>12}  {"works":>7}  {"cited":>9}  ratio  Primary Affiliation')
        print(sep)
        for r in sorted(low_rows, key=lambda x: (-x['top_cc'], x['hcr_last'])):
            ratio_s = f'{r["ratio"]:4.1f}×' if r['ratio'] != float('inf') else 'only1'
            print(f'  {r["hcr_first"]+" "+r["hcr_last"]:<30}  {r["category"]:<28}'
                  f'  {r["best_aid"]:>12}  {r["top_wc"]:>7,}  {r["top_cc"]:>9,}'
                  f'  {ratio_s}  {r["affil"]}')

    # ── MISS list (only truly unresolved) ─────────────────────────────────────
    misses = [r for r in results if _conf(r) == 'MISS']
    if misses:
        print(f'\n{sep2}')
        print(f'MISS ({len(misses)}) — no OA author found at any stage:')
        print(sep2)
        print(f'  {"First Name":<22}  {"Last Name":<22}  {"Category":<28}  Primary Affiliation')
        print(sep)
        for r in sorted(misses, key=lambda x: (x['last_norm'], x['first_norm'])):
            print(f'  {r["hcr_first"]:<22}  {r["hcr_last"]:<22}  {r["category"]:<28}  {r["affil"]}')
    else:
        print(f'\n  All {n_total:,} HCR persons matched to an OA author.')

    # ── field lookup + cross-check ────────────────────────────────────────────
    CATEGORY_FIELDS: dict[str, set[int]] = {
        'Agricultural Sciences':          {11, 27},
        'Biology and Biochemistry':       {13, 24},
        'Chemistry':                      {16},
        'Clinical Medicine':              {27, 29, 35, 36},
        'Computer Science':               {17},
        'Economics and Business':         {14, 20},
        'Engineering':                    {22, 15},
        'Environment and Ecology':        {23, 19},
        'Geosciences':                    {19},
        'Immunology':                     {24, 13},
        'Materials Science':              {25},
        'Mathematics':                    {26},
        'Microbiology':                   {24},
        'Molecular Biology and Genetics': {13},
        'Neuroscience and Behavior':      {28},
        'Pharmacology and Toxicology':    {30, 27},
        'Physics':                        {31},
        'Plant and Animal Science':       {11, 34},
        'Psychiatry and Psychology':      {32},
        'Social Sciences':                {33},
        'Space Science':                  {31, 19},
        'Cross-Field':                    set(),   # checked separately: n_fields >= 2
    }

    matched_aids = [r['best_aid'] for r in results if r['best_aid'] is not None]
    print(f'\nLooking up OA fields for {len(matched_aids):,} matched authors...')
    t0 = time.time()
    fw_path = str(next(paths.working.glob('flat_works_*.parquet')))
    import glob as _glob
    au_files = sorted(_glob.glob(str(paths.openalex / 'parquet' / 'authorships' / '*.parquet')))
    bad_names = {'updated_date=2025-11-06_part_0070.parquet'}
    au_files  = [f for f in au_files if Path(f).name not in bad_names]
    au_list   = str(au_files).replace("'", '"')

    FIELD_MIN_WT = 2.0   # min weighted works in a field to count as active

    con3 = duckdb.connect()
    con3.execute("CREATE TEMP TABLE _matched AS SELECT UNNEST($1) AS author_idx", [matched_aids])
    field_df = con3.execute(f"""
        WITH fw_dedup AS (
            SELECT DISTINCT work_idx, field_idx, field_weight
            FROM read_parquet('{fw_path}')
            WHERE publication_year BETWEEN {HCW_YEAR_LO} AND {HCW_YEAR_HI}
        ),
        fw_norm AS (
            SELECT work_idx, field_idx,
                   field_weight / SUM(field_weight) OVER (PARTITION BY work_idx) AS norm_wt
            FROM fw_dedup
        )
        SELECT a.author_idx, fn.field_idx,
               SUM(fn.norm_wt) AS field_wt
        FROM read_parquet({au_list}) a
        JOIN _matched m ON a.author_idx = m.author_idx
        JOIN fw_norm fn ON a.work_idx = fn.work_idx
        WHERE a.author_idx IS NOT NULL
        GROUP BY a.author_idx, fn.field_idx
    """).df()
    con3.close()
    print(f'  {len(field_df):,} (author_idx, field_idx) rows  [{time.time()-t0:.1f}s]')

    # author_fields: {author_idx: {field_idx: field_wt}}
    author_fields: dict[int, dict[int, float]] = {}
    for row in field_df.itertuples(index=False):
        aid = int(row.author_idx)
        author_fields.setdefault(aid, {})[int(row.field_idx)] = float(row.field_wt)

    for r in results:
        aid      = r['best_aid']
        fw_dict  = author_fields.get(aid, {}) if aid else {}
        active   = {fid for fid, wt in fw_dict.items() if wt >= FIELD_MIN_WT}
        expected = CATEGORY_FIELDS.get(r['category'], set())
        cat      = r['category']
        if cat == 'Cross-Field':
            field_match = len(active) >= 2
        else:
            field_match = bool(active & expected)
        r['oax_fields']       = fw_dict          # {field_idx: field_wt}
        r['n_oax_fields']     = len(active)
        r['n_expected_match'] = len(active & expected)
        r['field_match']      = field_match

    n_field_match = sum(1 for r in results if r.get('field_match'))
    n_nomatch     = sum(1 for r in results if not r.get('field_match') and r['best_aid'])
    print(f'  field_match=True : {n_field_match:,}  |  False : {n_nomatch:,}')

    # ── write mapping parquet ─────────────────────────────────────────────────
    import json as _json
    out_rows = []
    for r in results:
        out_rows.append({
            'hcr_first':       r['hcr_first'],
            'hcr_last':        r['hcr_last'],
            'category':        r['category'],
            'affil':           r['affil'],
            'author_idx':      r['best_aid'],
            'confidence':      _conf(r),
            'top_cc':          r['top_cc'],
            'top_wc':          r['top_wc'],
            'ratio':           r['ratio'] if r['ratio'] != float('inf') else None,
            'inst_hit':        r['inst_hit'],
            'a2_hit':          r.get('a2_hit', False),
            'oax_fields':      _json.dumps({str(k): round(v, 2) for k, v in sorted(r.get('oax_fields', {}).items(), key=lambda x: -x[1])}),
            'top_field_name':  FIELD_NAMES.get(max(r.get('oax_fields', {}).items(), key=lambda x: x[1])[0], '') if r.get('oax_fields') else '',
            'category_fields': ', '.join(FIELD_NAMES.get(fid, str(fid)) for fid in sorted(CATEGORY_FIELDS.get(r['category'], set()))),
            'n_oax_fields':    r.get('n_oax_fields', 0),
            'n_expected_match':r.get('n_expected_match', 0),
            'field_match':     r.get('field_match', False),
        })
    out_df  = pd.DataFrame(out_rows)
    out_path = paths.working / 'hcr_oax_map.parquet'
    out_df.to_parquet(out_path, index=False)
    print(f'\nWrote {out_path.name}: {len(out_df):,} rows')


if __name__ == '__main__':
    main()
