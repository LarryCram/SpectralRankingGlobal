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
from util import load_config, FIELD_NAMES, guard

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
        topic_idx_raw = W.argmax(axis=1)

        # sort topics by size descending; remap indices so topic 0 is largest
        counts = np.bincount(topic_idx_raw, minlength=k)
        order  = counts.argsort()[::-1]          # original idx → rank position
        remap  = np.empty(k, dtype=int)
        remap[order] = np.arange(k)
        topic_idx    = remap[topic_idx_raw]
        labels       = [labels[o] for o in order]

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


def apply_names(nmf_path: Path, field_idx: int, json_src: str) -> None:
    """
    Apply Claude's merge+name JSON to the NMF parquet for one field.

    json_src : path to a JSON file, or '-' to read from stdin.

    Merge semantics: all topic_idx values in a merge set are remapped to the
    minimum index in that set.  topic_name is set from the names dict.
    Saves the parquet in place and prints the resulting named summary.
    """
    import json

    if json_src == '-':
        payload = json.loads(sys.stdin.read())
    elif json_src.lstrip().startswith('{'):
        payload = json.loads(json_src)
    else:
        payload = json.loads(Path(json_src).read_text())

    merges: list[list[int]] = [[int(i) for i in m] for m in payload.get('merges', [])]
    names:  dict[int, str]  = {int(k): v for k, v in payload['names'].items()}

    # build remap: absorbed indices → canonical (lowest) index
    remap: dict[int, int] = {}
    for merge_set in merges:
        canon = min(merge_set)
        for idx in merge_set:
            remap[idx] = canon

    df = pd.read_parquet(nmf_path)
    mask = df['field_idx'] == field_idx
    if not mask.any():
        print(f'ERROR: field_idx={field_idx} not in {nmf_path.name}')
        sys.exit(1)

    if remap:
        df.loc[mask, 'topic_idx'] = df.loc[mask, 'topic_idx'].replace(remap)

    if 'topic_name' not in df.columns:
        df['topic_name'] = ''
    df.loc[mask, 'topic_name'] = df.loc[mask, 'topic_idx'].replace(names).where(
        df.loc[mask, 'topic_idx'].isin(names), ''
    )

    df.to_parquet(nmf_path, index=False)

    field_name = FIELD_NAMES.get(field_idx, str(field_idx))
    print(f'{field_idx} {field_name}  →  {nmf_path.name}')
    grp = df[mask].groupby('topic_idx').agg(
        n    = ('work_idx',    'count'),
        name = ('topic_name',  'first'),
        terms= ('topic_label', 'first'),
    ).sort_values('n', ascending=False)
    for tid, row in grp.iterrows():
        print(f'  {int(tid):>2}  n={int(row["n"]):>4,}  '  # type: ignore[arg-type]
              f'{str(row["name"]):30s}  {row["terms"]}')


def _build_prompt(nmf_path: Path, field_idx: int) -> str:
    """Build and return the merge+name prompt string for one field."""
    if not nmf_path.exists():
        print(f'ERROR: {nmf_path} not found — run nmf_enclave.py first')
        sys.exit(1)
    df = pd.read_parquet(nmf_path)
    grp = df[df['field_idx'] == field_idx]
    if grp.empty:
        print(f'ERROR: field_idx={field_idx} not found in {nmf_path.name}')
        sys.exit(1)

    field_name = FIELD_NAMES.get(field_idx, str(field_idx))
    topics = (
        grp.groupby('topic_idx')
        .agg(n=('work_idx', 'count'), terms=('topic_label', 'first'))
        .sort_index()
        .reset_index()
    )

    lines = [
        f'Field: {field_name} (field_idx={field_idx})',
        f'These are NMF topic groups from enclave works (highly cited, low-influence',
        f'source and citing context) clustered by TF-IDF of their titles.',
        f'Each group is described by its top TF-IDF terms ("|" separated).',
        f'',
        f'Tasks (in order):',
        f'1. Identify groups that represent the same research area and should be merged.',
        f'   List each merge as a set of group indices.',
        f'2. Name each resulting group (≤3 words). For merged groups use the lowest',
        f'   index in the merge set as the key. Omit indices absorbed into a merge.',
        f'',
        f'Return ONLY a JSON object:',
        f'  "merges": [[i, j, ...], ...]  — omit key if no merges',
        f'  "names":  {{"idx": "name", ...}}  — one entry per resulting group',
        f'',
        f'Groups:',
    ]
    for _, row in topics.iterrows():
        lines.append(f'  {int(row["topic_idx"])}: n={int(row["n"]):,}  {row["terms"]}')
    return '\n'.join(lines)


def print_claude_prompt(nmf_path: Path, field_idx: int) -> None:
    print(_build_prompt(nmf_path, field_idx))


def auto_name_field(nmf_path: Path, field_idx: int,
                    model: str = 'gemini-2.5-flash') -> None:
    """
    Call the Gemini API (via OpenAI-compatible endpoint) to merge and name
    NMF topics for one field, then apply results to the parquet.

    Requires GEMINI_API_KEY environment variable.
    """
    import json
    import os
    from openai import OpenAI

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / '.env')

    api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
    if not api_key:
        print('ERROR: set GEMINI_API_KEY in .env or environment')
        sys.exit(1)

    client = OpenAI(
        api_key=api_key,
        base_url='https://generativelanguage.googleapis.com/v1beta/openai/',
    )

    prompt = _build_prompt(nmf_path, field_idx)
    field_name = FIELD_NAMES.get(field_idx, str(field_idx))
    print(f'  field {field_idx} {field_name} — calling {model}...')

    response = client.chat.completions.create(
        model=model,
        messages=[{'role': 'user', 'content': prompt}],
    )
    raw = (response.choices[0].message.content or '').strip()

    # strip markdown fences if the model wraps the JSON
    if raw.startswith('```'):
        raw = '\n'.join(raw.split('\n')[1:]).rstrip('`').strip()

    print(f'  {raw}')
    try:
        json.loads(raw)  # validate before applying
    except json.JSONDecodeError as e:
        print(f'ERROR: response is not valid JSON — {e}')
        print(f'  full response ({len(raw)} chars): {repr(raw)}')
        sys.exit(1)

    apply_names(nmf_path, field_idx, raw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window',       default='2020_2024')
    parser.add_argument('--label',        default='baseline')
    parser.add_argument('--nmf-k',        type=int, default=10)
    parser.add_argument('--prompt-field',    type=int, default=0,
                        help='print naming prompt for this field_idx and exit')
    parser.add_argument('--apply-names',     default='',
                        help='JSON file (or - for stdin) with merges+names; '
                             'requires --prompt-field to specify the field')
    parser.add_argument('--auto-name-field', type=int, default=0,
                        help='call Gemini API to merge+name topics for this field_idx')
    parser.add_argument('--model',           default='gemini-2.5-flash',
                        help='Gemini model for --auto-name-field (default: gemini-2.0-flash)')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Rebuild stale output without prompting')
    args = parser.parse_args()

    paths = load_config()
    nmf_path = paths.working / f'enclave_nmf_{args.window}_{args.label}.parquet'

    if args.auto_name_field:
        auto_name_field(nmf_path, args.auto_name_field, model=args.model)
        return

    if args.apply_names:
        if not args.prompt_field:
            print('ERROR: --apply-names requires --prompt-field <field_idx>')
            sys.exit(1)
        apply_names(nmf_path, args.prompt_field, args.apply_names)
        return

    if args.prompt_field:
        print_claude_prompt(nmf_path, args.prompt_field)
        return

    hcw_path = paths.working / f'enclave_hcw_{args.window}_{args.label}.parquet'
    if not hcw_path.exists():
        print(f'ERROR: {hcw_path} not found — run build_enclave_hcw.py first')
        sys.exit(1)

    out_path = paths.working / f'enclave_nmf_{args.window}_{args.label}.parquet'
    if not guard.ensure_fresh(out_path, str(hcw_path), script=__file__, auto_yes=args.yes,
                              label='enclave_nmf'):
        return

    print(f'Loading {hcw_path.name}...')
    df = pd.read_parquet(hcw_path)
    n_enc = ((df['source_v'] < 1) & (df['mean_citer_v'] < 1)).sum()
    print(f'  {len(df):,} HCW rows  {n_enc:,} enclave')

    print(f'\nRunning NMF per field (k≤{args.nmf_k})...')
    t0 = time.time()
    result = compute_enclave_nmf(df, nmf_k=args.nmf_k)
    print(f'\nDone [{time.time()-t0:.1f}s]')

    print_summary(result)

    result.to_parquet(out_path, index=False)
    guard.record_build(out_path, str(hcw_path), script=__file__, build_seconds=time.time() - t0)
    print(f'\nSaved: {out_path}  ({len(result):,} rows)')


if __name__ == '__main__':
    main()
