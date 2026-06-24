"""
name_match.py — Two-tier name matching for HCR → HCA comparison.

Tier 1 (exact): normalised (first_norm, last_norm) must match.
Tier 2 (initial): (first_initial, last_norm) fallback for initials-format OA names.

The initial tier recovers cases where OA stores "N.H. Batjes" while HCR has
"Niels H. Batjes" — both yield first_initial="n", last_norm="batjes".

Does NOT use display_name_alternatives: OA alternatives lists are contaminated
with co-author names (confirmed in arc_grants disambiguation work).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import pandas as pd
from nameparser import HumanName
from unidecode import unidecode


# ── primitive normalisation ───────────────────────────────────────────────────

def _norm(s: str) -> str:
    """unidecode + lowercase + strip."""
    return unidecode(str(s or '')).lower().strip()


def _first_alpha(s: str) -> str:
    """First alphabetic character of a string, or ''."""
    for ch in s:
        if ch.isalpha():
            return ch
    return ''


# ── parsed name ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ParsedName:
    first_norm:    str   # normalised first field from nameparser (may be "n.h." for initials)
    last_norm:     str   # normalised last field
    first_initial: str   # first alphabetic char of first_norm, or ''

    @property
    def exact_key(self) -> tuple[str, str]:
        return (self.first_norm, self.last_norm)

    @property
    def initial_key(self) -> tuple[str, str]:
        return (self.first_initial, self.last_norm)

    def is_usable(self) -> bool:
        return bool(self.first_norm and self.last_norm and self.first_initial)


def parse_name(full_name: str) -> ParsedName:
    """Parse any name string via nameparser + unidecode."""
    raw = _norm(full_name)
    hn  = HumanName(raw)
    first = hn.first.strip(' .,')
    last  = hn.last.strip(' .,')
    return ParsedName(
        first_norm    = first,
        last_norm     = last,
        first_initial = _first_alpha(first),
    )


def parse_hcr(first: str, last: str) -> ParsedName:
    return parse_name(f'{first} {last}')


def parse_hca(display_name: str) -> ParsedName:
    return parse_name(display_name)


# ── match result ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MatchResult:
    tier:        str | None  # 'exact', 'initial', or None
    hca_matches: tuple       # ((display_name, field_idx), ...) — may have duplicates across fields
    n_ambiguous: int         # distinct author_idxs sharing the initial key (1 for exact)

    @property
    def matched(self) -> bool:
        return self.tier is not None


# ── lookup construction ───────────────────────────────────────────────────────

def build_hca_lookup(
    hca: pd.DataFrame,
) -> tuple[dict[tuple, list], dict[tuple, list]]:
    """
    Build exact and initial lookup dicts from HCA display_names.

    hca must have columns: display_name, field_idx, author_idx.

    Returns:
        exact_lookup  : {(first_norm, last_norm)   : [(display_name, field_idx), ...]}
        initial_lookup: {(first_initial, last_norm) : [(display_name, field_idx), ...]}

    The initial_lookup entry for a key may contain entries from multiple distinct
    authors (ambiguous); callers can count distinct display_names to assess risk.
    """
    exact:   dict[tuple, list] = defaultdict(list)
    initial: dict[tuple, list] = defaultdict(list)

    for _, row in hca.iterrows():
        pn = parse_hca(str(row.get('display_name') or ''))
        if not pn.is_usable():
            continue
        entry = (str(row.get('display_name', '')), int(row['field_idx']))
        exact[pn.exact_key].append(entry)
        initial[pn.initial_key].append(entry)

    return dict(exact), dict(initial)


# ── matching ──────────────────────────────────────────────────────────────────

def match_hcr_name(
    hcr_first: str,
    hcr_last: str,
    exact_lookup:   dict[tuple, list],
    initial_lookup: dict[tuple, list],
) -> MatchResult:
    """
    Match one HCR researcher against pre-built HCA lookups.

    Returns MatchResult with:
      tier='exact'   — full first_norm matched (high confidence)
      tier='initial' — only first_initial matched (medium confidence;
                       check n_ambiguous to assess false-positive risk)
      tier=None      — no match found
    """
    pn = parse_hcr(hcr_first, hcr_last)

    if not pn.is_usable():
        return MatchResult(tier=None, hca_matches=(), n_ambiguous=0)

    # Tier 1: exact first name
    hits = exact_lookup.get(pn.exact_key)
    if hits:
        return MatchResult(tier='exact', hca_matches=tuple(hits), n_ambiguous=1)

    # Tier 2: first initial only
    hits = initial_lookup.get(pn.initial_key)
    if hits:
        n_distinct = len({dn for dn, _ in hits})
        return MatchResult(tier='initial', hca_matches=tuple(hits), n_ambiguous=n_distinct)

    return MatchResult(tier=None, hca_matches=(), n_ambiguous=0)
