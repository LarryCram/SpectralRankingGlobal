"""
test_area_edge_list.py — Integration tests for AREA5-mode edge list construction.

Covers:
  - dispatch: an area run (field_idx 1–5) filters flat_works by field_idx IN the
    area's member OA fields (util.areas.AREA_GROUPS), not by the legacy leiden_idx column
  - subfield aggregation: SUM(field_weight) across subfields within an area
  - cross-area exclusion, wrong area → empty edge list, tau filtering

Synthetic network (area 1 = MCS = OA fields 17, 26; window 2020–2024)
---------------------------------------------------------------------
flat_works rows (subfield-level granularity):
  W1,S1,U1  subfield=1700 (field 17)  fw=0.6
  W1,S1,U1  subfield=2600 (field 26)  fw=0.4
  W2,S1,U1  subfield=1700 (field 17)  fw=1.0
  W3,S2,U1  subfield=1700 (field 17)  fw=1.0
  W4,S3,U2  subfield=2700 (field 27)  fw=1.0   ← area 4 (BHS)
The legacy leiden_idx column is deliberately wrong (9) on W1–W3 to prove it is unused.

Area candidacy (area id stored as field_idx):
  source:  area 1 → S1=100, S2=100, S3=5;  inst: area 1 → U1=100, U2=5
  tau = 10/yr × 5 yr = 50 → S1, S2, U1 retained

Corpus references: W1→W3, W3→W1, W1→W2, W1→W4 (W4 cross-area, excluded)
Edge list: W1→W3, W1→W2, W3→W1
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
def area_corpus(tmp_path):
    db = duckdb.connect()

    # flat_works: subfield granularity — W1 has two subfields in area 1
    db.execute("""
        CREATE TABLE fw AS FROM (VALUES
            (1, 2021, 101, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning',  17, 0.6, 9, 'legacy', 2),
            (1, 2021, 101, 201, 'AU', 1.0, 1.0, 2600, 'Combinatorics',     26, 0.4, 9, 'legacy', 2),
            (2, 2021, 101, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning',  17, 1.0, 9, 'legacy', 0),
            (3, 2021, 102, 201, 'AU', 1.0, 1.0, 1700, 'Machine Learning',  17, 1.0, 9, 'legacy', 1),
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

    # area candidacy — field_idx column holds area ids (1, 4)
    db.execute("""
        CREATE TABLE lsc AS FROM (VALUES
            (1, 101, 100.0),
            (1, 102, 100.0),
            (1, 103,   5.0),
            (4, 103, 100.0)
        ) t(field_idx, source_idx, weighted_works)
    """)
    sc_path = str(tmp_path / 'area_source_cands_2020_2024.parquet')
    db.execute(f"COPY lsc TO '{sc_path}'")

    db.execute("""
        CREATE TABLE lic AS FROM (VALUES
            (1, 201, 100.0),
            (1, 202,   5.0),
            (4, 202, 100.0)
        ) t(field_idx, institution_idx, weighted_works)
    """)
    ic_path = str(tmp_path / 'area_inst_cands_2020_2024.parquet')
    db.execute(f"COPY lic TO '{ic_path}'")
    db.close()

    base_run = Run(
        tc0=2020, tc1=2024,
        tau_s=10.0, tau_u=10.0,
        m=(0, 1, 1, 0),
        alpha=1.0, rho=0,
        label='test',
        field_idx=1,          # area 1 (MCS) → is_area=True
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

class TestAreaFilterDispatch:

    def test_run_is_area_true(self, area_corpus):
        assert area_corpus['run'].is_area

    def test_legacy_group_column_ignored(self, area_corpus):
        """W1–W3 carry a wrong legacy group id (9); the area-1 edge list still contains them."""
        el_path = _build_el(area_corpus, area_corpus['run'])
        with duckdb.connect() as db:
            works = set(db.execute(f"SELECT DISTINCT citer_work_idx FROM '{el_path}'").df().iloc[:, 0])
        assert works == {1, 3}

    def test_wrong_area_gives_empty_edge_list(self, area_corpus):
        """area-4 run on a corpus with only area-1 units retained → empty."""
        run4 = replace(area_corpus['run'], field_idx=4, label='a4')
        el_path = _build_el(area_corpus, run4)
        with duckdb.connect() as db:
            n = db.execute(f"SELECT COUNT(*) FROM '{el_path}'").fetchone()[0]
        assert n == 0, f"area 4 should give empty edge list but got {n} rows"

    def test_area1_excludes_works_from_area4(self, area_corpus):
        """S3=103 is only in area 4; it must not appear in the area-1 edge list."""
        el_path = _build_el(area_corpus, area_corpus['run'])
        with duckdb.connect() as db:
            sources = set(db.execute(
                f"SELECT DISTINCT citer_source_idx FROM '{el_path}' "
                f"UNION SELECT DISTINCT cited_source_idx FROM '{el_path}'"
            ).df().iloc[:, 0].tolist())
        assert 103 not in sources, f"S3=103 (area 4) should be excluded; found {sources}"

    def test_area1_includes_retained_sources(self, area_corpus):
        """S1=101 and S2=102 (both in area 1 with 100 w.w.) appear in edge list."""
        el_path = _build_el(area_corpus, area_corpus['run'])
        with duckdb.connect() as db:
            sources = set(db.execute(
                f"SELECT DISTINCT citer_source_idx FROM '{el_path}' "
                f"UNION SELECT DISTINCT cited_source_idx FROM '{el_path}'"
            ).df().iloc[:, 0].tolist())
        assert 101 in sources
        assert 102 in sources


# ─── subfield aggregation ─────────────────────────────────────────────────────

class TestAreaSubfieldAggregation:
    """
    W1 has two subfield rows in area 1 (fw=0.6 + fw=0.4).
    After GROUP BY (work, source, inst), field_weight must equal 1.0.
    """

    def test_w1_aggregated_field_weight_in_fw(self, area_corpus):
        """_fw inside build_edge_list sees W1 with field_weight=1.0."""
        el_path = _build_el(area_corpus, area_corpus['run'])
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

    def test_edge_list_row_count(self, area_corpus):
        """Expected edges after tau filter: W1→W3, W1→W2, W3→W1 = 3 rows."""
        el_path = _build_el(area_corpus, area_corpus['run'])
        with duckdb.connect() as db:
            n = db.execute(f"SELECT COUNT(*) FROM '{el_path}'").fetchone()[0]
        assert n == 3, f"Expected 3 edges, got {n}"

    def test_w4_cross_group_ref_excluded(self, area_corpus):
        """W1→W4 (cross-group reference) must not appear as an edge."""
        el_path = _build_el(area_corpus, area_corpus['run'])
        with duckdb.connect() as db:
            n = db.execute(
                f"SELECT COUNT(*) FROM '{el_path}' WHERE cited_work_idx = 4"
            ).fetchone()[0]
        assert n == 0, "Cross-group cited work W4 should not appear in edge list"


# ─── tau filtering with area candidacy ─────────────────────────────────────

class TestAreaTauFiltering:

    def test_low_source_excluded(self, area_corpus):
        """S3=103 has weighted_works=5.0 < tau_abs=50 → not in the area-1 edge list."""
        el_path = _build_el(area_corpus, area_corpus['run'])
        with duckdb.connect() as db:
            n = db.execute(
                f"SELECT COUNT(*) FROM '{el_path}' "
                f"WHERE citer_source_idx = 103 OR cited_source_idx = 103"
            ).fetchone()[0]
        assert n == 0

    def test_low_institution_excluded(self, area_corpus):
        """U2=202 has weighted_works=5.0 < tau_abs=50 → not in the area-1 edge list."""
        el_path = _build_el(area_corpus, area_corpus['run'])
        with duckdb.connect() as db:
            n = db.execute(
                f"SELECT COUNT(*) FROM '{el_path}' "
                f"WHERE citer_inst_idx = 202 OR cited_inst_idx = 202"
            ).fetchone()[0]
        assert n == 0

    def test_low_tau_includes_more_units(self, area_corpus):
        """At tau=0.5/yr (tau_abs=2.5), S3=103 (5.0 w.w.) clears the bar."""
        run_low = replace(area_corpus['run'], tau_s=0.5, tau_u=0.5, label='low')
        el_path = _build_el(area_corpus, run_low)
        with duckdb.connect() as db:
            n = db.execute(f"SELECT COUNT(*) FROM '{el_path}'").fetchone()[0]
        # S3 and U2 now retained but W4 (area 4) is still excluded by the area filter
        # so no extra citation pairs are possible in this corpus
        assert n >= 3, f"low tau should give ≥3 edges, got {n}"


# ─── sc_path / ic_path resolve correctly for area runs ─────────────────────

class TestAreaPathResolution:

    def test_sc_path_is_area_cands(self, area_corpus):
        run = area_corpus['run']
        assert 'area_source_cands' in run.sc_path(str(area_corpus['tmp']))

    def test_ic_path_is_area_cands(self, area_corpus):
        run = area_corpus['run']
        assert 'area_inst_cands' in run.ic_path(str(area_corpus['tmp']))

    def test_sc_path_not_field_cands(self, area_corpus):
        run = area_corpus['run']
        assert 'field_source_cands' not in run.sc_path(str(area_corpus['tmp']))
