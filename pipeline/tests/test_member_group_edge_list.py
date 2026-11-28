"""
A grouping scheme with several member subfields (ERA FoR group) whose scratch code is
>= 1000 must aggregate a work's member-subfield rows, not duplicate the work.

W1 (S1, U1) sits in two member subfields 1308 (fw 0.4) and 2704 (fw 0.6); W2 (S2, U1) in
2704 only (fw 1.0). W1 cites W2, W2 cites W1. With aggregation each work appears once
with its summed weight, giving exactly 2 edges.
"""

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from util.runs import Run
from build_edge_list_field import build_edge_list


def test_multi_subfield_group_with_large_code_is_aggregated(tmp_path):
    db = duckdb.connect()
    db.execute("""
        CREATE TABLE fw AS FROM (VALUES
            (1, 2012, 101, 201, 'AU', 1.0, 1.0, 1308, 'Clinical Biochemistry', 13, 0.4, 3, 'x', 1),
            (1, 2012, 101, 201, 'AU', 1.0, 1.0, 2704, 'Biochemistry (med)',   27, 0.6, 4, 'x', 1),
            (2, 2012, 102, 201, 'AU', 1.0, 1.0, 2704, 'Biochemistry (med)',   27, 1.0, 4, 'x', 1)
        ) t(work_idx, publication_year, source_idx, institution_idx, country_code,
            inst_weight, direct_inst_weight, subfield_idx, subfield_name, field_idx,
            field_weight, leiden_idx, leiden_name, referenced_works_count)
    """)
    fw = str(tmp_path / 'fw.parquet'); db.execute(f"COPY fw TO '{fw}'")
    cr = str(tmp_path / 'cr.parquet')
    db.execute(f"COPY (FROM (VALUES (1, 2), (2, 1)) t(citer_idx, cited_idx)) TO '{cr}'")
    sc = str(tmp_path / 'sc.parquet')
    db.execute(f"COPY (FROM (VALUES (1101, 101, 99.0), (1101, 102, 99.0)) "
               f"t(field_idx, source_idx, weighted_works)) TO '{sc}'")
    ic = str(tmp_path / 'ic.parquet')
    db.execute(f"COPY (FROM (VALUES (1101, 201, 99.0)) t(field_idx, institution_idx, weighted_works)) TO '{ic}'")
    run = Run(tc0=2011, tc1=2016, tau_s=1.0, tau_u=1.0, m=(0, 1, 1, 0), alpha=1.0, rho=0,
              label='t', field_idx=1101)
    assert run.is_subfield                     # the scratch code looks like a subfield
    el = str(tmp_path / 'el.parquet')
    n = build_edge_list(db, fw, cr, sc, ic, run, el,
                        member_ids=(1308, 2704), filter_col_override='subfield_idx')
    assert n == 2
    w = db.execute(f"SELECT DISTINCT edge_field_weight FROM '{el}'").fetchall()
    assert w == [(1.0,)]                       # (1.0 + 1.0) / 2 after aggregation
