"""
name_util.py — Name parsing and normalisation utilities.

Converts raw name strings into normalised, parsed form.
All nameparser configuration lives here so every pipeline that imports
this module treats names identically.

Exports:
  clean_name(s)   — punctuation normalisation before HumanName parsing
  norm(s)         — unidecode + lowercase + strip, for all comparisons
  last_norm(last) — last word of last-name field, normed; blocking key
  fi(first)       — first alpha char of first-name field
  fa(hn)          — full alpha string of hn.first
  mi(hn)          — first alpha char of hn.middle
"""
from __future__ import annotations

import unicodedata

from unidecode import unidecode
from nameparser import HumanName
from nameparser.config import CONSTANTS as _NP

# ── nameparser configuration ──────────────────────────────────────────────────
# Applied once on import; affects all HumanName() calls in this process.

_NP.titles.remove('se')            # Korean given-name component, not Señor
_NP.titles.remove('shaik')         # South Asian first-name component, not a title
_NP.titles.remove('mahdi')         # Arabic given name, not a title
_NP.titles.remove('sultan')        # Arabic/Turkish given name, not a title
_NP.titles.remove('ab')            # South Asian name initials, not a degree title
_NP.titles.remove('sa')            # name initials, not a title
_NP.titles.remove('gen')           # Chinese given name, not a military title
_NP.titles.remove('prince')        # given name, not a royal title
_NP.suffix_acronyms.remove('chi')  # Chinese/Korean surname
_NP.suffix_acronyms.remove('asa')  # Scandinavian/Hebrew surname
_NP.suffix_acronyms.remove('ma')   # Chinese surname (also MA degree)
_NP.prefixes.add('vanden')         # Belgian/Dutch particle


# ── pre-parse cleaning ────────────────────────────────────────────────────────

_HYPHENS = str.maketrans({
    '‐': '-',  # hyphen
    '‑': '-',  # non-breaking hyphen
    '‒': '-',  # figure dash
    '–': '-',  # en dash
    '—': '-',  # em dash
    '―': '-',  # horizontal bar
    '−': '-',  # minus sign
    '­': '-',  # soft hyphen
})

_APOSTROPHES = str.maketrans({
    '‘': "'",  # left single quotation mark
    '’': "'",  # right single quotation mark
    'ʼ': "'",  # modifier letter apostrophe
    '`': "'",  # grave accent
    '´': "'",  # acute accent
})

_SPACES = str.maketrans({
    ' ': ' ',  # non-breaking space
    ' ': ' ',  # narrow no-break space
    ' ': ' ',  # thin space
})


def clean_name(s: str) -> str:
    """
    Normalise punctuation before HumanName parsing.
    Strips Unicode control/formatting characters, then collapses hyphen,
    apostrophe, and non-standard space variants to ASCII equivalents.
    """
    s = str(s or '')
    s = ''.join(c for c in s if unicodedata.category(c) not in ('Cf', 'Cc'))
    s = s.translate(_HYPHENS)
    s = s.translate(_APOSTROPHES)
    s = s.translate(_SPACES)
    return s.strip()


# ── normalisation ─────────────────────────────────────────────────────────────

def norm(s: str) -> str:
    """Unidecode + lowercase + strip.  Used for all string comparisons."""
    return unidecode(str(s or '')).lower().strip()

def last_norm(last: str) -> str:
    """Last word of last-name field, normed.  Used as blocking key."""
    words = norm(last).split()
    return words[-1].strip('.,') if words else ''

def fi(first: str) -> str:
    """First alpha character of first-name field."""
    for c in norm(first):
        if c.isalpha():
            return c
    return ''


# ── HumanName field extractors ────────────────────────────────────────────────

def fa(hn: HumanName) -> str:
    """Full alpha string of hn.first (for full-name comparison)."""
    return ''.join(c for c in hn.first.lower() if c.isalpha())

def mi(hn: HumanName) -> str:
    """First alpha character of hn.middle."""
    return next((c for c in hn.middle.lower() if c.isalpha()), '')
