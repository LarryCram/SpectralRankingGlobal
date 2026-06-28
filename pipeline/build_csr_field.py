"""
build_csr_field.py — Stage 4a: build CSR block matrices from a field edge list.

Reads  : WORKING/el_{field_idx}_{window}_tauS{s}_tauU{u}.parquet
         WORKING/field_source_cands_{window}.parquet
         WORKING/field_inst_cands_{window}.parquet
Returns: CSRData (from spectral_ranking/build_csr.py schema, reused here)

Block weights:
  ew = edge_field_weight * rho_w
    rho=0: rho_w = R̄/R_i  (each citer work contributes equally regardless of ref count)
    rho=1: rho_w = 1.0

  C_SS: SUM(ew)                            de-duped on (citer_work, cited_work)
  C_SI: SUM(ew * cited_inst_weight)        de-duped on (citer_work, cited_work, cited_inst)
  C_IS: SUM(ew * inst_weight)              de-duped on (citer_work, citer_inst, cited_work)
  C_II: SUM(ew * inst_weight * cited_inst_weight)   no de-dup

Units restricted to those active in the edge list (implicit SCC pre-filter).
a_s / a_u drawn from candidacy tables (weighted works over window).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from util import Run

SX_IDX = 1   # sentinel source_idx for ε=1 runs
IX_IDX = 1   # sentinel institution_idx for ε=1 runs

# Re-use the CSRData dataclass from ZARCHIVE — import directly rather than
# duplicating; if it moves, update this import.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from build_csr import CSRData          # if old file still present
except ImportError:
    from dataclasses import dataclass
    from typing import Optional

    @dataclass
    class CSRData:
        C_SS: Optional[sp.csr_matrix]
        C_SI: Optional[sp.csr_matrix]
        C_IS: Optional[sp.csr_matrix]
        C_II: Optional[sp.csr_matrix]
        source_ids: np.ndarray
        inst_ids:   np.ndarray
        a_s: np.ndarray
        a_u: np.ndarray
        n_s: int
        n_u: int
        is_sentinel_s: Optional[np.ndarray] = None
        is_sentinel_u: Optional[np.ndarray] = None


def build_csr_field(db: duckdb.DuckDBPyConnection,
                    el_path: str,
                    sc_path: str,
                    ic_path: str,
                    run: Run) -> CSRData:
    """
    Build CSR block matrices for one field ranking run.

    Units are restricted to those present in the edge list (both as citer and
    cited), providing a natural citation-connectivity filter.
    """
    tau_s_abs = run.tau_s_abs()
    tau_u_abs = run.tau_u_abs()
    field_idx = run.field_idx

    # ── Unit sets: τ-retained AND active in edge list ─────────────────────────
    src_df = db.execute(f"""
        SELECT sc.source_idx, sc.weighted_works AS a_p
        FROM '{sc_path}' sc
        WHERE sc.field_idx      = {field_idx}
          AND sc.weighted_works >= {tau_s_abs}
          AND sc.source_idx IN (
              SELECT DISTINCT citer_source_idx FROM '{el_path}'
              UNION
              SELECT DISTINCT cited_source_idx  FROM '{el_path}'
          )
        ORDER BY sc.source_idx
    """).df()

    inst_df = db.execute(f"""
        SELECT ic.institution_idx, ic.weighted_works AS a_p
        FROM '{ic_path}' ic
        WHERE ic.field_idx      = {field_idx}
          AND ic.weighted_works >= {tau_u_abs}
          AND ic.institution_idx IN (
              SELECT DISTINCT citer_inst_idx FROM '{el_path}'
              UNION
              SELECT DISTINCT cited_inst_idx  FROM '{el_path}'
          )
        ORDER BY ic.institution_idx
    """).df()

    # ── ε: inject sentinel units (not in candidacy tables) ───────────────────────
    if run.epsilon:
        if SX_IDX not in src_df['source_idx'].values:
            src_df = pd.concat([
                pd.DataFrame({'source_idx': [SX_IDX], 'a_p': [1.0]}),
                src_df,
            ], ignore_index=True).sort_values('source_idx').reset_index(drop=True)
        if IX_IDX not in inst_df['institution_idx'].values:
            inst_df = pd.concat([
                pd.DataFrame({'institution_idx': [IX_IDX], 'a_p': [1.0]}),
                inst_df,
            ], ignore_index=True).sort_values('institution_idx').reset_index(drop=True)

    source_ids = src_df['source_idx'].to_numpy(dtype=np.int64)
    inst_ids   = inst_df['institution_idx'].to_numpy(dtype=np.int64)
    a_s = src_df['a_p'].to_numpy(dtype=np.float64)
    a_u = inst_df['a_p'].to_numpy(dtype=np.float64)
    n_s = len(source_ids)
    n_u = len(inst_ids)

    # ── ρ weighting ───────────────────────────────────────────────────────────
    if run.rho == 0:
        r_bar = db.execute(f"""
            SELECT AVG(R_i)
            FROM (SELECT DISTINCT citer_work_idx, R_i FROM '{el_path}')
        """).fetchone()[0]
        rho_expr = f"({r_bar} / R_i)" if r_bar is not None else "1.0"
    else:
        rho_expr = "1.0"

    # ── ω: select author-fractional (ω=0) or direct 1/N_inst (ω=1) weights ──
    iw_col  = "direct_inst_weight"       if run.omega else "inst_weight"
    ciw_col = "direct_cited_inst_weight" if run.omega else "cited_inst_weight"

    # ── β: exclude (source, inst) unit self-references ────────────────────────
    beta_clause = ("WHERE NOT (citer_source_idx = cited_source_idx "
                   "AND citer_inst_idx = cited_inst_idx)") if run.beta else ""

    # ── Slim working table with ew pre-computed ───────────────────────────────
    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _el AS
        SELECT citer_work_idx, citer_source_idx, citer_inst_idx,
               cited_work_idx,  cited_source_idx, cited_inst_idx,
               {iw_col}  AS inst_weight,
               {ciw_col} AS cited_inst_weight,
               edge_field_weight * {rho_expr} AS ew
        FROM '{el_path}'
        {beta_clause}
    """)

    src_idx  = pd.Index(source_ids)
    inst_idx = pd.Index(inst_ids)

    def _block(sql: str, row_idx: pd.Index, col_idx: pd.Index,
               shape: tuple) -> sp.csr_matrix:
        df = db.execute(sql).df()
        if df.empty:
            return sp.csr_matrix(shape)
        rows = row_idx.get_indexer(df.iloc[:, 0].to_numpy())
        cols = col_idx.get_indexer(df.iloc[:, 1].to_numpy())
        mask = (rows >= 0) & (cols >= 0)
        return sp.coo_matrix(
            (df.iloc[:, 2].to_numpy(dtype=np.float64)[mask],
             (rows[mask], cols[mask])),
            shape=shape,
        ).tocsr()

    m_SS, m_SI, m_IS, m_II = run.m

    C_SS = _block("""
        SELECT citer_source_idx, cited_source_idx, SUM(ew) AS w
        FROM (SELECT DISTINCT citer_work_idx, citer_source_idx,
                              cited_work_idx,  cited_source_idx, ew FROM _el)
        GROUP BY citer_source_idx, cited_source_idx
    """, src_idx, src_idx, (n_s, n_s)) if m_SS else None

    C_SI = _block("""
        SELECT citer_source_idx, cited_inst_idx,
               SUM(ew * cited_inst_weight) AS w
        FROM (SELECT DISTINCT citer_work_idx, citer_source_idx,
                              cited_work_idx,  cited_inst_idx,
                              ew, cited_inst_weight FROM _el)
        GROUP BY citer_source_idx, cited_inst_idx
    """, src_idx, inst_idx, (n_s, n_u)) if m_SI else None

    C_IS = _block("""
        SELECT citer_inst_idx, cited_source_idx,
               SUM(ew * inst_weight) AS w
        FROM (SELECT DISTINCT citer_work_idx, citer_inst_idx,
                              cited_work_idx,  cited_source_idx,
                              ew, inst_weight FROM _el)
        GROUP BY citer_inst_idx, cited_source_idx
    """, inst_idx, src_idx, (n_u, n_s)) if m_IS else None

    C_II = _block("""
        SELECT citer_inst_idx, cited_inst_idx,
               SUM(ew * inst_weight * cited_inst_weight) AS w
        FROM _el
        GROUP BY citer_inst_idx, cited_inst_idx
    """, inst_idx, inst_idx, (n_u, n_u)) if m_II else None

    db.execute("DROP TABLE IF EXISTS _el")

    is_sentinel_s = (source_ids == SX_IDX) if run.epsilon else None
    is_sentinel_u = (inst_ids   == IX_IDX) if run.epsilon else None

    return CSRData(
        C_SS=C_SS, C_SI=C_SI, C_IS=C_IS, C_II=C_II,
        source_ids=source_ids, inst_ids=inst_ids,
        a_s=a_s, a_u=a_u,
        n_s=n_s, n_u=n_u,
        is_sentinel_s=is_sentinel_s,
        is_sentinel_u=is_sentinel_u,
    )
