"""
name_cluster.py — Name clustering for HCR/HCA matching.

Implements same-last-name blocking, pairwise name compatibility,
union-find partitioning, and cluster hash assignment.
Used by hcr_match.py and hca_match.py.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict

from nameparser import HumanName

from util.name_util import fa, mi


# ── hashing ───────────────────────────────────────────────────────────────────

def sha(fields: list[str]) -> str:
    raw = json.dumps(fields, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── compatibility + partitioning ──────────────────────────────────────────────

def compatible(a: HumanName, b: HumanName) -> bool:
    fa_a, fa_b = fa(a), fa(b)
    if len(fa_a) >= 2 and len(fa_b) >= 2 and fa_a != fa_b:
        return False
    if fa_a and fa_b and fa_a[0] != fa_b[0]:
        return False
    mi_a, mi_b = mi(a), mi(b)
    if mi_a and mi_b and mi_a != mi_b:
        return False
    return True


def partition_family(rows: dict[int, HumanName]) -> list[dict[int, HumanName]]:
    """
    Split a same-last-name group into person clusters.

    Pass 1: cluster full names (≥2 alpha chars in first) by compatibility.
    Pass 2: attach each initial-only entry to exactly one matching cluster,
            or give it its own cluster if zero or multiple clusters match.
    """
    full  = {k: hn for k, hn in rows.items() if len(fa(hn)) >= 2}
    inits = {k: hn for k, hn in rows.items() if len(fa(hn)) <  2}

    keys   = list(full.keys())
    parent = list(range(len(keys)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
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

    # snapshot Pass-1 cluster sizes before any Pass-2 additions
    pass1_sizes = {i: len(cl) for i, cl in enumerate(clusters)}

    for k, hn in inits.items():
        matches = [ci for ci, cl in enumerate(clusters)
                   if ci in pass1_sizes  # original Pass-1 clusters only
                   and any(compatible(hn, h) for h in cl.values())]
        # only attach when exactly one Pass-1 cluster matches AND it had one member
        if len(matches) == 1 and pass1_sizes[matches[0]] == 1:
            clusters[matches[0]][k] = hn
        else:
            clusters.append({k: hn})

    return clusters


# ── name index + cluster hash assignment ──────────────────────────────────────

def build_name_index(row_hn: dict[int, HumanName]) -> dict[str, dict[int, HumanName]]:
    """Group row_hn by HumanName.last to form same-last-name blocking groups."""
    by_family: dict[str, dict[int, HumanName]] = defaultdict(dict)
    for row_idx, hn in row_hn.items():
        by_family[hn.last][row_idx] = hn
    return dict(by_family)


def assign_name_cluster_hashes(
    df,
    name_idx: dict[str, dict[int, HumanName]],
) -> tuple:
    """
    Assign a UUID cluster_hash to each row via partition_family.
    Rows indistinguishable by name share a hash.
    Returns (updated_df, unresolved) where unresolved is a list of
    (family, cluster) pairs with ≥2 rows and non-identical first names.
    """
    df = df.copy()
    row_to_hash: dict[int, str] = {}
    unresolved = []

    for family, rows in name_idx.items():
        for cluster in partition_family(rows):
            h = str(uuid.uuid4())
            for row_idx in cluster:
                row_to_hash[row_idx] = h
            if len(cluster) >= 2:
                names = set(fa(hn) for hn in cluster.values())
                if len(names) > 1:
                    unresolved.append((family, cluster))

    df['cluster_hash'] = df.index.map(row_to_hash)
    return df, unresolved
