"""
Tests for researchers/ SQL steps on synthetic tables (no file I/O).
"""

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from researchers.build_author_works import build_author_volume
from researchers.build_work_scores import aggregate_citations, build_citer_v, build_pub_v
from researchers.build_author_scores import aggregate_authors, rank_measures


@pytest.fixture
def con():
    c = duckdb.connect()
    yield c
    c.close()


def _reg(con, name, df):
    con.register(f'{name}_df', df)
    con.execute(f'CREATE TEMP TABLE {name} AS SELECT * FROM {name}_df')


# ── self-citation flagging ───────────────────────────────────────────────────

class TestAggregateCitations:

    def _setup(self, con):
        # work 1 (authors A=10, B=11) is cited by:
        #   work 2 (author 10)   -> self (shares A), v=2.0, 2024
        #   work 3 (author 12)   -> not self,        v=1.0, 2025
        #   work 4 (author 13)   -> not self,        v=None (no retained source)
        #   work 5 (authors 11,14) -> self (shares B), v=3.0, 2025
        _reg(con, 'awp', pd.DataFrame({
            'work_idx':   [1, 1, 2, 3, 4, 5, 5],
            'author_idx': [10, 11, 10, 12, 13, 11, 14],
        }))
        _reg(con, 'r', pd.DataFrame({
            'citer_idx':  [2, 3, 4, 5],
            'cited_idx':  [1, 1, 1, 1],
            'v':          [2.0, 1.0, None, 3.0],
            'citer_year': [2024, 2025, 2025, 2025],
        }))
        aggregate_citations(con, 'r', 'awp', cite_year=2025)
        return con.execute('SELECT * FROM _agg').fetchdf().iloc[0]

    def test_counts(self, con):
        g = self._setup(con)
        assert g.n_cit_raw == 4
        assert g.n_all == 3
        assert g.n_ns == 1

    def test_sums(self, con):
        g = self._setup(con)
        assert g.sv_all == pytest.approx(6.0)
        assert g.sv_ns == pytest.approx(1.0)

    def test_single_year(self, con):
        g = self._setup(con)
        assert g.n25_all == 2 and g.sv25_all == pytest.approx(4.0)
        assert g.n25_ns == 1 and g.sv25_ns == pytest.approx(1.0)

    def test_appends_across_buckets(self, con):
        self._setup(con)
        _reg(con, 'r2', pd.DataFrame({'citer_idx': [3], 'cited_idx': [2], 'v': [1.5],
                                      'citer_year': [2020]}))
        aggregate_citations(con, 'r2', 'awp', cite_year=2025)
        assert con.execute('SELECT COUNT(*) FROM _agg').fetchone()[0] == 2


# ── publication v ────────────────────────────────────────────────────────────

class TestPubV:

    def test_field_weighted_mean(self, con):
        _reg(con, 'wv', pd.DataFrame({
            'work_idx': [1, 1], 'field_idx': [11, 12],
            'source_v': [3.0, 0.5], 'work_v': [2.0, 1.0]}))
        # field 11 gets 0.25 + 0.5 = 0.75 from two subfields; field 12 gets 0.25
        _reg(con, 'wsf', pd.DataFrame({
            'work_idx': [1, 1, 1], 'subfield_idx': [1100, 1101, 1200],
            'field_idx': [11, 11, 12], 'w': [0.25, 0.5, 0.25]}))
        build_pub_v(con, 'wv', 'wsf')
        build_citer_v(con, 'wv')
        pv = con.execute('SELECT pub_v, pub_w FROM _pv').fetchone()
        assert pv[0] == pytest.approx(0.75 * 2.0 + 0.25 * 1.0)
        assert pv[1] == pytest.approx(1.0)
        assert con.execute('SELECT v FROM _cv').fetchone()[0] == pytest.approx(3.0)


# ── author volume and scores ─────────────────────────────────────────────────

class TestAuthorVolume:

    def test_eligibility_counts_only_stanford_types(self, con):
        _reg(con, 'aw', pd.DataFrame({
            'author_idx': [1] * 6 + [2] * 5,
            'publication_year': [2000, 2001, 2002, 2003, 2004, 2005] + [2010] * 5,
            'stanford_type': [True] * 4 + [False] * 2 + [True] * 5,
        }))
        build_author_volume(con, 'aw', min_works=5)
        v = con.execute('SELECT * FROM _vol ORDER BY author_idx').fetchdf()
        assert list(v.n_works) == [6, 5]
        assert list(v.n_works_st) == [4, 5]
        assert list(v.eligible) == [False, True]
        assert v.first_year[0] == 2000 and v.last_year[0] == 2005


class TestAuthorScores:

    def _setup(self, con):
        _reg(con, 'aw', pd.DataFrame({
            'author_idx':    [1, 1, 1],
            'work_idx':      [10, 11, 12],
            'authors_count': [1, 4, 2],
        }))
        _reg(con, 'ws', pd.DataFrame({
            'work_idx':  [10, 11, 12],
            'pub_v':     [2.0, 1.0, None],
            'n_cit_raw': [3, 2, 0],
            'n_all':     [2, 2, 0], 'sv_all': [4.0, 1.0, 0.0],
            'n_ns':      [1, 2, 0], 'sv_ns':  [3.0, 1.0, 0.0],
            'n25_all':   [0, 1, 0], 'sv25_all': [0.0, 0.5, 0.0],
            'n25_ns':    [0, 1, 0], 'sv25_ns':  [0.0, 0.5, 0.0],
        }))
        aggregate_authors(con, 'aw', 'ws')
        return con.execute('SELECT * FROM _as').fetchdf().iloc[0]

    def test_pub_v_full_and_frac(self, con):
        a = self._setup(con)
        assert a.n_pub == 2
        assert a.pubv_full == pytest.approx(1.5)
        # weights 1 and 1/4
        assert a.pubv_frac == pytest.approx((2.0 + 0.25 * 1.0) / 1.25)

    def test_citer_v_excludes_uncited(self, con):
        a = self._setup(con)
        assert a.n_cited_all == 2
        # per-work <v>: 2.0 and 0.5
        assert a.cv_full_all == pytest.approx(1.25)
        assert a.cv_frac_all == pytest.approx((2.0 + 0.25 * 0.5) / 1.25)

    def test_non_self_and_single_year(self, con):
        a = self._setup(con)
        assert a.cv_full_ns == pytest.approx((3.0 + 0.5) / 2)
        assert a.n_cited_25ns == 1 and a.cv_full_25ns == pytest.approx(0.5)

    def test_self_share(self, con):
        a = self._setup(con)
        assert a.cites_all == 4 and a.cites_ns == 3
        assert a.self_share == pytest.approx(0.25)


class TestRanks:

    def test_threshold_and_order(self, con):
        df = pd.DataFrame({
            'author_idx': [1, 2, 3],
            'main_field': [11, 11, 11], 'main_subfield': [1100, 1100, 1100],
            'n_pub': [5, 5, 4],
            'pubv_full': [1.0, 2.0, 9.0], 'pubv_frac': [1.0, 2.0, 9.0],
        })
        for m in ('all', 'ns', '25all', '25ns'):
            df[f'n_cited_{m}'] = 0
            df[f'cv_full_{m}'] = None
            df[f'cv_frac_{m}'] = None
        _reg(con, 'f', df)
        r = con.execute(f'SELECT author_idx, rank_pubv_full, top_field_pubv_full '
                        f'FROM (SELECT *, {rank_measures(5)} FROM f) ORDER BY author_idx').fetchdf()
        assert list(r.rank_pubv_full[:2]) == [2, 1]
        assert pd.isna(r.rank_pubv_full[2])          # below volume threshold: unranked
        assert r.top_field_pubv_full[1] == pytest.approx(0.5)
