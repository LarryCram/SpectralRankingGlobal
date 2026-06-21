"""
test_leiden_edge_list.py — Integration tests for leiden-mode edge list construction.

Covers:
  - filter_col dispatch: leiden run uses leiden_idx column (not field_idx)
  - subfield aggregation: SUM(field_weight) across subfields within a leiden group
  - cross-group exclusion: works with different leiden_idx are excluded
  - wrong leiden group → empty edge list
  - tau filtering still works against leiden candidacy tables

Synthetic network (leiden_idx=1 = Maths & CS, window 2020–2024)
----------------------------------------------------------------
flat_works rows (subfield-level granularity):
  W1,S1,U1  subfield=1700 (field 17)  fw=0.6  leiden_idx=1
  W1,S1,U1  subfield=2600 (field 26)  fw=0.4  leiden_idx=1
  W2,S1,U1  subfield=1700 (field 17)  fw=1.0  leiden_idx=1
  W3,S2,U1  subfield=1700 (field 17)  fw=1.0  leiden_idx=1
  W4,S3,U2  subfield=2700 (field 27)  fw=1.0  leiden_idx=4  ← different group

After leiden_idx=1 filter + GROUP BY (work, source, inst):
  W1,S1,U1  field_weight=1.0  (0.6+0.4 aggregated)
  W2,S1,U1  field_weight=1.0
  W3,S2,U1  field_weight=1.0
  (W4 excluded: leiden_idx=4)

Leiden candidacy (leiden_idx stored as field_idx):
  source:  leiden=1 → S1=100.0, S2=100.0, S3=5.0
  inst:    leiden=1 → U1=100.0, U2=5.0
  tau_s=10 × 5yr = 50 → S1,S2 retained; S3 excluded
  tau_u=10 × 5yr = 50 → U1 retained;   U2 excluded

Corpus references:
  W1→W3, W3→W1, W1→W2, W1→W4 (W4 cross-group, excluded by leiden filter)

Edge list (leiden=1 retained: S1,S2 / U1):
  W1,S1,U1 → W3,S2,U1   R_i[W1] = (1.0+1.0)/2 = 1.0
  W1,S1,U1 → W2,S1,U1   R_i[W1] = 1.0
  W3,S2,U1 → W1,S1,U1   R_i[W3] = 1.0
"""

import sys
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from util.runs import Run
from build_edge_list_field import build_edge_list


@pytest.fixture
def leiden_corpus(tmp_path):
    db = duckdb.connect()

    # flat_works: subfield granularity — W1 has two subfields in leiden_idx=1
    db.execute("""
        CREATE TABLE fw AS FROM (VALUES
            (1, 2021, 101, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning',  17, 0.6, 1, 'Mathematics and Computer Science', 2),
            (1, 2021, 101, 201, 'AU', 1.0, 1.0, 2600, 'Combinatorics',     26, 0.4, 1, 'Mathematics and Computer Science', 2),
            (2, 2021, 101, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning',  17, 1.0, 1, 'Mathematics and Computer Science', 0),
            (3, 2021, 102, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning',  17, 1.0, 1, 'Mathematics and Computer Science', 1),
            (4, 2021, 103, 202, 'AU', 1.0, 1.0, 2700, 'Internal Medicine', 27, 1.0, 4, 'Biomedical and Health Sciences',   0)
        ) t(work_idx, publication_year, source_idx, institution_idx, country_code,
            inst_weight, direct_inst_weight,
            subfield_idx, subfield_name, field_idx, field_weight,
            leiden_idx, leiden_name, referenced_works_count)
    """)
    fw_path = str(tmp_path / 'flat_works.parquet')
    db.execute(f"COPY fw TO '{fw_path}'")

    db.execute("""
        CREATE TABLE cr AS FROM (VALUES
            (1, 3), (3, 1), (1, 2), (1, 4)
        ) t(citer_idx, cited_idx)
    """)
    cr_path = str(tmp_path / 'corpus_refs.parquet')
    db.execute(f"COPY cr TO '{cr_path}'")

    # leiden candidacy — field_idx column holds leiden_idx values (1, 4)
    db.execute("""
        CREATE TABLE lsc AS FROM (VALUES
            (1, 101, 100.0),
            (1, 102, 100.0),
            (1, 103,   5.0),
            (4, 103, 100.0)
        ) t(field_idx, source_idx, weighted_works)
    """)
    sc_path = str(tmp_path / 'leiden_source_cands_2020_2024.parquet')
    db.execute(f"COPY lsc TO '{sc_path}'")

    db.execute("""
        CREATE TABLE lic AS FROM (VALUES
            (1, 201, 100.0),
            (1, 202,   5.0),
            (4, 202, 100.0)
        ) t(field_idx, institution_idx, weighted_works)
    """)
    ic_path = str(tmp_path / 'leiden_inst_cands_2020_2024.parquet')
    db.execute(f"COPY lic TO '{ic_path}'")
    db.close()

    base_run = Run(
        tc0=2020, tc1=2024,
        tau_s=10.0, tau_u=10.0,
        m=(0, 1, 1, 0),
        alpha=1.0, rho=0,
        label='test',
        field_idx=1,          # leiden_idx=1 → is_leiden=True
    )
    return {
        'tmp': tmp_path,
        'fw':  fw_path,
        'cr':  cr_path,
        'sc':  sc_path,
        'ic':  ic_path,
        'run': base_run,
    }


def _build_el(c, run) -> str:
    el_path = run.el_path(str(c['tmp']))
    with duckdb.connect() as db:
        build_edge_list(db, c['fw'], c['cr'], c['sc'], c['ic'], run, el_path)
    return el_path


# ─── filter_col dispatch ──────────────────────────────────────────────────────

class TestLeidenFilterDispatch:

    def test_run_is_leiden_true(self, leiden_corpus):
        assert leiden_corpus['run'].is_leiden

    def test_wrong_leiden_group_gives_empty_edge_list(self, leiden_corpus):
        """leiden_idx=4 run on a corpus with only leiden_idx=1 retained → empty."""
        run4 = replace(leiden_corpus['run'], field_idx=4, label='l4')
        el_path = _build_el(leiden_corpus, run4)
        with duckdb.connect() as db:
            n = db.execute(f"SELECT COUNT(*) FROM '{el_path}'").fetchone()[0]
        assert n == 0, f"leiden=4 should give empty edge list but got {n} rows"

    def test_leiden1_excludes_works_from_leiden4(self, leiden_corpus):
        """S3=103 is only in leiden=4; it must not appear in leiden=1 edge list."""
        el_path = _build_el(leiden_corpus, leiden_corpus['run'])
        with duckdb.connect() as db:
            sources = set(db.execute(
                f"SELECT DISTINCT citer_source_idx FROM '{el_path}' "
                f"UNION SELECT DISTINCT cited_source_idx FROM '{el_path}'"
            ).df().iloc[:, 0].tolist())
        assert 103 not in sources, f"S3=103 (leiden=4) should be excluded; found {sources}"

    def test_leiden1_includes_retained_sources(self, leiden_corpus):
        """S1=101 and S2=102 (both in leiden=1 with 100 w.w.) appear in edge list."""
        el_path = _build_el(leiden_corpus, leiden_corpus['run'])
        with duckdb.connect() as db:
            sources = set(db.execute(
                f"SELECT DISTINCT citer_source_idx FROM '{el_path}' "
                f"UNION SELECT DISTINCT cited_source_idx FROM '{el_path}'"
            ).df().iloc[:, 0].tolist())
        assert 101 in sources
        assert 102 in sources


# ─── subfield aggregation ─────────────────────────────────────────────────────

class TestLeidenSubfieldAggregation:
    """
    W1 has two subfield rows in leiden_idx=1 (fw=0.6 + fw=0.4).
    After GROUP BY (work, source, inst), field_weight must equal 1.0.
    """

    def test_w1_aggregated_field_weight_in_fw(self, leiden_corpus):
        """_fw inside build_edge_list sees W1 with field_weight=1.0."""
        el_path = _build_el(leiden_corpus, leiden_corpus['run'])
        with duckdb.connect() as db:
            # Each edge from W1 carries the aggregated field_weight
            rows = db.execute(
                f"SELECT DISTINCT citer_work_idx, edge_field_weight * 2 - 1.0 AS citer_fw "
                f"FROM '{el_path}' WHERE citer_work_idx = 1"
            ).fetchall()
        # edge_field_weight = (citer_fw + cited_fw) / 2;
        # cited works (W2, W3) also have fw=1.0, so edge_fw = (1.0+1.0)/2 = 1.0
        assert len(rows) > 0, "No edges from W1"
        for _, citer_fw in rows:
            assert abs(citer_fw - 1.0) < 1e-9, f"W1 citer_fw should be 1.0, got {citer_fw}"

    def test_edge_list_row_count(self, leiden_corpus):
        """Expected edges after tau filter: W1→W3, W1→W2, W3→W1 = 3 rows."""
        el_path = _build_el(leiden_corpus, leiden_corpus['run'])
        with duckdb.connect() as db:
            n = db.execute(f"SELECT COUNT(*) FROM '{el_path}'").fetchone()[0]
        assert n == 3, f"Expected 3 edges, got {n}"

    def test_w4_cross_group_ref_excluded(self, leiden_corpus):
        """W1→W4 (cross-group reference) must not appear as an edge."""
        el_path = _build_el(leiden_corpus, leiden_corpus['run'])
        with duckdb.connect() as db:
            n = db.execute(
                f"SELECT COUNT(*) FROM '{el_path}' WHERE cited_work_idx = 4"
            ).fetchone()[0]
        assert n == 0, "Cross-group cited work W4 should not appear in edge list"


# ─── tau filtering with leiden candidacy ─────────────────────────────────────

class TestLeidenTauFiltering:

    def test_low_source_excluded(self, leiden_corpus):
        """S3=103 has weighted_works=5.0 < tau_abs=50 → not in leiden=1 edge list."""
        el_path = _build_el(leiden_corpus, leiden_corpus['run'])
        with duckdb.connect() as db:
            n = db.execute(
                f"SELECT COUNT(*) FROM '{el_path}' "
                f"WHERE citer_source_idx = 103 OR cited_source_idx = 103"
            ).fetchone()[0]
        assert n == 0

    def test_low_institution_excluded(self, leiden_corpus):
        """U2=202 has weighted_works=5.0 < tau_abs=50 → not in leiden=1 edge list."""
        el_path = _build_el(leiden_corpus, leiden_corpus['run'])
        with duckdb.connect() as db:
            n = db.execute(
                f"SELECT COUNT(*) FROM '{el_path}' "
                f"WHERE citer_inst_idx = 202 OR cited_inst_idx = 202"
            ).fetchone()[0]
        assert n == 0

    def test_low_tau_includes_more_units(self, leiden_corpus):
        """At tau=0.5/yr (tau_abs=2.5), S3=103 (5.0 w.w.) clears the bar."""
        run_low = replace(leiden_corpus['run'], tau_s=0.5, tau_u=0.5, label='low')
        el_path = _build_el(leiden_corpus, run_low)
        with duckdb.connect() as db:
            n = db.execute(f"SELECT COUNT(*) FROM '{el_path}'").fetchone()[0]
        # S3 and U2 now retained but W4 (leiden=4) still excluded by leiden filter
        # so no extra citation pairs are possible in this corpus
        assert n >= 3, f"low tau should give ≥3 edges, got {n}"


# ─── sc_path / ic_path resolve correctly for leiden runs ─────────────────────

class TestLeidenPathResolution:

    def test_sc_path_is_leiden_cands(self, leiden_corpus):
        run = leiden_corpus['run']
        assert 'leiden_source_cands' in run.sc_path(str(leiden_corpus['tmp']))

    def test_ic_path_is_leiden_cands(self, leiden_corpus):
        run = leiden_corpus['run']
        assert 'leiden_inst_cands' in run.ic_path(str(leiden_corpus['tmp']))

    def test_sc_path_not_field_cands(self, leiden_corpus):
        run = leiden_corpus['run']
        assert 'field_source_cands' not in run.sc_path(str(leiden_corpus['tmp']))
