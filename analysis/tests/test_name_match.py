"""
Tests for analysis/name_match.py

Run with:
    .venv/bin/python -m pytest analysis/tests/test_name_match.py -v

Coverage:
  _first_alpha         — edge cases in initial extraction
  parse_name/hca/hcr   — nameparser integration + diacritics
  is_usable            — parsability guard
  exact_key/initial_key — key derivation
  build_hca_lookup     — lookup dict construction, indexing, ambiguity
  match_hcr_name       — two-tier matching end-to-end
"""

import pytest
import pandas as pd

from name_match import (
    _first_alpha,
    parse_name,
    parse_hca,
    parse_hcr,
    build_hca_lookup,
    match_hcr_name,
    MatchResult,
)


# ── _first_alpha ──────────────────────────────────────────────────────────────

class TestFirstAlpha:
    @pytest.mark.parametrize("s, expected", [
        ("niels",  "n"),
        ("j.",     "j"),      # single initial with period
        ("n.h.",   "n"),      # dotted compound initial — first alpha is n
        (".h.",    "h"),      # leading dot, first alpha is h
        ("",       ""),       # empty string
        ("123",    ""),       # digits only
        ("123abc", "a"),      # digits before alpha
        ("   ",    ""),       # whitespace only
    ])
    def test_basic(self, s, expected):
        assert _first_alpha(s) == expected


# ── parse_name / parse_hca / parse_hcr ───────────────────────────────────────

class TestParseName:
    def test_plain_name(self):
        pn = parse_name("John Smith")
        assert pn.first_norm == "john"
        assert pn.last_norm  == "smith"
        assert pn.first_initial == "j"
        assert pn.is_usable()

    def test_middle_name_stripped(self):
        """Middle name does not appear in first_norm or affect keys."""
        pn_with    = parse_name("Niels Henrik Batjes")
        pn_without = parse_name("Niels Batjes")
        assert pn_with.first_norm    == pn_without.first_norm
        assert pn_with.last_norm     == pn_without.last_norm
        assert pn_with.first_initial == pn_without.first_initial

    def test_middle_initial_stripped(self):
        pn_with    = parse_name("Niels H. Batjes")
        pn_without = parse_name("Niels Batjes")
        assert pn_with.first_norm    == pn_without.first_norm
        assert pn_with.last_norm     == pn_without.last_norm

    def test_single_initial(self):
        pn = parse_name("J. Smith")
        assert pn.first_initial == "j"
        assert pn.last_norm     == "smith"
        assert pn.is_usable()

    def test_compound_dotted_initials(self):
        """'N.H. Batjes' — OA display_name initials format."""
        pn = parse_hca("N.H. Batjes")
        assert pn.first_initial == "n"
        assert pn.last_norm     == "batjes"
        assert pn.is_usable()

    def test_diacritics_normalised(self):
        """Diacritics removed via unidecode so ü→u, ö→o, etc."""
        pn = parse_name("Jürgen Schmidt")
        assert pn.first_norm == "jurgen"
        assert pn.last_norm  == "schmidt"

    def test_hyphenated_last(self):
        pn = parse_name("Ghassan Abou-Alfa")
        assert pn.last_norm     == "abou-alfa"
        assert pn.first_initial == "g"
        assert pn.is_usable()

    def test_empty_string_not_usable(self):
        pn = parse_name("")
        assert not pn.is_usable()

    def test_parse_hcr_two_args(self):
        """parse_hcr(first, last) equivalent to parse_name('first last')."""
        pn_two  = parse_hcr("Niels", "Batjes")
        pn_full = parse_name("Niels Batjes")
        assert pn_two.exact_key   == pn_full.exact_key
        assert pn_two.initial_key == pn_full.initial_key

    def test_parse_hcr_with_middle_initial(self):
        """HCR First Name column often includes middle initial."""
        pn = parse_hcr("Niels H.", "Batjes")
        assert pn.first_norm    == "niels"
        assert pn.last_norm     == "batjes"
        assert pn.first_initial == "n"


# ── key properties ────────────────────────────────────────────────────────────

class TestKeys:
    def test_exact_key_structure(self):
        pn = parse_name("John Smith")
        assert pn.exact_key == ("john", "smith")

    def test_initial_key_structure(self):
        pn = parse_name("John Smith")
        assert pn.initial_key == ("j", "smith")

    def test_batjes_keys_differ(self):
        """HCA initials form and HCR full-name form have different exact keys."""
        hca_pn = parse_hca("N.H. Batjes")
        hcr_pn = parse_hcr("Niels H.", "Batjes")
        assert hca_pn.exact_key != hcr_pn.exact_key

    def test_batjes_initial_keys_match(self):
        """Same first_initial and last_norm → initial_key matches."""
        hca_pn = parse_hca("N.H. Batjes")
        hcr_pn = parse_hcr("Niels H.", "Batjes")
        assert hca_pn.initial_key == hcr_pn.initial_key

    def test_different_initial_different_key(self):
        pn_alice = parse_name("Alice Wong")
        pn_bob   = parse_name("Bob Wong")
        assert pn_alice.initial_key != pn_bob.initial_key


# ── build_hca_lookup ──────────────────────────────────────────────────────────

def _make_hca(rows):
    """Build a minimal HCA DataFrame from (display_name, field_idx) tuples."""
    return pd.DataFrame(rows, columns=['display_name', 'field_idx'])


class TestBuildHcaLookup:
    def test_exact_key_indexed(self):
        hca = _make_hca([("John Smith", 11)])
        exact, _ = build_hca_lookup(hca)
        assert ("john", "smith") in exact

    def test_initial_key_indexed(self):
        hca = _make_hca([("John Smith", 11)])
        _, initial = build_hca_lookup(hca)
        assert ("j", "smith") in initial

    def test_initials_format_indexed_by_initial(self):
        """'N.H. Batjes' must appear in initial_lookup under ('n', 'batjes')."""
        hca = _make_hca([("N.H. Batjes", 19)])
        _, initial = build_hca_lookup(hca)
        assert ("n", "batjes") in initial

    def test_initials_format_exact_key_not_plain(self):
        """'N.H. Batjes' exact key is NOT ('niels', 'batjes')."""
        hca = _make_hca([("N.H. Batjes", 19)])
        exact, _ = build_hca_lookup(hca)
        assert ("niels", "batjes") not in exact

    def test_multiple_fields_same_author(self):
        """Same display_name appearing in two fields → both entries in lookup."""
        hca = _make_hca([("John Smith", 11), ("John Smith", 27)])
        exact, initial = build_hca_lookup(hca)
        assert len(exact[("john", "smith")]) == 2
        assert len(initial[("j", "smith")]) == 2

    def test_two_authors_same_initial_key(self):
        """Two different people with same initial+last → ambiguous initial key."""
        hca = _make_hca([("Jane Wong", 11), ("John Wong", 27)])
        _, initial = build_hca_lookup(hca)
        entries = initial[("j", "wong")]
        assert len(entries) == 2
        display_names = {dn for dn, _ in entries}
        assert "Jane Wong" in display_names
        assert "John Wong" in display_names

    def test_unparseable_row_skipped(self):
        hca = _make_hca([("", 11), ("John Smith", 27)])
        exact, _ = build_hca_lookup(hca)
        assert len(exact) == 1    # only Smith entry

    def test_returns_plain_dicts(self):
        hca = _make_hca([("John Smith", 11)])
        exact, initial = build_hca_lookup(hca)
        assert isinstance(exact, dict)
        assert isinstance(initial, dict)


# ── match_hcr_name ────────────────────────────────────────────────────────────

def _lookup(rows):
    return build_hca_lookup(_make_hca(rows))


class TestMatchHcrName:
    def test_exact_match(self):
        exact, initial = _lookup([("John Smith", 11)])
        result = match_hcr_name("John", "Smith", exact, initial)
        assert result.tier == 'exact'
        assert result.matched
        assert result.n_ambiguous == 1

    def test_exact_preferred_over_initial(self):
        """When full name matches exactly, tier must be 'exact' not 'initial'."""
        exact, initial = _lookup([("Niels Batjes", 19)])
        result = match_hcr_name("Niels", "Batjes", exact, initial)
        assert result.tier == 'exact'

    def test_initial_match_for_initials_hca(self):
        """Core use case: OA 'N.H. Batjes' matches HCR 'Niels H. Batjes'."""
        exact, initial = _lookup([("N.H. Batjes", 19)])
        result = match_hcr_name("Niels H.", "Batjes", exact, initial)
        assert result.tier == 'initial'
        assert result.matched

    def test_initial_match_single_period_initial(self):
        """'J. Caro' in HCA matches 'Juergen Caro' in HCR via initial."""
        exact, initial = _lookup([("J. Caro", 15)])
        result = match_hcr_name("Juergen", "Caro", exact, initial)
        assert result.tier == 'initial'

    def test_no_match_different_initial(self):
        """'Alice Wong' does not match HCR 'Bob Wong' — initials differ."""
        exact, initial = _lookup([("Alice Wong", 11)])
        result = match_hcr_name("Bob", "Wong", exact, initial)
        assert result.tier is None
        assert not result.matched

    def test_no_match_different_last(self):
        exact, initial = _lookup([("John Smith", 11)])
        result = match_hcr_name("John", "Jones", exact, initial)
        assert result.tier is None

    def test_no_match_empty_hca(self):
        exact, initial = build_hca_lookup(_make_hca([]))
        result = match_hcr_name("John", "Smith", exact, initial)
        assert result.tier is None

    def test_unparseable_hcr_no_match(self):
        exact, initial = _lookup([("John Smith", 11)])
        result = match_hcr_name("", "", exact, initial)
        assert result.tier is None
        assert result.n_ambiguous == 0

    def test_diacritics_exact_match(self):
        """'Jürgen Schmidt' in HCA matches 'Jurgen Schmidt' in HCR (unidecode)."""
        exact, initial = _lookup([("Jürgen Schmidt", 16)])
        result = match_hcr_name("Jurgen", "Schmidt", exact, initial)
        assert result.tier == 'exact'

    def test_ambiguity_count_for_initial_match(self):
        """Two different HCA with same initial+last → n_ambiguous = 2."""
        exact, initial = _lookup([("Jane Wong", 11), ("John Wong", 27)])
        result = match_hcr_name("James", "Wong", exact, initial)
        assert result.tier == 'initial'
        assert result.n_ambiguous == 2

    def test_no_ambiguity_same_person_two_fields(self):
        """Same display_name in two fields → still n_ambiguous = 1."""
        exact, initial = _lookup([("J. Smith", 11), ("J. Smith", 27)])
        result = match_hcr_name("James", "Smith", exact, initial)
        assert result.tier == 'initial'
        assert result.n_ambiguous == 1

    def test_hca_matches_contains_field_idx(self):
        """hca_matches entries are (display_name, field_idx) tuples."""
        exact, initial = _lookup([("John Smith", 11)])
        result = match_hcr_name("John", "Smith", exact, initial)
        assert result.hca_matches
        dn, fid = result.hca_matches[0]
        assert fid == 11
        assert dn == "John Smith"

    def test_initial_match_hyphenated_last(self):
        """Hyphenated last names match correctly."""
        exact, initial = _lookup([("G.K. Abou-Alfa", 27)])
        result = match_hcr_name("Ghassan K.", "Abou-Alfa", exact, initial)
        assert result.tier == 'initial'
        assert result.matched

    def test_middle_name_does_not_block_exact(self):
        """HCA 'Niels H. Batjes' (full middle) still exact-matches HCR 'Niels Batjes'."""
        exact, initial = _lookup([("Niels H. Batjes", 19)])
        result = match_hcr_name("Niels", "Batjes", exact, initial)
        assert result.tier == 'exact'
