"""
hca_crossfield.py — Cross-Field HCR scoring (Clarivate methodology).

For each OAX author in hcw_authors.parquet, computes:

  paper_score_f  = n_hcw[author, f] / thresh_paper[f]
  cf_paper_score = SUM(paper_score_f  across all fields)

  cite_score_f   = sum_cites[author, f] / thresh_cite[f]
  cf_cite_score  = SUM(cite_score_f across all fields)

  qualified      = cf_paper_score >= 1.0 AND cf_cite_score >= 1.0

thresh_paper[f] and thresh_cite[f] are calibrated so that HCA_TARGET
researchers per field would qualify in that single field alone — analogous
to Clarivate's per-ESI-field thresholds.  Clarivate publishes ~250 per
field; HCA_TARGET=250 gives thresholds comparable to their examples
(Maths ≈ 7, Clinical Medicine ≈ 37).

Paper counts come from hcw_authors.parquet (n_hcw per author × field).
Citation counts come from hcw_authorships × hcw_works (cited_by_count
already in hcw_works — no extra OAX join needed).

Reference: Clarivate 2024 "Highly Cited Researchers" methodology note.

Output: WORKING/hca_crossfield_{window}_{label}.parquet
  author_idx, display_name, inst_name, inst_country,
  n_fields, cf_paper_score, cf_cite_score, qualified

Usage:
  .venv/bin/python analysis/hca_crossfield.py
  .venv/bin/python analysis/hca_crossfield.py --hca-target 300 --window 2020_2024 --label baseline
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, FIELD_NAMES, guard
from hca_extract import HCA_YEAR_MIN, HCA_YEAR_MAX

HCA_TARGET_DEFAULT = 250


# ── threshold calibration ─────────────────────────────────────────────────────

def calibrate(series_by_field: dict[int, pd.Series], target: int) -> dict[int, float]:
    """
    For each field, the value at the 1 - target/N percentile — i.e. the
    minimum score to rank in the top `target` for that field alone.
    Floor at 1.0 so division never produces Inf.
    """
    out: dict[int, float] = {}
    for fid, s in series_by_field.items():
        n   = len(s)
        pct = max(0.0, 1.0 - target / n)
        out[fid] = max(1.0, float(s.quantile(pct)))
    return out


# ── paper scores ──────────────────────────────────────────────────────────────

def paper_scores(
    hcw: pd.DataFrame,
    thresh_paper: dict[int, float],
) -> pd.Series:
    """cf_paper_score per author_idx (sum of n_hcw/thresh across fields)."""
    df = hcw[['author_idx', 'field_idx', 'n_hcw']].copy()
    df['t'] = df['field_idx'].map(thresh_paper)
    df['s'] = df['n_hcw'] / df['t']
    return df.groupby('author_idx')['s'].sum().rename('cf_paper_score')


# ── citation scores ───────────────────────────────────────────────────────────

def citation_scores(
    authorships_path: str,
    hcw_works_path: str,
    thresh_cite: dict[int, float] | None,
    target: int,
) -> tuple[pd.Series, dict[int, float]]:
    """
    Sum cited_by_count per (author_idx, field_idx) via DuckDB.
    hcw_works already carries cited_by_count and field_idx — no OAX join.
    Restricted to HCW with publication_year in [HCA_YEAR_MIN, HCA_YEAR_MAX] —
    same Clarivate-emulating window as n_hcw (hca_extract.py stage 4), so
    cf_paper_score and cf_cite_score qualify against the same time window.
    Returns (cf_cite_score Series, thresh_cite dict).
    """
    con = duckdb.connect()
    con.execute("SET memory_limit='16GB'; SET threads=8")

    t0 = time.time()
    ac = con.execute(f"""
        SELECT a.author_idx,
               w.field_idx,
               SUM(w.cited_by_count) AS sum_cites
        FROM read_parquet('{authorships_path}') a
        JOIN read_parquet('{hcw_works_path}')   w ON w.work_idx = a.work_idx
        WHERE a.author_idx IS NOT NULL
          AND w.publication_year BETWEEN {HCA_YEAR_MIN} AND {HCA_YEAR_MAX}
        GROUP BY a.author_idx, w.field_idx
    """).df()
    con.close()
    print(f"  {len(ac):,} (author × field) citation rows  [{time.time()-t0:.1f}s]")

    if thresh_cite is None:
        thresh_cite = calibrate(
            {fid: g['sum_cites'] for fid, g in ac.groupby('field_idx')},
            target,
        )

    ac['t'] = ac['field_idx'].map(thresh_cite)
    ac['s'] = ac['sum_cites'] / ac['t']
    return ac.groupby('author_idx')['s'].sum().rename('cf_cite_score'), thresh_cite


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window',     default='2020_2024')
    parser.add_argument('--label',      default='baseline')
    parser.add_argument('--hca-target', type=int, default=HCA_TARGET_DEFAULT,
                        help='Target researchers per field for threshold calibration '
                             f'(default {HCA_TARGET_DEFAULT})')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Rebuild stale output without prompting')
    args = parser.parse_args()

    paths = load_config()
    w     = paths.working

    inputs = []
    for fname in ('hcw_authors.parquet', 'hcw_authorships.parquet', 'hcw_works.parquet'):
        if not (w / fname).exists():
            print(f'ERROR: {fname} not found — run hca_extract.py first')
            sys.exit(1)
        inputs.append(str(w / fname))

    out_path = w / f'hca_crossfield_{args.window}_{args.label}.parquet'
    if not guard.ensure_fresh(out_path, *inputs, script=__file__, auto_yes=args.yes,
                              label='hca_crossfield'):
        return
    t0 = time.time()

    # ── 1. load ───────────────────────────────────────────────────────────────
    print('Loading hcw_authors...')
    hcw = pd.read_parquet(w / 'hcw_authors.parquet')
    print(f'  {len(hcw):,} rows  |  '
          f'{hcw["author_idx"].nunique():,} authors  |  '
          f'{hcw["field_idx"].nunique()} fields')

    # ── 2. paper thresholds + scores ─────────────────────────────────────────
    print(f'\nCalibrating paper thresholds (hca_target={args.hca_target})...')
    thresh_paper = calibrate(
        {fid: g['n_hcw'] for fid, g in hcw.groupby('field_idx')},
        args.hca_target,
    )
    sep = '-' * 68
    print(f'\n  {sep}')
    print(f"  {'fid':>4}  {'field':<22}  {'n_authors':>10}  {'thresh_paper':>12}")
    print(f'  {sep}')
    for fid, t in sorted(thresh_paper.items()):
        n = (hcw['field_idx'] == fid).sum()
        print(f"  {fid:>4}  {FIELD_NAMES.get(fid,''):<22}  {n:>10,}  {t:>12.1f}")

    print('\nComputing paper scores...')
    cf_paper = paper_scores(hcw, thresh_paper)
    n_pp = (cf_paper >= 1.0).sum()
    print(f'  cf_paper_score >= 1.0: {n_pp:,}  ({n_pp*100/len(cf_paper):.1f}% of all authors)')

    # ── 3. citation thresholds + scores ──────────────────────────────────────
    print('\nComputing citation scores...')
    cf_cite, thresh_cite = citation_scores(
        str(w / 'hcw_authorships.parquet'),
        str(w / 'hcw_works.parquet'),
        thresh_cite=None,
        target=args.hca_target,
    )
    n_cc = (cf_cite >= 1.0).sum()
    print(f'  cf_cite_score  >= 1.0: {n_cc:,}')

    # ── 4. assemble ───────────────────────────────────────────────────────────
    print('\nAssembling output...')

    inst_modal = (
        hcw.dropna(subset=['inst_name'])
           .groupby(['author_idx', 'inst_name', 'inst_country'])
           .size()
           .reset_index(name='_n')
           .sort_values('_n', ascending=False)
           .drop_duplicates('author_idx')
           .set_index('author_idx')[['inst_name', 'inst_country']]
    )
    names    = hcw.groupby('author_idx')['display_name'].first()
    n_fields = hcw.groupby('author_idx').size().rename('n_fields')

    result = (
        cf_paper.to_frame()
        .join(cf_cite,    how='outer')
        .join(names)
        .join(inst_modal)
        .join(n_fields)
    )
    result['cf_paper_score'] = result['cf_paper_score'].fillna(0.0)
    result['cf_cite_score']  = result['cf_cite_score'].fillna(0.0)
    result['n_fields']       = result['n_fields'].fillna(0).astype(int)
    result['qualified']      = (
        (result['cf_paper_score'] >= 1.0) &
        (result['cf_cite_score']  >= 1.0)
    )
    result = (
        result.reset_index()
        .rename(columns={'author_idx': 'author_idx'})
        [['author_idx', 'display_name', 'inst_name', 'inst_country',
          'n_fields', 'cf_paper_score', 'cf_cite_score', 'qualified']]
        .sort_values('cf_paper_score', ascending=False)
    )

    # ── 5. summary ────────────────────────────────────────────────────────────
    n_qual  = int(result['qualified'].sum())
    n_total = len(result)

    print(f'\n{"═"*68}')
    print(f'Cross-Field summary  (hca_target={args.hca_target})')
    print(f'{"═"*68}')
    print(f'  Total authors:                {n_total:>9,}')
    print(f'  cf_paper_score >= 1.0:        {n_pp:>9,}  ({n_pp*100/n_total:.1f}%)')
    print(f'  cf_cite_score  >= 1.0:        {n_cc:>9,}  ({n_cc*100/n_total:.1f}%)')
    print(f'  Both >= 1.0  (qualified):     {n_qual:>9,}  ({n_qual*100/n_total:.1f}%)')

    print(f'\n  n_fields among qualified:')
    for nf, cnt in result[result['qualified']]['n_fields'].value_counts().sort_index().items():
        print(f'    {nf} field{"s" if nf > 1 else " "}: {cnt:,}')

    print(f'\n  Top 20 by cf_paper_score:')
    print(f"  {'display_name':<32} {'nf':>3}  {'paper':>7}  {'cite':>7}  q  inst_country")
    print('  ' + '-'*80)
    for _, r in result.head(20).iterrows():
        print(f"  {str(r['display_name']):<32} {r['n_fields']:>3}  "
              f"{r['cf_paper_score']:>7.3f}  {r['cf_cite_score']:>7.3f}  "
              f"{'Y' if r['qualified'] else 'N'}  "
              f"{str(r['inst_name'])[:30]}  {r['inst_country']}")

    print(f'\n  Threshold table:')
    print(f"  {'fid':>4}  {'field':<22}  {'thresh_paper':>12}  {'thresh_cite':>12}")
    print(f'  {sep}')
    for fid in sorted(thresh_paper):
        print(f"  {fid:>4}  {FIELD_NAMES.get(fid,''):<22}  "
              f"{thresh_paper[fid]:>12.1f}  {thresh_cite.get(fid, 0):>12.0f}")

    # ── 6. write ──────────────────────────────────────────────────────────────
    result.to_parquet(out_path, index=False)
    guard.record_build(out_path, *inputs, script=__file__, build_seconds=time.time() - t0)
    print(f'\nWrote {len(result):,} rows → {out_path.name}')


if __name__ == '__main__':
    main()
