"""
Tests for enclaves/build_enclave_hcw.py

All tests use synthetic DataFrames — no file I/O.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from enclaves.build_enclave_hcw import (
    compute_hcw_flags,
    count_intra_field_citations,
    compute_mean_inst_v,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _rw(records: list[dict]) -> pd.DataFrame:
    """Build a retained-works DataFrame from a list of dicts."""
    return pd.DataFrame(records, columns=['field_idx', 'work_idx', 'publication_year', 'n_intra'])


def _cr(pairs: list[tuple]) -> pd.DataFrame:
    """Build a citations DataFrame from (citer, cited) tuples."""
    return pd.DataFrame(pairs, columns=['citer_idx', 'cited_idx'])


# ── compute_hcw_flags ─────────────────────────────────────────────────────────

class TestComputeHcwFlags:

    def test_threshold_is_per_year_per_field(self):
        """Each (field, year) gets its own threshold; cross-group isolation."""
        # Field 10, year 2020: 10 works with n_intra 0..9 → 99th pctile = 8.91
        # Field 20, year 2020: 10 works with n_intra 100..109 → 99th pctile ~108.91
        rows = (
            [{'field_idx': 10, 'work_idx': i, 'publication_year': 2020, 'n_intra': i}
             for i in range(10)]
            + [{'field_idx': 20, 'work_idx': 100 + i, 'publication_year': 2020, 'n_intra': 100 + i}
               for i in range(10)]
        )
        df = _rw(rows)
        out = compute_hcw_flags(df, hcw_pct=90)  # 90th pctile for easier math

        # Field 10: threshold = 8.1  → works 9 is HCW (n_intra=9 >= 8.1)
        f10 = out[out['field_idx'] == 10]
        f20 = out[out['field_idx'] == 20]

        # Thresholds must differ between fields
        assert f10['year_threshold'].iloc[0] != f20['year_threshold'].iloc[0]

        # No field-10 work has n_intra in the field-20 range
        hcw10 = f10[f10['is_hcw']]
        assert (hcw10['n_intra'] < 100).all(), 'Field-10 HCW should have small n_intra'

    def test_threshold_per_year(self):
        """Year 2021 has higher baseline; its threshold is computed separately."""
        rows = (
            # year 2020: n_intra 0..9
            [{'field_idx': 1, 'work_idx': i, 'publication_year': 2020, 'n_intra': i}
             for i in range(10)]
            # year 2021: n_intra 50..59
            + [{'field_idx': 1, 'work_idx': 100 + i, 'publication_year': 2021, 'n_intra': 50 + i}
               for i in range(10)]
        )
        df = _rw(rows)
        out = compute_hcw_flags(df, hcw_pct=90)

        t2020 = out.loc[out['publication_year'] == 2020, 'year_threshold'].iloc[0]
        t2021 = out.loc[out['publication_year'] == 2021, 'year_threshold'].iloc[0]
        assert t2021 > t2020, 'Year with higher citation counts should have higher threshold'

    def test_approximately_one_percent_flagged(self):
        """With 99th percentile, roughly 1% of works per (field, year) become HCW."""
        n = 1000
        rows = [{'field_idx': 1, 'work_idx': i, 'publication_year': 2020, 'n_intra': i}
                for i in range(n)]
        df = _rw(rows)
        out = compute_hcw_flags(df, hcw_pct=99)
        frac = out['is_hcw'].mean()
        assert 0.005 <= frac <= 0.02, f'Expected ~1% HCW, got {frac:.2%}'

    def test_output_has_required_columns(self):
        rows = [{'field_idx': 1, 'work_idx': i, 'publication_year': 2020, 'n_intra': i}
                for i in range(20)]
        out = compute_hcw_flags(_rw(rows))
        assert 'year_threshold' in out.columns
        assert 'is_hcw' in out.columns
        assert out['is_hcw'].dtype == bool

    def test_all_zero_citations(self):
        """When all works have 0 citations, the 99th percentile is 0; all works are HCW."""
        rows = [{'field_idx': 1, 'work_idx': i, 'publication_year': 2020, 'n_intra': 0}
                for i in range(100)]
        out = compute_hcw_flags(_rw(rows), hcw_pct=99)
        # threshold = 0.0; 0 >= 0 is True → all are HCW
        assert out['is_hcw'].all()


# ── count_intra_field_citations ───────────────────────────────────────────────

class TestCountIntraFieldCitations:

    def test_basic_intra_count(self):
        """A citation between two works in the same field is counted once."""
        rw = pd.DataFrame({
            'field_idx': [10, 10],
            'work_idx':  [1,  2],
        })
        cr = _cr([(2, 1)])  # work 2 cites work 1
        result = count_intra_field_citations(rw, cr)
        assert len(result) == 1
        row = result[result['cited_idx'] == 1].iloc[0]
        assert row['n_intra'] == 1
        assert row['field_idx'] == 10

    def test_cross_field_excluded(self):
        """A citation where citer is in field 10 but cited is in field 20 is not counted."""
        rw = pd.DataFrame({
            'field_idx': [10, 20],
            'work_idx':  [1,  2],
        })
        cr = _cr([(1, 2)])  # work 1 (field 10) cites work 2 (field 20)
        result = count_intra_field_citations(rw, cr)
        assert len(result) == 0, 'Cross-field citation should produce no rows'

    def test_same_works_in_multiple_fields(self):
        """A work retained in two fields receives independent intra-field counts."""
        # Work 1 and 2 are both retained in field 10 AND field 20.
        # Work 2 cites work 1 → should appear in BOTH fields.
        rw = pd.DataFrame({
            'field_idx': [10, 10, 20, 20],
            'work_idx':  [1,   2,  1,  2],
        })
        cr = _cr([(2, 1)])
        result = count_intra_field_citations(rw, cr)
        assert len(result) == 2, 'Should count once per field'
        for fid in [10, 20]:
            row = result[(result['field_idx'] == fid) & (result['cited_idx'] == 1)]
            assert len(row) == 1 and row.iloc[0]['n_intra'] == 1

    def test_multiple_citers(self):
        """Three works in field 10 each cite work 1 → n_intra = 3."""
        rw = pd.DataFrame({
            'field_idx': [10, 10, 10, 10],
            'work_idx':  [1,   2,  3,  4],
        })
        cr = _cr([(2, 1), (3, 1), (4, 1)])
        result = count_intra_field_citations(rw, cr)
        row = result[result['cited_idx'] == 1].iloc[0]
        assert row['n_intra'] == 3

    def test_self_citation_counted(self):
        """Self-citations (citer == cited) are counted; filtering is done elsewhere."""
        rw = pd.DataFrame({'field_idx': [10], 'work_idx': [1]})
        cr = _cr([(1, 1)])
        result = count_intra_field_citations(rw, cr)
        assert len(result) == 1
        assert result.iloc[0]['n_intra'] == 1

    def test_empty_citations(self):
        """No citation pairs → empty result."""
        rw = pd.DataFrame({'field_idx': [10, 10], 'work_idx': [1, 2]})
        cr = _cr([])
        result = count_intra_field_citations(rw, cr)
        assert len(result) == 0

    def test_uncited_works_not_returned(self):
        """Works with no intra-field citations are absent from the result (caller merges as 0)."""
        rw = pd.DataFrame({
            'field_idx': [10, 10, 10],
            'work_idx':  [1,   2,  3],
        })
        cr = _cr([(2, 1)])  # only work 1 is cited
        result = count_intra_field_citations(rw, cr)
        assert 2 not in result['cited_idx'].values
        assert 3 not in result['cited_idx'].values


# ── compute_mean_inst_v ───────────────────────────────────────────────────────

class TestComputeMeanInstV:

    def _base_fixtures(self):
        hcw = pd.DataFrame({'field_idx': [10], 'work_idx': [1]})
        # Institution 100 and 200 both affiliated with work 1
        fw_inst = pd.DataFrame({'work_idx': [1, 1], 'institution_idx': [100, 200]})
        inst_v  = pd.DataFrame({
            'field_idx':       [10,  10],
            'institution_idx': [100, 200],
            'v':               [2.0, 4.0],
        })
        return hcw, fw_inst, inst_v

    def test_basic_mean(self):
        """Mean of (2.0 + 4.0) / 2 = 3.0."""
        hcw, fw_inst, inst_v = self._base_fixtures()
        result = compute_mean_inst_v(hcw, fw_inst, inst_v)
        assert len(result) == 1
        assert abs(result.iloc[0]['mean_inst_v'] - 3.0) < 1e-9

    def test_distinct_subfield_rows_not_double_counted(self):
        """Institution 100 appears in 3 subfield rows but is counted once."""
        hcw = pd.DataFrame({'field_idx': [10], 'work_idx': [1]})
        fw_inst = pd.DataFrame({
            'work_idx':       [1, 1, 1, 1],
            'institution_idx': [100, 100, 100, 200],  # 100 repeated 3 times
        })
        inst_v = pd.DataFrame({
            'field_idx':       [10,  10],
            'institution_idx': [100, 200],
            'v':               [2.0, 4.0],
        })
        # fw_inst passed to compute_mean_inst_v should already be DISTINCT;
        # but the SQL uses a simple join — caller must deduplicate fw_inst.
        fw_inst_dedup = fw_inst.drop_duplicates()
        result = compute_mean_inst_v(hcw, fw_inst_dedup, inst_v)
        assert abs(result.iloc[0]['mean_inst_v'] - 3.0) < 1e-9

    def test_field_specific_v(self):
        """Institution v is taken from the HCW's field, not from another field."""
        hcw = pd.DataFrame({'field_idx': [10], 'work_idx': [1]})
        fw_inst = pd.DataFrame({'work_idx': [1], 'institution_idx': [100]})
        inst_v = pd.DataFrame({
            'field_idx':       [10,  20],   # same institution, two fields
            'institution_idx': [100, 100],
            'v':               [2.0, 99.0], # field 20 has very different v
        })
        result = compute_mean_inst_v(hcw, fw_inst, inst_v)
        assert abs(result.iloc[0]['mean_inst_v'] - 2.0) < 1e-9

    def test_no_ranked_institution(self):
        """HCW with no ranked institution in its field → absent from result."""
        hcw = pd.DataFrame({'field_idx': [10], 'work_idx': [1]})
        fw_inst = pd.DataFrame({'work_idx': [1], 'institution_idx': [999]})
        inst_v = pd.DataFrame({  # institution 999 not in field 10
            'field_idx':       [20],
            'institution_idx': [999],
            'v':               [1.5],
        })
        result = compute_mean_inst_v(hcw, fw_inst, inst_v)
        assert len(result) == 0

    def test_multiple_hcw_independent(self):
        """Each HCW (field, work) gets its own mean_inst_v independently."""
        hcw = pd.DataFrame({
            'field_idx': [10, 10],
            'work_idx':  [1,   2],
        })
        fw_inst = pd.DataFrame({
            'work_idx':       [1,  2],
            'institution_idx': [100, 200],
        })
        inst_v = pd.DataFrame({
            'field_idx':       [10,  10],
            'institution_idx': [100, 200],
            'v':               [2.0, 6.0],
        })
        result = compute_mean_inst_v(hcw, fw_inst, inst_v).set_index('work_idx')
        assert abs(result.loc[1, 'mean_inst_v'] - 2.0) < 1e-9
        assert abs(result.loc[2, 'mean_inst_v'] - 6.0) < 1e-9

    def test_gap_derived_correctly(self):
        """Validate gap = source_v - mean_inst_v on a small assembled example."""
        hcw = pd.DataFrame({'field_idx': [10], 'work_idx': [1], 'source_v': [1.5]})
        fw_inst = pd.DataFrame({'work_idx': [1], 'institution_idx': [100]})
        inst_v  = pd.DataFrame({'field_idx': [10], 'institution_idx': [100], 'v': [2.0]})
        miv = compute_mean_inst_v(hcw[['field_idx', 'work_idx']], fw_inst, inst_v)
        merged = hcw.merge(miv, on=['field_idx', 'work_idx'], how='left')
        merged['gap'] = merged['source_v'] - merged['mean_inst_v']
        assert abs(merged.iloc[0]['gap'] - (1.5 - 2.0)) < 1e-9
