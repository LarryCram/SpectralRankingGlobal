"""
scopus_link.py — link Stanford top-2% persons to Scopus author IDs and then to OpenAlex
author IDs through shared DOIs.

Stage A  Scopus Author Search, grouped:  AFFIL(k) AND (name1 OR name2 ...) per institution
         keyword k, several groups OR-ed into one query (≤ MAX_QUERY_LEN chars, ~45 persons),
         sorted by document count, first page only (200 profiles). A profile is a candidate for
         a person when surname and first initial agree and its document count is at least
         VOL_FRAC × the Stanford paper count. Persons with no candidate are retried one by one
         with name + country.
Stage B  Scopus Search per candidate: AU-ID(x), newest first, N_DOI documents (plus the oldest
         page when the newest give fewer than MIN_SHARED DOIs) → candidate DOIs.
Stage C  DOIs → Oct26 OpenAlex (compact works.doi → compact authorships). For each candidate,
         the OpenAlex author whose name carries the Stanford surname on the most of its DOIs is
         the link; accepted when shared DOIs ≥ MIN_SHARED and they are a majority of the
         candidate's DOIs found in OpenAlex.
Stage D  Per person: the linked candidate whose Scopus document count is closest to the Stanford
         paper count (institution hit breaks ties). Confidence = shared / looked-up DOIs.

All Elsevier API responses are cached as JSON under SCOPUS_CACHE_DIR/raw_api/ (the ERA cache
location from .env), so reruns cost no quota. author_idx is OpenAlex's own numeric author ID
(A<author_idx>), stable across snapshots.

Outputs (WORKING/researchers/scopus/):
  candidates.parquet   pid, au_id, surname, given, documents, affiliation, country, areas, query
  candidate_dois.parquet  au_id, doi, cover_date
  links.parquet        au_id, author_idx, openalex_id, shared, n_dois, n_found
  person_links.parquet pid, authfull, au_id, author_idx, openalex_id, confidence, status,
                       name_match_author_idx (from stanford_oa_match), agrees

Usage:
  .venv/bin/python researchers/scopus_link.py --sample 200
  .venv/bin/python researchers/scopus_link.py            # all Stanford persons
"""

from __future__ import annotations

import argparse
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from researchers.common import ROOT, add_path_args, resolve_paths  # noqa: E402
from researchers.match_stanford import stanford_persons  # noqa: E402
from util.human_names import HumanNameParser, ParsedName  # noqa: E402

_PARSER = HumanNameParser()

MAX_QUERY_LEN = 2300
VOL_FRAC = 0.5
N_DOI = 10
MIN_SHARED = 2
RATE = 6.0            # max uncached requests per second, shared by all worker threads
                      # (Elsevier throttled with HTTP 429 at ~10-12/s on this key)
AUTHOR_URL = 'https://api.elsevier.com/content/search/author'
SCOPUS_URL = 'https://api.elsevier.com/content/search/scopus'
_STOP = {'university', 'of', 'the', 'and', 'school', 'college', 'faculty', 'department', 'institute',
         'de', 'di', 'du', 'des', 'la', 'le', 'for', 'in', 'at', 'medicine', 'medical', 'sciences',
         'science', 'national', 'research', 'center', 'centre', 'hospital', 'health', 'technology',
         'engineering', 'graduate', 'division', 'laboratory', 'universite', 'universidad',
         'universita', 'universitat', 'universidade', 'universiteit'}


# ── helpers ───────────────────────────────────────────────────────────────────

def asc(s) -> str:
    return unicodedata.normalize('NFKD', s if isinstance(s, str) else '').encode('ascii', 'ignore').decode()


def affil_key(inst) -> str | None:
    """Most distinctive single word of an institution name (longest non-generic token)."""
    toks = [t for t in re.findall(r'[A-Za-z]{3,}', asc(inst)) if t.lower() not in _STOP]
    return max(toks, key=len) if toks else None


@lru_cache(maxsize=None)
def pn(name) -> ParsedName:
    """HumanNameParser (util/human_names.py) parse, cached; name is a string or (given, family)."""
    return _PARSER.parse(name)


def split_name(authfull: str) -> tuple[str, str]:
    """(family, first) in ASCII form, from the parsed name."""
    p = pn(authfull)
    return (p.family_name_main or ''), (p.first_name_canonical or '')


def name_clause(authfull: str) -> str:
    # query with plain diacritic-stripped forms (Muller, not the Mueller digraph variant)
    p = pn(authfull)
    last = asc(p.family_name_raw or '') or (p.family_name_main or '')
    first = asc(p.given_tokens_raw[0]) if p.given_tokens_raw else (p.first_name_canonical or '')
    last = re.sub(r'[()"{}\[\]]', ' ', last).strip()
    return f'(AUTHLAST({last}) AND AUTHFIRST({first}))' if first else f'(AUTHLAST({last}))'


def names_agree(a: ParsedName, b: ParsedName) -> bool:
    """
    Same person's name? Family names must share a letters-only form (hyphen/space/diacritic
    variants agree). Given names: first initials must agree; when both sides give full names,
    the first full names must be equal (second full names too, when both have one), or the
    concatenations equal (Zhong Lin = Zhonglin), or one side's nickname equal the other's first
    name. Initials-only on either side is accepted here (callers add volume/affiliation checks).
    """
    fa_set = {letters(f) for f in a.family_names if f}
    fb_set = {letters(f) for f in b.family_names if f}
    if not (fa_set & fb_set):
        # compound surname split differently (Roti-Roti vs "Joseph L. Roti Roti"): accept when a
        # long (>= 5 letters) family form of one side is contained in the other's whole name
        whole_a = letters(''.join(a.given_tokens_raw) + (a.family_name_raw or ''))
        whole_b = letters(''.join(b.given_tokens_raw) + (b.family_name_raw or ''))
        if not (any(len(f) >= 5 and f in whole_b for f in fa_set) or
                any(len(f) >= 5 and f in whole_a for f in fb_set)):
            return False
    # given names, from the raw (unwidened) tokens in written order
    ra = [letters(t) for t in a.given_tokens_raw if letters(t)]
    rb = [letters(t) for t in b.given_tokens_raw if letters(t)]
    if not ra or not rb:
        return True
    ia, ib = ''.join(t[0] for t in ra), ''.join(t[0] for t in rb)
    if not (ia.startswith(ib) or ib.startswith(ia)):          # initials must agree as far as both go
        return False
    ga, gb = [t for t in ra if len(t) > 1], [t for t in rb if len(t) > 1]
    if not ga or not gb:                                      # initials only on one side
        return True
    if ''.join(ga) == ''.join(gb):                            # Gangding = Gang-Ding, Zhonglin = Zhong Lin
        return True
    abbr_a = [t.endswith('.') for t in a.given_tokens_raw if letters(t)]
    abbr_b = [t.endswith('.') for t in b.given_tokens_raw if letters(t)]
    if (abbr_b and abbr_b[0] and ga[0].startswith(rb[0])) or (abbr_a and abbr_a[0] and gb[0].startswith(ra[0])):
        return True                                           # Yu. = Yuri (written abbreviation)
    if ga[0] == gb[0]:                                        # Kristin Y. = Kristin Ytterstad
        return not (len(ga) > 1 and len(gb) > 1 and ga[1] != gb[1])
    na = {letters(t) for t in a.nickname_tokens if len(t) > 1}
    nb = {letters(t) for t in b.nickname_tokens if len(t) > 1}
    return bool(na & {gb[0]} or nb & {ga[0]})


def letters(s: str) -> str:
    return re.sub(r'[^a-z]', '', asc(s).lower())


def norm_doi(d) -> str | None:
    if not isinstance(d, str) or not d.strip():
        return None
    d = d.strip().lower()
    return re.sub(r'^https?://(dx\.)?doi\.org/', '', d)


class Elsevier:
    """Minimal cached client; every response is stored as JSON keyed by URL + params."""

    def __init__(self, env_path: Path, rate: float = RATE):
        env = {}
        for line in env_path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
        self.h = {'X-ELS-APIKey': env['sco_apikey'], 'Accept': 'application/json'}
        if env.get('sco_insttoken'):
            self.h['X-ELS-Insttoken'] = env['sco_insttoken']
        self.cache = Path(env['SCOPUS_CACHE_DIR']) / 'raw_api'
        self.calls = 0
        self.remaining = {}
        self.status = Counter()            # HTTP status counts (incl. retries)
        self.lock = threading.Lock()
        self.session = threading.local()   # one requests.Session per thread
        self.interval = 1.0 / rate          # global rate limit across threads
        self.next_t = 0.0

    def _wait_turn(self) -> None:
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next_t)
            self.next_t = t + self.interval
        if t > now:
            time.sleep(t - now)

    def get(self, url: str, params: dict) -> dict | None:
        key = hashlib.sha1(json.dumps([url, sorted(params.items())]).encode()).hexdigest()
        f = self.cache / url.rsplit('/', 1)[-1] / f'{key}.json'
        if f.exists():
            return json.loads(f.read_text())
        if not hasattr(self.session, 's'):
            self.session.s = requests.Session()
        for attempt in range(6):
            self._wait_turn()
            try:
                r = self.session.s.get(url, headers=self.h, params=params, timeout=60)
            except requests.RequestException:
                with self.lock:
                    self.status['exception'] += 1
                time.sleep(2 ** attempt)
                continue
            with self.lock:
                self.calls += 1
                self.status[r.status_code] += 1
                if 'X-RateLimit-Remaining' in r.headers:
                    self.remaining[url] = r.headers['X-RateLimit-Remaining']
            if r.status_code == 200:
                data = r.json()
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(data))
                return data
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            # 400 (bad query) etc.: cache the failure so it is not retried
            data = {'error': r.status_code, 'text': r.text[:300]}
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data))
            return data
        return None


# ── stage A: grouped author search ────────────────────────────────────────────

def build_grouped_queries(per_all: pd.DataFrame, wanted=None) -> list[tuple[str, list[int]]]:
    """
    Canonical grouped queries over the WHOLE Stanford person list, so that the query strings
    (and hence the response cache keys) do not depend on which sample is being run or on the
    row order of the input: persons are sorted by (affiliation keyword, name clause, authfull,
    institution, country); identical clauses within a keyword are written once; a keyword whose
    clauses exceed MAX_QUERY_LEN is split into consecutive chunks; chunks are packed greedily in
    that fixed order. `wanted` (an iterable of pids) keeps only the queries that contain at
    least one wanted person — each kept query is sent whole, exactly as in a full run.
    """
    df = per_all.assign(k=per_all['inst_name'].map(affil_key))
    df = df[df.k.notna()].copy()
    df['clause'] = df.authfull.map(name_clause)
    df = df.sort_values(['k', 'clause', 'authfull', 'inst_name', 'cntry'], kind='mergesort')
    parts: list[tuple[str, list[int]]] = []
    for k, g in df.groupby('k', sort=True):
        clauses = list(dict.fromkeys(g.clause))                 # unique, in sorted order
        pids_by_clause = g.groupby('clause').pid.apply(list).to_dict()
        head = f'(AFFIL({k}) AND ('
        chunk: list[str] = []
        for c in clauses:
            if chunk and len(head) + len(' OR '.join(chunk + [c])) + 2 > MAX_QUERY_LEN:
                parts.append((head + ' OR '.join(chunk) + '))', [x for cc in chunk for x in pids_by_clause[cc]]))
                chunk = []
            chunk.append(c)
        if chunk:
            parts.append((head + ' OR '.join(chunk) + '))', [x for cc in chunk for x in pids_by_clause[cc]]))
    queries, cur, cur_p = [], [], []
    for part, pids in parts:
        if cur and len(' OR '.join(cur + [part])) > MAX_QUERY_LEN:
            queries.append((' OR '.join(cur), cur_p))
            cur, cur_p = [], []
        cur.append(part)
        cur_p += pids
    if cur:
        queries.append((' OR '.join(cur), cur_p))
    if wanted is not None:
        w = set(wanted)
        queries = [(q, [x for x in pids if x in w]) for q, pids in queries if w.intersection(pids)]
    return queries


def parse_profiles(data: dict | None) -> list[dict]:
    out = []
    for e in ((data or {}).get('search-results', {}) or {}).get('entry', []) or []:
        if 'error' in e or 'dc:identifier' not in e:
            continue
        pn = e.get('preferred-name', {}) or {}
        variants = e.get('name-variant', []) or []
        if isinstance(variants, dict):
            variants = [variants]
        aff = e.get('affiliation-current', {}) or {}
        areas = e.get('subject-area', []) or []
        if isinstance(areas, dict):
            areas = [areas]
        out.append(dict(
            au_id=e['dc:identifier'].split(':')[-1],
            surname=pn.get('surname', ''), given=pn.get('given-name', ''),
            names=[(pn.get('surname', ''), pn.get('given-name', ''))] +
                  [(v.get('surname', ''), v.get('given-name', '')) for v in variants],
            documents=int(e.get('document-count', 0) or 0),
            affiliation=aff.get('affiliation-name'), country=aff.get('affiliation-country'),
            areas=';'.join(a.get('$', '') for a in areas if isinstance(a, dict)),
        ))
    return out


def profile_fits(p: dict, person: ParsedName) -> bool:
    return any(names_agree(pn((g or None, sname or None)), person) for sname, g in p['names'] if sname)


def stage_a(api: Elsevier, per: pd.DataFrame, iso_name: dict, per_all: pd.DataFrame) -> pd.DataFrame:
    rows, found = [], set()
    byid = per.set_index('pid')
    queries = build_grouped_queries(per_all, wanted=per.pid)
    print(f'  stage A: {len(per):,} persons → {len(queries):,} grouped queries', flush=True)
    for qi, (q, pids) in enumerate(queries):
        data = api.get(AUTHOR_URL, {'query': q, 'count': 200, 'sort': '-document-count'})
        profs = parse_profiles(data)
        for pid in pids:
            r = byid.loc[pid]
            person = pn(r.authfull)
            for p in profs:
                if profile_fits(p, person) and p['documents'] >= VOL_FRAC * (r.np or 0):
                    rows.append({**{k: v for k, v in p.items() if k != 'names'}, 'pid': pid, 'query': f'group{qi}'})
                    found.add(pid)
    missing = per[~per.pid.isin(found)]
    print(f'  stage A: {len(found):,} found in grouped pass; retrying {len(missing):,} individually', flush=True)
    for r in missing.itertuples():
        person = pn(r.authfull)
        country = iso_name.get(r.cntry)
        q = name_clause(r.authfull) + (f' AND AFFILCOUNTRY({asc(country)})' if country else '')
        profs = parse_profiles(api.get(AUTHOR_URL, {'query': q, 'count': 200, 'sort': '-document-count'}))
        for p in profs:
            if profile_fits(p, person) and p['documents'] >= VOL_FRAC * (r.np or 0):
                rows.append({**{k: v for k, v in p.items() if k != 'names'}, 'pid': r.pid, 'query': 'single'})
    cand = pd.DataFrame(rows).drop_duplicates(['pid', 'au_id']) if rows else pd.DataFrame(
        columns=['pid', 'au_id', 'surname', 'given', 'documents', 'affiliation', 'country', 'areas', 'query'])
    return cand


# ── stage B: candidate DOIs ───────────────────────────────────────────────────

def parse_dois(data: dict | None) -> list[tuple[str, str]]:
    out = []
    for e in ((data or {}).get('search-results', {}) or {}).get('entry', []) or []:
        d = norm_doi(e.get('prism:doi'))
        if d:
            out.append((d, e.get('prism:coverDate')))
    return out


def _candidate_dois(api: Elsevier, au: str) -> list[dict]:
    base = {'query': f'AU-ID({au})', 'count': N_DOI, 'field': 'prism:doi,prism:coverDate'}
    dois = parse_dois(api.get(SCOPUS_URL, {**base, 'sort': '-coverDate'}))
    if len(dois) < MIN_SHARED + 1:
        dois += parse_dois(api.get(SCOPUS_URL, {**base, 'sort': '+coverDate'}))
    return [dict(au_id=au, doi=d, cover_date=c) for d, c in dict(dois).items()]


def stage_b(api: Elsevier, au_ids: list[str], workers: int = 1) -> pd.DataFrame:
    rows, t0, done = [], time.time(), 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(lambda au: _candidate_dois(api, au), au_ids):
            rows += res
            done += 1
            if done % 500 == 0:
                rate = api.calls / max(time.time() - t0, 1e-9)
                print(f'    stage B: {done:,}/{len(au_ids):,} candidates  {rate:.1f} calls/s  '
                      f'status={dict(api.status)}', flush=True)
    return pd.DataFrame(rows, columns=['au_id', 'doi', 'cover_date'])


# ── stage C: DOIs → OpenAlex authors ──────────────────────────────────────────

def stage_c(p, cdois: pd.DataFrame, cand: pd.DataFrame, per: pd.DataFrame) -> pd.DataFrame:
    con = duckdb.connect()
    con.execute("SET memory_limit = '4GB'")
    con.execute('SET threads = 4')
    con.register('_cd', cdois)
    con.execute(f"""
        CREATE TEMP TABLE _w AS
        SELECT w.work_idx, lower(w.doi) AS doi
        FROM parquet_scan('{p.works_glob}') w
        WHERE lower(w.doi) IN (SELECT DISTINCT doi FROM _cd)
    """)
    con.execute(f"""
        CREATE TEMP TABLE _wa AS
        SELECT DISTINCT a.work_idx, a.author_idx, a.author_name
        FROM parquet_scan('{p.authorships_glob}') a
        SEMI JOIN _w USING (work_idx)
        WHERE a.author_idx IS NOT NULL
    """)
    hits = con.execute("""
        SELECT cd.au_id, w.doi, wa.author_idx, wa.author_name
        FROM _cd cd JOIN _w w USING (doi) JOIN _wa wa USING (work_idx)
    """).fetchdf()
    found = con.execute('SELECT cd.au_id, COUNT(DISTINCT w.doi) AS n_found FROM _cd cd '
                        'JOIN _w w USING (doi) GROUP BY 1').fetchdf()
    con.close()
    # name check: the OA author name must agree with the Stanford name (names_agree)
    per_au = cand.merge(per[['pid', 'authfull', 'np']], on='pid')
    keys = {}
    for au, af in zip(per_au.au_id, per_au.authfull):
        keys.setdefault(au, []).append(pn(af))
    def ok(au, name):
        return isinstance(name, str) and any(names_agree(pn(name), person) for person in keys.get(au, ()))
    hits = hits[[ok(au, n) for au, n in zip(hits.au_id, hits.author_name)]]
    n_dois = cdois.groupby('au_id').doi.nunique().rename('n_dois')
    tally = (hits.groupby(['au_id', 'author_idx']).doi.nunique().rename('shared').reset_index()
             .sort_values(['au_id', 'shared'], ascending=[True, False]))
    members = (tally[tally.shared >= MIN_SHARED].groupby('au_id')
               .apply(lambda g: ['A' + str(int(x)) for x in g.author_idx], include_groups=False)
               .rename('member_ids'))
    best = (tally.groupby('au_id').head(1).merge(n_dois, on='au_id').merge(found, on='au_id', how='left')
            .merge(members, on='au_id', how='left'))
    best['n_found'] = best.n_found.fillna(0).astype(int)
    member_shared = tally[tally.shared >= MIN_SHARED].groupby('au_id').shared.sum().rename('member_shared')
    best = best.merge(member_shared, on='au_id', how='left')
    # split OA profiles share the papers: accept when the members together hold a majority
    best['accepted'] = (best.shared >= MIN_SHARED) & (best.member_shared.fillna(0) > 0.5 * best.n_found)
    best['openalex_id'] = 'A' + best.author_idx.astype('int64').astype(str)
    best['member_ids'] = [m if isinstance(m, list) else [] for m in best.member_ids]
    # OpenAlex work counts, to flag conflated OA profiles (many people merged into one ID)
    ids = pd.DataFrame({'author_idx': best.author_idx.astype('int64').unique()})
    con = duckdb.connect()
    con.register('_ids', ids)
    wc = con.execute(f"SELECT author_idx, works_count AS oa_works_count FROM parquet_scan('{p.authors_glob}') "
                     f"SEMI JOIN _ids USING (author_idx)").fetchdf()
    con.close()
    best = best.merge(wc, on='author_idx', how='left')
    npmax = per_au.groupby('au_id').np.max().rename('np_max')
    best = best.merge(npmax, on='au_id', how='left')
    best['oa_conflated'] = best.oa_works_count > 4 * (best.np_max.fillna(0) + 10)
    return best


# ── stage D: one link per person ──────────────────────────────────────────────

def stage_d(per: pd.DataFrame, cand: pd.DataFrame, links: pd.DataFrame, name_match: pd.DataFrame) -> pd.DataFrame:
    c = cand.merge(links[links.accepted], on='au_id', how='inner').merge(
        per[['pid', 'authfull', 'np', 'inst_name']], on='pid')
    c['ik'] = c.inst_name.map(affil_key)
    c['inst_hit'] = [isinstance(k, str) and isinstance(a, str) and k.lower() in asc(a).lower()
                     for k, a in zip(c.ik, c.affiliation)]
    c['dvol'] = np.abs(np.log((c.documents + 1) / (c.np.fillna(0) + 1)))
    c = c.sort_values(['pid', 'inst_hit', 'dvol'], ascending=[True, False, True])
    c['n_linked'] = c.groupby('pid').au_id.transform('size')
    best = c.groupby('pid').head(1).copy()
    best['confidence'] = best.shared / best.n_found.clip(lower=1)
    out = per[['pid', 'authfull', 'inst_name', 'cntry', 'np']].merge(
        best[['pid', 'au_id', 'documents', 'affiliation', 'author_idx', 'openalex_id', 'member_ids',
              'shared', 'n_found', 'confidence', 'inst_hit', 'n_linked', 'oa_works_count',
              'oa_conflated']], on='pid', how='left')
    has_cand = set(cand.pid)
    out['status'] = np.where(out.author_idx.notna(), 'linked',
                             np.where(out.pid.isin(has_cand), 'scopus_only', 'no_scopus'))
    nm = name_match[['pid', 'status', 'author_idx']].rename(
        columns={'status': 'name_status', 'author_idx': 'name_author_idx'})
    out = out.merge(nm, on='pid', how='left')
    out['oa_split'] = [isinstance(m, list) and len(m) > 1 for m in out.member_ids]
    both = out.author_idx.notna() & (out.name_status == 'matched')
    out['agrees'] = pd.Series(pd.NA, index=out.index, dtype='object')
    out.loc[both, 'agrees'] = [f'A{int(n)}' in (m if isinstance(m, list) else []) or a == n
                               for a, n, m in zip(out.loc[both, 'author_idx'], out.loc[both, 'name_author_idx'],
                                                  out.loc[both, 'member_ids'])]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    add_path_args(ap)
    ap.add_argument('--sample', type=int, default=0, help='random sample of Stanford persons (0 = all)')
    ap.add_argument('--seed', type=int, default=20261128)
    ap.add_argument('--workers', type=int, default=4, help='parallel Scopus Search requests in stage B')
    ap.add_argument('--rate', type=float, default=RATE, help='max uncached API requests per second (all threads)')
    args = ap.parse_args()
    p = resolve_paths(args)
    out_dir = p.out / 'scopus'
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    per_all = stanford_persons(p.stanford_dir)
    per = per_all
    if args.sample:
        per = per_all.sample(args.sample, random_state=args.seed)
    iso_name = dict(duckdb.connect().execute(
        f"SELECT lower(alpha_3), display_name FROM parquet_scan('{p.openalex}/countries.parquet')").fetchall())
    api = Elsevier(ROOT / '.env', rate=args.rate)

    cand = stage_a(api, per, iso_name, per_all)
    cand.to_parquet(out_dir / 'candidates.parquet', index=False)
    print(f'  stage A done: {cand.pid.nunique():,} persons with candidates, {len(cand):,} candidates, '
          f'{api.calls} API calls  [{time.time()-t0:.0f}s]', flush=True)

    calls_b0, t_b0 = api.calls, time.time()
    cdois = stage_b(api, sorted(cand.au_id.unique()), workers=args.workers)
    cdois.to_parquet(out_dir / 'candidate_dois.parquet', index=False)
    nb = api.calls - calls_b0
    print(f'  stage B done: {len(cdois):,} DOIs for {cdois.au_id.nunique():,} candidates, {nb:,} calls in '
          f'{time.time()-t_b0:.0f}s ({nb/max(time.time()-t_b0,1e-9):.1f}/s, {args.workers} workers), '
          f'HTTP status {dict(api.status)}  [{time.time()-t0:.0f}s]', flush=True)

    links = stage_c(p, cdois, cand, per)
    links.to_parquet(out_dir / 'links.parquet', index=False)
    print(f'  stage C done: {int(links.accepted.sum()):,}/{cand.au_id.nunique():,} candidates linked to '
          f'OpenAlex  [{time.time()-t0:.0f}s]', flush=True)

    nm = pd.read_parquet(p.stanford_match)
    res = stage_d(per, cand, links, nm)
    res.to_parquet(out_dir / ('person_links_sample.parquet' if args.sample else 'person_links.parquet'), index=False)

    print(f'\nPersons: {len(res):,}   API calls this run: {api.calls}   quota left: {api.remaining}')
    print(res.status.value_counts().to_string())
    lk = res[res.status == 'linked']
    print(f'linked: median confidence {lk.confidence.median():.2f}, '
          f'≥2 shared DOIs {int((lk.shared >= 2).sum())}, multiple linked candidates {int((lk.n_linked > 1).sum())}')
    both = res[res.agrees.notna()]
    print(f'vs name matching: both linked {len(both):,}, agree {int(both.agrees.astype(bool).sum()):,} '
          f'(split OA profiles among linked: {int(lk.oa_split.sum())}, conflated OA profiles: {int(lk.oa_conflated.fillna(False).sum())})')
    newly = res[(res.status == 'linked') & (res.name_status != 'matched')]
    print(f'newly linked (name matching had failed): {len(newly):,} of {int((res.name_status != "matched").sum()):,}')
    print(f'Total [{time.time()-t0:.0f}s]')


if __name__ == '__main__':
    main()
