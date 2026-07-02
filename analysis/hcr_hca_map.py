"""
hcr_hca_map.py — Match every HCR person to an HCA cluster.

Name waterfall — uses ONLY hn struct output (first, middle, last, fi).
Never re-derives name information from raw fields after the name utility has run.

Derived fields used:
  first_norm  = norm(hn.first)
  middle_norm = norm(hn.middle)
  last_norm   = norm(hn.last)   (= hcr.last_norm / hca.last_norm)
  fi          = first_norm[0]   (= hcr.fi / hca.fi)

Four stages (most → least specific):
  1. F+M+L  : first_norm + middle_norm + last_norm  (require middle != '')
  2. F+L    : first_norm + last_norm
  3. fi+M+L : fi + middle_norm + last_norm          (require middle != '')
  4. fi+L   : fi + last_norm

At each stage:
  n = 1  → assigned (unique / inst_resolved)
  n ≥ 2  → institution matching:
              1 survives  → inst_resolved
              0 survive   → ambiguous (person IS in HCA, institution signal absent)
              2+ survive  → ambiguous
  n = 0  → fall to next stage; 'zero' only if all stages exhausted

Institution matching: lower(hca_inst_modal or hca_inst_recent) is a
substring of lower(hcr_affil), or lower(hcr_affil_main) (before the last
comma) is a substring of lower(hca_inst).

Output: WORKING/hcr_hca_map.parquet — 7,131 rows (one per HCR person×category)

Columns added to the HCR row:
  match_stage          : 'F+M+L' | 'F+L' | 'fi+M+L' | 'fi+L' | 'none'
  n_cands              : candidate count at the resolved stage
  inst_attempted       : bool — institution matching was tried
  match_status         : 'unique' | 'inst_resolved' | 'ambiguous' | 'zero'
  hca_cluster_hash     : assigned cluster (null if ambiguous/zero)
  hca_display_name     : display name of assigned cluster
  hca_inst_modal       : modal institution of assigned cluster
  hca_inst_country     : country of modal institution
  hca_cand_hashes      : list of candidate cluster_hashes (for ambiguous)
  hca_cand_names       : list of candidate display_names  (for ambiguous)

Cross-field columns (joined from hca_crossfield_{window}_{label}.parquet;
  null if crossfield file absent or person unmatched):
  hca_cf_paper_score   : sum of n_hcw/thresh_paper across all fields (cluster total)
  hca_cf_cite_score    : sum of sum_cites/thresh_cite across all fields (cluster total)
  hca_cf_qualified     : bool — both scores >= 1.0 (Clarivate cross-field criterion)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from util import load_config
from util.name_util import norm
from analysis.hcr_match import load_hcr


# ── institution match ─────────────────────────────────────────────────────────

def _inst_hit(hcr_affil: str, hca_inst: object) -> bool:
    """True if HCA inst name and HCR affil share a meaningful substring."""
    if not hca_inst or (isinstance(hca_inst, float)):
        return False
    aff      = hcr_affil.lower()
    inst     = str(hca_inst).lower()
    aff_main = aff.rsplit(',', 1)[0].strip()   # drop ", Country" suffix
    return inst in aff or aff_main in inst


def _inst_filter(hcr_affil: str, cands: pd.DataFrame) -> pd.DataFrame:
    """Return subset of cands where modal OR recent institution matches HCR affil."""
    mask = cands.apply(
        lambda r: _inst_hit(hcr_affil, r['inst_name_modal'])
                  or _inst_hit(hcr_affil, r['inst_name_recent']),
        axis=1,
    )
    return cands[mask]


# ── waterfall ─────────────────────────────────────────────────────────────────

def _make_result(
    stage: str,
    cands: pd.DataFrame,
    inst_attempted: bool = False,
) -> dict:
    n = len(cands)
    if n == 1:
        r = cands.iloc[0]
        return dict(
            match_stage=stage,
            n_cands=n,
            inst_attempted=inst_attempted,
            match_status='inst_resolved' if inst_attempted else 'unique',
            hca_cluster_hash=r['cluster_hash'],
            hca_display_name=r['display_name'],
            hca_inst_modal=r['inst_name_modal'],
            hca_inst_country=r['inst_country_modal'],
            hca_cand_hashes=[r['cluster_hash']],
            hca_cand_names=[r['display_name']],
        )
    status = 'ambiguous' if n > 1 else 'zero'
    return dict(
        match_stage=stage,
        n_cands=n,
        inst_attempted=inst_attempted,
        match_status=status,
        hca_cluster_hash=None,
        hca_display_name=None,
        hca_inst_modal=None,
        hca_inst_country=None,
        hca_cand_hashes=list(cands['cluster_hash']) if n > 1 else [],
        hca_cand_names=list(cands['display_name']) if n > 1 else [],
    )


def _resolve_stage(
    stage: str,
    affil: str,
    cands: pd.DataFrame,
) -> dict | None:
    """
    Try to resolve at one stage.  Returns result dict if resolved (n=1) or
    truly ambiguous (n≥2 after optional inst filter).  Returns None if n=0
    (caller should try next stage).
    """
    if len(cands) == 0:
        return None
    if len(cands) == 1:
        return _make_result(stage, cands)
    # 2+ → institution filter
    filtered = _inst_filter(affil, cands)
    if len(filtered) == 1:
        return _make_result(stage, filtered, inst_attempted=True)
    # 0 or 2+ after inst filter → keep all originals as ambiguous
    return _make_result(stage, cands, inst_attempted=len(filtered) != len(cands))


def _match_person(
    hn: dict,
    last_norm_: str,
    fi_: str,
    affil: str,
    idx_fml:  dict[tuple, pd.DataFrame],
    idx_fl:   dict[tuple, pd.DataFrame],
    idx_fiml: dict[tuple, pd.DataFrame],
    idx_fil:  dict[tuple, pd.DataFrame],
) -> dict:
    """Run the four-stage waterfall for one HCR person."""
    first_n  = norm(hn.get('first',  ''))
    middle_n = norm(hn.get('middle', ''))

    # Stage 1: F+M+L (only if middle is non-empty on HCR side)
    if middle_n:
        cands = idx_fml.get((first_n, middle_n, last_norm_), pd.DataFrame())
        result = _resolve_stage('F+M+L', affil, cands)
        if result is not None:
            return result

    # Stage 2: F+L
    cands  = idx_fl.get((first_n, last_norm_), pd.DataFrame())
    result = _resolve_stage('F+L', affil, cands)
    if result is not None:
        return result

    # Stage 3: fi+M+L (only if middle is non-empty on HCR side)
    if middle_n:
        cands  = idx_fiml.get((fi_, middle_n, last_norm_), pd.DataFrame())
        result = _resolve_stage('fi+M+L', affil, cands)
        if result is not None:
            return result

    # Stage 4: fi+L
    cands  = idx_fil.get((fi_, last_norm_), pd.DataFrame())
    result = _resolve_stage('fi+L', affil, cands)
    if result is not None:
        return result

    # exhausted all stages
    return dict(
        match_stage='none', n_cands=0, inst_attempted=False,
        match_status='zero',
        hca_cluster_hash=None, hca_display_name=None,
        hca_inst_modal=None, hca_inst_country=None,
        hca_cand_hashes=[], hca_cand_names=[],
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', default='2020_2024')
    parser.add_argument('--label',  default='baseline')
    args = parser.parse_args()

    paths   = load_config()
    working = paths.working

    print('Loading HCR persons...')
    hcr, _ = load_hcr(Path('data/2025_HCR.xlsx'))
    print(f'  {len(hcr):,} rows (person × category)')

    print('Loading HCA clusters...')
    hca = pd.read_parquet(working / 'hca_clusters.parquet')
    # derive name parts from hn struct only
    hca['first_norm']  = hca['hn'].apply(lambda h: norm(h.get('first',  '') if isinstance(h, dict) else ''))
    hca['middle_norm'] = hca['hn'].apply(lambda h: norm(h.get('middle', '') if isinstance(h, dict) else ''))
    hca_c = hca.drop_duplicates('cluster_hash').copy()
    print(f'  {len(hca_c):,} distinct HCA clusters')

    # ── cross-field scores at cluster level ───────────────────────────────────
    xf_path = working / f'hca_crossfield_{args.window}_{args.label}.parquet'
    cluster_xf: pd.DataFrame | None = None
    if xf_path.exists():
        print(f'Loading cross-field scores ({xf_path.name})...')
        xf = pd.read_parquet(
            xf_path,
            columns=['author_idx', 'cf_paper_score', 'cf_cite_score'],
        )
        # aggregate to cluster level: sum scores across all author_idxes in cluster
        cluster_xf = (
            hca[['author_idx', 'cluster_hash']]
            .merge(xf, on='author_idx', how='inner')
            .groupby('cluster_hash', as_index=False)
            .agg(
                hca_cf_paper_score=('cf_paper_score', 'sum'),
                hca_cf_cite_score =('cf_cite_score',  'sum'),
            )
        )
        cluster_xf['hca_cf_qualified'] = (
            (cluster_xf['hca_cf_paper_score'] >= 1.0) &
            (cluster_xf['hca_cf_cite_score']  >= 1.0)
        )
        print(f'  {len(cluster_xf):,} clusters with cross-field scores')
    else:
        print(f'  (no crossfield file — run hca_crossfield.py first)')

    print('Building name indices...')
    idx_fml:  dict[tuple, list] = {}
    idx_fl:   dict[tuple, list] = {}
    idx_fiml: dict[tuple, list] = {}
    idx_fil:  dict[tuple, list] = {}

    for _, r in hca_c.iterrows():
        fn = r['first_norm']
        mn = r['middle_norm']
        ln = r['last_norm']
        fi_ = r['fi']

        if mn:
            idx_fml.setdefault((fn, mn, ln), []).append(r)
            idx_fiml.setdefault((fi_, mn, ln), []).append(r)
        idx_fl.setdefault((fn, ln), []).append(r)
        idx_fil.setdefault((fi_, ln), []).append(r)

    idx_fml  = {k: pd.DataFrame(v) for k, v in idx_fml.items()}
    idx_fl   = {k: pd.DataFrame(v) for k, v in idx_fl.items()}
    idx_fiml = {k: pd.DataFrame(v) for k, v in idx_fiml.items()}
    idx_fil  = {k: pd.DataFrame(v) for k, v in idx_fil.items()}

    # ── match at person level ─────────────────────────────────────────────────
    print('Matching...')
    person_results: dict[str, dict] = {}
    seen: set[str] = set()
    for _, row in hcr.iterrows():
        ph = row['person_hash']
        if ph in seen:
            continue
        seen.add(ph)
        person_results[ph] = _match_person(
            hn        = row['hn'] if isinstance(row['hn'], dict) else {},
            last_norm_= row['last_norm'],
            fi_       = row['fi'],
            affil     = row['affil'],
            idx_fml   = idx_fml,
            idx_fl    = idx_fl,
            idx_fiml  = idx_fiml,
            idx_fil   = idx_fil,
        )

    # ── join back to 7,131 HCR rows ──────────────────────────────────────────
    result_df = (
        pd.DataFrame.from_dict(person_results, orient='index')
        .rename_axis('person_hash')
        .reset_index()
    )
    out = hcr.merge(result_df, on='person_hash', how='left')

    # ── summary ──────────────────────────────────────────────────────────────
    person_level  = out.drop_duplicates('person_hash')
    status_counts = person_level['match_status'].value_counts()
    n_persons     = len(person_level)

    print(f'\nMatch summary ({n_persons:,} unique persons):')
    for status in ['unique', 'inst_resolved', 'ambiguous', 'zero']:
        n = status_counts.get(status, 0)
        print(f'  {status:<16} {n:>5,}  ({n * 100 / n_persons:.1f}%)')

    stage_counts = person_level['match_stage'].value_counts()
    print(f'\nStage breakdown:')
    for stage in ['F+M+L', 'F+L', 'fi+M+L', 'fi+L', 'none']:
        n = stage_counts.get(stage, 0)
        print(f'  {stage:<8} {n:>5,}')

    print('\nMiss rate by category:')
    cat_stats = (
        out.groupby('category').apply(
            lambda g: pd.Series({
                'n_hcr':   g['person_hash'].nunique(),
                'matched': g.loc[g['match_status'].isin(['unique', 'inst_resolved']), 'person_hash'].nunique(),
                'ambig':   g.loc[g['match_status'] == 'ambiguous', 'person_hash'].nunique(),
                'zero':    g.loc[g['match_status'] == 'zero', 'person_hash'].nunique(),
            })
        )
        .assign(miss_pct=lambda d: (d['zero'] * 100 / d['n_hcr']).round(1))
        .sort_values('miss_pct', ascending=False)
    )
    print(cat_stats.to_string())

    # ── join cross-field scores ───────────────────────────────────────────────
    if cluster_xf is not None:
        out = out.merge(
            cluster_xf.rename(columns={'cluster_hash': 'hca_cluster_hash'}),
            on='hca_cluster_hash',
            how='left',
        )

        matched = out.drop_duplicates('person_hash')
        matched_cf = matched[matched['match_status'].isin(['unique', 'inst_resolved'])]
        n_matched  = len(matched_cf)
        n_qual     = int(matched_cf['hca_cf_qualified'].fillna(False).sum())
        n_cf_pp    = int((matched_cf['hca_cf_paper_score'] >= 1.0).sum())
        n_cf_cc    = int((matched_cf['hca_cf_cite_score']  >= 1.0).sum())

        print(f'\nCross-field scores (matched HCR persons, n={n_matched:,}):')
        print(f'  cf_paper_score >= 1.0:    {n_cf_pp:>5,}  ({n_cf_pp*100/n_matched:.1f}%)')
        print(f'  cf_cite_score  >= 1.0:    {n_cf_cc:>5,}  ({n_cf_cc*100/n_matched:.1f}%)')
        print(f'  Both >= 1.0 (qualified):  {n_qual:>5,}  ({n_qual*100/n_matched:.1f}%)')

        cf_by_cat = (
            out[out['match_status'].isin(['unique', 'inst_resolved'])]
            .groupby('category')['hca_cf_qualified']
            .agg(
                n_matched='count',
                n_cf_qual=lambda x: x.fillna(False).sum(),
            )
            .assign(cf_pct=lambda d: (d['n_cf_qual'] * 100 / d['n_matched']).round(1))
            .sort_values('cf_pct', ascending=False)
        )
        print('\n  Cross-field qualified by category:')
        print(f"  {'category':<35} {'matched':>7}  {'cf_qual':>7}  {'cf_%':>6}")
        print('  ' + '-' * 62)
        for cat, row in cf_by_cat.iterrows():
            print(f"  {cat:<35} {int(row['n_matched']):>7,}  "
                  f"{int(row['n_cf_qual']):>7,}  {row['cf_pct']:>6.1f}%")

    # ── write ─────────────────────────────────────────────────────────────────
    out_path = working / 'hcr_hca_map.parquet'
    out.to_parquet(out_path, index=False)
    print(f'\nWrote {len(out):,} rows → {out_path.name}')


if __name__ == '__main__':
    main()
