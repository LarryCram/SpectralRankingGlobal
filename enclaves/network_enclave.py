"""
network_enclave.py — Stage E4: 1-hop citation network per named enclave.

Reads the AI-named enclave assignments from nmf_enclave.py and builds a
separate 1-hop citation network for each enclave within each field.

HCW quadrant classification:
  HCW++  source_v ≥ 1, mean_citer_v ≥ 1
  HCW+-  source_v ≥ 1, mean_citer_v < 1
  HCW-+  source_v < 1, mean_citer_v ≥ 1
  HCW--  source_v < 1, mean_citer_v < 1  ← enclave seeds

NMF groups HCW-- by title similarity → AI names each group → named enclave.
One 1-hop network is built per (field, enclave).

Inputs:
  WORKING/enclave_nmf_{window}_{label}.parquet    HCW-- with topic_name
  WORKING/enclave_hcw_{window}_{label}.parquet    all HCW, for source_idx
  WORKING/corpus_references_{ymin}_{ymax}.parquet
  OPENALEX/parquet_converted/sources.parquet

Outputs:
  enclaves/reports/{field_idx}_{window}_{label}.md   one report per field
  stdout: per-field summary table

Usage:
  .venv/bin/python enclaves/network_enclave.py
  .venv/bin/python enclaves/network_enclave.py --field 26
  .venv/bin/python enclaves/network_enclave.py --window 2020_2024 --label baseline
"""

import sys
import time
import argparse
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config, load_settings, FIELD_NAMES, guard


# ── union-find ────────────────────────────────────────────────────────────────

def _find_components(nodes: set, edges: list[tuple]) -> list[set]:
    """
    Returns list of node sets (one per connected component), sorted by
    size descending.  Edges referencing nodes not in 'nodes' are ignored.
    """
    parent = {n: n for n in nodes}

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for a, b in edges:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

    groups: dict = defaultdict(set)
    for n in nodes:
        groups[find(n)].add(n)
    return sorted(groups.values(), key=len, reverse=True)


# ── data loading ──────────────────────────────────────────────────────────────

def load_source_names(openalex_path: Path, source_ids: set) -> dict:
    """Returns {source_idx: display_name}."""
    src_path = str(openalex_path / 'sources.parquet')
    con = duckdb.connect()
    con.register('_ids', pd.DataFrame({'source_idx': list(source_ids)}))
    df = con.execute(f"""
        SELECT
            CAST(REGEXP_EXTRACT(s.id, 'S([0-9]+)$', 1) AS BIGINT) AS source_idx,
            s.display_name
        FROM parquet_scan('{src_path}') s
        JOIN _ids i
          ON CAST(REGEXP_EXTRACT(s.id, 'S([0-9]+)$', 1) AS BIGINT) = i.source_idx
    """).df()
    con.close()
    return dict(zip(df['source_idx'], df['display_name']))


def scan_citation_edges(cr_path: str, seed_work_idx: set) -> pd.DataFrame:
    """
    Single corpus_references scan.
    Returns all (citer_idx, cited_idx) where cited_idx ∈ seed_work_idx.
    """
    con = duckdb.connect()
    con.register('_seeds', pd.DataFrame({'work_idx': list(seed_work_idx)}))
    t0 = time.time()
    df = con.execute(f"""
        SELECT cr.citer_idx, cr.cited_idx
        FROM parquet_scan('{cr_path}') cr
        JOIN _seeds s ON s.work_idx = cr.cited_idx
    """).df()
    con.close()
    print(f'  {len(df):,} citation edges  [{time.time()-t0:.1f}s]')
    return df


# ── per-enclave network analysis ──────────────────────────────────────────────

def analyse_enclave(
    fid: int,
    enc_name: str,
    enc_seeds: set,
    enc_edges: pd.DataFrame,
    seed_source: dict,
    source_names: dict,
    hcw_quad: dict,
    work_v_dict: dict,
    field_quad_totals: dict,
    top_n: int = 4,
) -> dict:
    """
    Build 1-hop network for one enclave and return stats dict.

    fid               : field index (for quadrant lookups)
    enc_seeds         : work_idx of HCW-- in this enclave
    enc_edges         : (citer_idx, cited_idx) where cited_idx ∈ enc_seeds
    seed_source       : {work_idx: source_idx} for enclave seeds
    hcw_quad          : {(field_idx, work_idx): quadrant str} for all HCW
    work_v_dict       : {work_idx: work_v} for this field's retained works
    field_quad_totals : {field_idx: {quadrant: total_count}} for penetration rates
    """
    n_edges    = len(enc_edges)
    all_citers = set(enc_edges['citer_idx']) if n_edges else set()
    n_citers   = len(all_citers)

    loop_mask = enc_edges['citer_idx'].isin(enc_seeds) if n_edges else pd.Series([], dtype=bool)
    n_loop    = int(loop_mask.sum())
    loop_pct  = 100.0 * n_loop / n_edges if n_edges else 0.0

    # count non-seed citers by HCW quadrant
    non_seed_citers = all_citers - enc_seeds
    quad: dict = {'--': 0, '-+': 0, '+-': 0, '++': 0}
    for w in non_seed_citers:
        q = hcw_quad.get((fid, w))
        if q in quad:
            quad[q] += 1

    # work_v = (v_S + mean(v_I)) / 2 for seeds and for the whole 1-hop network
    import math
    seed_wvs = [work_v_dict[w] for w in enc_seeds if w in work_v_dict]
    mean_wv_seeds = sum(seed_wvs) / len(seed_wvs) if seed_wvs else math.nan
    net_wvs = [work_v_dict[w] for w in (enc_seeds | all_citers) if w in work_v_dict]
    mean_wv_net = sum(net_wvs) / len(net_wvs) if net_wvs else math.nan

    # penetration rates: fraction of each field quadrant pool that cites this enclave
    N = field_quad_totals.get(fid, {})
    def _pct(n: int, key: str) -> float:
        d = N.get(key, 0)
        return 100.0 * n / d if d else 0.0
    pct_mp = _pct(quad['-+'], '-+')
    pct_pm = _pct(quad['+-'], '+-')
    pct_pp = _pct(quad['++'], '++')

    all_nodes  = enc_seeds | all_citers
    edges_list = list(zip(enc_edges['citer_idx'], enc_edges['cited_idx'])) if n_edges else []
    components = _find_components(all_nodes, edges_list)

    # top sources across the whole enclave
    src_counts: dict = defaultdict(int)
    for w in enc_seeds:
        sx = seed_source.get(w)
        if sx is not None:
            src_counts[int(sx)] += 1
    top_sources = sorted(src_counts.items(), key=lambda kv: -kv[1])[:top_n]
    top_src_str = '  '.join(
        f'{n}×{source_names.get(sx, f"src_{sx}")[:30]}'
        for sx, n in top_sources
    )

    # per-component detail
    comp_records = []
    for comp_nodes in components:
        comp_seeds  = comp_nodes & enc_seeds
        comp_citers = comp_nodes - enc_seeds
        if not comp_seeds:
            continue

        cs: dict = defaultdict(int)
        for w in comp_seeds:
            sx = seed_source.get(w)
            if sx is not None:
                cs[int(sx)] += 1
        comp_top = sorted(cs.items(), key=lambda kv: -kv[1])[:top_n]
        comp_top_str = '  '.join(
            f'{n}×{source_names.get(sx, f"src_{sx}")[:28]}'
            for sx, n in comp_top
        )
        comp_records.append(dict(
            n_hcw    = len(comp_seeds),
            n_citers = len(comp_citers),
            top_src  = comp_top_str,
        ))

    comp_records.sort(key=lambda x: -x['n_hcw'])
    for i, c in enumerate(comp_records):
        c['comp_id'] = i + 1

    largest = comp_records[0] if comp_records else {}

    return dict(
        enc_name      = enc_name,
        n_hcw         = len(enc_seeds),
        mean_wv_seeds = mean_wv_seeds,
        mean_wv_net   = mean_wv_net,
        n_hcw_mp    = quad['-+'],
        pct_hcw_mp  = pct_mp,
        n_hcw_pm    = quad['+-'],
        pct_hcw_pm  = pct_pm,
        n_hcw_pp    = quad['++'],
        pct_hcw_pp  = pct_pp,
        n_citers    = n_citers,
        loop_pct    = loop_pct,
        n_comp      = len(comp_records),
        lg_hcw      = largest.get('n_hcw', 0),
        lg_pct      = largest.get('n_hcw', 0) / len(enc_seeds) if enc_seeds else 0.0,
        top_src     = top_src_str,
        components  = comp_records,
    )


# ── markdown report ───────────────────────────────────────────────────────────

def write_md(
    fid: int,
    enc_records: list[dict],
    out_path: Path,
    window: str,
    label: str,
) -> None:
    field_name = FIELD_NAMES.get(fid, str(fid))
    lines = [
        f'# Enclave network report — {field_name} (field {fid}, {window}, {label})',
        '',
        '## HCW quadrants',
        '| quadrant | condition | meaning |',
        '|----------|-----------|---------|',
        '| HCW++    | v ≥ 1, \\<v\\> ≥ 1 | high-prestige, cited by high-prestige |',
        '| HCW+-    | v ≥ 1, \\<v\\> < 1 | high-prestige, cited by low-prestige  |',
        '| HCW-+    | v < 1, \\<v\\> ≥ 1 | low-prestige,  cited by high-prestige |',
        '| HCW--    | v < 1, \\<v\\> < 1 | low-prestige,  cited by low-prestige  |',
        '',
        'Enclaves are NMF topic clusters of HCW--.',
        'Quadrant counts are for non-seed citers; % is penetration into that quadrant\'s pool.',
        '⟨v⟩-- = mean work_v of HCW-- seeds; ⟨v⟩net = mean work_v of all network works with retained source.',
        'work_v = (source_v + mean_inst_v) / 2; mean_inst_v falls back to source_v when no retained institutions.',
        '',
        '## Summary',
        '',
        '| Enclave | n_hcw-- | ⟨v⟩-- | ⟨v⟩net | n_hcw-+ | %-+ | n_hcw+- | %+- | n_hcw++ | %++ | citers | loop% | n_comp | lg_hcw | lg% |',
        '|---------|---------|-------|--------|---------|-----|---------|-----|---------|-----|--------|-------|--------|--------|-----|',
    ]

    for r in sorted(enc_records, key=lambda x: -x['n_hcw']):
        lines.append(
            f'| {r["enc_name"]} '
            f'| {r["n_hcw"]:,} '
            f'| {r["mean_wv_seeds"]:.3f} '
            f'| {r["mean_wv_net"]:.3f} '
            f'| {r["n_hcw_mp"]:,} '
            f'| {r["pct_hcw_mp"]:.1f}% '
            f'| {r["n_hcw_pm"]:,} '
            f'| {r["pct_hcw_pm"]:.1f}% '
            f'| {r["n_hcw_pp"]:,} '
            f'| {r["pct_hcw_pp"]:.1f}% '
            f'| {r["n_citers"]:,} '
            f'| {r["loop_pct"]:.1f}% '
            f'| {r["n_comp"]} '
            f'| {r["lg_hcw"]:,} '
            f'| {r["lg_pct"]:.0%} |'
        )

    lines += ['', '## Enclave detail', '']

    for r in sorted(enc_records, key=lambda x: -x['n_hcw']):
        lines += [f'### {r["enc_name"]}  (n={r["n_hcw"]:,})', '']
        # show component table; truncate to largest row only when lg% > 80%
        show_comps = (r['components'][:1] if r['lg_pct'] > 0.80
                      else r['components'])
        if show_comps:
            lines.append('| comp | n_hcw-- | citers | top sources |')
            lines.append('|------|---------|--------|-------------|')
            for c in show_comps:
                lines.append(
                    f'| {c["comp_id"]} '
                    f'| {c["n_hcw"]:,} '
                    f'| {c["n_citers"]:,} '
                    f'| {c["top_src"]} |'
                )
        lines.append('')

    out_path.write_text('\n'.join(lines))
    print(f'  → {out_path.name}')


# ── stdout summary ────────────────────────────────────────────────────────────

def print_summary_table(all_records: dict) -> None:
    hdr = (f'  {"fid":>3}  {"enclave":25s}  {"hcw--":>5}  {"wv--":>5}  {"wvnet":>5}  '
           f'{"hcw-+":>5} {"%-+":>5}  {"hcw+-":>5} {"%+-":>5}  '
           f'{"hcw++":>5} {"%++":>5}  {"citers":>8}  {"loop%":>5}  '
           f'{"ncomp":>5}  {"lg_hcw":>6}  {"lg%":>4}')
    sep = '─' * 130
    print(f'\n{sep}')
    print(hdr)
    print(sep)
    for fid in sorted(all_records):
        for r in sorted(all_records[fid], key=lambda x: -x['n_hcw']):
            print(f'  {fid:>3}  {r["enc_name"]:25s}  {r["n_hcw"]:>5,}  '
                  f'{r["mean_wv_seeds"]:>5.3f}  {r["mean_wv_net"]:>5.3f}  '
                  f'{r["n_hcw_mp"]:>5,} {r["pct_hcw_mp"]:>4.1f}%  '
                  f'{r["n_hcw_pm"]:>5,} {r["pct_hcw_pm"]:>4.1f}%  '
                  f'{r["n_hcw_pp"]:>5,} {r["pct_hcw_pp"]:>4.1f}%  '
                  f'{r["n_citers"]:>8,}  {r["loop_pct"]:>5.1f}  {r["n_comp"]:>5}  '
                  f'{r["lg_hcw"]:>6,}  {r["lg_pct"]:>4.0%}')
    print(sep)


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', default='2020_2024')
    parser.add_argument('--label',  default='baseline')
    parser.add_argument('--top-n',  type=int, default=4,
                        help='top sources to show per enclave/component')
    parser.add_argument('--field',  type=int, default=0,
                        help='if set, process only this field_idx')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Rebuild stale reports without prompting')
    args = parser.parse_args()

    paths    = load_config()
    settings = load_settings()

    nmf_path = paths.working / f'enclave_nmf_{args.window}_{args.label}.parquet'
    hcw_path = paths.working / f'enclave_hcw_{args.window}_{args.label}.parquet'
    wv_path  = paths.working / f'work_v_{args.window}_{args.label}.parquet'
    cr_path  = str(paths.working /
                   f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')
    out_dir  = Path(__file__).parent / 'reports'
    out_dir.mkdir(exist_ok=True)

    for p in (nmf_path, hcw_path, wv_path):
        if not p.exists():
            print(f'ERROR: {p} not found')
            sys.exit(1)

    # ── load NMF parquet (HCW-- with enclave assignments) ────────────────────
    print('Loading NMF enclave assignments...')
    nmf_df = pd.read_parquet(nmf_path)
    # use topic_name if present and non-empty, else fall back to "Topic N"
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

    # The citation scan below runs once for every targeted field together, so
    # a single representative freshness check gates the whole batch.
    report_inputs = [str(nmf_path), str(hcw_path), str(wv_path), cr_path]
    report_paths = [
        out_dir / f'{fid}_{args.window}_{args.label}.md'
        for fid in sorted(nmf_df['field_idx'].unique())
    ]
    if not guard.ensure_fresh(report_paths[0], *report_inputs, script=__file__,
                              auto_yes=args.yes, label='network enclave reports'):
        return

    n_enclaves = nmf_df.groupby(['field_idx', 'enc_name']).ngroups
    print(f'  {len(nmf_df):,} HCW-- rows  |  '
          f'{nmf_df["field_idx"].nunique()} fields  |  '
          f'{n_enclaves} enclaves')

    # ── load full HCW parquet: source_idx + quadrant classification ──────────
    print('Loading HCW parquet (source_idx + quadrant)...')
    hcw_full = pd.read_parquet(
        hcw_path,
        columns=['field_idx', 'work_idx', 'source_idx', 'source_v', 'mean_citer_v'],
    )
    # build quadrant lookup for all HCW (not filtered by field)
    sv = hcw_full['source_v']
    cv = hcw_full['mean_citer_v']
    import numpy as np
    hcw_full['quadrant'] = np.select(
        [(sv >= 1) & (cv >= 1), (sv >= 1) & (cv < 1),
         (sv <  1) & (cv >= 1), (sv <  1) & (cv < 1)],
        ['++', '+-', '-+', '--'], default='',
    )
    hcw_quad: dict = dict(zip(
        zip(hcw_full['field_idx'].astype(int), hcw_full['work_idx'].astype(int)),
        hcw_full['quadrant'],
    ))
    print(f'  {len(hcw_quad):,} HCW entries in quadrant map')

    # per-field quadrant pool sizes (for penetration rate normalisation)
    _qt = (hcw_full.groupby('field_idx')['quadrant']
           .value_counts().unstack(fill_value=0))
    for q in ('--', '-+', '+-', '++'):
        if q not in _qt.columns:
            _qt[q] = 0
    field_quad_totals: dict = {
        int(fid_i): {q: int(_qt.at[fid_i, q]) for q in ('--', '-+', '+-', '++')}
        for fid_i in _qt.index
    }

    if args.field:
        hcw_full = hcw_full[hcw_full['field_idx'] == args.field]
    nmf_df = nmf_df.merge(
        hcw_full[['field_idx', 'work_idx', 'source_idx']],
        on=['field_idx', 'work_idx'], how='left',
    )

    # ── single corpus_references scan ────────────────────────────────────────
    all_seeds = set(nmf_df['work_idx'])
    print(f'Scanning corpus_references ({len(all_seeds):,} seeds)...')
    cite_edges = scan_citation_edges(cr_path, all_seeds)

    # ── load source names ─────────────────────────────────────────────────────
    source_ids   = set(nmf_df['source_idx'].dropna().astype(int))
    source_names = load_source_names(paths.openalex, source_ids)
    print(f'  {len(source_names):,} source names loaded')

    # build {work_idx: source_idx} map
    seed_source = dict(zip(nmf_df['work_idx'], nmf_df['source_idx']))

    # ── analyse per (field, enclave) — load work_v per field ─────────────────
    print('Analysing enclaves...')
    all_records: dict = defaultdict(list)

    for fid_int in sorted(nmf_df['field_idx'].unique()):
        fid_int = int(fid_int)  # type: ignore[arg-type]

        # load work_v for this field only (predicate pushdown keeps memory bounded)
        wv_fid = pd.read_parquet(
            wv_path,
            filters=[('field_idx', '==', fid_int)],
            columns=['work_idx', 'work_v'],
        )
        work_v_dict: dict = dict(zip(wv_fid['work_idx'].astype(int), wv_fid['work_v']))

        field_df = nmf_df[nmf_df['field_idx'] == fid_int]
        for enc_name, grp in field_df.groupby('enc_name'):
            enc_name  = str(enc_name)   # type: ignore[arg-type]
            enc_seeds = set(grp['work_idx'])
            enc_edges = cite_edges[cite_edges['cited_idx'].isin(enc_seeds)]

            rec = analyse_enclave(
                fid_int, enc_name, enc_seeds, enc_edges,
                seed_source, source_names, hcw_quad, work_v_dict, field_quad_totals,
                top_n=args.top_n,
            )
            all_records[fid_int].append(rec)
            print(f'  field {fid_int:>3} {FIELD_NAMES.get(fid_int,"?"):12s}  '
                  f'{enc_name:25s}  hcw={rec["n_hcw"]:>4,}  '
                  f'comp={rec["n_comp"]:>3}  lg={rec["lg_hcw"]:,}')

    # ── write MD reports ──────────────────────────────────────────────────────
    print('\nWriting reports...')
    for fid, enc_records in all_records.items():
        out_path = out_dir / f'{fid}_{args.window}_{args.label}.md'
        write_md(fid, enc_records, out_path, args.window, args.label)
        guard.record_build(out_path, *report_inputs, script=__file__)

    print_summary_table(dict(all_records))


if __name__ == '__main__':
    main()
