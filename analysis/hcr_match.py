"""
hcr_match.py — Match Clarivate HCR 2025 to OpenAlex author records.

Pipeline:
  Phase 1  Load HCR, apply corrections, assign cluster_hash + person_hash
  Phase 2  Collapse exact duplicates (same person, multiple categories)
  Phase 3  (TODO) OAX candidate retrieval and scoring
  Phase 4  (TODO) Output hcr_oax_map.parquet

Usage:
  .venv/bin/python analysis/hcr_match.py
"""

from __future__ import annotations

import sys
import hashlib
import json
from pathlib import Path
from collections import defaultdict

import re
import unicodedata

import pandas as pd
from unidecode import unidecode
from nameparser import HumanName
from nameparser.config import CONSTANTS as _NP

# Remove words that nameparser wrongly treats as titles or suffixes
_NP.titles.remove('se')       # Korean given-name component, not Señor
_NP.titles.remove('shaik')    # South Asian first-name component, not a title
_NP.suffix_acronyms.remove('chi')  # Chinese/Korean surname
_NP.suffix_acronyms.remove('asa')  # Scandinavian/Hebrew surname
_NP.suffix_acronyms.remove('ma')   # Chinese surname (also MA degree)
# Add Belgian/Dutch particle so "Vanden Berghe" stays as compound last name
_NP.prefixes.add('vanden')

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config


# ── hashing ──────────────────────────────────────────────────────────────────

def _sha(fields: list[str]) -> str:
    raw = json.dumps(fields, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def person_hash(first: str, last: str, affil: str) -> str:
    """Verbatim (first, last, affil) → reproducible 16-char hex."""
    return _sha([first, last, affil])


def cluster_hash(last_norm: str, fi: str) -> str:
    """Normalised (last_norm, fi) → reproducible 16-char hex blocking key."""
    return _sha([last_norm, fi])


# ── name normalisation (for blocking only) ────────────────────────────────────

def _norm(s: str) -> str:
    return unidecode(str(s or '')).lower().strip()


def _last_norm(last: str) -> str:
    """Last word of last-name field, unidecode+lower."""
    words = _norm(last).split()
    return words[-1].strip('.,') if words else ''


def _fi(first: str) -> str:
    """First alpha character of first-name field."""
    for c in _norm(first):
        if c.isalpha():
            return c
    return ''


# ── HCR load corrections ──────────────────────────────────────────────────────

_GEN_SUFFIXES = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv', 'v'}

_POSTNOMINALS = re.compile(
    r'\s+(FBA|FFASL|FAA|FRS|FRSE|FRSC|FRSB|FMEDSCI|CBE|OBE|MBE|DBE|KBE|AO|AC|AM'
    r'|FRCPE|FRCP|FRCS|FRACP|FRACS|FRCPA|FACSS|FAAS|FAPS|FASSA'
    r'|PHD|MD|DPHIL|DSC|ESQ)(\s+.*)?$',
    re.IGNORECASE,
)

def _strip_controls(s: str) -> str:
    """Remove Unicode control/formatting characters (e.g. bidi marks)."""
    return ''.join(c for c in s if unicodedata.category(c) not in ('Cf', 'Cc'))


def _apply_corrections(hcr: pd.DataFrame) -> pd.DataFrame:
    hcr = hcr.copy()

    # 1. strip Unicode control/formatting characters
    for col in ('First Name', 'Last Name', 'Primary Affiliation'):
        hcr[col] = hcr[col].fillna('').apply(_strip_controls).str.strip()

    # 2. verbatim corrections on raw data (before any case transforms)
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

    # 3. strip postnominals from Last Name (e.g. "Autio FBA FFASL" → "Autio")
    hcr['Last Name'] = hcr['Last Name'].apply(
        lambda s: _POSTNOMINALS.sub('', s).strip()
    )

    # 4. generational suffixes in Last Name: "Smith Jr." → "Smith"
    def _fix_gen_suffix(row):
        words = row['Last Name'].split()
        if len(words) >= 2 and words[-1].lower().rstrip('.') in _GEN_SUFFIXES:
            row = row.copy()
            row['Last Name'] = ' '.join(words[:-1])
        return row
    hcr = hcr.apply(_fix_gen_suffix, axis=1)

    # 5. normalise all-caps last names (VAN CALSTER → Van Calster)
    hcr['Last Name'] = hcr['Last Name'].apply(
        lambda s: s.title() if s.isupper() and len(s) > 2 else s
    )

    return hcr


# ── Phase 1: load + hash ──────────────────────────────────────────────────────

def load_hcr(path: Path) -> pd.DataFrame:
    hcr = pd.read_excel(path)
    hcr = _apply_corrections(hcr)
    hcr = hcr.rename(columns={
        'First Name':           'first_name',
        'Last Name':            'last_name',
        'Category':             'category',
        'Primary Affiliation':  'affil',
        'Secondary Affiliations': 'affil2',
    })
    hcr['first_name'] = hcr['first_name'].fillna('').str.strip()
    hcr['last_name']  = hcr['last_name'].fillna('').str.strip()
    hcr['affil']      = hcr['affil'].fillna('').str.strip()
    hcr['category']   = hcr['category'].fillna('').str.strip()

    hcr['last_norm']     = hcr['last_name'].apply(_last_norm)
    hcr['fi']            = hcr['first_name'].apply(_fi)
    hcr['cluster_hash']  = hcr.apply(lambda r: cluster_hash(r['last_norm'], r['fi']), axis=1)
    hcr['person_hash']   = hcr.apply(lambda r: person_hash(r['first_name'], r['last_name'], r['affil']), axis=1)
    return hcr


# ── Phase 2: collapse same-person multi-category rows ─────────────────────────

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


# ── name index ───────────────────────────────────────────────────────────────

def build_name_index(hcr: pd.DataFrame) -> dict[str, dict[int, HumanName]]:
    by_family: dict[str, dict[int, HumanName]] = defaultdict(dict)
    for row_idx, row in hcr.iterrows():
        hn = HumanName(f"{row['first_name']} {row['last_name']}".strip())
        by_family[hn.last][row_idx] = hn
    return dict(by_family)


# ── name compatibility + partitioning ────────────────────────────────────────

def _fa(hn: HumanName) -> str:
    return ''.join(c for c in hn.first.lower() if c.isalpha())

def _mi(hn: HumanName) -> str:
    return next((c for c in hn.middle.lower() if c.isalpha()), '')

def compatible(a: HumanName, b: HumanName) -> bool:
    fa, fb = _fa(a), _fa(b)
    if len(fa) >= 2 and len(fb) >= 2 and fa != fb:
        return False
    if fa and fb and fa[0] != fb[0]:
        return False
    ma, mb = _mi(a), _mi(b)
    if ma and mb and ma != mb:
        return False
    return True

def partition_family(rows: dict[int, HumanName]) -> list[dict[int, HumanName]]:
    """
    Split a family-name group into person-clusters.

    Pass 1: cluster full names (≥2 alpha chars) by direct compatibility.
    Pass 2: attach each initial (1 alpha char) to its cluster only if it
            matches exactly one cluster; otherwise it becomes its own cluster.
    """
    full  = {k: hn for k, hn in rows.items() if len(_fa(hn)) >= 2}
    inits = {k: hn for k, hn in rows.items() if len(_fa(hn)) <  2}

    # pass 1 — union-find on full names only
    keys   = list(full.keys())
    parent = list(range(len(keys)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if compatible(full[keys[i]], full[keys[j]]):
                parent[find(i)] = find(j)

    clusters: list[dict[int, HumanName]] = []
    root_to_idx: dict[int, int] = {}
    for i, key in enumerate(keys):
        r = find(i)
        if r not in root_to_idx:
            root_to_idx[r] = len(clusters)
            clusters.append({})
        clusters[root_to_idx[r]][key] = full[key]

    # pass 2 — attach initials to exactly one matching cluster, else own cluster
    for k, hn in inits.items():
        matches = [ci for ci, cl in enumerate(clusters) if
                   any(compatible(hn, h) for h in cl.values())]
        if len(matches) == 1:
            clusters[matches[0]][k] = hn
        else:
            clusters.append({k: hn})   # unresolved or no match → own cluster

    return clusters


# ── name parse diagnostic ─────────────────────────────────────────────────────

def name_parse_diag(hcr: pd.DataFrame) -> None:
    """
    Parse corrected first+last as a full name through HumanName.
    Flag rows where title, prefix, or suffix is non-empty,
    or where hn.last doesn't match the corrected last name.
    """
    flags = []
    for idx, row in hcr.iterrows():
        full = f"{row['first_name']} {row['last_name']}".strip()
        hn = HumanName(full)
        issues = []
        if hn.title:   issues.append(f"title={hn.title!r}")
        if hn.suffix:  issues.append(f"suffix={hn.suffix!r}")
        if _norm(hn.last) != _norm(row['last_name']):
            issues.append(f"hn.last={hn.last!r} ≠ {row['last_name']!r}")
        if issues:
            flags.append((idx, row['first_name'], row['last_name'], hn, ', '.join(issues)))

    print(f"\nName parse flags: {len(flags)} rows\n")
    print(f"  {'#':<6} {'FIRST':<22} {'LAST':<22} {'HN.FIRST':<14} {'HN.MID':<10} {'ISSUES'}")
    print('  ' + '-' * 110)
    for idx, first, last, hn, issues in flags:
        print(f"  {idx:<6} {first:<22} {last:<22} {hn.first:<14} {hn.middle:<10} {issues}")


# ── diagnostics ───────────────────────────────────────────────────────────────

def _print_ambiguous_clusters(persons: pd.DataFrame) -> None:
    """Print clusters where multiple distinct persons share the same cluster_hash."""
    cluster_counts = persons.groupby('cluster_hash')['person_hash'].count()
    multi_hashes   = cluster_counts[cluster_counts > 1].index
    multi = persons[persons['cluster_hash'].isin(multi_hashes)].sort_values(
        ['cluster_hash', 'last_norm', 'first_name', 'affil']
    )
    print(f"\nAmbiguous clusters (multiple persons per cluster_hash): {len(multi_hashes):,}")
    print(f"Persons in ambiguous clusters: {len(multi):,}\n")

    sep  = '-' * 130
    hdr  = f"{'FIRST NAME':<24} {'LAST NAME':<18} {'CATEGORIES':<38} AFFILIATION"
    prev = None
    for _, r in multi.iterrows():
        ch = r['cluster_hash']
        if ch != prev:
            if prev is not None:
                print()
            print(f"cluster {ch}  [{r['last_norm']}|{r['fi']}]")
            print(sep)
            print(hdr)
            prev = ch
        cats = ', '.join(r['categories'])
        print(f"  {r['first_name']:<22} {r['last_name']:<18} {cats:<38} {r['affil'][:50]}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    paths    = load_config()
    hcr_path = Path('data/2025_HCR.xlsx')

    print('Phase 1: loading HCR...')
    hcr = load_hcr(hcr_path)
    print(f'  {len(hcr):,} rows loaded')

    name_parse_diag(hcr)

    print('\nBuilding name index and partitioning...')
    name_idx = build_name_index(hcr)
    print(f'  {len(name_idx):,} distinct family names')

    unresolved = []
    for family, rows in name_idx.items():
        for cluster in partition_family(rows):
            if len(cluster) >= 2:
                names = set(_fa(hn) for hn in cluster.values())
                if len(names) > 1:   # non-identical first names → unresolved
                    unresolved.append((family, cluster))

    print(f'  {len(unresolved):,} unresolved clusters (2+ rows, non-identical first names)\n')
    for family, cluster in sorted(unresolved, key=lambda x: x[0]):
        print(f"{family}")
        for row_idx, hn in sorted(cluster.items()):
            print(f"  {row_idx:5d}  {hn.first} {hn.middle}")
        print()

    print('Phase 2: collapsing same-person multi-category rows...')
    persons = collapse_persons(hcr)

    multi_cat = persons[persons['n_categories'] > 1]
    print(f'  {len(persons):,} distinct persons')
    print(f'  {len(multi_cat):,} persons with >1 category  '
          f'({multi_cat["n_categories"].sum() - len(multi_cat):,} duplicate rows removed)')

    cluster_counts = persons.groupby('cluster_hash')['person_hash'].count()
    n_single_cluster = (cluster_counts == 1).sum()
    n_multi_cluster  = (cluster_counts  > 1).sum()
    print(f'  {n_single_cluster:,} single-person clusters  (unambiguous)')
    print(f'  {n_multi_cluster:,}   multi-person clusters  (need OAX to split)')

    _print_ambiguous_clusters(persons)


if __name__ == '__main__':
    main()
