"""
test_hcr_inst_oax.py — Resolve HCR affiliation strings to OA institution_idx.

Resolution tiers (applied in order until a match is found):
  T0:  MANUAL_MAP override (institutions absent from OA or needing parent mapping)
  T1:  OA display_name exact norm match + country_code
  T2:  OA display_name_alternatives exact norm match + country_code
  T2b: OA display_name_acronyms lookup — acronym extracted from "(XXX)" in HCR string
  T3:  OA token-overlap fuzzy match (Jaccard ≥ FUZZY_THRESHOLD) + country_code
  T4:  pyalex autocomplete → filter by country hint + token overlap
  T5:  pyalex institution search + country_code filter
  MISS: unresolved

Country is always matched as a hard filter (ISO-2 derived from the trailing
"Country" token in the HCR affiliation string).

Usage:
  .venv/bin/python analysis/test_hcr_inst_oax.py
  .venv/bin/python analysis/test_hcr_inst_oax.py --no-api   # skip pyalex API calls
"""

from __future__ import annotations

import re
import sys
import json
import time
import argparse
from pathlib import Path
from collections import defaultdict

import os

import duckdb
import pandas as pd
import pyalex
from unidecode import unidecode

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import load_config

FUZZY_THRESHOLD    = 0.5   # Jaccard token overlap for T3 and T4 name verification
API_DELAY          = 0.12  # seconds between pyalex API calls
CACHE_PATH         = Path(__file__).parent.parent / 'data' / 'hcr_inst_cache.json'


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2))

# ── load .env from project root ───────────────────────────────────────────────
_env_path = Path(__file__).parent.parent / '.env'
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _, _v = _line.partition('=')
            os.environ.setdefault(_k.strip(), _v.strip())

_api_key = os.environ.get('OPENALEX_API_KEY', '')
if _api_key:
    pyalex.config.api_key = _api_key
_email = os.environ.get('OPENALEX_EMAIL', '')
if _email:
    pyalex.config.email = _email

STOPWORDS = {'of', 'the', 'and', 'for', 'in', 'at', 'de', 'du', 'la', 'le',
             'les', 'l', 'd', 'a', 'an', 'et', 'en', 'del', 'der', 'van',
             'university', 'institute', 'center', 'centre', 'national',
             'research', 'science', 'sciences', 'technology'}

# ── country name → ISO-2 ──────────────────────────────────────────────────────

COUNTRY_MAP: dict[str, str] = {
    'argentina': 'AR', 'australia': 'AU', 'austria': 'AT',
    'bahrain': 'BH', 'belgium': 'BE', 'brazil': 'BR',
    'canada': 'CA', 'chile': 'CL', 'china mainland': 'CN', 'china': 'CN',
    'colombia': 'CO', 'czech republic': 'CZ', 'denmark': 'DK',
    'egypt': 'EG', 'ethiopia': 'ET',
    'finland': 'FI', 'france': 'FR',
    'germany': 'DE', 'ghana': 'GH', 'greece': 'GR',
    'hong kong': 'HK', 'hungary': 'HU',
    'india': 'IN', 'indonesia': 'ID', 'iran': 'IR', 'ireland': 'IE',
    'israel': 'IL', 'italy': 'IT',
    'japan': 'JP', 'jordan': 'JO',
    'kenya': 'KE', 'south korea': 'KR', 'korea': 'KR',
    'malaysia': 'MY', 'mexico': 'MX', 'morocco': 'MA',
    'netherlands': 'NL', 'new zealand': 'NZ', 'nigeria': 'NG', 'norway': 'NO',
    'pakistan': 'PK', 'peru': 'PE', 'poland': 'PL', 'portugal': 'PT',
    'qatar': 'QA',
    'russia': 'RU',
    'saudi arabia': 'SA', 'singapore': 'SG', 'south africa': 'ZA',
    'spain': 'ES', 'sweden': 'SE', 'switzerland': 'CH',
    'taiwan': 'TW', 'thailand': 'TH', 'turkey': 'TR',
    'ukraine': 'UA', 'united arab emirates': 'AE',
    'united kingdom': 'GB', 'united states': 'US', 'usa': 'US',
    'venezuela': 'VE', 'vietnam': 'VN',
    'hong kong': 'HK', 'hong kong sar': 'HK', 'macao': 'MO', 'macau': 'MO',
    'romania': 'RO', 'bulgaria': 'BG', 'croatia': 'HR', 'serbia': 'RS',
    'slovenia': 'SI', 'slovakia': 'SK', 'czech republic': 'CZ', 'czechia': 'CZ',
    'lithuania': 'LT', 'latvia': 'LV', 'estonia': 'EE',
    'luxembourg': 'LU', 'cyprus': 'CY', 'malta': 'MT',
    'iceland': 'IS', 'north macedonia': 'MK', 'albania': 'AL',
    'bosnia and herzegovina': 'BA', 'montenegro': 'ME',
    'tunisia': 'TN', 'algeria': 'DZ', 'cameroon': 'CM',
    'senegal': 'SN', 'uganda': 'UG', 'tanzania': 'TZ',
    'sri lanka': 'LK', 'bangladesh': 'BD', 'nepal': 'NP',
    'philippines': 'PH', 'cambodia': 'KH',
    'united states of america': 'US',
}


# ── manual overrides ─────────────────────────────────────────────────────────
# Institutions absent from OA or needing parent-institution mapping.
# Key: _norm(inst_name) from the HCR affiliation string.
# Value: institution_idx in OA.
MANUAL_MAP: dict[str, int] = {
    # Harvard Medical School is not a separate OA institution → map to Harvard University
    'harvard medical school':                    136199984,
    'harvard university medical affiliates':     136199984,
    # Niels Bohr Institute is under University of Copenhagen in OA
    'niels bohr institute':                      124055696,
    # Addenbrooke's Hospital (abbreviated in HCR)
    'addenbrookes hosp':                         4210156194,
    # Woods Hole Research Center → Woods Hole Oceanographic Institution
    'woods hole res ctr':                        66958751,
    # Ragon Institute (HCR uses short name; OA has full tri-institutional name)
    'ragon institute':                           2802422659,
    # Flatiron Institute → Simons Foundation (parent org in OA)
    'flatiron institute':                        4210113618,

    # --- US government / federal institutes ---
    # "NIH National Cancer Institute (NCI), United States"
    'nih national cancer institute nci':         4210140884,
    # "NIH National Library of Medicine (NLM), United States"
    'nih national library of medicine nlm':      2800548410,
    # "US Food and Drug Administration (FDA), United States"
    'us food and drug administration fda':       1320320070,
    # "National Center Atmospheric Research (NCAR) - USA, United States"
    'national center atmospheric research ncar usa': 107766831,
    # "National Institute of Standards and Technology (NIST) - USA, United States"
    'national institute of standards and technology nist usa': 1321296531,
    # "US Army Research Laboratory (ARL), United States"
    'us army research laboratory arl':           166416128,

    # --- German institutions ---
    # "Potsdam Institut fur Klimafolgenforschung, Germany" (PIK)
    'potsdam institut fur klimafolgenforschung': 60200437,
    # "Eberhard Karls University of Tubingen, Germany"
    'eberhard karls university of tubingen':     8087733,
    # "Humboldt University of Berlin, Germany"
    'humboldt university of berlin':             39343248,
    # "German Center for Lunge Research (DZL), German" — HCR typo "Lunge" → "Lung"
    'german center for lunge research dzl':      4210129183,
    # "Deutsches Primatenzentrum (DPZ), Germany" → German Primate Center in OA
    'deutsches primatenzentrum dpz':             2801496451,
    # "Zoologisches Forschungsmuseum Alexander Koenig (ZFMK), Germany"
    # (also caught by T2b via acronym ZFMK, but listed here as fallback)
    'zoologisches forschungsmuseum alexander koenig zfmk': 4210099549,
    # "Nanoelectronic Materials Laboratory (Namlab), Germany"
    'nanoelectronic materials laboratory namlab': 4210122489,

    # --- Belgian/Dutch institutions ---
    # "Universite Catholique Louvain, Belgium" — OA alt has "de" missing in HCR
    'universite catholique louvain':             95674353,
    # "Hubrecht Institute (KNAW), Netherlands" — parens denote parent org, not acronym
    'hubrecht institute knaw':                   4210115206,

    # --- Italian institutions ---
    # "Istituto Italiano di Tecnologia - IIT, Italy"
    'istituto italiano di tecnologia iit':       30771326,
    # "International School for Advanced Studies (SISSA), Italy"
    'international school for advanced studies sissa': 138549579,
    # "Istituto di Ricerche Farmacologiche Mario Negri IRCCS, Italy"
    'istituto di ricerche farmacologiche mario negri irccs': 19630809,
    # "Universita della Campania Vanvitelli, Italy"
    'universita della campania vanvitelli':      197809005,

    # --- Irish / UK institutions ---
    # "Atlantic Technological University (ATU), Ireland"
    'atlantic technological university atu':     4387152698,
    # "Kennedy Institute Pheumatol, United Kingdom" — HCR typo for Rheumatology
    'kennedy institute pheumatol':               4405264228,

    # --- Spanish institutions ---
    # ICFO and CREAF are not in OA as standalone institutions; left as MISS

    # --- Australian ---
    # "Blacktown and Westmead Hospitals, Australia" → University of Sydney (teaching hospital)
    'blacktown and westmead hospitals':          129604602,

    # --- Portuguese ---
    # "Applied Molecular Biosciences Unit UCIBIO, Portugal" — trailing acronym, no parens
    'applied molecular biosciences unit ucibio': 4391767960,

    # --- Other ---
    # "Ruprecht Karls University Heidelberg, Germany"
    'ruprecht karls university heidelberg':      223822909,

    # --- US research institutions ---
    # "Salk Institute, United States"
    'salk institute':                            1307098950,
    # "University at Buffalo SUNY, United States"
    'university at buffalo suny':                63190737,
    # "Joint Research Centre / European Commission Joint Research Centre"
    'european commission joint research centre': 4210118689,
    # "Consultative Group On International Agricultural Research, France" (CGIAR)
    'consultative group on international agricultural research': 1286583668,

    # --- German ---
    # "Alfred Wegener Institute Helmholtz Centre for Polar and Marine Research, Germany"
    'alfred wegener institute helmholtz centre for polar and marine research': 127251866,
    # "Alfred Wegener Institute, Germany" (short form also used)
    'alfred wegener institute':                  127251866,
    # "Bundeswehr University Munich, Germany"
    'bundeswehr university munich':              40527276,
    # "Grosshansdorf Hospital, Germany" (LungenClinic Grosshansdorf in OA)
    'grosshansdorf hospital':                    4210140903,
    # "NaMLab GmbH, Germany" (same OA entry as NaMLab)
    'namlab gmbh':                               4210122489,

    # --- French ---
    # "Picardie Universites, France" → Université de Picardie Jules Verne
    'picardie universites':                      4647051,

    # --- Italian ---
    # "Center for Nanoscience and Technology IIT, Italy" → IIT
    'center for nanoscience and technology iit': 30771326,
    # "IFOM - FIRC Institute of Molecular Oncology, Italy" (IFOM in OA)
    'ifom firc institute of molecular oncology': 4210114103,

    # --- Iranian ---
    # "Khaje-Nassir-Toosi University of Technology, Iran" → K.N.Toosi in OA
    'khaje nassir toosi university of technology': 80543232,

    # --- Chinese ---
    # "Shandong First Med University Shandong Acad Med Sci, China Mainland"
    'shandong first med university shandong acad med sci': 4210163399,
    # "Tsinghua University Grad Sch Shenzen, China Mainland"
    'tsinghua university grad sch shenzen':      4405263052,
    # "Agriculture Genomes Institute at Shenzhen CAAS" (child of CAAS in OA)
    'agriculture genomes institute at shenzhen caas': 4210088191,

    # --- Other ---
    # "Luiss Guido Carli University, Italy"
    'luiss guido carli university':              56441308,
}


def _country_code(country_str: str) -> str | None:
    return COUNTRY_MAP.get(country_str.strip().lower())


def _norm(s: str) -> str:
    s = unidecode(str(s or '')).lower()
    s = re.sub(r"[''`]", '', s)        # apostrophes removed (Queen's → Queens)
    s = re.sub(r'[^\w\s]', ' ', s)     # other punct → space (Erlangen-Nuremberg → Erlangen Nuremberg)
    return re.sub(r'\s+', ' ', s).strip()


def _tokens(s: str) -> frozenset[str]:
    return frozenset(t for t in _norm(s).split() if t not in STOPWORDS and len(t) > 1)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parse_affil(raw: str) -> tuple[str, str | None]:
    """
    "Institution Name, Country" → (institution_name, country_code).
    Splits on the LAST comma.
    """
    raw = raw.strip()
    if ',' not in raw:
        return raw, None
    inst, country = raw.rsplit(',', 1)
    return inst.strip(), _country_code(country)


def collect_hcr_affils(hcr: pd.DataFrame) -> list[str]:
    """All unique non-null affiliation strings from Primary + Secondary columns."""
    seen: set[str] = set()
    for _, row in hcr.iterrows():
        for col in ('Primary Affiliation', 'Secondary Affiliations'):
            val = str(row.get(col) or '')
            if val and val.lower() != 'nan':
                for part in val.split(';'):
                    part = part.strip()
                    if part:
                        seen.add(part)
    return sorted(seen)


# ── OA institution index ──────────────────────────────────────────────────────

def build_oa_inst_index(inst_path: str) -> pd.DataFrame:
    """
    Load OA institutions: institution_idx, display_name, display_name_alternatives,
    display_name_acronyms, country_code, ror.  Adds norm_name column.
    """
    print('Loading OA institutions...')
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT institution_idx, display_name, display_name_alternatives,
               display_name_acronyms, country_code, ror
        FROM '{inst_path}'
        WHERE country_code IS NOT NULL
    """).df()
    con.close()
    df['norm_name'] = df['display_name'].apply(_norm)
    df['tokens']    = df['display_name'].apply(_tokens)
    print(f'  {len(df):,} institutions loaded')
    return df


def build_indexes(oa: pd.DataFrame) -> tuple[dict, dict, dict]:
    """
    Build three lookup structures from the OA institutions DataFrame:
      alt_idx  : {cc: {norm_alt: institution_idx}}       — for T2
      tok_idx  : {cc: {token: [row positions]}}           — for T3 candidate retrieval
      acr_idx  : {cc: {acronym_norm: institution_idx}}   — for T2b
    Also stores a '*' key covering all countries (for no-country-code queries).
    """
    alt_idx: dict[str, dict[str, int]] = defaultdict(dict)
    tok_idx: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    acr_idx: dict[str, dict[str, int]] = defaultdict(dict)

    for pos, row in enumerate(oa.itertuples(index=False)):
        cc  = row.country_code
        idx = int(row.institution_idx)

        # T2: alternatives
        alts = row.display_name_alternatives if row.display_name_alternatives is not None else []
        for a in alts:
            na = _norm(a)
            if na:
                alt_idx[cc][na]  = idx
                alt_idx['*'][na] = idx

        # T2b: acronyms
        acrs = row.display_name_acronyms if row.display_name_acronyms is not None else []
        for a in acrs:
            na = _norm(a)
            if na:
                acr_idx[cc][na]  = idx
                acr_idx['*'][na] = idx

        # T3: token inverted index (position in df, not idx, so we can slice df)
        for tok in row.tokens:
            tok_idx[cc][tok].append(pos)
            tok_idx['*'][tok].append(pos)

    return dict(alt_idx), {k: dict(v) for k, v in tok_idx.items()}, dict(acr_idx)


_PAREN_RE    = re.compile(r'\(([A-Z][A-Z0-9\-]{1,9})\)')
_LEADING_ACR = re.compile(r'^([A-Z][A-Z0-9]{2,9})\b')
_TRAILING_ACR = re.compile(r'\b([A-Z][A-Z0-9]{2,9})$')


def match_oa(inst_name: str, country_code: str | None,
             oa: pd.DataFrame,
             alt_idx: dict,
             acr_idx: dict) -> tuple[int | None, str]:
    """T1 (exact norm on display_name) + T2 (alternatives) + T2b (acronyms)."""
    cc_key = country_code or '*'
    norm   = _norm(inst_name)

    # T1: exact norm match in display_name column
    mask = oa['norm_name'] == norm
    if country_code:
        mask &= oa['country_code'] == country_code
    hits = oa[mask]
    if len(hits) == 1:
        return int(hits.iloc[0]['institution_idx']), 'T1'
    if len(hits) > 1:
        return int(hits.iloc[0]['institution_idx']), 'T1-amb'

    # T2: alternatives index
    for key in ([cc_key, '*'] if cc_key != '*' else ['*']):
        idx = alt_idx.get(key, {}).get(norm)
        if idx is not None:
            return idx, 'T2'

    # T2b: acronym lookup via:
    #   case A: parenthetical "(XXX)" in HCR string (e.g. "... (ZFMK)")
    #   case B: all-caps leading word (e.g. "INRAE", "ICREA", "IFOM - ...")
    acr_norms: list[str] = []
    m = _PAREN_RE.search(inst_name)
    if m:
        acr_norms.append(_norm(m.group(1)))
    m2 = _LEADING_ACR.match(inst_name)
    if m2:
        n2 = _norm(m2.group(1))
        if n2 not in acr_norms:
            acr_norms.append(n2)
    m3 = _TRAILING_ACR.search(inst_name)
    if m3:
        n3 = _norm(m3.group(1))
        if n3 not in acr_norms:
            acr_norms.append(n3)
    for acr_norm in acr_norms:
        for key in ([cc_key, '*'] if cc_key != '*' else ['*']):
            idx = acr_idx.get(key, {}).get(acr_norm)
            if idx is not None:
                return idx, 'T2b'

    return None, ''


def match_oa_fuzzy(inst_name: str, country_code: str | None,
                   oa: pd.DataFrame,
                   tok_idx: dict) -> tuple[int | None, str, float]:
    """T3: token-overlap fuzzy via inverted index (fast candidate retrieval)."""
    toks = _tokens(inst_name)
    if not toks:
        return None, '', 0.0

    cc_key = country_code or '*'

    # collect candidate row positions that share ≥1 token
    ti = tok_idx.get(cc_key) or tok_idx.get('*', {})
    cand_pos: set[int] = set()
    for tok in toks:
        cand_pos.update(ti.get(tok, []))

    if not cand_pos:
        return None, '', 0.0

    rows = oa.iloc[sorted(cand_pos)]
    best_score, best_idx = 0.0, None
    for row in rows.itertuples(index=False):
        jac      = _jaccard(toks, row.tokens)
        directed = len(toks & row.tokens) / len(toks)   # query recall
        sc       = max(jac, directed)
        if sc > best_score:
            best_score, best_idx = sc, int(row.institution_idx)

    if best_score >= FUZZY_THRESHOLD and best_idx is not None:
        return best_idx, 'T3', best_score
    return None, '', best_score


# ── pyalex API tiers ─────────────────────────────────────────────────────────

def _oax_id_to_idx(oa_id: str) -> int | None:
    """'https://openalex.org/I4210088668' → 4210088668."""
    prefix = 'https://openalex.org/I'
    if oa_id.startswith(prefix):
        tail = oa_id[len(prefix):]
        if tail.isdigit():
            return int(tail)
    return None


def _hint_country(hint: str | None) -> str | None:
    """'Paris, France' → 'FR'  (last comma-token, COUNTRY_MAP lookup)."""
    if not hint or ',' not in hint:
        return None
    return _country_code(hint.rsplit(',', 1)[-1])


def oax_autocomplete(inst_name: str, country_code: str | None
                     ) -> tuple[int | None, str, float]:
    """
    T4: pyalex autocomplete endpoint.
    Accepts the top result that:
      - passes a minimum token-overlap with inst_name (>= FUZZY_THRESHOLD), AND
      - whose hint country matches country_code (if we have one).
    Picks the result with highest works_count among acceptable candidates.
    """
    try:
        raw = pyalex.Institutions().autocomplete(inst_name)
        items: list[dict] = list(raw) if raw else []  # type: ignore[arg-type]
    except Exception:
        return None, '', 0.0

    inst_toks = _tokens(inst_name)
    best: tuple[int | None, float, int] = (None, 0.0, -1)   # (idx, overlap, works_count)

    for item in items:
        oa_id       = item['id']           if 'id'           in item else ''
        hint        = item['hint']         if 'hint'         in item else ''
        works_count = (item['works_count'] if 'works_count'  in item else 0) or 0
        disp        = item['display_name'] if 'display_name' in item else ''

        if country_code:
            hint_cc = _hint_country(hint)
            if hint_cc and hint_cc != country_code:
                continue

        # Accept if token overlap is good OR if institution is large enough that
        # OA's relevance ranking is trustworthy (avoids noise from tiny stubs)
        ov = _jaccard(inst_toks, _tokens(disp))
        directed = len(inst_toks & _tokens(disp)) / len(inst_toks) if inst_toks else 0.0
        if directed < FUZZY_THRESHOLD and works_count < 1000:
            continue

        idx = _oax_id_to_idx(oa_id)
        if idx is not None and works_count > best[2]:
            best = (idx, max(ov, directed), works_count)

    if best[0] is not None:
        return best[0], 'T4', best[1]
    return None, '', 0.0


def oax_search(inst_name: str, country_code: str | None
               ) -> tuple[int | None, str, float]:
    """
    T5: pyalex institution search (full-text) with country_code filter.
    Takes the result with highest token-overlap >= FUZZY_THRESHOLD.
    """
    try:
        q = pyalex.Institutions().search(inst_name)
        if country_code:
            q = q.filter(country_code=country_code)
        raw = q.get()
        items: list[dict] = list(raw) if raw else []  # type: ignore[arg-type]
    except Exception:
        return None, '', 0.0

    inst_toks = _tokens(inst_name)
    best: tuple[int | None, float] = (None, 0.0)

    for item in items:
        oa_id       = item['id']           if 'id'           in item else ''
        disp        = item['display_name'] if 'display_name' in item else ''
        works_count = (item.get('works_count') or 0)
        ov          = _jaccard(inst_toks, _tokens(disp))
        directed    = len(inst_toks & _tokens(disp)) / len(inst_toks) if inst_toks else 0.0
        score       = max(ov, directed)
        if score < FUZZY_THRESHOLD and works_count < 1000:
            continue
        if score > best[1]:
            idx = _oax_id_to_idx(oa_id)
            if idx is not None:
                best = (idx, score)

    if best[0] is not None:
        return best[0], 'T5', best[1]
    return None, '', 0.0


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-api', action='store_true',
                        help='Skip pyalex API calls (T4/T5); faster for OA-only test')
    args = parser.parse_args()

    paths = load_config()
    inst_path = str(paths.openalex / 'institutions.parquet')
    hcr_path  = Path(__file__).parent.parent / 'data' / '2025_HCR.xlsx'

    hcr  = pd.read_excel(hcr_path)
    oa   = build_oa_inst_index(inst_path)
    print('Building indexes...')
    alt_idx, tok_idx, acr_idx = build_indexes(oa)
    print(f'  alt_idx: {sum(len(v) for v in alt_idx.values()):,} entries  '
          f'tok_idx: {sum(len(v) for v in tok_idx.values()):,} entries  '
          f'acr_idx: {sum(len(v) for v in acr_idx.values()):,} entries')
    raw_affils = collect_hcr_affils(hcr)
    print(f'\n{len(raw_affils)} unique affiliation strings to resolve\n')

    cache = _load_cache()
    n_cache_hits = 0
    print(f'Cache: {len(cache)} entries loaded from {CACHE_PATH.name}')

    tier_counts: dict[str, int] = defaultdict(int)
    results: list[dict] = []

    for i, raw in enumerate(raw_affils):
        inst_name, cc = parse_affil(raw)

        # T0: manual override
        manual_idx = MANUAL_MAP.get(_norm(inst_name))
        idx  = manual_idx
        tier = 'T0' if manual_idx is not None else ''

        # T1 + T2 + T2b
        if idx is None:
            idx, tier = match_oa(inst_name, cc, oa, alt_idx, acr_idx)

        # T3
        if idx is None:
            idx, tier, _ = match_oa_fuzzy(inst_name, cc, oa, tok_idx)

        # T4 + T5: pyalex API (with disk cache)
        if idx is None and not args.no_api:
            cache_key = f'{inst_name}|{cc or ""}'
            if cache_key in cache:
                entry = cache[cache_key]
                idx   = entry['idx']
                tier  = entry['tier']
                n_cache_hits += 1
            else:
                time.sleep(API_DELAY)
                idx, tier, _ = oax_autocomplete(inst_name, cc)
                if idx is None:
                    time.sleep(API_DELAY)
                    idx, tier, _ = oax_search(inst_name, cc)
                cache[cache_key] = {'idx': idx, 'tier': tier}
                _save_cache(cache)
                status = f'{tier} → {idx}' if idx else 'MISS'
                print(f'  [{cc or "??"}] {inst_name[:60]}  {status}', flush=True)

        tier_counts[tier if idx is not None else 'MISS'] += 1
        results.append({
            'raw':          raw,
            'inst_name':    inst_name,
            'country_code': cc,
            'institution_idx': idx,
            'tier':         tier,
        })

        if (i + 1) % 100 == 0:
            resolved = sum(1 for r in results if r['institution_idx'] is not None)
            print(f'  {i+1:>4}/{len(raw_affils)}  resolved so far: {resolved}')

    # ── summary ──────────────────────────────────────────────────────────────
    n_total    = len(results)
    n_resolved = sum(1 for r in results if r['institution_idx'] is not None)
    n_miss     = n_total - n_resolved
    sep = '─' * 80

    print(f'\n{"═"*80}')
    print(f'Resolution summary  ({n_resolved}/{n_total} = {n_resolved/n_total*100:.1f}%)')
    print(f'{"═"*80}')
    print(f'  {"Tier":<8}  {"Count":>6}')
    print(sep)
    for tier in ['T0', 'T1', 'T1-amb', 'T2', 'T2b', 'T3', 'T4', 'T5', 'MISS']:
        n = tier_counts.get(tier, 0)
        if n:
            print(f'  {tier:<8}  {n:>6}')

    if n_miss:
        print(f'\n{sep}')
        print(f'UNRESOLVED ({n_miss}):')
        print(sep)
        for r in results:
            if r['institution_idx'] is None:
                cc_str = r['country_code'] or '??'
                print(f'  [{cc_str}]  {r["raw"]}')

    # ── per-country miss breakdown ────────────────────────────────────────────
    if n_miss:
        from collections import Counter
        miss_cc = Counter(r['country_code'] or '??' for r in results
                          if r['institution_idx'] is None)
        print(f'\nMisses by country:')
        for cc, n in miss_cc.most_common(20):
            print(f'  {cc}  {n}')

    # ── person-level resolution ───────────────────────────────────────────────
    # Resolution is person-level: an HCR person is resolvable if ANY of their
    # affiliations (primary or secondary) maps to a retained OA institution.
    # Missing a company or NFP co-affiliation is not a problem — those won't
    # appear in the rankings anyway.
    print(f'\n{"═"*80}')
    print('Person-level resolution  (resolved if ≥1 affiliation hits)')
    print(f'{"═"*80}')
    raw_to_idx = {r['raw']: r['institution_idx'] for r in results}
    n_persons       = len(hcr)
    n_p_resolved    = 0
    n_p_all_miss    = 0
    n_p_only_co     = 0   # all resolved affiliations are companies/NFP (no ranking)
    for _, row in hcr.iterrows():
        affils = []
        for col in ('Primary Affiliation', 'Secondary Affiliations'):
            val = str(row.get(col) or '')
            if val and val.lower() != 'nan':
                for part in val.split(';'):
                    part = part.strip()
                    if part:
                        affils.append(part)
        resolved = [raw_to_idx.get(a) for a in affils if raw_to_idx.get(a) is not None]
        if resolved:
            n_p_resolved += 1
        else:
            n_p_all_miss += 1
    print(f'  Total HCR persons      : {n_persons:,}')
    print(f'  ≥1 affil resolved      : {n_p_resolved:,}  ({n_p_resolved/n_persons*100:.1f}%)')
    print(f'  All affiliations MISS  : {n_p_all_miss:,}  ({n_p_all_miss/n_persons*100:.1f}%)')
    if n_p_all_miss:
        print(f'\n  HCR persons with NO resolved affiliation:')
        for _, row in hcr.iterrows():
            affils = []
            for col in ('Primary Affiliation', 'Secondary Affiliations'):
                val = str(row.get(col) or '')
                if val and val.lower() != 'nan':
                    for part in val.split(';'):
                        part = part.strip()
                        if part:
                            affils.append(part)
            resolved = [raw_to_idx.get(a) for a in affils if raw_to_idx.get(a) is not None]
            if not resolved:
                affil_str = ' | '.join(affils[:2])
                print(f'    {row.get("First Name","")} {row.get("Last Name","")}  '
                      f'[{row.get("Category","")}]  {affil_str}')

    # ── write per-person institution map for downstream disambiguation ────────
    person_inst: dict[str, list[int]] = {}
    for _, row in hcr.iterrows():
        first = str(row.get('First Name', '') or '').strip()
        last  = str(row.get('Last Name',  '') or '').strip()
        cat   = str(row.get('Category',   '') or '').strip()
        key   = f'{first}|||{last}|||{cat}'
        affils: list[str] = []
        for col in ('Primary Affiliation', 'Secondary Affiliations'):
            val = str(row.get(col) or '')
            if val and val.lower() != 'nan':
                for part in val.split(';'):
                    part = part.strip()
                    if part:
                        affils.append(part)
        inst_idxs = [raw_to_idx[a] for a in affils if raw_to_idx.get(a) is not None]
        person_inst[key] = inst_idxs

    inst_map_path = Path(__file__).parent.parent / 'data' / 'hcr_person_inst.json'
    inst_map_path.write_text(json.dumps(person_inst))
    n_with_inst = sum(1 for v in person_inst.values() if v)
    print(f'\nWrote {inst_map_path.name}:  {len(person_inst):,} persons, '
          f'{n_with_inst:,} with ≥1 resolved institution')


if __name__ == '__main__':
    main()
