"""
explore_hcr_hca.py — Transient exploration of HCR → HCA candidate matching.

Joins HCR persons (from hcr_match logic) to hca_clusters on (last_norm, fi)
and reports candidate counts, examples, and institution overlap.

NOT part of the pipeline — run interactively to understand match quality.

Usage:
  .venv/bin/python analysis/explore_hcr_hca.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config
from analysis.hcr_match import load_hcr, collapse_persons, split_clusters_by_affil

# ── load ──────────────────────────────────────────────────────────────────────

paths = load_config()
hca_path = paths.working / 'hca_clusters.parquet'

print('Loading HCR...')
hcr, row_hn = load_hcr(Path('data/2025_HCR.xlsx'))
persons = split_clusters_by_affil(collapse_persons(hcr))
print(f'  {len(persons):,} distinct HCR persons')

hca = pd.read_parquet(hca_path)
print(f'  {len(hca):,} HCA author_idx  ({hca["cluster_hash"].nunique():,} clusters)')

# ── register in DuckDB ────────────────────────────────────────────────────────

con = duckdb.connect()
con.register('hcr', persons)
con.register('hca', hca)

# ── 1. candidate count distribution ──────────────────────────────────────────

print('\n── Candidate counts (HCR person → HCA clusters on last_norm+fi) ──')
dist = con.execute("""
    SELECT n_cands,
           COUNT(*) AS n_hcr,
           ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct
    FROM (
        SELECT h.person_hash,
               COUNT(DISTINCT a.cluster_hash) AS n_cands
        FROM hcr h
        LEFT JOIN hca a ON a.last_norm = h.last_norm AND a.fi = h.fi
        GROUP BY h.person_hash
    )
    GROUP BY n_cands ORDER BY n_cands
""").df()
print(dist.to_string(index=False))

# ── 2. zero-candidate cases ───────────────────────────────────────────────────

print('\n── Zero-candidate HCR persons (not found in HCA at all) ──')
zero = con.execute("""
    SELECT h.first_name, h.last_name, h.last_norm, h.fi, h.affil,
           h.categories
    FROM hcr h
    LEFT JOIN hca a ON a.last_norm = h.last_norm AND a.fi = h.fi
    WHERE a.cluster_hash IS NULL
    ORDER BY h.last_norm, h.first_name
    LIMIT 20
""").df()
print(f'  (first 20 of {len(zero)} — may reflect non-HCW authors or OAX gaps)')
for _, r in zero.iterrows():
    print(f'  {r["first_name"]:<20} {r["last_name"]:<22} [{r["last_norm"]}|{r["fi"]}]  {r["affil"][:50]}')

# ── 3. exactly-1-candidate cases (sample) ────────────────────────────────────

print('\n── Sample: HCR persons with exactly 1 HCA candidate ──')
one = con.execute("""
    WITH cands AS (
        SELECT h.person_hash, h.first_name, h.last_name, h.affil,
               h.last_norm, h.fi, h.categories,
               a.cluster_hash, a.display_name, a.cluster_n, a.cluster_status,
               a.inst_name_modal, a.inst_country_modal,
               a.inst_name_recent, a.inst_country_recent
        FROM hcr h
        JOIN hca a ON a.last_norm = h.last_norm AND a.fi = h.fi
    ),
    counts AS (
        SELECT person_hash, COUNT(DISTINCT cluster_hash) AS n
        FROM cands GROUP BY person_hash
    )
    SELECT c.*
    FROM cands c JOIN counts USING (person_hash)
    WHERE n = 1
    ORDER BY RANDOM()
    LIMIT 10
""").df()
for _, r in one.iterrows():
    print(f'  HCR: {r["first_name"]:<20} {r["last_name"]:<22}  affil: {str(r["affil"])[:45]}')
    print(f'  HCA: {r["display_name"]:<22}  [{r["cluster_status"]}  n={r["cluster_n"]}]  modal: {str(r["inst_name_modal"])[:40]} ({r["inst_country_modal"]})')
    print()

# ── 4. many-candidate cases (hardest to resolve) ─────────────────────────────

print('\n── HCR persons with most HCA candidates ──')
many = con.execute("""
    SELECT h.first_name, h.last_name, h.affil,
           COUNT(DISTINCT a.cluster_hash) AS n_cands
    FROM hcr h
    JOIN hca a ON a.last_norm = h.last_norm AND a.fi = h.fi
    GROUP BY h.person_hash, h.first_name, h.last_name, h.affil
    ORDER BY n_cands DESC
    LIMIT 10
""").df()
print(many.to_string(index=False))

# ── 5. institution overlap for 1-candidate matches ───────────────────────────

print('\n── Institution token overlap (1-candidate HCR persons, sample) ──')
overlap = con.execute("""
    WITH cands AS (
        SELECT h.person_hash, h.first_name, h.last_name,
               LOWER(h.affil)                AS hcr_affil,
               LOWER(a.inst_name_modal)      AS hca_modal,
               LOWER(a.inst_name_recent)     AS hca_recent,
               a.inst_country_modal          AS hca_cc
        FROM hcr h
        JOIN hca a ON a.last_norm = h.last_norm AND a.fi = h.fi
    ),
    counts AS (
        SELECT person_hash, COUNT(*) AS n FROM cands GROUP BY person_hash
    )
    SELECT c.first_name, c.last_name, c.hcr_affil, c.hca_modal, c.hca_recent, c.hca_cc
    FROM cands c JOIN counts USING (person_hash)
    WHERE n = 1 AND c.hcr_affil != '' AND c.hca_modal IS NOT NULL
    ORDER BY RANDOM()
    LIMIT 8
""").df()
for _, r in overlap.iterrows():
    print(f'  {r["first_name"]:<18} {r["last_name"]:<20}')
    print(f'    HCR affil : {r["hcr_affil"][:65]}')
    print(f'    HCA modal : {r["hca_modal"][:65]} ({r["hca_cc"]})')
    print(f'    HCA recent: {str(r["hca_recent"])[:65]}')
    print()

# ── 6. institution matching for 1-candidate HCR persons ─────────────────────

print('\n── Institution match: HCR resolved insts vs HCA modal inst ──')

inst_map_path = Path('data/hcr_person_inst.json')
if not inst_map_path.exists():
    print('  hcr_person_inst.json not found — run analysis/test_hcr_inst_oax.py first')
else:
    person_inst_raw: dict[str, list[int]] = json.loads(inst_map_path.read_text())

    # build person_hash → set[institution_idx]
    # key in person_inst_raw is "first_name|||last_name|||category"
    hash_to_inst: dict[str, set[int]] = {}
    for _, r in persons.iterrows():
        for cat in r['categories']:
            key = f"{r['first_name']}|||{r['last_name']}|||{cat}"
            for idx in person_inst_raw.get(key, []):
                hash_to_inst.setdefault(r['person_hash'], set()).add(idx)

    n_with_inst = sum(1 for v in hash_to_inst.values() if v)
    print(f'  {len(persons):,} HCR persons — {n_with_inst:,} with ≥1 resolved OAX institution')

    # reverse-map OAX display_name → institution_idx (for modal/recent lookup)
    oax_parquet = paths.openalex / 'parquet'
    inst_df = con.execute(f"""
        SELECT institution_idx, display_name
        FROM '{oax_parquet}/institutions.parquet'
    """).df()
    idx_to_name: dict[int, str]  = dict(zip(inst_df['institution_idx'], inst_df['display_name']))
    name_to_idx: dict[str, int]  = dict(zip(inst_df['display_name'], inst_df['institution_idx']))

    def hca_idxs(modal: str | float | None, recent: str | float | None) -> set[int]:
        out: set[int] = set()
        for name in (modal, recent):
            if name and not (isinstance(name, float) and pd.isna(name)):
                idx = name_to_idx.get(str(name))
                if idx is not None:
                    out.add(idx)
        return out

    # 1-candidate pairs
    one_cand = con.execute("""
        WITH cands AS (
            SELECT h.person_hash, h.first_name, h.last_name,
                   a.cluster_hash, a.inst_name_modal, a.inst_name_recent
            FROM hcr h
            JOIN hca a ON a.last_norm = h.last_norm AND a.fi = h.fi
        ),
        counts AS (
            SELECT person_hash, COUNT(DISTINCT cluster_hash) AS n
            FROM cands GROUP BY person_hash
        )
        SELECT c.* FROM cands c JOIN counts USING (person_hash) WHERE n = 1
    """).df()

    rows = []
    for _, r in one_cand.iterrows():
        hcr_idxs   = hash_to_inst.get(r['person_hash'], set())
        hca_idx_set = hca_idxs(r['inst_name_modal'], r['inst_name_recent'])
        inst_match  = bool(hcr_idxs & hca_idx_set)
        hcr_names   = [idx_to_name.get(i, f'idx:{i}') for i in hcr_idxs]
        rows.append({
            'person_hash':      r['person_hash'],
            'first_name':       r['first_name'],
            'last_name':        r['last_name'],
            'inst_match':       inst_match,
            'no_hcr_inst':      len(hcr_idxs) == 0,
            'no_hca_inst':      len(hca_idx_set) == 0,
            'inst_name_modal':  r['inst_name_modal'],
            'inst_name_recent': r['inst_name_recent'],
            'hcr_insts':        '; '.join(hcr_names[:3]),
        })
    sdf = pd.DataFrame(rows)

    n_match    = sdf['inst_match'].sum()
    n_no_hcr   = sdf['no_hcr_inst'].sum()
    n_no_hca   = (~sdf['no_hcr_inst'] & sdf['no_hca_inst']).sum()
    n_mismatch = (~sdf['inst_match'] & ~sdf['no_hcr_inst'] & ~sdf['no_hca_inst']).sum()
    print(f'\n  Institution idx match (1-candidate HCR persons, n={len(sdf):,}):')
    print(f'    match  (HCR idx ∩ HCA idx ≠ ∅)  {n_match:>6}  ({n_match*100/len(sdf):.1f}%)')
    print(f'    no HCR institution resolved       {n_no_hcr:>6}')
    print(f'    no HCA institution (null)         {n_no_hca:>6}')
    print(f'    mismatch (both have, no overlap)  {n_mismatch:>6}  ({n_mismatch*100/len(sdf):.1f}%)')

    print('\n  Sample: match=True')
    ok = sdf[sdf['inst_match']].sample(n=min(5, sdf['inst_match'].sum()), random_state=1)
    for _, r in ok.iterrows():
        print(f'  {r["first_name"]:<18} {r["last_name"]:<22}')
        print(f'    HCR: {r["hcr_insts"][:70]}')
        print(f'    HCA modal : {str(r["inst_name_modal"])[:70]}')
        print(f'    HCA recent: {str(r["inst_name_recent"])[:70]}')

    print('\n  Sample: mismatch (has both, no idx overlap)')
    mm_mask = (~sdf['inst_match'] & ~sdf['no_hcr_inst'] & ~sdf['no_hca_inst'])
    mm = sdf[mm_mask].sample(n=min(6, mm_mask.sum()), random_state=3)
    for _, r in mm.iterrows():
        print(f'  {r["first_name"]:<18} {r["last_name"]:<22}')
        print(f'    HCR: {r["hcr_insts"][:70]}')
        print(f'    HCA modal : {str(r["inst_name_modal"])[:70]}')
        print(f'    HCA recent: {str(r["inst_name_recent"])[:70]}')

con.close()
