"""
build_csr.py — Build raw CSR blocks from a pre-built edge list.

Reads one edge list table (el_t{tx}_{fx}_tauU{tau_u}_tauS{tau_s}) and the
corresponding unit index table (_units_t{tx}_{fx}_tauU{tau_u}_tauS{tau_s})
from edge_lists.duckdb.
Applies ρ weighting, builds scipy CSR matrices for the requested blocks,
and returns a CSRData object.

No matrix assembly, no row-normalisation, no algorithm logic — all of that
lives in katz_ranker.py.

The edge list and _units tables must already exist (run
prepare_data/build_edge_lists.py first).

Public API
----------
build_csr(db, run_code, fx, tau_u, tau_s, rho, m) -> CSRData
"""

import numpy as np
import pandas as pd
import scipy.sparse as sp
from dataclasses import dataclass
from typing import Optional


SX_IDX = 1   # sentinel source_idx for ε=1 runs
IX_IDX = 1   # sentinel institution_idx for ε=1 runs


@dataclass
class CSRData:
    """
    Raw (un-normalised) CSR block matrices and unit metadata for one corpus.

    Blocks that were not requested (corresponding m bit = 0) are None.
    Matrices use dense indices 0…n_s-1 (sources) and 0…n_u-1 (institutions).

    For ε=1 runs, is_sentinel_s and is_sentinel_u flag the positions of
    SX_IDX/IX_IDX in the unit arrays.  Both are None for ε=0 runs.
    """
    C_SS: Optional[sp.csr_matrix]   # (n_s × n_s) source–source
    C_SI: Optional[sp.csr_matrix]   # (n_s × n_u) source–institution
    C_IS: Optional[sp.csr_matrix]   # (n_u × n_s) institution–source
    C_II: Optional[sp.csr_matrix]   # (n_u × n_u) institution–institution

    source_ids: np.ndarray   # original source_idx values, length n_s
    inst_ids:   np.ndarray   # original institution_idx values, length n_u
    a_s: np.ndarray          # integer work count per source,       length n_s
    a_u: np.ndarray          # fractional work count per institution, length n_u

    n_s: int
    n_u: int

    is_sentinel_s: Optional[np.ndarray] = None  # bool, True where source_ids == SX_IDX
    is_sentinel_u: Optional[np.ndarray] = None  # bool, True where inst_ids == IX_IDX


def build_csr(db, run_code: str, fx: str, tau_u: int, tau_s: int, rho: int, m: tuple,
              ref_units: str = '', direct_inst: bool = False,
              epsilon: int = 0, no_self_cite: bool = False) -> CSRData:
    """
    Build raw CSR blocks for corpus (run_code, fx, tau_u, tau_s) with ρ weighting.

    Parameters
    ----------
    db : duckdb connection (open, writeable not required).
    run_code : 8-char time window key, e.g. '20242024'.
    fx : field subset — letters from {'E','B','A','X'} e.g. 'EBAX', 'EBA', 'E'.
    tau_u : institution retention threshold (mean annual census works).
    tau_s : source retention threshold (mean annual census works).
    rho : 0 → fixed count (ρ_i = R̄/R_i); 1 → full count (ρ_i = 1).
    m : (m_SS, m_SI, m_IS, m_II) ∈ {0,1}^4 — which blocks to build.
    ref_units : non-empty string for fixtau corpora.
    direct_inst : False → author-fractional ω (inst_weight / cited_inst_weight);
                  True  → direct 1/N_inst ω (direct_inst_weight / direct_cited_inst_weight).
    no_self_cite : if True, exclude edges where citer_source_idx == cited_source_idx
                   AND citer_inst_idx == cited_inst_idx before building any block.
                   Parallels zeroing the diagonal of C_SS (sources) and C_II (institutions)
                   in the single-mode Eigenvalue method.

    Returns
    -------
    CSRData with requested blocks populated; others None.
    """
    tau_sfx = '_fixtau' if ref_units else '_vartau'
    eps_sfx = '_eps1' if epsilon else ''
    mstr   = ''.join(str(x) for x in m)
    tname  = f'el_{run_code}_{fx}_tauU{tau_u}_tauS{tau_s}{tau_sfx}{eps_sfx}'
    uname  = f'_units_{run_code}_{fx}_tauU{tau_u}_tauS{tau_s}{tau_sfx}_m{mstr}{eps_sfx}'

    # ── Load unit index ────────────────────────────────────────────────────
    try:
        units_df = db.execute(
            f"SELECT unit_idx, unit_type, a_p FROM {uname} ORDER BY unit_type, unit_idx"
        ).fetchdf()
    except Exception as e:
        raise RuntimeError(
            f"Mode-specific unit table '{uname}' not found in edge_lists.duckdb. "
            f"Run prepare_data/filter_mode_units.py first."
        ) from e

    src_df  = units_df[units_df['unit_type'] == 'S'].reset_index(drop=True)
    inst_df = units_df[units_df['unit_type'] == 'U'].reset_index(drop=True)

    source_ids = src_df['unit_idx'].to_numpy(dtype=np.int64)
    inst_ids   = inst_df['unit_idx'].to_numpy(dtype=np.int64)
    a_s = src_df['a_p'].to_numpy(dtype=np.float64)
    a_u = inst_df['a_p'].to_numpy(dtype=np.float64)
    n_s = len(source_ids)
    n_u = len(inst_ids)

    # pd.Index.get_indexer: single hash-table pass, avoids intermediate Series
    src_idx  = pd.Index(source_ids)
    inst_idx = pd.Index(inst_ids)

    # ── Materialise slim, ρ-weighted temp table (one scan of {tname}) ──────
    # All block queries below read _tmp_el from memory rather than
    # re-scanning the on-disk edge-list table each time.
    if rho == 0:
        r_bar = db.execute(
            f"SELECT AVG(rval) FROM "
            f"(SELECT DISTINCT citer_work_idx, CAST(R_i AS DOUBLE) AS rval FROM {tname})"
        ).fetchone()[0]
        if r_bar is None:
            raise RuntimeError(
                f"Edge list table '{tname}' is empty — cannot compute R̄ for ρ=0 weighting."
            )
        rho_col = f"{r_bar} / CAST(R_i AS DOUBLE)"
    else:
        rho_col = "1.0"

    iw_col  = 'direct_inst_weight'       if direct_inst else 'inst_weight'
    ciw_col = 'direct_cited_inst_weight' if direct_inst else 'cited_inst_weight'
    self_filter = (
        "WHERE citer_source_idx != cited_source_idx"
        "  AND citer_inst_idx   != cited_inst_idx"
    ) if no_self_cite else ""
    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE _tmp_el AS
        SELECT citer_work_idx, citer_source_idx, citer_inst_idx,
               cited_work_idx,  cited_source_idx, cited_inst_idx,
               {iw_col}  AS inst_weight,
               {ciw_col} AS cited_inst_weight,
               {rho_col} AS rho_w
        FROM {tname}
        {self_filter}
    """)

    # ── Block builders ─────────────────────────────────────────────────────

    def _block(sql: str, row_idx: pd.Index, col_idx: pd.Index,
               shape: tuple) -> sp.csr_matrix:
        df = db.execute(sql).fetchdf()
        if df.empty:
            return sp.csr_matrix(shape)
        rows = row_idx.get_indexer(df.iloc[:, 0].to_numpy())
        cols = col_idx.get_indexer(df.iloc[:, 1].to_numpy())
        mask = (rows >= 0) & (cols >= 0)
        return sp.coo_matrix(
            (df.iloc[:, 2].to_numpy(dtype=np.float64)[mask], (rows[mask], cols[mask])),
            shape=shape
        ).tocsr()

    # C_SS: de-duplicate on (citer_work, cited_work) — one reference per work pair
    _sql_ss = """
        SELECT citer_source_idx, cited_source_idx, SUM(rho_w) AS weight
        FROM (SELECT DISTINCT citer_work_idx, citer_source_idx,
                              cited_work_idx,  cited_source_idx, rho_w FROM _tmp_el)
        GROUP BY citer_source_idx, cited_source_idx"""

    # C_SI: de-duplicate over citer_inst — each (citer_work, cited_work, cited_inst) once
    _sql_si = """
        SELECT citer_source_idx, cited_inst_idx,
               SUM(rho_w * cited_inst_weight) AS weight
        FROM (SELECT DISTINCT citer_work_idx, citer_source_idx,
                              cited_work_idx,  cited_inst_idx,
                              rho_w, cited_inst_weight FROM _tmp_el)
        GROUP BY citer_source_idx, cited_inst_idx"""

    # C_IS: de-duplicate over cited_inst — each (citer_work, citer_inst, cited_work) once
    _sql_is = """
        SELECT citer_inst_idx, cited_source_idx,
               SUM(rho_w * inst_weight) AS weight
        FROM (SELECT DISTINCT citer_work_idx, citer_inst_idx,
                              cited_work_idx,  cited_source_idx,
                              rho_w, inst_weight FROM _tmp_el)
        GROUP BY citer_inst_idx, cited_source_idx"""

    # C_II: no de-duplication — every (citer_inst, cited_inst) cross-product counts
    _sql_ii = """
        SELECT citer_inst_idx, cited_inst_idx,
               SUM(rho_w * inst_weight * cited_inst_weight) AS weight
        FROM _tmp_el
        GROUP BY citer_inst_idx, cited_inst_idx"""

    # ── Assemble requested blocks ──────────────────────────────────────────
    m_SS, m_SI, m_IS, m_II = m
    C_SS = _block(_sql_ss, src_idx,  src_idx,  (n_s, n_s)) if m_SS else None
    C_SI = _block(_sql_si, src_idx,  inst_idx, (n_s, n_u)) if m_SI else None
    C_IS = _block(_sql_is, inst_idx, src_idx,  (n_u, n_s)) if m_IS else None
    C_II = _block(_sql_ii, inst_idx, inst_idx, (n_u, n_u)) if m_II else None

    db.execute("DROP TABLE IF EXISTS _tmp_el")

    is_sentinel_s = (source_ids == SX_IDX) if epsilon else None
    is_sentinel_u = (inst_ids   == IX_IDX) if epsilon else None

    return CSRData(
        C_SS=C_SS, C_SI=C_SI, C_IS=C_IS, C_II=C_II,
        source_ids=source_ids, inst_ids=inst_ids,
        a_s=a_s, a_u=a_u,
        n_s=n_s, n_u=n_u,
        is_sentinel_s=is_sentinel_s,
        is_sentinel_u=is_sentinel_u,
    )
