"""
test_pipeline_params.py — Integration tests for pipeline parameters.

Uses synthetic in-memory parquets. No connection to the real OA snapshot.

Covers:
  build_field_candidacy  : window filter, source deduplication, inst weighting
  build_edge_list        : tau_s, tau_u, field_idx filtering, R_i computation
  build_csr_field        : m (which blocks are built), rho (edge weight scaling)
  rank_field             : output schema, scores positive and finite, alpha=1 vs <1

Synthetic network (field 17, window 2020–2024)
----------------------------------------------
Sources:
  S1=101  weighted_works=100  (retained at tau_s=10, tau_abs=50)
  S2=102  weighted_works=100  (retained)
  S3=103  weighted_works=10   (excluded at tau_abs=50)

Institutions:
  U1=201  weighted_works=100  (retained at tau_u=10)
  U2=202  weighted_works=10   (excluded at tau_abs=50)

Flat works (field 17, year 2021 — inside window):
  W1=1  S1 U1  inst_weight=1.0  field_weight=1.0  ref_count=2
  W2=2  S1 U1  inst_weight=1.0  field_weight=1.0  ref_count=1
        (same source as W1 — for testing self-source-reference exclusion in beta)
  W3=3  S2 U1  inst_weight=1.0  field_weight=1.0  ref_count=1
  W4=4  S3 U2  inst_weight=1.0  field_weight=1.0  ref_count=1
        (excluded: S3 below tau_s, U2 below tau_u)

Corpus references:
  W1→W3  (S1→S2 via U1)
  W1→W2  (S1→S1 via U1 — same-source self-ref)
  W3→W1  (S2→S1 via U1)
  W1→W4  (would be S1→S3 but W4 excluded by tau)

Edge list after tau filtering (tau_s_abs=50, tau_u_abs=50):
  W1,S1,U1 → W3,S2,U1   R_i[W1]=2
  W1,S1,U1 → W2,S1,U1   R_i[W1]=2
  W3,S2,U1 → W1,S1,U1   R_i[W3]=1

R̄ = (2 + 1) / 2 = 1.5

rho=0 edge weights (ew = efg × R̄/R_i):
  rows from W1:  1.0 × (1.5/2) = 0.75
  row  from W3:  1.0 × (1.5/1) = 1.5

rho=1 edge weights: ew = 1.0 for all rows.
"""

import sys
from dataclasses import replace
from pathlib import Path

import duckdb
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from util.runs import Run
from build_field_candidacy import (build_field_candidacy,
                                   build_leiden_candidacy,
                                   build_subfield_candidacy)
from build_edge_list_field import build_edge_list
from build_csr_field import build_csr_field
from run_rankings import rank_field


# ─── Corpus fixture ───────────────────────────────────────────────────────────

@pytest.fixture
def corpus(tmp_path):
    """
    Write all synthetic parquets to tmp_path and return a dict of paths + base Run.
    Candidacy parquets are pre-built (not via build_field_candidacy) so the
    tau tests exercise only build_edge_list and build_csr_field.
    """
    db = duckdb.connect()

    # flat_works — one row per (work × institution × subfield) for field 17
    # country_code: U1(201)=AU, U2(202)=IN — used by bloc filter tests
    db.execute("""
        CREATE TABLE fw AS FROM (VALUES
            (1, 2021, 101, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning', 17, 1.0, 1, 'Mathematics and Computer Science', 2),
            (2, 2021, 101, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning', 17, 1.0, 1, 'Mathematics and Computer Science', 1),
            (3, 2021, 102, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning', 17, 1.0, 1, 'Mathematics and Computer Science', 1),
            (4, 2021, 103, 202, 'IN', 1.0, 1.0, 1700, 'Machine Learning', 17, 1.0, 1, 'Mathematics and Computer Science', 1)
        ) t(work_idx, publication_year, source_idx, institution_idx, country_code,
            inst_weight, direct_inst_weight,
            subfield_idx, subfield_name, field_idx, field_weight,
            leiden_idx, leiden_name, referenced_works_count)
    """)
    fw_path = str(tmp_path / 'flat_works.parquet')
    db.execute(f"COPY fw TO '{fw_path}'")

    # corpus_references — both works must be in flat_works
    db.execute("""
        CREATE TABLE cr AS FROM (VALUES
            (1, 3),
            (1, 2),
            (3, 1),
            (1, 4)
        ) t(citer_idx, cited_idx)
    """)
    cr_path = str(tmp_path / 'corpus_refs.parquet')
    db.execute(f"COPY cr TO '{cr_path}'")

    # source candidacy (field 17)
    db.execute("""
        CREATE TABLE sc AS FROM (VALUES
            (17, 101, 100.0),
            (17, 102, 100.0),
            (17, 103, 10.0)
        ) t(field_idx, source_idx, weighted_works)
    """)
    sc_path = str(tmp_path / 'field_source_cands_2020_2024.parquet')
    db.execute(f"COPY sc TO '{sc_path}'")

    # institution candidacy (field 17)
    db.execute("""
        CREATE TABLE ic AS FROM (VALUES
            (17, 201, 100.0),
            (17, 202, 10.0)
        ) t(field_idx, institution_idx, weighted_works)
    """)
    ic_path = str(tmp_path / 'field_inst_cands_2020_2024.parquet')
    db.execute(f"COPY ic TO '{ic_path}'")
    db.close()

    base_run = Run(
        tc0=2020, tc1=2024,
        tau_s=10.0, tau_u=10.0,
        m=(0, 1, 1, 0),
        alpha=1.0, rho=0,
        label='test',
        field_idx=17,
    )

    return {
        'tmp':    tmp_path,
        'fw':     fw_path,
        'cr':     cr_path,
        'sc':     sc_path,
        'ic':     ic_path,
        'run':    base_run,
    }


def _build_el(corpus, run) -> str:
    """Build edge list for `run` and return the path."""
    el_path = run.el_path(str(corpus['tmp']))
    with duckdb.connect() as db:
        build_edge_list(db, corpus['fw'], corpus['cr'], corpus['sc'], corpus['ic'], run, el_path)
    return el_path


def _build_csr(corpus, run, el_path=None):
    """Build CSRData for `run` (builds edge list if not supplied)."""
    if el_path is None:
        el_path = _build_el(corpus, run)
    with duckdb.connect() as db:
        csr = build_csr_field(db, el_path, corpus['sc'], corpus['ic'], run)
    return csr


# ─── build_field_candidacy ────────────────────────────────────────────────────

class TestBuildFieldCandidacy:
    """Stage 2: window filter, source deduplication, institution weighting."""

    @pytest.fixture
    def flat_works_multi(self, tmp_path):
        """
        Work 10 has source S5 and TWO institutions U5/U6 in field 17.
        Source candidacy must count field_weight ONCE for S5 (DISTINCT work fix).
        Institution candidacy must use inst_weight:
          U5: inst_weight=0.7 → contribution = 1.0 × 0.7 = 0.7
          U6: inst_weight=0.3 → contribution = 1.0 × 0.3 = 0.3
        Work 11 (year 2019) is OUTSIDE the 2020-2024 window → excluded.
        """
        db = duckdb.connect()
        db.execute("""
            CREATE TABLE fw AS FROM (VALUES
                (10, 2021, 501, 601, 0.7, 0.5, 1700, 'Machine Learning', 17, 1.0, 1, 'Mathematics and Computer Science', 5),
                (10, 2021, 501, 602, 0.3, 0.5, 1700, 'Machine Learning', 17, 1.0, 1, 'Mathematics and Computer Science', 5),
                (11, 2019, 501, 601, 1.0, 1.0, 1700, 'Machine Learning', 17, 1.0, 1, 'Mathematics and Computer Science', 3)
            ) t(work_idx, publication_year, source_idx, institution_idx,
                inst_weight, direct_inst_weight,
                subfield_idx, subfield_name, field_idx, field_weight,
                leiden_idx, leiden_name, referenced_works_count)
        """)
        fw_path = str(tmp_path / 'fw_multi.parquet')
        db.execute(f"COPY fw TO '{fw_path}'")
        db.close()
        return fw_path

    def test_source_deduplication(self, flat_works_multi, tmp_path):
        """S5 must contribute field_weight=1.0 once, not twice (one per institution)."""
        out_s = str(tmp_path / 'sc_multi.parquet')
        out_u = str(tmp_path / 'ic_multi.parquet')
        with duckdb.connect() as db:
            build_field_candidacy(db, flat_works_multi, '2020_2024', out_s, out_u)
        with duckdb.connect() as db:
            row = db.execute(
                f"SELECT CAST(weighted_works AS DOUBLE) FROM '{out_s}' WHERE field_idx=17 AND source_idx=501"
            ).fetchone()
        assert row is not None
        assert abs(row[0] - 1.0) < 1e-9, f"source dedup: expected 1.0, got {row[0]}"

    def test_institution_weight_u5(self, flat_works_multi, tmp_path):
        """U5 contributes inst_weight=0.7 × field_weight=1.0 = 0.7."""
        out_s = str(tmp_path / 'sc_multi2.parquet')
        out_u = str(tmp_path / 'ic_multi2.parquet')
        with duckdb.connect() as db:
            build_field_candidacy(db, flat_works_multi, '2020_2024', out_s, out_u)
        with duckdb.connect() as db:
            row = db.execute(
                f"SELECT CAST(weighted_works AS DOUBLE) FROM '{out_u}' WHERE field_idx=17 AND institution_idx=601"
            ).fetchone()
        assert row is not None
        assert abs(row[0] - 0.7) < 1e-9, f"U5 inst weight: expected 0.7, got {row[0]}"

    def test_institution_weight_u6(self, flat_works_multi, tmp_path):
        """U6 contributes inst_weight=0.3 × field_weight=1.0 = 0.3."""
        out_s = str(tmp_path / 'sc_multi3.parquet')
        out_u = str(tmp_path / 'ic_multi3.parquet')
        with duckdb.connect() as db:
            build_field_candidacy(db, flat_works_multi, '2020_2024', out_s, out_u)
        with duckdb.connect() as db:
            row = db.execute(
                f"SELECT CAST(weighted_works AS DOUBLE) FROM '{out_u}' WHERE field_idx=17 AND institution_idx=602"
            ).fetchone()
        assert row is not None
        assert abs(row[0] - 0.3) < 1e-9, f"U6 inst weight: expected 0.3, got {row[0]}"

    def test_window_filter_excludes_year_2019(self, flat_works_multi, tmp_path):
        """Work 11 (year 2019) is outside 2020–2024 and must not add to source candidacy."""
        out_s = str(tmp_path / 'sc_win.parquet')
        out_u = str(tmp_path / 'ic_win.parquet')
        with duckdb.connect() as db:
            # only 2020-2024 window — W11 (2019) excluded, W10 (2021) included
            build_field_candidacy(db, flat_works_multi, '2020_2024', out_s, out_u)
        with duckdb.connect() as db:
            row = db.execute(
                f"SELECT CAST(weighted_works AS DOUBLE) FROM '{out_s}' WHERE field_idx=17 AND source_idx=501"
            ).fetchone()
        # W10 contributes 1.0; W11 is out of window → total must be exactly 1.0, not 2.0
        assert abs(row[0] - 1.0) < 1e-9, f"window filter: expected 1.0, got {row[0]}"


# ─── build_leiden_candidacy / build_subfield_candidacy ────────────────────────

class TestLeidenSubfieldCandidacy:
    """
    Uses corpus fixture (all 4 works in field 17 → leiden_idx=1, subfield_idx=1700).
    Works 1,2: source 101, inst 201, iw=1.0, fw=1.0
    Work 3:    source 102, inst 201, iw=1.0, fw=1.0
    Work 4:    source 103, inst 202, iw=1.0, fw=1.0

    Leiden source candidacy (leiden=1): S1=2.0, S2=1.0, S3=1.0
    Leiden inst candidacy  (leiden=1): I1=3.0, I2=1.0
    Subfield candidacy (sf=1700): same values as leiden (single subfield in test corpus)
    """

    def test_leiden_source_candidacy_value(self, corpus, tmp_path):
        """S1=101: 2 distinct works (W1, W2) in leiden=1 → weighted_works=2.0."""
        out_s = str(tmp_path / 'ls.parquet')
        out_u = str(tmp_path / 'lu.parquet')
        with duckdb.connect() as db:
            build_leiden_candidacy(db, corpus['fw'], '2020_2024', out_s, out_u)
        with duckdb.connect() as db:
            row = db.execute(
                f"SELECT CAST(weighted_works AS DOUBLE) FROM '{out_s}' WHERE field_idx=1 AND source_idx=101"
            ).fetchone()
        assert row is not None
        assert abs(row[0] - 2.0) < 1e-9

    def test_leiden_inst_candidacy_value(self, corpus, tmp_path):
        """I1=201: works W1,W2,W3 all in leiden=1 with iw=1.0 → weighted_works=3.0."""
        out_s = str(tmp_path / 'ls2.parquet')
        out_u = str(tmp_path / 'lu2.parquet')
        with duckdb.connect() as db:
            build_leiden_candidacy(db, corpus['fw'], '2020_2024', out_s, out_u)
        with duckdb.connect() as db:
            row = db.execute(
                f"SELECT CAST(weighted_works AS DOUBLE) FROM '{out_u}' WHERE field_idx=1 AND institution_idx=201"
            ).fetchone()
        assert row is not None
        assert abs(row[0] - 3.0) < 1e-9

    def test_leiden_output_uses_leiden_idx_as_field_idx(self, corpus, tmp_path):
        """Output schema: field_idx column holds leiden_idx values (1–5)."""
        out_s = str(tmp_path / 'ls3.parquet')
        out_u = str(tmp_path / 'lu3.parquet')
        with duckdb.connect() as db:
            build_leiden_candidacy(db, corpus['fw'], '2020_2024', out_s, out_u)
        with duckdb.connect() as db:
            vals = set(db.execute(f"SELECT DISTINCT field_idx FROM '{out_s}'").df()['field_idx'].tolist())
        assert vals == {1}  # all test works are in leiden group 1 (CS, field 17)

    def test_subfield_source_candidacy_value(self, corpus, tmp_path):
        """subfield_idx=1700: S1=101 contributes 2.0 (works W1, W2)."""
        out_s = str(tmp_path / 'ss.parquet')
        out_u = str(tmp_path / 'su.parquet')
        with duckdb.connect() as db:
            build_subfield_candidacy(db, corpus['fw'], '2020_2024', out_s, out_u)
        with duckdb.connect() as db:
            row = db.execute(
                f"SELECT CAST(weighted_works AS DOUBLE) FROM '{out_s}' WHERE field_idx=1700 AND source_idx=101"
            ).fetchone()
        assert row is not None
        assert abs(row[0] - 2.0) < 1e-9

    def test_subfield_output_uses_subfield_idx_as_field_idx(self, corpus, tmp_path):
        """Output schema: field_idx column holds subfield_idx values."""
        out_s = str(tmp_path / 'ss2.parquet')
        out_u = str(tmp_path / 'su2.parquet')
        with duckdb.connect() as db:
            build_subfield_candidacy(db, corpus['fw'], '2020_2024', out_s, out_u)
        with duckdb.connect() as db:
            vals = set(db.execute(f"SELECT DISTINCT field_idx FROM '{out_s}'").df()['field_idx'].tolist())
        assert vals == {1700}


# ─── tau_s / tau_u filtering (build_edge_list) ────────────────────────────────

class TestTauFiltering:
    """Stage 3: retention thresholds applied to edge list construction."""

    def test_tau_s_excludes_low_source(self, corpus):
        """S3=103 (weighted_works=10) must not appear in the edge list at tau_s=10."""
        el_path = _build_el(corpus, corpus['run'])
        with duckdb.connect() as db:
            sources = set(db.execute(
                f"SELECT DISTINCT citer_source_idx FROM '{el_path}' "
                f"UNION SELECT DISTINCT cited_source_idx FROM '{el_path}'"
            ).df().iloc[:, 0].tolist())
        assert 103 not in sources, f"S3=103 should be excluded but found in edge list: {sources}"

    def test_tau_u_excludes_low_institution(self, corpus):
        """U2=202 (weighted_works=10) must not appear in the edge list at tau_u=10."""
        el_path = _build_el(corpus, corpus['run'])
        with duckdb.connect() as db:
            insts = set(db.execute(
                f"SELECT DISTINCT citer_inst_idx FROM '{el_path}' "
                f"UNION SELECT DISTINCT cited_inst_idx FROM '{el_path}'"
            ).df().iloc[:, 0].tolist())
        assert 202 not in insts, f"U2=202 should be excluded but found: {insts}"

    def test_low_tau_includes_previously_excluded_source(self, corpus):
        """At tau_s=1 (tau_abs=5), S3=103 (weighted_works=10) clears the bar."""
        run_low = replace(corpus['run'], tau_s=1.0, tau_u=1.0,
                          label='low-tau', field_idx=17)
        el_path = _build_el(corpus, run_low)
        with duckdb.connect() as db:
            n = db.execute(f"SELECT COUNT(*) FROM '{el_path}'").fetchone()[0]
        # W1→W4 is now valid (S3 and U2 are retained); extra edges expected
        assert n > 0

    def test_tau_filters_csr_source_ids(self, corpus):
        """CSR source_ids must not include S3=103."""
        csr = _build_csr(corpus, corpus['run'])
        assert 103 not in csr.source_ids

    def test_tau_filters_csr_inst_ids(self, corpus):
        """CSR inst_ids must not include U2=202."""
        csr = _build_csr(corpus, corpus['run'])
        assert 202 not in csr.inst_ids

    def test_retained_sources_in_csr(self, corpus):
        """S1=101 and S2=102 (both 100 w.w.) must appear in CSR."""
        csr = _build_csr(corpus, corpus['run'])
        assert 101 in csr.source_ids
        assert 102 in csr.source_ids

    def test_retained_institution_in_csr(self, corpus):
        """U1=201 (100 w.w.) must appear in CSR."""
        csr = _build_csr(corpus, corpus['run'])
        assert 201 in csr.inst_ids


# ─── field_idx filter ─────────────────────────────────────────────────────────

class TestFieldIdxFilter:

    def test_wrong_field_gives_empty_edge_list(self, corpus):
        """field_idx=14 has no data in our field-17 corpus → edge list must be empty."""
        run_14 = replace(corpus['run'], field_idx=14, label='f14')
        el_path = run_14.el_path(str(corpus['tmp']))
        with duckdb.connect() as db:
            build_edge_list(
                db, corpus['fw'], corpus['cr'], corpus['sc'], corpus['ic'],
                run_14, el_path,
            )
            n = db.execute(f"SELECT COUNT(*) FROM '{el_path}'").fetchone()[0]
        assert n == 0, f"field_idx=14 should yield empty edge list, got {n} rows"


# ─── m block mask (build_csr_field) ───────────────────────────────────────────

class TestMaskParameter:
    """
    Each of the four valid m values must populate exactly the right CSR blocks.

    The synthetic network has S1, S2 sources and U1 institution (after tau),
    with citations S1→U1, S2→U1 and back. All four blocks can be populated.
    """

    def _csr_for_m(self, corpus, m_str: str):
        m = tuple(int(c) for c in m_str)
        run = replace(corpus['run'], m=m, label=m_str)
        return _build_csr(corpus, run)

    def test_0110_c_si_not_none(self, corpus):
        assert self._csr_for_m(corpus, '0110').C_SI is not None

    def test_0110_c_is_not_none(self, corpus):
        assert self._csr_for_m(corpus, '0110').C_IS is not None

    def test_0110_c_ss_is_none(self, corpus):
        assert self._csr_for_m(corpus, '0110').C_SS is None

    def test_0110_c_ii_is_none(self, corpus):
        assert self._csr_for_m(corpus, '0110').C_II is None

    def test_1000_c_ss_not_none(self, corpus):
        assert self._csr_for_m(corpus, '1000').C_SS is not None

    def test_1000_c_si_is_none(self, corpus):
        assert self._csr_for_m(corpus, '1000').C_SI is None

    def test_1000_c_is_is_none(self, corpus):
        assert self._csr_for_m(corpus, '1000').C_IS is None

    def test_1000_c_ii_is_none(self, corpus):
        assert self._csr_for_m(corpus, '1000').C_II is None

    def test_0001_c_ii_not_none(self, corpus):
        assert self._csr_for_m(corpus, '0001').C_II is not None

    def test_0001_c_ss_is_none(self, corpus):
        assert self._csr_for_m(corpus, '0001').C_SS is None

    def test_0001_c_si_is_none(self, corpus):
        assert self._csr_for_m(corpus, '0001').C_SI is None

    def test_0001_c_is_is_none(self, corpus):
        assert self._csr_for_m(corpus, '0001').C_IS is None

    def test_1111_all_blocks_not_none(self, corpus):
        csr = self._csr_for_m(corpus, '1111')
        assert csr.C_SS is not None
        assert csr.C_SI is not None
        assert csr.C_IS is not None
        assert csr.C_II is not None


# ─── rho parameter (build_csr_field) ─────────────────────────────────────────

class TestRhoParameter:
    """
    rho=0: each citer work weighted by R̄/R_i (works citing many get down-weighted).
    rho=1: uniform raw edge weights.

    W1 (R_i=2) and W3 (R_i=1); R̄=1.5.
    Under rho=0 the edge from W3 (R_i=1 → R̄/R_i=1.5) weighs more than the edges
    from W1 (R_i=2 → R̄/R_i=0.75).

    C_IS raw sums (before row normalization):
      rho=0: C_IS[U1→S1] = W1→W2 (0.75) + W3→W1 (1.5) = 2.25;  U1→S2 = 0.75
      rho=1: C_IS[U1→S1] = 1.0 + 1.0 = 2.0;                     U1→S2 = 1.0
    """

    def _csr(self, corpus, rho: int):
        run = replace(corpus['run'], rho=rho, label=f'rho{rho}')
        return _build_csr(corpus, run)

    def test_rho0_and_rho1_differ(self, corpus):
        csr0 = self._csr(corpus, 0)
        csr1 = self._csr(corpus, 1)
        # C_IS must differ because rho changes edge weights
        assert not np.allclose(csr0.C_IS.toarray(), csr1.C_IS.toarray()), \
            "C_IS should differ between rho=0 and rho=1"

    def test_rho0_c_is_u1_to_s1(self, corpus):
        """C_IS[U1→S1] under rho=0 should equal 2.25 (raw, before normalization)."""
        csr = self._csr(corpus, 0)
        # inst_ids=[201], source_ids=[101,102] sorted
        i_u1 = list(csr.inst_ids).index(201)
        i_s1 = list(csr.source_ids).index(101)
        val = csr.C_IS.toarray()[i_u1, i_s1]
        assert abs(val - 2.25) < 1e-9, f"C_IS[U1→S1] rho=0: expected 2.25, got {val}"

    def test_rho0_c_is_u1_to_s2(self, corpus):
        """C_IS[U1→S2] under rho=0 should equal 0.75."""
        csr = self._csr(corpus, 0)
        i_u1 = list(csr.inst_ids).index(201)
        i_s2 = list(csr.source_ids).index(102)
        val = csr.C_IS.toarray()[i_u1, i_s2]
        assert abs(val - 0.75) < 1e-9, f"C_IS[U1→S2] rho=0: expected 0.75, got {val}"

    def test_rho1_c_is_u1_to_s1(self, corpus):
        """C_IS[U1→S1] under rho=1 should equal 2.0."""
        csr = self._csr(corpus, 1)
        i_u1 = list(csr.inst_ids).index(201)
        i_s1 = list(csr.source_ids).index(101)
        val = csr.C_IS.toarray()[i_u1, i_s1]
        assert abs(val - 2.0) < 1e-9, f"C_IS[U1→S1] rho=1: expected 2.0, got {val}"

    def test_rho1_c_is_u1_to_s2(self, corpus):
        """C_IS[U1→S2] under rho=1 should equal 1.0."""
        csr = self._csr(corpus, 1)
        i_u1 = list(csr.inst_ids).index(201)
        i_s2 = list(csr.source_ids).index(102)
        val = csr.C_IS.toarray()[i_u1, i_s2]
        assert abs(val - 1.0) < 1e-9, f"C_IS[U1→S2] rho=1: expected 1.0, got {val}"

    def test_rho0_high_ri_source_downweighted(self, corpus):
        """
        W1 (R_i=2) cites both S1 and S2; W3 (R_i=1) cites only S1.
        Under rho=0, W3's edge to S1 is weighted more than W1's edges.
        This means C_IS[U1→S1] > C_IS[U1→S2] because W3→S1 (weight 1.5)
        outweighs W1→S2 (weight 0.75).
        """
        csr = self._csr(corpus, 0)
        i_u1 = list(csr.inst_ids).index(201)
        i_s1 = list(csr.source_ids).index(101)
        i_s2 = list(csr.source_ids).index(102)
        arr = csr.C_IS.toarray()
        assert arr[i_u1, i_s1] > arr[i_u1, i_s2]


# ─── rank_field (end-to-end) ──────────────────────────────────────────────────

class TestRankField:
    """Stage 4b: rank_field returns correct schema, scores, and ranks."""

    @pytest.fixture
    def ranking(self, corpus, tmp_path):
        run = corpus['run']
        el_path  = _build_el(corpus, run)
        out_path = run.rankings_path(str(tmp_path))
        with duckdb.connect() as db:
            df, diag = rank_field(db, run, str(tmp_path), out_path, verbose=False)
        return df, diag

    def test_has_unit_type_S_and_U(self, ranking):
        df, _ = ranking
        assert set(df['unit_type'].unique()) == {'S', 'U'}

    def test_v_scores_positive(self, ranking):
        df, _ = ranking
        assert (df['v'] > 0).all(), "All v scores should be positive"

    def test_v_scores_finite(self, ranking):
        df, _ = ranking
        assert df['v'].notna().all() and np.isfinite(df['v'].values).all()

    def test_pi_joint_sum_is_one(self, ranking):
        """
        For bipartite m=(0,1,1,0), rank() divides each side by 2 so the
        JOINT sum pi_s.sum() + pi_u.sum() = 1.0 (not 1.0 per type separately).
        """
        df, _ = ranking
        total = df['pi'].sum()
        assert abs(total - 1.0) < 1e-6, f"joint pi sum: {total}"

    def test_rank_pi_starts_at_one(self, ranking):
        df, _ = ranking
        assert df['rank_pi'].min() == 1

    def test_rank_v_starts_at_one(self, ranking):
        df, _ = ranking
        assert df['rank_v'].min() == 1

    def test_required_columns_present(self, ranking):
        df, _ = ranking
        expected = {'field_idx', 'unit_idx', 'unit_type', 'pi', 'v',
                    'rank_pi', 'rank_v', 'a_p'}
        assert expected.issubset(set(df.columns))

    def test_diag_has_required_keys(self, ranking):
        _, diag = ranking
        for key in ('field_idx', 'window', 'label', 'm', 'alpha', 'rho',
                    'tau_s', 'tau_u', 'n_s_ranked', 'n_u_ranked'):
            assert key in diag, f"Missing diag key: {key}"

    def test_diag_lam1_near_one_alpha1(self, ranking):
        _, diag = ranking
        assert abs(diag['lam1'] - 1.0) < 1e-4, f"lam1={diag['lam1']}"

    def test_diag_spectral_gap_positive(self, ranking):
        _, diag = ranking
        assert diag['spectral_gap'] is not None and diag['spectral_gap'] > 0

    def test_diag_window_matches_run(self, ranking, corpus):
        _, diag = ranking
        assert diag['window'] == corpus['run'].window

    def test_diag_label_matches_run(self, ranking, corpus):
        _, diag = ranking
        assert diag['label'] == corpus['run'].label

    def test_alpha_lt1_runs_katz(self, corpus, tmp_path):
        """alpha=0.85 should complete without error and return finite scores."""
        run = replace(corpus['run'], alpha=0.85, mu_type='uniform', label='katz')
        el_path  = _build_el(corpus, run)
        out_path = run.rankings_path(str(tmp_path))
        with duckdb.connect() as db:
            df, diag = rank_field(db, run, str(tmp_path), out_path, verbose=False)
        assert np.isfinite(df['v'].values).all()
        assert diag['lam1'] == 0.0    # lam1/lam2 not computed for alpha<1
        assert diag['iters'] > 0

    def test_m_1000_returns_sources_only(self, corpus, tmp_path):
        """m=(1,0,0,0): ranking uses only C_SS — only sources are ranked."""
        run = replace(corpus['run'], m=(1, 0, 0, 0), label='ss-only')
        el_path  = _build_el(corpus, run)
        out_path = run.rankings_path(str(tmp_path))
        with duckdb.connect() as db:
            df, _ = rank_field(db, run, str(tmp_path), out_path, verbose=False)
        assert set(df['unit_type'].unique()) == {'S'}

    def test_m_0001_returns_institutions_only(self, corpus, tmp_path):
        """m=(0,0,0,1): ranking uses only C_II — only institutions are ranked."""
        run = replace(corpus['run'], m=(0, 0, 0, 1), label='ii-only')
        el_path  = _build_el(corpus, run)
        out_path = run.rankings_path(str(tmp_path))
        with duckdb.connect() as db:
            df, _ = rank_field(db, run, str(tmp_path), out_path, verbose=False)
        assert set(df['unit_type'].unique()) == {'U'}

    def test_s2_ranks_lower_than_s1(self, ranking):
        """
        S1=101 appears as cited by W3 (R_i=1, ew=1.5 under rho=0) and is
        also the citer in 2 retained edges. S2=102 is only cited via W1→W3.
        S1 should rank at or above S2 by v score.
        """
        df, _ = ranking
        s_df = df[df.unit_type == 'S'].set_index('unit_idx')
        assert s_df.loc[101, 'rank_v'] <= s_df.loc[102, 'rank_v'], \
            f"S1 (rank {s_df.loc[101,'rank_v']}) should not rank below S2 (rank {s_df.loc[102,'rank_v']})"


# ─── beta parameter (build_csr_field) ────────────────────────────────────────

class TestBetaParameter:
    """
    β=0 (default): all edges included, even (S,U)→(S,U) unit self-refs.
    β=1: exclude edges where citer_source=cited_source AND citer_inst=cited_inst.

    Synthetic network: W1→W2 has citer=(W1,S1,U1) cited=(W2,S1,U1) — same
    source AND same institution → excluded by β=1.
    W1→W3 (S1≠S2) and W3→W1 (S2≠S1) are unaffected.

    rho=0: ew(W1→W2) = ew(W1→W3) = 0.75;  ew(W3→W1) = 1.5

    C_IS[U1→S1]  β=0: W1→W2 (0.75) + W3→W1 (1.5) = 2.25
    C_IS[U1→S1]  β=1: W3→W1 only = 1.5
    C_IS[U1→S2]: unchanged (W1→W3, different source) = 0.75 in both cases
    """

    def _csr(self, corpus, beta: int):
        run = replace(corpus['run'], beta=beta, label=f'beta{beta}')
        return _build_csr(corpus, run)

    def test_beta0_and_beta1_differ(self, corpus):
        csr0 = self._csr(corpus, 0)
        csr1 = self._csr(corpus, 1)
        assert not np.allclose(csr0.C_IS.toarray(), csr1.C_IS.toarray())

    def test_beta0_c_is_u1_to_s1(self, corpus):
        """β=0: W1→W2 included → C_IS[U1,S1] = 2.25."""
        csr = self._csr(corpus, 0)
        i_u = list(csr.inst_ids).index(201)
        i_s1 = list(csr.source_ids).index(101)
        assert abs(csr.C_IS.toarray()[i_u, i_s1] - 2.25) < 1e-9

    def test_beta1_c_is_u1_to_s1(self, corpus):
        """β=1: W1→W2 excluded (same S1+U1) → C_IS[U1,S1] = 1.5."""
        csr = self._csr(corpus, 1)
        i_u = list(csr.inst_ids).index(201)
        i_s1 = list(csr.source_ids).index(101)
        assert abs(csr.C_IS.toarray()[i_u, i_s1] - 1.5) < 1e-9

    def test_beta1_preserves_cross_source_edge(self, corpus):
        """β=1: W1→W3 (S1≠S2) is kept → C_IS[U1,S2] still 0.75."""
        csr = self._csr(corpus, 1)
        i_u = list(csr.inst_ids).index(201)
        i_s2 = list(csr.source_ids).index(102)
        assert abs(csr.C_IS.toarray()[i_u, i_s2] - 0.75) < 1e-9


# ─── omega parameter (build_csr_field) ────────────────────────────────────────

@pytest.fixture
def omega_corpus(tmp_path):
    """
    Minimal 1-edge edge list where inst_weight (0.5) ≠ direct_inst_weight (1.0).
    rho=1 → ew=1.0.

    omega=0: C_IS[U1,S1] = ew × inst_weight              = 1.0 × 0.5 = 0.5
             C_SI[S1,U1] = ew × cited_inst_weight         = 1.0 × 0.5 = 0.5
    omega=1: C_IS[U1,S1] = ew × direct_inst_weight        = 1.0 × 1.0 = 1.0
             C_SI[S1,U1] = ew × direct_cited_inst_weight  = 1.0 × 1.0 = 1.0
    """
    db = duckdb.connect()

    db.execute("""
        CREATE TABLE el AS FROM (VALUES
            (1::BIGINT, 1::BIGINT, 1::BIGINT,
             2::BIGINT, 1::BIGINT, 1::BIGINT,
             0.5, 1.0, 0.5, 1.0, 1.0, 1.0)
        ) t(citer_work_idx, citer_source_idx, citer_inst_idx,
            cited_work_idx,  cited_source_idx, cited_inst_idx,
            inst_weight, direct_inst_weight,
            cited_inst_weight, direct_cited_inst_weight,
            edge_field_weight, R_i)
    """)
    el_path = str(tmp_path / 'el_omega.parquet')
    db.execute(f"COPY el TO '{el_path}'")

    db.execute("""
        CREATE TABLE sc AS FROM (VALUES (17, 1::BIGINT, 100.0))
        t(field_idx, source_idx, weighted_works)
    """)
    sc_path = str(tmp_path / 'sc_omega.parquet')
    db.execute(f"COPY sc TO '{sc_path}'")

    db.execute("""
        CREATE TABLE ic AS FROM (VALUES (17, 1::BIGINT, 100.0))
        t(field_idx, institution_idx, weighted_works)
    """)
    ic_path = str(tmp_path / 'ic_omega.parquet')
    db.execute(f"COPY ic TO '{ic_path}'")
    db.close()

    base_run = Run(
        tc0=2020, tc1=2024, tau_s=1.0, tau_u=1.0,
        m=(0, 1, 1, 0), alpha=1.0, rho=1,
        label='omega-test', field_idx=17,
    )
    return {'el': el_path, 'sc': sc_path, 'ic': ic_path, 'run': base_run}


class TestOmegaParameter:
    """
    ω=0: use inst_weight / cited_inst_weight (author-fractional).
    ω=1: use direct_inst_weight / direct_cited_inst_weight (1/N_inst).

    omega_corpus has inst_weight=0.5, direct_inst_weight=1.0 (and same for cited side).
    """

    def _csr(self, omega_corpus, omega: int):
        run = replace(omega_corpus['run'], omega=omega, label=f'omega{omega}')
        with duckdb.connect() as db:
            return build_csr_field(db, omega_corpus['el'],
                                   omega_corpus['sc'], omega_corpus['ic'], run)

    def test_omega0_and_omega1_differ(self, omega_corpus):
        csr0 = self._csr(omega_corpus, 0)
        csr1 = self._csr(omega_corpus, 1)
        assert not np.allclose(csr0.C_IS.toarray(), csr1.C_IS.toarray())

    def test_omega0_c_is_uses_inst_weight(self, omega_corpus):
        """ω=0: C_IS[U1,S1] = ew(1.0) × inst_weight(0.5) = 0.5."""
        csr = self._csr(omega_corpus, 0)
        assert abs(csr.C_IS.toarray()[0, 0] - 0.5) < 1e-9

    def test_omega1_c_is_uses_direct_inst_weight(self, omega_corpus):
        """ω=1: C_IS[U1,S1] = ew(1.0) × direct_inst_weight(1.0) = 1.0."""
        csr = self._csr(omega_corpus, 1)
        assert abs(csr.C_IS.toarray()[0, 0] - 1.0) < 1e-9

    def test_omega0_c_si_uses_cited_inst_weight(self, omega_corpus):
        """ω=0: C_SI[S1,U1] = ew(1.0) × cited_inst_weight(0.5) = 0.5."""
        csr = self._csr(omega_corpus, 0)
        assert abs(csr.C_SI.toarray()[0, 0] - 0.5) < 1e-9

    def test_omega1_c_si_uses_direct_cited_inst_weight(self, omega_corpus):
        """ω=1: C_SI[S1,U1] = ew(1.0) × direct_cited_inst_weight(1.0) = 1.0."""
        csr = self._csr(omega_corpus, 1)
        assert abs(csr.C_SI.toarray()[0, 0] - 1.0) < 1e-9


# ─── epsilon parameter (build_edge_list_field + build_csr_field) ──────────────

@pytest.fixture
def epsilon_corpus(tmp_path):
    """
    Same as corpus but with an extra corpus_reference (4→1) so that W4 (not
    retained in field 17) acts as a type2 cross-field citer.

    W1→W4: type1 sentinel edge  (W1 retained, W4 not — in corpus_refs as (1,4))
    W4→W1: type2 sentinel edge  (W4 not retained cites W1 retained — added here)

    With epsilon=1 and rho=1:
      type1: W1(S1,U1) → sentinel(SX=1, IX=1)  edge_fw = 1.0/2 = 0.5
      type2: sentinel  → W1(S1,U1)              edge_fw = 1.0/2 = 0.5  R_i=0.5
    """
    db = duckdb.connect()
    db.execute("""
        CREATE TABLE fw AS FROM (VALUES
            (1, 2021, 101, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning', 17, 1.0, 1, 'Mathematics and Computer Science', 2),
            (2, 2021, 101, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning', 17, 1.0, 1, 'Mathematics and Computer Science', 1),
            (3, 2021, 102, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning', 17, 1.0, 1, 'Mathematics and Computer Science', 1),
            (4, 2021, 103, 202, 'IN', 1.0, 1.0, 1700, 'Machine Learning', 17, 1.0, 1, 'Mathematics and Computer Science', 1)
        ) t(work_idx, publication_year, source_idx, institution_idx, country_code,
            inst_weight, direct_inst_weight,
            subfield_idx, subfield_name, field_idx, field_weight,
            leiden_idx, leiden_name, referenced_works_count)
    """)
    fw_path = str(tmp_path / 'flat_works_eps.parquet')
    db.execute(f"COPY fw TO '{fw_path}'")

    # Add (4,1) so W4 acts as a cross-field citer of W1 (type2 epsilon edge)
    db.execute("""
        CREATE TABLE cr AS FROM (VALUES
            (1, 3), (1, 2), (3, 1), (1, 4), (4, 1)
        ) t(citer_idx, cited_idx)
    """)
    cr_path = str(tmp_path / 'corpus_refs_eps.parquet')
    db.execute(f"COPY cr TO '{cr_path}'")

    db.execute("""
        CREATE TABLE sc AS FROM (VALUES
            (17, 101, 100.0), (17, 102, 100.0), (17, 103, 10.0)
        ) t(field_idx, source_idx, weighted_works)
    """)
    sc_path = str(tmp_path / 'field_source_cands_eps.parquet')
    db.execute(f"COPY sc TO '{sc_path}'")

    db.execute("""
        CREATE TABLE ic AS FROM (VALUES (17, 201, 100.0), (17, 202, 10.0))
        t(field_idx, institution_idx, weighted_works)
    """)
    ic_path = str(tmp_path / 'field_inst_cands_eps.parquet')
    db.execute(f"COPY ic TO '{ic_path}'")
    db.close()

    base_run = Run(
        tc0=2020, tc1=2024, tau_s=10.0, tau_u=10.0,
        m=(0, 1, 1, 0), alpha=1.0, rho=1,
        label='eps-test', field_idx=17,
    )
    return {'tmp': tmp_path, 'fw': fw_path, 'cr': cr_path,
            'sc': sc_path, 'ic': ic_path, 'run': base_run}


class TestEpsilonParameter:
    """
    ε=0: only within-field (retained↔retained) edges; no sentinel.
    ε=1: also add sentinel edges for cross-field citations.

    Sentinel: source_idx=1 (SX_IDX), institution_idx=1 (IX_IDX).
    Cross-field in epsilon_corpus:
      type1 W1→W4: W1 retained, W4 not → (W1,S1,U1) → (W4,SX=1,IX=1)
      type2 W4→W1: W4 not retained cites W1 → (W4,SX=1,IX=1) → (W1,S1,U1)
    """

    def _el(self, epsilon_corpus, epsilon: int) -> str:
        run = replace(epsilon_corpus['run'], epsilon=epsilon,
                      label=f'eps{epsilon}')
        el_path = run.el_path(str(epsilon_corpus['tmp']))
        with duckdb.connect() as db:
            build_edge_list(
                db, epsilon_corpus['fw'], epsilon_corpus['cr'],
                epsilon_corpus['sc'], epsilon_corpus['ic'],
                run, el_path,
            )
        return el_path

    def _csr(self, epsilon_corpus, epsilon: int):
        run = replace(epsilon_corpus['run'], epsilon=epsilon,
                      label=f'eps{epsilon}')
        el_path = self._el(epsilon_corpus, epsilon)
        with duckdb.connect() as db:
            return build_csr_field(db, el_path,
                                   epsilon_corpus['sc'], epsilon_corpus['ic'], run)

    def test_epsilon0_no_sentinel_in_edge_list(self, epsilon_corpus):
        """ε=0: no sentinel source (idx=1) in edge list."""
        el_path = self._el(epsilon_corpus, 0)
        with duckdb.connect() as db:
            sources = set(db.execute(
                f"SELECT DISTINCT cited_source_idx FROM '{el_path}'"
            ).df().iloc[:, 0].tolist())
        assert 1 not in sources

    def test_epsilon1_type1_sentinel_in_edge_list(self, epsilon_corpus):
        """ε=1: type1 edge W1→sentinel (cited_source_idx=1) appears."""
        el_path = self._el(epsilon_corpus, 1)
        with duckdb.connect() as db:
            n = db.execute(
                f"SELECT COUNT(*) FROM '{el_path}' WHERE cited_source_idx = 1"
            ).fetchone()[0]
        assert n > 0, "expected type1 sentinel edges but found none"

    def test_epsilon1_type2_sentinel_in_edge_list(self, epsilon_corpus):
        """ε=1: type2 edge sentinel→W1 (citer_source_idx=1) appears."""
        el_path = self._el(epsilon_corpus, 1)
        with duckdb.connect() as db:
            n = db.execute(
                f"SELECT COUNT(*) FROM '{el_path}' WHERE citer_source_idx = 1"
            ).fetchone()[0]
        assert n > 0, "expected type2 sentinel edges but found none"

    def test_epsilon1_more_rows_than_epsilon0(self, epsilon_corpus):
        """ε=1 edge list has strictly more rows than ε=0 (sentinel edges added)."""
        n0 = duckdb.connect().execute(
            f"SELECT COUNT(*) FROM '{self._el(epsilon_corpus, 0)}'"
        ).fetchone()[0]
        n1 = duckdb.connect().execute(
            f"SELECT COUNT(*) FROM '{self._el(epsilon_corpus, 1)}'"
        ).fetchone()[0]
        assert n1 > n0, f"epsilon=1 ({n1}) should have more rows than epsilon=0 ({n0})"

    def test_epsilon0_sentinel_not_in_source_ids(self, epsilon_corpus):
        """ε=0: sentinel source_idx=1 must not appear in CSR source_ids."""
        csr = self._csr(epsilon_corpus, 0)
        assert 1 not in csr.source_ids
        assert csr.is_sentinel_s is None

    def test_epsilon1_sentinel_in_source_ids(self, epsilon_corpus):
        """ε=1: sentinel source_idx=1 appears in CSR source_ids."""
        csr = self._csr(epsilon_corpus, 1)
        assert 1 in csr.source_ids

    def test_epsilon1_sentinel_in_inst_ids(self, epsilon_corpus):
        """ε=1: sentinel institution_idx=1 appears in CSR inst_ids."""
        csr = self._csr(epsilon_corpus, 1)
        assert 1 in csr.inst_ids

    def test_epsilon1_is_sentinel_s_set(self, epsilon_corpus):
        """ε=1: is_sentinel_s is a bool array marking position of SX_IDX=1."""
        csr = self._csr(epsilon_corpus, 1)
        assert csr.is_sentinel_s is not None
        assert csr.is_sentinel_s.any()

    def test_epsilon1_is_sentinel_u_set(self, epsilon_corpus):
        """ε=1: is_sentinel_u is a bool array marking position of IX_IDX=1."""
        csr = self._csr(epsilon_corpus, 1)
        assert csr.is_sentinel_u is not None
        assert csr.is_sentinel_u.any()
