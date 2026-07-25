"""
hca_coauthor.py — Co-author overlap splitting of multi-author_idx clusters.

Reads hca_clusters.parquet (from hca_match.py) and the full OAX authorships.
For each multi-author_idx cluster, computes pairwise shared co-author counts
across ALL works by cluster members (not just HCW works), then re-runs
union-find to refine clusters.

Using all works (not just HCW) ensures every HCA has co-author evidence —
HCW is top 1% of citations, so most of an author's output is non-HCW,
but the co-author network is the same person's network throughout.

Decision rules per pair (a, b):
  jaccard >= JACCARD_MERGE               → merge (same person, OAX split)
  jaccard <  JACCARD_MERGE,
    n_coauth_a >= 1 AND n_coauth_b >= 1  → split (disjoint networks)
  n_coauth_a = 0 OR n_coauth_b = 0      → no_coauthor (unresolvable)

Final cluster_status per author_idx:
  name_only    — was never in a multi-member cluster (unchanged)
  merged       — confirmed same person with ≥1 other author_idx (OAX split)
  split        — confirmed different; all pairs had network evidence
  no_coauthor  — split (now singleton) but ≥1 member had no co-authors

Output: updated hca_clusters.parquet.

Usage:
  .venv/bin/python analysis/hca_coauthor.py
"""
from __future__ import annotations

import sys
import uuid
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config

JACCARD_MERGE = 0.10   # min Jaccard similarity to confirm same-person merge


# ── co-author evidence ────────────────────────────────────────────────────────

def coauthor_evidence(
    clusters_path: Path,
    oax_authorships: Path,
    cluster_hashes: list[str] | None = None,
) -> pd.DataFrame:
    """
    For every pair of author_idx in the same multi-member cluster compute:
      n_shared    — distinct co-authors shared by BOTH members across all works
      n_coauth_a  — distinct co-authors of member a (all works)
      n_coauth_b  — distinct co-authors of member b (all works)

    Uses the full OAX authorships (not just HCW works) so every HCA has
    co-author evidence — HCW is top 1%, most output is non-HCW.
    Pre-filters to the ~22K multi-member author_idx before the self-join.

    Returns DataFrame: cluster_hash, a, b, n_shared, n_coauth_a, n_coauth_b
    All pairs are returned (n_shared = 0 for pairs with no shared co-authors).
    """
    con = duckdb.connect()
    con.execute("SET memory_limit='16GB'; SET threads=8")

    hash_filter = ''
    if cluster_hashes is not None:
        quoted = ', '.join(f"'{h}'" for h in cluster_hashes)
        hash_filter = f"AND cluster_hash IN ({quoted})"

    df = con.execute(f"""
    WITH multi AS (
        SELECT author_idx, cluster_hash
        FROM '{clusters_path}'
        WHERE cluster_n > 1 {hash_filter}
    ),
    hca_works AS (
        SELECT m.author_idx, m.cluster_hash, a.work_idx
        FROM multi m
        JOIN '{oax_authorships}' a USING (author_idx)
        WHERE a.author_idx IS NOT NULL
    ),
    ca AS (
        SELECT hw.author_idx, hw.cluster_hash, a2.author_idx AS coauth
        FROM hca_works hw
        JOIN '{oax_authorships}' a2 ON a2.work_idx = hw.work_idx
        WHERE a2.author_idx IS NOT NULL
          AND a2.author_idx != hw.author_idx
    ),
    nc AS (
        SELECT author_idx, cluster_hash, COUNT(DISTINCT coauth) AS n_coauth
        FROM ca GROUP BY author_idx, cluster_hash
    ),
    nc_all AS (
        SELECT m.author_idx, m.cluster_hash,
               COALESCE(nc.n_coauth, 0) AS n_coauth
        FROM multi m LEFT JOIN nc USING (author_idx, cluster_hash)
    ),
    shared AS (
        SELECT c1.cluster_hash,
               c1.author_idx AS a,
               c2.author_idx AS b,
               COUNT(DISTINCT c1.coauth) AS n_shared
        FROM ca c1
        JOIN ca c2 ON  c1.cluster_hash = c2.cluster_hash
                   AND c1.author_idx   <  c2.author_idx
                   AND c1.coauth       =  c2.coauth
        GROUP BY c1.cluster_hash, c1.author_idx, c2.author_idx
    )
    SELECT
        na.cluster_hash,
        na.author_idx                    AS a,
        nb.author_idx                    AS b,
        COALESCE(s.n_shared, 0)          AS n_shared,
        na.n_coauth                      AS n_coauth_a,
        nb.n_coauth                      AS n_coauth_b
    FROM nc_all na
    JOIN nc_all nb ON  na.cluster_hash = nb.cluster_hash
                   AND na.author_idx   <  nb.author_idx
    LEFT JOIN shared s ON  s.cluster_hash = na.cluster_hash
                       AND s.a = na.author_idx
                       AND s.b = nb.author_idx
    ORDER BY na.cluster_hash, na.author_idx, nb.author_idx
    """).df()
    con.close()
    return df


# ── union-find ────────────────────────────────────────────────────────────────

def _find(parent: dict[int, int], x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def refine_clusters(
    clusters: pd.DataFrame,
    evidence: pd.DataFrame,
) -> pd.DataFrame:
    """
    Re-run union-find within each multi-member cluster using merge edges
    (n_shared >= 1).  Assign refined cluster_hash and cluster_status.
    """
    multi = clusters[clusters['cluster_n'] > 1]
    members_by_cluster: dict[str, list[int]] = (
        multi.groupby('cluster_hash')['author_idx'].apply(list).to_dict()
    )
    merge_edges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for _, row in evidence[evidence['jaccard'] >= JACCARD_MERGE].iterrows():
        merge_edges[row['cluster_hash']].append((int(row['a']), int(row['b'])))

    # clusters where any pair lacks co-author evidence
    ambig_clusters: set[str] = set(
        evidence.loc[
            (evidence['n_coauth_a'] == 0) | (evidence['n_coauth_b'] == 0),
            'cluster_hash'
        ].unique()
    )

    new_hash:   dict[int, str] = {}
    new_status: dict[int, str] = {}

    for ch, members in members_by_cluster.items():
        parent = {m: m for m in members}
        for a, b in merge_edges.get(ch, []):
            parent[_find(parent, a)] = _find(parent, b)

        # component UUID per root
        roots: dict[int, str] = {}
        for m in members:
            r = _find(parent, m)
            if r not in roots:
                roots[r] = str(uuid.uuid4())
            new_hash[m] = roots[r]

        # status: ambiguous if ANY pair lacked evidence
        has_ambig = ch in ambig_clusters
        for m in members:
            r = _find(parent, m)
            comp_size = sum(1 for x in members if _find(parent, x) == r)
            if has_ambig:
                new_status[m] = 'no_coauthor'
            elif comp_size >= 2:
                new_status[m] = 'merged'
            else:
                new_status[m] = 'split'

    result = clusters.copy()
    mask = result['cluster_n'] > 1
    result.loc[mask, 'cluster_hash']   = result.loc[mask, 'author_idx'].map(new_hash)
    result.loc[mask, 'cluster_status'] = result.loc[mask, 'author_idx'].map(new_status)

    # recompute cluster_n
    new_n = result.groupby('cluster_hash').size().rename('cluster_n')
    result = result.drop(columns='cluster_n').join(new_n, on='cluster_hash')

    return result


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    test_mode = '--test' in sys.argv
    paths = load_config()
    clusters_path   = paths.working / 'hca_clusters.parquet'
    oax_authorships = paths.openalex / 'authorships' / '*.parquet'

    clusters = pd.read_parquet(clusters_path)
    n_multi = (clusters['cluster_n'] > 1).sum()
    print(f'Loaded {len(clusters):,} authors  ({n_multi:,} in multi-member clusters)')

    if test_mode:
        # pick 5 examples of each case:
        # (a) same display_name, 2 author_idx  (OAX split)
        # (b) 2 distinct display_names in cluster
        # (c) 3+ distinct display_names in cluster
        multi = clusters[clusters['cluster_n'] > 1]
        name_counts = multi.groupby('cluster_hash')['display_name'].nunique()
        case_a = name_counts[name_counts == 1].index[:5].tolist()
        case_b = name_counts[name_counts == 2].index[:5].tolist()
        case_c = name_counts[name_counts >= 3].index[:5].tolist()
        test_hashes = case_a + case_b + case_c
        clusters = clusters[clusters['cluster_hash'].isin(test_hashes)].copy()
        print(f'TEST MODE: {len(case_a)} same-name, {len(case_b)} 2-name, '
              f'{len(case_c)} 3+-name clusters ({len(clusters)} author_idx)')

    print('Computing co-author evidence...', flush=True)
    evidence = coauthor_evidence(
        clusters_path, oax_authorships,
        cluster_hashes=test_hashes if test_mode else None,  # type: ignore[possibly-undefined]
    )
    evidence['jaccard'] = evidence['n_shared'] / (
        evidence['n_coauth_a'] + evidence['n_coauth_b'] - evidence['n_shared']
    ).clip(lower=1)

    n_pairs = len(evidence)
    n_merge = (evidence['jaccard'] >= JACCARD_MERGE).sum()
    n_split = ((evidence['jaccard'] < JACCARD_MERGE) &
               (evidence['n_coauth_a'] >= 1) &
               (evidence['n_coauth_b'] >= 1)).sum()
    n_no_coauth = ((evidence['n_coauth_a'] == 0) |
                   (evidence['n_coauth_b'] == 0)).sum()
    print(f'  {n_pairs:,} pairs across multi-member clusters  (Jaccard threshold={JACCARD_MERGE})')
    print(f'  {n_merge:,} merge  (Jaccard >= {JACCARD_MERGE})')
    print(f'  {n_split:,} split  (Jaccard < {JACCARD_MERGE}, both have co-authors)')
    print(f'  {n_no_coauth:,} no_coauthor (one or both have no co-authors in full OAX)')

    if test_mode:
        print('\nEvidence per test cluster:')
        for ch in test_hashes:
            members = clusters[clusters['cluster_hash'] == ch][['author_idx','display_name']].values
            ev = evidence[evidence['cluster_hash'] == ch]
            print(f'  cluster {ch[:8]}...')
            for aid, dn in members:
                print(f'    {aid}  {dn}')
            for _, r in ev.iterrows():
                print(f'    pair ({int(r.a)}, {int(r.b)}): '
                      f'n_shared={int(r.n_shared)}  '
                      f'n_coauth=({int(r.n_coauth_a)}, {int(r.n_coauth_b)})  '
                      f'jaccard={r.jaccard:.3f}')

    print('\nRefining clusters...', flush=True)
    clusters = refine_clusters(clusters, evidence)

    sc = clusters['cluster_status'].value_counts()
    print(f'\nCluster status after co-author pass:')
    for status, n in sc.items():
        print(f'  {status:<12} {n:,} author_idx')

    n_new_multi = (clusters['cluster_n'] > 1).sum()
    print(f'\n  {n_new_multi:,} author_idx still in multi-member clusters after refinement')

    if test_mode:
        print('\nTEST MODE: not writing output')
    else:
        clusters.to_parquet(clusters_path, index=False)
        print(f'\nWrote {len(clusters):,} rows → {clusters_path.name}')


if __name__ == '__main__':
    main()
