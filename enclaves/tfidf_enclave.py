"""
tfidf_enclave.py — Stage 2: TF-IDF characterisation of enclave vs non-enclave HCW.

For each OA field, fits a TF-IDF vectorizer on all HCW titles in that field,
then ranks terms by how much more they appear in enclave works (source_v < 1
AND mean_citer_v < 1) relative to non-enclave HCW.

Ranking score: lift × mean_enc
  mean_enc  = mean TF-IDF score across enclave works
  mean_non  = mean TF-IDF score across non-enclave HCW
  lift      = mean_enc / (mean_non + ε)

Input:  WORKING/enclave_hcw_{window}_{label}.parquet
Output: WORKING/enclave_tfidf_{window}_{label}.parquet

Usage:
  .venv/bin/python enclaves/tfidf_enclave.py
  .venv/bin/python enclaves/tfidf_enclave.py --window 2020_2024 --label baseline --top-n 50
"""

import sys
import time
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, FIELD_NAMES, guard

EPS = 1e-9


def compute_field_tfidf(
    titles: list[str],
    enc_mask: np.ndarray,
    top_n: int,
) -> pd.DataFrame:
    """
    Fit TF-IDF on all titles in one field, return top_n enclave-distinctive terms.

    titles   : list of title strings (all HCW in the field)
    enc_mask : boolean array, True = enclave work
    top_n    : terms to return

    Returns DataFrame: term, mean_enc, mean_non, lift, score, rank
    """
    vec = TfidfVectorizer(
        min_df=2,
        max_df=0.95,
        stop_words='english',
        ngram_range=(1, 2),
        sublinear_tf=True,
    )
    X = vec.fit_transform(titles)
    terms = vec.get_feature_names_out()

    mean_enc = np.asarray(X[enc_mask].mean(axis=0)).ravel()
    mean_non = np.asarray(X[~enc_mask].mean(axis=0)).ravel()
    lift     = mean_enc / (mean_non + EPS)
    score    = lift * mean_enc

    top_idx = np.argsort(-score)[:top_n]
    return pd.DataFrame({
        'term':     terms[top_idx],
        'mean_enc': mean_enc[top_idx].round(6),
        'mean_non': mean_non[top_idx].round(6),
        'lift':     lift[top_idx].round(3),
        'score':    score[top_idx].round(6),
        'rank':     np.arange(1, len(top_idx) + 1),
    })


def compute_enclave_tfidf(df: pd.DataFrame, top_n: int = 50) -> pd.DataFrame:
    enc_mask_all = (df['source_v'] < 1) & (df['mean_citer_v'] < 1)

    rows = []
    for fid, grp in df.groupby('field_idx'):
        enc_mask = enc_mask_all.loc[grp.index].values
        n_enc = enc_mask.sum()
        n_non = (~enc_mask).sum()

        if n_enc < 10 or n_non < 10:
            print(f'  field {fid}: skipped (enc={n_enc}, non={n_non})')
            continue

        titles = grp['title'].fillna('').tolist()
        field_df = compute_field_tfidf(titles, enc_mask, top_n)
        field_df.insert(0, 'field_idx', fid)
        field_df.insert(1, 'field_name', FIELD_NAMES.get(fid, '?'))
        rows.append(field_df)
        print(f'  field {fid:>3} {FIELD_NAMES.get(fid,"?"):>12}: '
              f'{n_enc:>6,} enc  {n_non:>7,} non  '
              f'top term: {field_df.iloc[0]["term"]!r}')

    return pd.concat(rows, ignore_index=True)


def print_summary(df: pd.DataFrame, n: int = 10) -> None:
    print(f'\nTop {n} enclave-distinctive terms per field:')
    print('─' * 72)
    for fid, grp in df.groupby('field_idx'):
        top = grp.nsmallest(n, 'rank')
        terms = '  |  '.join(
            f'{r["term"]} ({r["lift"]:.1f}×)' for _, r in top.iterrows()
        )
        print(f'{fid:>3} {FIELD_NAMES.get(fid,"?"):>12}:  {terms}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', default='2020_2024')
    parser.add_argument('--label',  default='baseline')
    parser.add_argument('--top-n',  type=int, default=50)
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Rebuild stale output without prompting')
    args = parser.parse_args()

    paths = load_config()
    hcw_path = paths.working / f'enclave_hcw_{args.window}_{args.label}.parquet'
    if not hcw_path.exists():
        print(f'ERROR: {hcw_path} not found — run build_enclave_hcw.py first')
        sys.exit(1)

    out_path = paths.working / f'enclave_tfidf_{args.window}_{args.label}.parquet'
    if not guard.ensure_fresh(out_path, str(hcw_path), script=__file__, auto_yes=args.yes,
                              label='enclave_tfidf'):
        return

    print(f'Loading {hcw_path.name}...')
    df = pd.read_parquet(hcw_path)
    print(f'  {len(df):,} HCW rows, '
          f'{(df["source_v"] < 1).sum():,} low-source, '
          f'{((df["source_v"] < 1) & (df["mean_citer_v"] < 1)).sum():,} enclave')

    print('\nComputing per-field TF-IDF...')
    t0 = time.time()
    result = compute_enclave_tfidf(df, top_n=args.top_n)
    print(f'Done [{time.time()-t0:.1f}s]  {len(result):,} term rows across '
          f'{result["field_idx"].nunique()} fields')

    print_summary(result, n=10)

    result.to_parquet(out_path, index=False)
    guard.record_build(out_path, str(hcw_path), script=__file__, build_seconds=time.time() - t0)
    print(f'\nSaved: {out_path}')


if __name__ == '__main__':
    main()
