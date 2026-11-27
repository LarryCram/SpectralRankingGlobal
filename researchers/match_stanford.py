"""
match_stanford.py — R5: map Stanford top-2% authors to OpenAlex author_idx.

The Stanford tables carry only name ("Last, First M."), one institution, an ISO3
country and Scopus counts — no Scopus ID or ORCID — so the mapping is by name,
then by location and volume.

Candidates
  OA author_name variants from compact authorships, for eligible authors only
  (>= MIN_WORKS article/review/conference-paper works; researchers/author_volume),
  blocked on (last word of name, first initial). Name variants come from
  authorships rather than the authors table (lesson from the HCR matching work).

Compatibility (names_compatible)
  first initials equal; when both first names have >= 4 letters their first three
  letters must agree (with an umlaut-transliteration fallback).

Score for each compatible (Stanford person, OA author) pair
  + W_INST    institution match (normalised-token Jaccard >= 0.5 with any OA affiliation)
  + W_CNTRY   Stanford country among the OA author's affiliation countries
  + W_MID     middle initials agree (both present)
  - W_VOL * |log((OA works_count + 1)/(Stanford np + 1))|
  - W_VOL * |log((OA h_index + 1)/(Stanford h + 1))|

Accepted ("matched") when the best candidate has a country or institution match,
|log h ratio| <= MAX_LOGH and leads the runner-up by >= MARGIN. Otherwise the
person is "ambiguous" (best kept for audit) or "unmatched". An OA author claimed
by two Stanford persons is kept only for the higher score.

Output: WORKING/researchers/stanford_oa_match.parquet
  pid, sid_career, sid_singleyr, authfull, inst_name, cntry, author_idx, status,
  score, margin, n_cand, oa_name, oa_works_count, oa_h_index, inst_hit, cntry_hit
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from nameparser import HumanName

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from researchers.common import add_path_args, connect, resolve_paths
from util import guard
from util.name_util import clean_name, last_norm, norm  # noqa: F401

W_INST, W_CNTRY, W_MID, W_VOL, W_NAME = 3.0, 2.0, 1.0, 1.5, 1.5
MAX_LOGH = math.log(2.5)
MARGIN = 1.0
TOP_K = 5        # candidates kept per person, by closeness in works_count and h

_STOP = {'university', 'of', 'the', 'and', 'universidad', 'universite', 'universitat',
         'universita', 'universiteit', 'universidade', 'de', 'di', 'du', 'des', 'der',
         'la', 'le', 'for', 'in', 'at', 'institute', 'college', 'school', 'centre',
         'center', 'hospital', 'national', 'research', 'medical', 'sciences', 'science'}


# ── name handling ─────────────────────────────────────────────────────────────

def _alpha(s: str) -> str:
    return ''.join(c for c in s if c.isalpha())


def _de_umlaut(s: str) -> str:
    return s.replace('ae', 'a').replace('oe', 'o').replace('ue', 'u')


def block_key(last: str) -> str:
    """Last token of the surname after the same normalisation the SQL blocking applies."""
    toks = re.sub(r'[^a-z\- ]', ' ', norm(last)).split()
    return toks[-1].strip('-') if toks else ''


def split_stanford(authfull: str) -> tuple[str, str, str]:
    """'Last, First M.' -> (block_key, first, middle_initial)."""
    s = clean_name(authfull)
    if ',' in s:
        last, first = s.split(',', 1)
    else:
        hn = HumanName(s)
        last, first = hn.last, f'{hn.first} {hn.middle}'
    toks = [t for t in re.split(r'[\s.]+', norm(first)) if _alpha(t)]
    f = _alpha(toks[0]) if toks else ''
    m = _alpha(toks[1])[:1] if len(toks) > 1 else ''
    return block_key(last), f, m


def split_oa(name: str) -> tuple[str, str, str]:
    """'First M. Last' (OpenAlex display order) -> (last_key, first, middle_initial)."""
    hn = HumanName(clean_name(name))
    toks = [t for t in re.split(r'[\s.]+', norm(hn.first)) if _alpha(t)]
    f = _alpha(toks[0]) if toks else ''
    mid = [t for t in re.split(r'[\s.]+', norm(hn.middle)) if _alpha(t)]
    m = _alpha(toks[1])[:1] if len(toks) > 1 else (_alpha(mid[0])[:1] if mid else '')
    return last_norm(hn.last), f, m


def names_compatible(f1: str, f2: str) -> bool:
    if not f1 or not f2 or f1[0] != f2[0]:
        return False
    if len(f1) >= 4 and len(f2) >= 4:
        return f1[:3] == f2[:3] or _de_umlaut(f1)[:3] == _de_umlaut(f2)[:3]
    if len(f1) > 1 and len(f2) > 1:
        return f1.startswith(f2) or f2.startswith(f1)
    return True


def inst_tokens(s: str) -> frozenset:
    return frozenset(t for t in re.split(r'[^a-z0-9]+', norm(s)) if t and t not in _STOP)


def inst_hit(st_tokens: frozenset, oa_inst_tokens: list[frozenset]) -> bool:
    if not st_tokens:
        return False
    for t in oa_inst_tokens:
        if t and len(st_tokens & t) / len(st_tokens | t) >= 0.5:
            return True
    return False


_PARTICLES = {'van', 'der', 'den', 'de', 'del', 'della', 'di', 'da', 'dos', 'das', 'du', 'la',
              'le', 'von', 'zu', 'ter', 'ten'}   # not 'bin', 'al': also given names (Bin Li)


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r'[^a-z]+', norm(clean_name(s)).replace("'", '')) if t]


def stanford_given(authfull: str) -> list[str]:
    s = clean_name(authfull)
    first = s.split(',', 1)[1] if ',' in s else ''
    return [t for t in _tokens(first) if t not in _PARTICLES]


def oa_given(display_name: str, surname_key: str) -> list[str] | None:
    """Given-name tokens of an OA display name, or None if the surname is absent."""
    toks = _tokens(display_name)
    sk = [t for t in re.split(r'[^a-z]+', surname_key) if t]
    if not sk or not all(t in toks for t in sk):
        return None
    return [t for t in toks if t not in sk and t not in _PARTICLES]


def name_quality(st: list[str], oa: list[str] | None) -> int:
    """
    -1 incompatible, 0 initials only, 2 same given name.
    Initials must agree as far as both go; full given names must be equal once
    concatenated (Zhong Lin = Zhonglin), or share the first name when one side has no
    further full names (Kristin Y. = Kristin Ytterstad). No prefix rule: it confuses
    Chinese given names (Yanfei vs Yan).
    """
    if not st or not oa:
        return -1 if oa is None else 0
    si, oi = ''.join(t[0] for t in st), ''.join(t[0] for t in oa)
    if not (si.startswith(oi) or oi.startswith(si)):
        return -1
    s0, o0 = st[0], oa[0]
    if len(s0) < 2 or len(o0) < 2:
        return 0
    sf = _de_umlaut(''.join(t for t in st if len(t) > 1))
    of = _de_umlaut(''.join(t for t in oa if len(t) > 1))
    if sf == of:
        return 2
    if _de_umlaut(s0) == _de_umlaut(o0) and (sf == _de_umlaut(s0) or of == _de_umlaut(o0)):
        return 2
    return -1


# ── Stanford persons ──────────────────────────────────────────────────────────

def stanford_persons(sdir: Path) -> pd.DataFrame:
    keys = ['authfull', 'inst_name', 'cntry', 'firstyr', 'np']
    cols = keys + ['sid', 'h', 'sm_field']
    car = pd.read_parquet(sdir / 'stanford_career.parquet', columns=cols)
    one = pd.read_parquet(sdir / 'stanford_singleyr.parquet', columns=cols)
    per = car.rename(columns={'sid': 'sid_career', 'h': 'h_career'}).merge(
        one.rename(columns={'sid': 'sid_singleyr', 'h': 'h_singleyr', 'sm_field': 'sm_field_1'}),
        on=keys, how='outer')
    per['sm_field'] = per['sm_field'].fillna(per.pop('sm_field_1'))
    # career h only: the single-year h counts 2025 citations and is not comparable with an
    # OpenAlex h-index; persons only in the single-year table are checked on paper count
    per['h'] = per['h_career']
    per = per.reset_index(drop=True)
    per.insert(0, 'pid', np.arange(len(per)))
    parts = per['authfull'].map(split_stanford)
    per['last_key'] = [p[0] for p in parts]
    per['first'] = [p[1] for p in parts]
    per['mid'] = [p[2] for p in parts]
    per['fi'] = per['first'].str[:1]
    per['given'] = per['authfull'].map(stanford_given)
    return per


# ── candidate retrieval ───────────────────────────────────────────────────────

def candidates(p, per: pd.DataFrame, iso2: dict, con) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Three plain DuckDB steps, then one join that applies every hard filter, so only
    plausible pairs ever reach pandas:
      _meta  eligible OA authors: works_count, h_index, affiliation countries
      _blk   their name variants (authorships), keyed on surname token + first initial
      pairs  person x name on (surname, initial), keeping only pairs that agree on
             country, have |log h ratio| <= MAX_LOGH and compatible first names
    """
    t0 = time.time()
    ps = per[['pid', 'last_key', 'fi', 'first', 'mid', 'cntry', 'h', 'np']].copy()
    ps['iso2'] = ps['cntry'].map(iso2)
    ps = ps[(ps.last_key != '') & (ps.fi != '') & ps.iso2.notna()]
    con.register('_per_df', ps)
    con.execute('CREATE TEMP TABLE _per AS SELECT * FROM _per_df')

    con.execute(f"""
        CREATE TEMP TABLE _meta AS
        SELECT a.author_idx, a.works_count, a.h_index,
               list_distinct(list_transform(a.affiliations, x -> x.institution.country_code)) AS ccs
        FROM parquet_scan('{p.authors_glob}') a
        SEMI JOIN (SELECT author_idx FROM parquet_scan('{p.author_volume}') WHERE eligible) e
          USING (author_idx)
    """)
    print(f"  eligible OA authors with records: {con.execute('SELECT COUNT(*) FROM _meta').fetchone()[0]:,}  [{time.time()-t0:.0f}s]", flush=True)

    # name keys of eligible authors' authorship name variants (cached: ~6 min to build)
    keys_pq = p.out / 'oa_name_keys.parquet'
    if not keys_pq.exists():
        con.execute(r"""
            COPY (
              SELECT DISTINCT author_idx,
                     t[len(t)] AS lk, left(t[1], 1) AS fi, t[1] AS ofirst,
                     CASE WHEN len(t) >= 3 THEN left(t[2], 1) ELSE '' END AS omid
              FROM (SELECT author_idx,
                           list_filter(string_split(regexp_replace(lower(strip_accents(
                               regexp_replace(author_name, '[\x{2010}-\x{2015}\x{2212}]', '-', 'g'))),
                               '[^a-z\- ]', ' ', 'g'), ' '), x -> trim(x, '-') <> '') AS t
                    FROM parquet_scan('""" + p.authorships_glob + r"""')
                    SEMI JOIN _meta USING (author_idx)
                    WHERE author_name IS NOT NULL)
              WHERE len(t) >= 2
            ) TO '""" + str(keys_pq) + r"""' (FORMAT PARQUET)
        """)
    con.execute(f"CREATE TEMP TABLE _blk AS SELECT * FROM parquet_scan('{keys_pq}')")
    print(f"  name keys: {con.execute('SELECT COUNT(*) FROM _blk').fetchone()[0]:,}  [{time.time()-t0:.0f}s]", flush=True)

    # 1. person x name key: same surname token and initial, compatible first name
    con.execute("""
        CREATE TEMP TABLE _pr0 AS
        SELECT p.pid, b.author_idx, (p.mid <> '' AND b.omid = p.mid) AS mid_ok
        FROM _per p
        JOIN _blk b ON b.lk = trim(p.last_key, '-') AND b.fi = p.fi
        WHERE CASE
          WHEN length(p.first) >= 4 AND length(b.ofirst) >= 4 THEN
               left(p.first, 3) = left(b.ofirst, 3)
            OR left(replace(replace(replace(p.first, 'ae', 'a'), 'oe', 'o'), 'ue', 'u'), 3)
             = left(replace(replace(replace(b.ofirst, 'ae', 'a'), 'oe', 'o'), 'ue', 'u'), 3)
          WHEN length(p.first) > 1 AND length(b.ofirst) > 1 THEN
               starts_with(p.first, b.ofirst) OR starts_with(b.ofirst, p.first)
          ELSE TRUE END
    """)
    con.execute('DROP TABLE _blk')
    print(f"  name-compatible rows: {con.execute('SELECT COUNT(*) FROM _pr0').fetchone()[0]:,}  [{time.time()-t0:.0f}s]", flush=True)

    # 2. one row per (person, author) that agrees on country and h
    con.execute(f"""
        CREATE TEMP TABLE _pr1 AS
        SELECT r.pid, r.author_idx, bool_or(r.mid_ok) AS mid_ok,
               ANY_VALUE(abs(ln((m.works_count + 1) / (p.np + 1)))
                       + COALESCE(abs(ln((m.h_index + 1) / (p.h + 1))), 0)) AS dvol
        FROM _pr0 r
        JOIN _per  p USING (pid)
        JOIN _meta m USING (author_idx)
        WHERE list_contains(m.ccs, p.iso2)
          AND CASE WHEN p.h IS NULL THEN abs(ln((m.works_count + 1) / (p.np + 1))) <= {MAX_LOGH}
                   ELSE abs(ln((m.h_index + 1) / (p.h + 1))) <= {MAX_LOGH} END
        GROUP BY r.pid, r.author_idx
    """)
    con.execute('DROP TABLE _pr0')
    print(f"  country- and h-consistent pairs: {con.execute('SELECT COUNT(*) FROM _pr1').fetchone()[0]:,}  [{time.time()-t0:.0f}s]", flush=True)

    # 3. keep the TOP_K closest in volume per person
    pairs = con.execute(f"""
        SELECT pid, author_idx, mid_ok FROM _pr1
        QUALIFY row_number() OVER (PARTITION BY pid ORDER BY dvol) <= {TOP_K}
    """).fetchdf()
    print(f'  candidate (person, author) pairs: {len(pairs):,}  [{time.time()-t0:.0f}s]', flush=True)

    con.register('_pids', pd.DataFrame({'author_idx': pairs.author_idx.unique()}))
    meta = con.execute(f"""
        SELECT a.author_idx, a.display_name, a.works_count, a.h_index,
               list_distinct(list_transform(a.affiliations, x -> x.institution.display_name)) AS insts
        FROM parquet_scan('{p.authors_glob}') a SEMI JOIN _pids USING (author_idx)
    """).fetchdf()
    pairs = pairs.merge(meta[['author_idx', 'display_name']], on='author_idx', how='left')
    pairs['author_name'] = pairs.pop('display_name')
    return pairs, meta


# ── scoring ───────────────────────────────────────────────────────────────────

def score_pairs(pairs: pd.DataFrame, per: pd.DataFrame, meta: pd.DataFrame,
                iso2: dict) -> pd.DataFrame:
    d = pairs.merge(meta, on='author_idx', how='left').merge(
        per[['pid', 'inst_name', 'cntry', 'np', 'h']], on='pid')
    st_tok = {i: inst_tokens(i or '') for i in d['inst_name'].unique()}
    oa_tok = [[inst_tokens(x or '') for x in (l if l is not None else [])] for l in d['insts']]
    d['inst_hit'] = [inst_hit(st_tok[s], o) for s, o in zip(d['inst_name'], oa_tok)]
    d['cntry_hit'] = True  # enforced in candidates()
    sk = dict(zip(per.pid, per.last_key))
    gv = dict(zip(per.pid, per.given))
    d['name_q'] = [name_quality(gv[pid], oa_given(dn or '', sk[pid]))
                   for pid, dn in zip(d['pid'], d['display_name'])]
    d = d[d['name_q'] >= 0].copy()   # OA canonical name contradicts the Stanford name
    d['log_np'] = np.abs(np.log((d['works_count'].fillna(0) + 1) / (d['np'].fillna(0) + 1)))
    d['log_h'] = np.abs(np.log((d['h_index'].fillna(0) + 1) / (d['h'] + 1)))  # NaN if no career h
    d['score'] = (W_INST * d['inst_hit'] + W_CNTRY * d['cntry_hit'] + W_MID * d['mid_ok']
                  + W_NAME * d['name_q']
                  - W_VOL * d['log_np'] - W_VOL * d['log_h'].fillna(d['log_np']))
    return d


def resolve(d: pd.DataFrame, per: pd.DataFrame) -> pd.DataFrame:
    d = d.sort_values(['pid', 'score'], ascending=[True, False])
    d['n_cand'] = d.groupby('pid')['author_idx'].transform('size')
    second = d.groupby('pid')['score'].nth(1)
    best = d.groupby('pid').head(1).copy()
    best['margin'] = best['pid'].map(second).sub(best['score']).mul(-1).fillna(np.inf)
    # initials-only names need an institution hit; full given names need country (enforced)
    vol_ok = best['log_h'].fillna(best['log_np']) <= MAX_LOGH
    plausible = (best['inst_hit'] | (best['name_q'] >= 2)) & vol_ok
    best['status'] = np.where(plausible & (best['margin'] >= MARGIN), 'matched',
                              np.where(plausible, 'ambiguous', 'implausible'))
    # one OA author per Stanford person: keep the higher-scoring claim
    m = best['status'] == 'matched'
    dup = best[m].sort_values('score', ascending=False).duplicated('author_idx')
    best.loc[dup[dup].index, 'status'] = 'conflict'
    out = per[['pid', 'sid_career', 'sid_singleyr', 'authfull', 'inst_name', 'cntry', 'np', 'h']].merge(
        best[['pid', 'author_idx', 'status', 'score', 'margin', 'n_cand', 'author_name',
              'display_name', 'works_count', 'h_index', 'inst_hit', 'cntry_hit', 'name_q', 'log_np', 'log_h']],
        on='pid', how='left')
    out['status'] = out['status'].fillna('unmatched')
    return out.rename(columns={'author_name': 'oa_name', 'works_count': 'oa_works_count',
                               'h_index': 'oa_h_index', 'display_name': 'oa_display_name'})


def main() -> None:
    ap = argparse.ArgumentParser()
    add_path_args(ap)
    args = ap.parse_args()
    p = resolve_paths(args)
    sdir = p.stanford_dir
    inputs = [sdir / 'stanford_career.parquet', sdir / 'stanford_singleyr.parquet', p.author_volume]
    if not guard.ensure_fresh(p.stanford_match, *inputs, script=__file__,
                              auto_yes=args.yes, label='stanford_match'):
        return
    t0 = time.time()
    con = connect(memory_limit='16GB', threads=6)
    per = stanford_persons(sdir)
    print(f'Stanford persons (career ∪ single-year): {len(per):,}')
    iso2 = dict(con.execute(f"SELECT lower(alpha_3), country_code "
                            f"FROM parquet_scan('{p.openalex}/countries.parquet')").fetchall())
    pairs, meta = candidates(p, per, iso2, con)
    d = score_pairs(pairs, per, meta, iso2)
    out = resolve(d, per)
    out.to_parquet(p.stanford_match, index=False)
    print(out['status'].value_counts().to_string())
    for col in ('sid_career', 'sid_singleyr'):
        sub = out[out[col].notna()]
        print(f'  {col}: matched {(sub.status == "matched").mean():.1%} of {len(sub):,}')
    audit = out[out.status == 'matched'].sample(100, random_state=20260)
    audit[['authfull', 'inst_name', 'cntry', 'np', 'h', 'oa_display_name', 'oa_works_count',
           'oa_h_index', 'inst_hit', 'cntry_hit', 'score', 'margin']].to_csv(
        p.out / 'stanford_match_audit_sample.csv', index=False)
    print(f'Total [{time.time()-t0:.0f}s]')
    guard.record_build(p.stanford_match, *inputs, script=__file__, build_seconds=time.time() - t0)


if __name__ == '__main__':
    main()
