"""
researcher_enclaves.py — Stage E5: frequent researchers in enclave networks.

For each named enclave, identifies the top-N most frequent authors across the
full 1-hop citation network (HCW-- seeds + all citers), then reports their OA
author statistics (h_index, works_count, cited_by_count).

Inputs:
  WORKING/enclave_nmf_{window}_{label}.parquet
  WORKING/enclave_hcw_{window}_{label}.parquet    (for source_idx)
  WORKING/corpus_references_{ymin}_{ymax}.parquet
  OPENALEX/parquet_converted/authorships/*.parquet
  OPENALEX/parquet_converted/authors/*.parquet

Outputs:
  enclaves/reports/{field_idx}_{window}_{label}_researchers.md  (one per field)
  stdout: top-1 author per enclave

Usage:
  .venv/bin/python enclaves/researcher_enclaves.py --field 14
  .venv/bin/python enclaves/researcher_enclaves.py
"""

import sys
import time
import argparse
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from util import load_config, load_settings, FIELD_NAMES, guard
from network_enclave import scan_citation_edges


# ── author scan ───────────────────────────────────────────────────────────────

def top_authors_per_enclave(
    membership: pd.DataFrame,       # cols: field_idx, enc_name, work_idx
    authorships_glob: str,
    authors_glob: str,
    top_n: int,
) -> pd.DataFrame:
    """
    Single DuckDB pass over all authorships, filtered to network works.
    Returns df: field_idx, enc_name, rnk, author_idx, n_works,
                display_name, works_count, cited_by_count, h_index
    """
    con = duckdb.connect()
    con.register('_memb', membership)

    t0 = time.time()
    con.execute(f"""
        CREATE TEMP TABLE _auth AS
        SELECT m.field_idx, m.enc_name, a.work_idx, a.author_idx
        FROM parquet_scan('{authorships_glob}') a
        JOIN _memb m ON a.work_idx = m.work_idx
        WHERE a.author_idx IS NOT NULL
    """)
    n = con.execute('SELECT COUNT(*) FROM _auth').fetchone()[0]
    print(f'  authorships scan: {n:,} rows  [{time.time()-t0:.1f}s]')

    top_df = con.execute(f"""
        WITH _counts AS (
            SELECT field_idx, enc_name, author_idx,
                   COUNT(DISTINCT work_idx) AS n_works
            FROM _auth
            GROUP BY field_idx, enc_name, author_idx
        ),
        _ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY field_idx, enc_name
                       ORDER BY n_works DESC
                   ) AS rnk
            FROM _counts
        )
        SELECT * FROM _ranked WHERE rnk <= {top_n}
    """).df()

    if top_df.empty:
        con.close()
        return top_df

    author_ids = top_df['author_idx'].dropna().astype(int).unique().tolist()
    con.register('_aid', pd.DataFrame({'author_idx': author_ids}))

    au_df = con.execute(f"""
        SELECT
            a.author_idx,
            a.display_name,
            a.works_count,
            a.cited_by_count,
            a.h_index
        FROM parquet_scan('{authors_glob}') a
        JOIN _aid t ON a.author_idx = t.author_idx
    """).df()
    con.close()

    return top_df.merge(au_df, on='author_idx', how='left')


# ── markdown report ───────────────────────────────────────────────────────────

def write_md(
    fid: int,
    enc_info: dict,             # {enc_name: {'n_hcw': int, 'n_citers': int}}
    top_auth: pd.DataFrame,
    out_path: Path,
    window: str,
    label: str,
    top_n: int,
) -> None:
    field_name = FIELD_NAMES.get(fid, str(fid))
    lines = [
        f'# Researcher analysis — {field_name} (field {fid}, {window}, {label})',
        '',
        (f'Top-{top_n} authors by distinct works in each enclave\'s 1-hop citation network '
         '(HCW-- seeds + all citers with any authorship record).'),
        '',
    ]

    for enc_name in sorted(enc_info, key=lambda n: -enc_info[n]['n_hcw']):
        info  = enc_info[enc_name]
        n_net = info['n_hcw'] + info['n_citers']
        lines += [
            f'## {enc_name}  (n_hcw--={info["n_hcw"]:,}, network={n_net:,} works)',
            '',
            '| # | author | h_index | works_count | cited_by | in_network |',
            '|---|--------|---------|-------------|----------|------------|',
        ]
        enc_top = top_auth[
            (top_auth['field_idx'] == fid) & (top_auth['enc_name'] == enc_name)
        ].sort_values('rnk')

        for _, row in enc_top.iterrows():
            # escape pipe characters in author names
            name = (str(row.get('display_name') or '—')).replace('|', '/')
            h    = f'{int(row["h_index"])}' if pd.notna(row.get('h_index')) else '—'
            wc   = f'{int(row["works_count"]):,}' if pd.notna(row.get('works_count')) else '—'
            cbc  = f'{int(row["cited_by_count"]):,}' if pd.notna(row.get('cited_by_count')) else '—'
            lines.append(
                f'| {int(row["rnk"])} | {name} | {h} | {wc} | {cbc} | {int(row["n_works"]):,} |'
            )
        lines += ['']

    out_path.write_text('\n'.join(lines))
    print(f'  → {out_path.name}')


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', default='2020_2024')
    parser.add_argument('--label',  default='baseline')
    parser.add_argument('--top-n',  type=int, default=3)
    parser.add_argument('--field',  type=int, default=0,
                        help='process only this field_idx (default: all fields)')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Rebuild stale reports without prompting')
    args = parser.parse_args()

    paths    = load_config()
    settings = load_settings()

    nmf_path  = paths.working / f'enclave_nmf_{args.window}_{args.label}.parquet'
    hcw_path  = paths.working / f'enclave_hcw_{args.window}_{args.label}.parquet'
    cr_path   = str(paths.working /
                    f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')
    out_dir   = Path(__file__).parent / 'reports'
    out_dir.mkdir(exist_ok=True)

    oa        = paths.openalex
    auth_glob = str(oa / 'authorships' / '*.parquet')
    au_glob   = str(oa / 'authors'     / '*.parquet')

    for p in (nmf_path, hcw_path):
        if not p.exists():
            print(f'ERROR: {p} not found')
            sys.exit(1)

    # ── load NMF + HCW source_idx ─────────────────────────────────────────────
    print('Loading NMF enclave assignments...')
    nmf_df = pd.read_parquet(nmf_path)
    if 'topic_name' not in nmf_df.columns:
        nmf_df['topic_name'] = ''
    nmf_df['enc_name'] = nmf_df['topic_name'].where(
        nmf_df['topic_name'].str.strip() != '', 'Topic ' + nmf_df['topic_idx'].astype(str)
    )
    if args.field:
        nmf_df = nmf_df[nmf_df['field_idx'] == args.field]
        if nmf_df.empty:
            print(f'ERROR: no NMF rows for field {args.field}')
            sys.exit(1)

    # The authorship scan below runs once for every targeted field together,
    # so a single representative freshness check gates the whole batch.
    report_inputs = [str(nmf_path), str(hcw_path), cr_path, auth_glob, au_glob]
    report_paths = [
        out_dir / f'{fid}_{args.window}_{args.label}_researchers.md'
        for fid in sorted(nmf_df['field_idx'].unique())
    ]
    if not guard.ensure_fresh(report_paths[0], *report_inputs, script=__file__,
                              auto_yes=args.yes, label='researcher enclave reports'):
        return

    hcw_src = pd.read_parquet(hcw_path, columns=['field_idx', 'work_idx', 'source_idx'])
    if args.field:
        hcw_src = hcw_src[hcw_src['field_idx'] == args.field]
    nmf_df = nmf_df.merge(hcw_src, on=['field_idx', 'work_idx'], how='left')

    n_enc = nmf_df.groupby(['field_idx', 'enc_name']).ngroups
    print(f'  {len(nmf_df):,} HCW-- rows  |  '
          f'{nmf_df["field_idx"].nunique()} field(s)  |  {n_enc} enclaves')

    # ── single corpus_references scan for all seeds ───────────────────────────
    all_seeds = set(nmf_df['work_idx'])
    print(f'Scanning corpus_references ({len(all_seeds):,} seeds)...')
    cite_edges = scan_citation_edges(cr_path, all_seeds)

    # ── build network membership: (field_idx, enc_name, work_idx) ────────────
    print('Building enclave network membership...')
    membership_rows: list[dict] = []
    field_enc_info: dict[int, dict[str, dict]] = defaultdict(dict)

    for (fid, enc_name), grp in nmf_df.groupby(['field_idx', 'enc_name']):
        fid      = int(fid)
        enc_name = str(enc_name)
        seeds    = set(grp['work_idx'])
        citers   = (set(cite_edges[cite_edges['cited_idx'].isin(seeds)]['citer_idx'])
                    - seeds)
        field_enc_info[fid][enc_name] = {
            'n_hcw': len(seeds), 'n_citers': len(citers),
        }
        for w in seeds | citers:
            membership_rows.append({
                'field_idx': fid, 'enc_name': enc_name, 'work_idx': int(w),
            })

    membership   = pd.DataFrame(membership_rows)
    unique_works = membership['work_idx'].nunique()
    print(f'  {len(membership):,} (enclave×work) pairs  |  {unique_works:,} unique works')

    # ── one authorship scan covering all enclave networks ─────────────────────
    print(f'Finding top-{args.top_n} authors per enclave...')
    top_auth = top_authors_per_enclave(membership, auth_glob, au_glob, args.top_n)
    print(f'  {len(top_auth):,} top-author rows across {top_auth["enc_name"].nunique()} enclaves')

    # ── write per-field MD reports ────────────────────────────────────────────
    print('\nWriting reports...')
    for fid in sorted(field_enc_info):
        out_path = out_dir / f'{fid}_{args.window}_{args.label}_researchers.md'
        write_md(fid, field_enc_info[fid], top_auth, out_path,
                 args.window, args.label, args.top_n)
        guard.record_build(out_path, *report_inputs, script=__file__)

    # ── stdout: top-1 per enclave ─────────────────────────────────────────────
    if not top_auth.empty:
        top1 = top_auth[top_auth['rnk'] == 1].sort_values(
            ['field_idx', 'n_works'], ascending=[True, False]
        )
        print('\nTop-1 author per enclave:')
        sep = '─' * 100
        print(sep)
        for _, row in top1.iterrows():
            name = str(row.get('display_name') or '—')
            h    = f'h={int(row["h_index"])}' if pd.notna(row.get('h_index')) else 'h=—'
            print(f'  {int(row["field_idx"]):>3}  {str(row["enc_name"]):30s}  '
                  f'{name:40s}  n={int(row["n_works"]):,}  {h}')
        print(sep)


if __name__ == '__main__':
    main()
