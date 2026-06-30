"""
hcr_match.py — Clarivate HCR 2025 name cleaning and person clustering.

Pipeline:
  Phase 1a  Load HCR, apply corrections, normalise names;
            collapse certain name duplicates; assign cluster_hash + person_hash
  Phase 1b  Report: successes and name-parse failures

  Phase 2   Bring in category and affiliation data
  Phase 2a  Split clusters by distinct affiliation; preserve provenance;
            assign prominence flag; rehash sub-clusters
  Phase 2b  Report: remaining multi-name clusters (successes and failures)

Usage:
  .venv/bin/python analysis/hcr_match.py
"""

from __future__ import annotations

import sys
import re
import unicodedata
from pathlib import Path

import pandas as pd
from nameparser import HumanName

sys.path.insert(0, str(Path(__file__).parent.parent))

from util.name_util import clean_name, norm, last_norm, fi
from analysis.name_cluster import sha, build_name_index, assign_name_cluster_hashes


# ── HCR-specific hashes ───────────────────────────────────────────────────────

def person_hash(first: str, last: str, affil: str) -> str:
    return sha([first, last, affil])

def cluster_hash(ln: str, f: str) -> str:
    return sha([ln, f])


# ── HCR load corrections ──────────────────────────────────────────────────────

_GEN_SUFFIXES = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv', 'v'}

_POSTNOMINALS = re.compile(
    r'\s+(FBA|FFASL|FAA|FRS|FRSE|FRSC|FRSB|FMEDSCI|CBE|OBE|MBE|DBE|KBE|AO|AC|AM'
    r'|FRCPE|FRCP|FRCS|FRACP|FRACS|FRCPA|FACSS|FAAS|FAPS|FASSA'
    r'|PHD|MD|DPHIL|DSC|ESQ)(\s+.*)?$',
    re.IGNORECASE,
)

def _strip_controls(s: str) -> str:
    return ''.join(c for c in s if unicodedata.category(c) not in ('Cf', 'Cc'))


def _apply_corrections(hcr: pd.DataFrame) -> pd.DataFrame:
    hcr = hcr.copy()

    for col in ('First Name', 'Last Name', 'Primary Affiliation'):
        hcr[col] = hcr[col].fillna('').apply(_strip_controls).str.strip()

    # spreadsheet split errors
    mask = (hcr['First Name'] == 'Ann C. Mc') & (hcr['Last Name'] == 'Kee')
    hcr.loc[mask, ['First Name', 'Last Name']] = ['Ann C.', 'McKee']
    mask = (hcr['First Name'] == 'George R. Thompson') & (hcr['Last Name'] == 'III')
    hcr.loc[mask, ['First Name', 'Last Name']] = ['George R.', 'Thompson']
    # name-field swaps
    mask = (hcr['Last Name'] == 'Alejandra Tortorici') & (hcr['First Name'] == 'M.')
    hcr.loc[mask, ['First Name', 'Last Name']] = ['M. Alejandra', 'Tortorici']
    mask = (hcr['Last Name'] == 'Ping Loh') & (hcr['First Name'] == 'Kian')
    hcr.loc[mask, ['First Name', 'Last Name']] = ['Kian Ping', 'Loh']
    mask = (hcr['Last Name'] == 'Quang Minh') & (hcr['First Name'] == 'Bui')
    hcr.loc[mask, ['First Name', 'Last Name']] = ['Bui Quang', 'Minh']
    mask = (hcr['First Name'] == 'David Autor') & (hcr['Last Name'] == 'David Autor')
    hcr.loc[mask, ['First Name', 'Last Name']] = ['David', 'Autor']

    # strip postnominals from Last Name
    hcr['Last Name'] = hcr['Last Name'].apply(
        lambda s: _POSTNOMINALS.sub('', s).strip()
    )

    # generational suffixes in Last Name: "Smith Jr." → "Smith"
    def _fix_gen_suffix(row):
        words = row['Last Name'].split()
        if len(words) >= 2 and words[-1].lower().rstrip('.') in _GEN_SUFFIXES:
            row = row.copy()
            row['Last Name'] = ' '.join(words[:-1])
        return row
    hcr = hcr.apply(_fix_gen_suffix, axis=1)

    # normalise all-caps last names
    hcr['Last Name'] = hcr['Last Name'].apply(
        lambda s: s.title() if s.isupper() and len(s) > 2 else s
    )

    return hcr


# ── Phase 1a: load ────────────────────────────────────────────────────────────

def load_hcr(path: Path) -> tuple[pd.DataFrame, dict[int, HumanName]]:
    hcr = pd.read_excel(path)
    hcr = _apply_corrections(hcr)
    hcr = hcr.rename(columns={
        'First Name':             'first_name',
        'Last Name':              'last_name',
        'Category':               'category',
        'Primary Affiliation':    'affil',
        'Secondary Affiliations': 'affil2',
    })
    hcr['first_name'] = hcr['first_name'].fillna('').str.strip()
    hcr['last_name']  = hcr['last_name'].fillna('').str.strip()
    hcr['affil']      = hcr['affil'].fillna('').str.strip()
    hcr['category']   = hcr['category'].fillna('').str.strip()

    hcr['last_norm']    = hcr['last_name'].apply(last_norm)
    hcr['fi']           = hcr['first_name'].apply(fi)
    hcr['cluster_hash'] = hcr.apply(lambda r: cluster_hash(r['last_norm'], r['fi']), axis=1)
    hcr['person_hash']  = hcr.apply(lambda r: person_hash(r['first_name'], r['last_name'], r['affil']), axis=1)

    row_hn: dict[int, HumanName] = {
        idx: HumanName(clean_name(f"{fn} {ln}"))
        for idx, fn, ln in zip(hcr.index.tolist(), hcr['first_name'], hcr['last_name'])
    }
    return hcr, row_hn


# ── Phase 2a: collapse same-person multi-category rows ────────────────────────

def collapse_persons(hcr: pd.DataFrame) -> pd.DataFrame:
    """
    One row per unique person_hash.
    Verbatim (first_name, last_name, affil) defines identity.
    Categories collected into a list.
    """
    rows = []
    for phash, grp in hcr.groupby('person_hash', sort=False):
        r = grp.iloc[0]
        rows.append({
            'person_hash':  phash,
            'cluster_hash': r['cluster_hash'],
            'first_name':   r['first_name'],
            'last_name':    r['last_name'],
            'affil':        r['affil'],
            'last_norm':    r['last_norm'],
            'fi':           r['fi'],
            'categories':   sorted(grp['category'].tolist()),
            'n_categories': len(grp),
        })
    return pd.DataFrame(rows)


def split_clusters_by_affil(persons: pd.DataFrame) -> pd.DataFrame:
    """
    Within each cluster_hash, split by distinct affiliation.
    multi_category = True when name form appears in >1 HCR category.
    """
    persons = persons.copy()
    persons['multi_category'] = persons['n_categories'] > 1
    persons['sub_cluster_hash'] = persons.apply(
        lambda r: sha([r['cluster_hash'], norm(r['affil'])]), axis=1
    )
    return persons


# ── Phase 1b: name parse diagnostic ──────────────────────────────────────────

def name_parse_diag(hcr: pd.DataFrame, row_hn: dict[int, HumanName]) -> None:
    """
    Flag rows where HumanName found a title or suffix, or where hn.last
    doesn't match the corrected last name.
    """
    flags = []
    for idx, fn, ln in zip(hcr.index.tolist(), hcr['first_name'], hcr['last_name']):
        hn = row_hn[idx]
        issues = []
        if hn.title:  issues.append(f"title={hn.title!r}")
        if hn.suffix: issues.append(f"suffix={hn.suffix!r}")
        if norm(hn.last) != norm(ln):
            issues.append(f"hn.last={hn.last!r} ≠ {ln!r}")
        if issues:
            flags.append((idx, fn, ln, hn, ', '.join(issues)))

    print(f"\nName parse flags: {len(flags)} rows\n")
    print(f"  {'#':<6} {'FIRST':<22} {'LAST':<22} {'HN.FIRST':<14} {'HN.MID':<10} {'ISSUES'}")
    print('  ' + '-' * 110)
    for idx, fn, ln, hn, issues in flags:
        print(f"  {idx:<6} {fn:<22} {ln:<22} {hn.first:<14} {hn.middle:<10} {issues}")


# ── Phase 2 Step 1: multi-category breakdown ─────────────────────────────────

def _print_category_breakdown(persons: pd.DataFrame) -> None:
    multi  = persons[persons['n_categories'] >= 2]
    has_cf = multi['categories'].apply(lambda c: 'Cross-Field' in c)
    cf     = multi[has_cf].copy()
    no_cf  = multi[~has_cf].copy()

    print(f'  Multi-category breakdown ({len(multi):,} entries, {(multi["n_categories"] - 1).sum():,} extra rows):')

    cf_n_others = [len([x for x in cats if x != 'Cross-Field']) for cats in cf['categories']]
    cf_dist: dict[int, int] = {}
    for n in cf_n_others:
        cf_dist[n] = cf_dist.get(n, 0) + 1
    print(f'\n  With Cross-Field ({len(cf)}):')
    for n, count in sorted(cf_dist.items()):
        print(f'    Cross-Field + {n} other field{"s" if n > 1 else ""} : {count}')
    for _, r in cf.sort_values('last_name').iterrows():
        others = [c for c in r['categories'] if c != 'Cross-Field']
        print(f'      {r["first_name"]} {r["last_name"]}  —  {", ".join(others)}')

    no_cf_dist = no_cf['n_categories'].value_counts().sort_index()
    print(f'\n  Without Cross-Field ({len(no_cf)}):')
    for n, count in no_cf_dist.items():
        print(f'    {n} specific fields : {count}')

    two_no_cf = no_cf[no_cf['n_categories'] == 2].sample(n=4, random_state=42)
    print(f'\n    2-field sample (4 of {(no_cf["n_categories"] == 2).sum()}):')
    for _, r in two_no_cf.iterrows():
        print(f'      {r["first_name"]} {r["last_name"]}  —  {", ".join(r["categories"])}')

    for n in sorted(no_cf['n_categories'].unique()):
        if n == 2:
            continue
        grp = no_cf[no_cf['n_categories'] == n].sort_values('last_name')
        print(f'\n    {n}-field entries ({len(grp)}):')
        for _, r in grp.iterrows():
            print(f'      {r["first_name"]} {r["last_name"]}  —  {", ".join(r["categories"])}')


# ── Phase 2b: remaining multi-name clusters ───────────────────────────────────

def _print_remaining_multi_name_clusters(persons: pd.DataFrame) -> None:
    """Truly unresolved sub-clusters: same name pattern AND same affiliation."""
    sep = '-' * 130
    sub_counts   = persons.groupby('sub_cluster_hash')['person_hash'].count()
    multi_hashes = sub_counts[sub_counts > 1].index
    multi = persons[persons['sub_cluster_hash'].isin(multi_hashes)].sort_values(
        ['sub_cluster_hash', 'last_norm', 'first_name']
    )
    print(f"\nUnresolved sub-clusters (same name pattern AND same affiliation): {len(multi_hashes):,}")
    print(f"Name forms: {len(multi):,}\n")

    hdr  = f"  {'FIRST NAME':<24} {'LAST NAME':<18} {'*':<3} CATEGORIES"
    prev = None
    for _, r in multi.iterrows():
        ch = r['sub_cluster_hash']
        if ch != prev:
            if prev is not None:
                print()
            print(f"sub-cluster {ch}  [{r['last_norm']}|{r['fi']}]")
            print(f"  affil: {r['affil']}")
            print(sep)
            print(hdr)
            prev = ch
        cats = ', '.join(r['categories'])
        prom = '*' if r['multi_category'] else ''
        print(f"  {r['first_name']:<24} {r['last_name']:<18} {prom:<3} {cats}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    hcr_path = Path('data/2025_HCR.xlsx')

    print('Phase 1a: loading HCR...')
    hcr, row_hn = load_hcr(hcr_path)
    print(f'  {len(hcr):,} rows loaded')

    print('\nPhase 1b: name parse diagnostic...')
    name_parse_diag(hcr, row_hn)

    print('\nPhase 1b continued: partitioning name forms and assigning cluster keys...')
    name_idx = build_name_index(row_hn)
    hcr, unresolved_name = assign_name_cluster_hashes(hcr, name_idx)
    print(f'  {len(name_idx):,} distinct family names')
    print(f'  {len(unresolved_name):,} unresolved name clusters (2+ rows, non-identical first names)\n')
    for family, cluster in sorted(unresolved_name, key=lambda x: x[0]):
        print(f"{family}")
        for row_idx, hn in sorted(cluster.items()):
            print(f"  {row_idx:5d}  {hn.first} {hn.middle}")
        print()

    persons = collapse_persons(hcr)
    n_collapsed = len(hcr) - len(persons)

    cluster_counts   = persons.groupby('cluster_hash')['person_hash'].count()
    n_single_cluster = (cluster_counts == 1).sum()
    n_in_multi       = int(len(persons) - n_single_cluster)

    persons = split_clusters_by_affil(persons)

    sub_counts       = persons.groupby('sub_cluster_hash')['person_hash'].count()
    n_sub_multi      = int((sub_counts > 1).sum())
    orig_multi_mask  = persons['cluster_hash'].isin(cluster_counts[cluster_counts > 1].index)
    sub_of_multi     = persons.loc[orig_multi_mask, 'sub_cluster_hash'].map(sub_counts)
    n_resolved_affil = int((sub_of_multi == 1).sum())
    n_unresolved     = int((sub_of_multi  > 1).sum())
    n_resolved_name  = int(n_single_cluster)
    n_total_resolved = n_resolved_name + n_resolved_affil

    SEP  = '─' * 64
    SEP2 = '─' * 40

    print(SEP)
    print('Phase 2: adding affiliation data and resolving clusters')
    print(SEP)
    print(f'\n  Starting point: {len(hcr):,} HCR rows (one row per name × HCR category)\n')

    print(f'  Step 1 — Deduplicate by (name, affiliation)')
    print(f'  {n_collapsed:,} rows are the same (name, affiliation) appearing in multiple')
    print(f'  HCR categories; these are collapsed to a single entry.')
    print(f'  Result: {len(persons):,} distinct (name, affiliation) entries  '
          f'({n_collapsed:,} = {len(hcr):,} − {len(persons):,})')
    _print_category_breakdown(persons)
    print()

    print(f'  Step 2 — Group by name pattern (normalised last-name word + first initial)')
    print(f'  {n_single_cluster:,} name patterns are unique in the list.')
    print(f'  → {n_single_cluster:,} entries are unambiguous by name alone.')
    print(f'  {cluster_counts[cluster_counts > 1].shape[0]:,} name patterns cover 2 or more entries.')
    print(f'  → {n_in_multi:,} entries share a name pattern with at least one other entry.\n')

    print(f'  Step 3 — Split each ambiguous name pattern by affiliation')
    print(f'  Of the {n_in_multi:,} entries in ambiguous name patterns:')
    print(f'  {n_resolved_affil:,} have a distinct (name pattern + affiliation) → resolved.')
    print(f'  {n_unresolved:,} share both name pattern AND affiliation with another entry')
    print(f'  → {n_unresolved:,} entries in {n_sub_multi:,} sub-clusters remain unresolved.\n')

    print(f'  {SEP2}')
    print(f'  Resolution summary')
    print(f'  {SEP2}')
    print(f'  Resolved by name alone:          {n_resolved_name:>5,}')
    print(f'  Resolved by affiliation:         {n_resolved_affil:>5,}')
    print(f'  {SEP2}')
    print(f'  Total resolved:                  {n_total_resolved:>5,}  of {len(persons):,}  '
          f'({n_total_resolved / len(persons) * 100:.1f}%)')
    print(f'  Unresolved ({n_sub_multi} sub-clusters):     {n_unresolved:>5,}')
    print(f'  {SEP2}')
    print(f'  Check: {n_resolved_name:,} + {n_resolved_affil:,} + {n_unresolved:,} = '
          f'{n_resolved_name + n_resolved_affil + n_unresolved:,}')

    print(f'\n{SEP}')
    print('Phase 2b: unresolved clusters')
    print(SEP)
    _print_remaining_multi_name_clusters(persons)


if __name__ == '__main__':
    main()
