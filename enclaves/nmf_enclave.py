"""
nmf_enclave.py — Stage 3: NMF topic clustering of enclave HCW per OA field.

For each OA field, runs NMF on TF-IDF of enclave work titles to discover
distinct topic sub-communities within the enclave.

Enclave definition: source_v < 1  AND  mean_citer_v < 1

k (number of topics) adapts to enclave size:
  n_enc < MIN_ENC  → skip
  otherwise        → min(nmf_k, n_enc // MIN_PER_TOPIC)

Input:  WORKING/enclave_hcw_{window}_{label}.parquet
Output: WORKING/enclave_nmf_{window}_{label}.parquet

Usage:
  .venv/bin/python enclaves/nmf_enclave.py
  .venv/bin/python enclaves/nmf_enclave.py --window 2020_2024 --label baseline --nmf-k 10
"""

import sys
import time
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import NMF

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config

FIELD_NAMES = {
    11: 'Ag&Bio',      12: 'Arts&Hum',    13: 'Biochem',     14: 'Business',
    15: 'ChemEng',     16: 'Chemistry',   17: 'CompSci',     18: 'Decision',
    19: 'Earth',       20: 'Economics',   21: 'Energy',      22: 'Engineering',
    23: 'EnvSci',      24: 'Immunol',     25: 'Materials',   26: 'Maths',
    27: 'Medicine',    28: 'Neurosci',    29: 'Nursing',     30: 'Pharma',
    31: 'Physics',     32: 'Psychology',  33: 'SocSci',      34: 'Vet',
    35: 'Dentistry',   36: 'HealthProf',
}

NMF_SEED      = 42
MIN_ENC       = 30   # skip field if fewer enclave works than this
MIN_PER_TOPIC = 20   # minimum works per topic; caps k

# Artifact tokens added beyond sklearn's English stop list.
# MathML XML tags leaking through title text
_MATHML = {'mml', 'mi', 'mo', 'mrow', 'mn', 'msub', 'msup', 'mfrac', 'mover',
           'math', 'mathml', 'http', 'https', 'xmlns', 'www', 'w3', 'org',
           '1998', 'inline', 'display', 'stretchy', 'false', 'true',
           'altimg', 'svg', 'alttext', 'overflow', 'scroll'}
# Romance-language function words (Spanish/Portuguese/French articles & prepositions)
_ROMANCE = {'la', 'el', 'del', 'en', 'em', 'da', 'de', 'do', 'um',
            'los', 'las', 'le', 'les', 'des', 'un', 'una', 'das', 'der', 'die'}
# Generic non-English content words too common to be distinctive within their cluster
_GENERIC = {'karakter', 'pelajar'}
EXTRA_STOP_WORDS = _MATHML | _ROMANCE | _GENERIC


def _top_terms(component: np.ndarray, terms: np.ndarray, n: int = 8) -> str:
    return '  |  '.join(terms[component.argsort()[-n:][::-1]])


def compute_field_nmf(
    titles: list[str],
    k: int,
    seed: int = NMF_SEED,
) -> tuple[np.ndarray, list[str]]:
    """
    Fit TF-IDF + NMF on titles. Returns (W, topic_labels).

    W            : (n_docs, k) document-topic matrix
    topic_labels : list of k strings, each the top-5 terms for that topic
    """
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
    stop_words = list(ENGLISH_STOP_WORDS | {'sub'} | EXTRA_STOP_WORDS)

    vec = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=2,
        max_df=0.85,
        sublinear_tf=True,
        stop_words=stop_words,
    )
    X    = vec.fit_transform(titles)
    terms = vec.get_feature_names_out()

    nmf  = NMF(n_components=k, random_state=seed, max_iter=400)
    W    = nmf.fit_transform(X)

    labels = [_top_terms(nmf.components_[t], terms, n=5) for t in range(k)]
    return W, labels


def compute_enclave_nmf(
    df: pd.DataFrame,
    nmf_k: int = 10,
    seed: int = NMF_SEED,
) -> pd.DataFrame:
    enc_mask_all = (df['source_v'] < 1) & (df['mean_citer_v'] < 1)

    rows = []
    for fid, grp in df.groupby('field_idx'):
        enc_idx  = grp.index[enc_mask_all.loc[grp.index]]
        n_enc    = len(enc_idx)

        if n_enc < MIN_ENC:
            print(f'  field {fid:>3} {FIELD_NAMES.get(fid,"?"):>12}: '
                  f'skipped (n_enc={n_enc} < {MIN_ENC})')
            continue

        k = min(nmf_k, max(2, n_enc // MIN_PER_TOPIC))
        titles = df.loc[enc_idx, 'title'].fillna('').tolist()

        t0 = time.time()
        W, labels = compute_field_nmf(titles, k, seed)
        topic_idx = W.argmax(axis=1)

        print(f'  field {fid:>3} {FIELD_NAMES.get(fid,"?"):>12}: '
              f'n_enc={n_enc:>5,}  k={k}  [{time.time()-t0:.1f}s]')
        for t, lbl in enumerate(labels):
            n_t = (topic_idx == t).sum()
            print(f'    topic {t}: n={n_t:>4,}  {lbl}')

        for i, idx in enumerate(enc_idx):
            rows.append({
                'field_idx':   fid,
                'field_name':  FIELD_NAMES.get(fid, '?'),
                'work_idx':    df.at[idx, 'work_idx'],
                'topic_idx':   int(topic_idx[i]),
                'topic_label': labels[topic_idx[i]],
                'source_v':    df.at[idx, 'source_v'],
                'mean_citer_v': df.at[idx, 'mean_citer_v'],
                'n_intra':     df.at[idx, 'n_intra'],
            })

    return pd.DataFrame(rows)


def print_summary(result: pd.DataFrame) -> None:
    print(f'\n{"─"*80}')
    print(f'NMF enclave topics — {len(result):,} works across '
          f'{result["field_idx"].nunique()} fields')
    print(f'{"─"*80}')
    for fid, grp in result.groupby('field_idx'):
        fname = FIELD_NAMES.get(fid, '?')
        print(f'\n{fid:>3} {fname}  (n={len(grp):,})')
        for t, tgrp in grp.groupby('topic_idx'):
            lbl = tgrp.iloc[0]['topic_label']
            print(f'    topic {t}: n={len(tgrp):>4,}  {lbl}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', default='2020_2024')
    parser.add_argument('--label',  default='baseline')
    parser.add_argument('--nmf-k',  type=int, default=10)
    args = parser.parse_args()

    paths    = load_config()
    hcw_path = paths.working / f'enclave_hcw_{args.window}_{args.label}.parquet'
    if not hcw_path.exists():
        print(f'ERROR: {hcw_path} not found — run build_enclave_hcw.py first')
        sys.exit(1)

    print(f'Loading {hcw_path.name}...')
    df = pd.read_parquet(hcw_path)
    n_enc = ((df['source_v'] < 1) & (df['mean_citer_v'] < 1)).sum()
    print(f'  {len(df):,} HCW rows  {n_enc:,} enclave')

    print(f'\nRunning NMF per field (k≤{args.nmf_k})...')
    t0 = time.time()
    result = compute_enclave_nmf(df, nmf_k=args.nmf_k)
    print(f'\nDone [{time.time()-t0:.1f}s]')

    print_summary(result)

    out_path = paths.working / f'enclave_nmf_{args.window}_{args.label}.parquet'
    result.to_parquet(out_path, index=False)
    print(f'\nSaved: {out_path}  ({len(result):,} rows)')


if __name__ == '__main__':
    main()
