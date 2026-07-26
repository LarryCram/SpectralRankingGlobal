"""
run_rankings.py — Stage 4b: run spectral rankings for one or more fields.

For each Run, reads the pre-built edge list parquet, assembles CSR matrices,
computes Perron/Katz prestige scores, and writes a ranking parquet.

Usage (from project root):
  .venv/bin/python pipeline/run_rankings.py          # Business baseline
  .venv/bin/python pipeline/run_rankings.py --field 14

Output: WORKING/rankings_{field_idx}_{window}.parquet

Schema:
  field_idx  BIGINT
  unit_idx   BIGINT
  unit_type  VARCHAR   'S' = source, 'U' = institution
  pi         DOUBLE    Perron prestige score (L1-normalised within type)
  v          DOUBLE    size-adjusted score: pi / a_p, scaled so mean = 1
  rank_pi    INTEGER   rank by pi (1 = highest)
  rank_v     INTEGER   rank by v  (1 = highest)
  a_p        DOUBLE    weighted works over window (unit size)
"""

import sys
from pathlib import Path
import time

import numpy as np
import pandas as pd
import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from util import load_config, load_settings, load_runs, Run
from build_csr_field import build_csr_field
from katz_ranker import rank as katz_rank


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mu(mu_type: str, n_s: int, n_u: int) -> 'np.ndarray | None':
    if not mu_type:
        return None
    N = n_s + n_u
    if mu_type == 'uniform':
        return np.full(N, 1.0 / N, dtype=np.float64)
    elif mu_type == 'unit_scaled':
        return np.concatenate([
            np.full(n_s, 0.5 / n_s, dtype=np.float64),
            np.full(n_u, 0.5 / n_u, dtype=np.float64),
        ])
    raise ValueError(f"Unknown mu_type {mu_type!r}")


def _dense_rank_desc(values: np.ndarray) -> np.ndarray:
    safe  = np.where(np.isnan(values), -np.inf, values)
    order = np.argsort(-safe, kind='stable')
    ranks = np.empty(len(safe), dtype=np.int32)
    ranks[order[0]] = 1
    current = 1
    for i in range(1, len(order)):
        if safe[order[i]] < safe[order[i - 1]]:
            current += 1
        ranks[order[i]] = current
    return ranks


# ── Core ranking function ─────────────────────────────────────────────────────

def rank_field(db: duckdb.DuckDBPyConnection,
               run: Run,
               working_dir: str,
               out_path: str,
               verbose: bool = True,
               el_path: str = None,
               sc_path: str = None,
               ic_path: str = None) -> tuple[pd.DataFrame, dict]:
    """
    Build CSR matrices, run spectral ranking, write rankings parquet.
    Returns (df, diag) where diag holds counts and algorithm diagnostics.

    el_path/sc_path/ic_path default to run.el_path()/sc_path()/ic_path()'s own
    field/leiden/subfield dispatch; pass them explicitly for a grouping scheme
    decoupled from that dispatch (e.g. AREA5/FOR-division), where run.field_idx is
    just this call's own scratch-candidacy identity, not something Run's path
    methods should derive a file location from.
    """
    import duckdb as _duckdb
    el_path = el_path or run.el_path(working_dir)
    sc_path = sc_path or run.sc_path(working_dir)
    ic_path = ic_path or run.ic_path(working_dir)

    if not Path(el_path).exists():
        raise FileNotFoundError(
            f"Edge list not found: {el_path}\n"
            f"Run pipeline/build_edge_list_field.py first."
        )

    # Candidacy counts (τ-retained before connectivity filter)
    tau_s_abs = run.tau_s_abs()
    tau_u_abs = run.tau_u_abs()
    n_s_cands = db.execute(
        f"SELECT COUNT(*) FROM '{sc_path}' "
        f"WHERE field_idx={run.field_idx} AND weighted_works>={tau_s_abs}"
    ).fetchone()[0]
    n_u_cands = db.execute(
        f"SELECT COUNT(*) FROM '{ic_path}' "
        f"WHERE field_idx={run.field_idx} AND weighted_works>={tau_u_abs}"
    ).fetchone()[0]

    if verbose:
        print(f"  Building CSR (m={run.m}, rho={run.rho}) ...", flush=True)
    t0 = time.time()
    csr = build_csr_field(db, el_path, sc_path, ic_path, run)
    if verbose:
        print(f"    candidacy  n_s={n_s_cands:,}  n_u={n_u_cands:,}")
        print(f"    in edge list  n_s={csr.n_s:,} ({n_s_cands-csr.n_s:+d})  "
              f"n_u={csr.n_u:,} ({n_u_cands-csr.n_u:+d})  [{time.time()-t0:.1f}s]")

    chi = run.chi
    if chi == -1.0:
        chi = csr.n_u / (csr.n_s + csr.n_u)
        if verbose:
            print(f"    χ* = {chi:.4f}")

    mu = _make_mu(run.mu_type, csr.n_s, csr.n_u)

    if verbose:
        print(f"  Running katz_rank (alpha={run.alpha}) ...", flush=True)
    t1 = time.time()
    result = katz_rank(csr, run.m, chi, run.alpha, mu=mu)
    algo = 'eigs (ARPACK Perron)' if result.iters == 0 else f'power iteration ({result.iters} iters)'
    if verbose:
        if result.lam1 != 0.0:
            print(f"    algo={algo}  lam1={result.lam1:.6f}  lam2={result.lam2:.6f}  "
                  f"gap={1.0 - abs(result.lam2):.2e}  [{time.time()-t1:.1f}s]")
        else:
            print(f"    algo={algo}  norm={result.final_norm:.2e}  "
                  f"[{time.time()-t1:.1f}s]")

    # ── Assemble output dataframe ─────────────────────────────────────────────
    parts = []
    if result.pi_s is not None:
        parts.append(pd.DataFrame({
            'field_idx': run.field_idx,
            'unit_idx':  csr.source_ids.astype(np.int64),
            'unit_type': 'S',
            'pi':        result.pi_s.astype(np.float64),
            'v':         result.v_s.astype(np.float64),
            'a_p':       csr.a_s.astype(np.float64),
        }))
    if result.pi_u is not None:
        parts.append(pd.DataFrame({
            'field_idx': run.field_idx,
            'unit_idx':  csr.inst_ids.astype(np.int64),
            'unit_type': 'U',
            'pi':        result.pi_u.astype(np.float64),
            'v':         result.v_u.astype(np.float64),
            'a_p':       csr.a_u.astype(np.float64),
        }))

    df = pd.concat(parts, ignore_index=True)
    for col in ('pi', 'v'):
        df[f'rank_{col}'] = (
            df.groupby('unit_type')[col]
              .transform(lambda x: _dense_rank_desc(x.to_numpy()))
              .astype(np.int32)
        )
    df = df[['field_idx', 'unit_idx', 'unit_type', 'pi', 'v',
             'rank_pi', 'rank_v', 'a_p']]

    db.register('_rank_df', df)
    db.execute(f"COPY (SELECT * FROM _rank_df) TO '{out_path}' (FORMAT PARQUET)")
    db.unregister('_rank_df')

    diag = dict(
        field_idx=run.field_idx,
        window=run.window,
        label=run.label,
        m=list(run.m),
        alpha=run.alpha,
        rho=run.rho,
        tau_s=run.tau_s,
        tau_u=run.tau_u,
        n_s_cands=int(n_s_cands),
        n_u_cands=int(n_u_cands),
        n_s_ranked=int(csr.n_s),
        n_u_ranked=int(csr.n_u),
        algo=algo,
        lam1=float(result.lam1),
        lam2=float(result.lam2),
        spectral_gap=float(1.0 - abs(result.lam2)) if result.lam1 != 0.0 else None,
        iters=int(result.iters),
        final_norm=float(result.final_norm),
    )
    return df, diag


# ── Display helpers ───────────────────────────────────────────────────────────

def show_top(df: pd.DataFrame, unit_type: str, n: int = 20,
             label: str = '') -> None:
    tag   = {'S': 'sources', 'U': 'institutions'}[unit_type]
    title = f"Top {n} {tag}" + (f"  [{label}]" if label else '')
    sub   = (df[df.unit_type == unit_type]
             .sort_values('rank_v')
             .head(n)
             [['unit_idx', 'rank_pi', 'rank_v', 'pi', 'v', 'a_p']])
    print(f"\n{title}")
    print(sub.to_string(index=False))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from dataclasses import replace as dc_replace
    import json

    paths    = load_config()
    settings = load_settings()
    runs     = load_runs()
    working  = str(paths.working)

    run      = dc_replace(runs[0], field_idx=14)   # single-field entry point: default field 14
    out_path = run.rankings_path(working)

    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 160)
    pd.set_option('display.float_format', '{:.6f}'.format)

    with duckdb.connect() as db:
        db.execute(f"SET temp_directory = '{paths.working}/.tmp'")
        db.execute(f"SET memory_limit = '{settings.memory_limit}'")
        db.execute(f"SET preserve_insertion_order = {str(settings.preserve_insertion_order).lower()}")

        print(f"Ranking field {run.field_idx}, window {run.window}, label={run.label!r} "
              f"(τ={run.tau_s}/yr, m={run.m}, α={run.alpha}, ρ={run.rho}) ...")
        t0 = time.time()
        df, diag = rank_field(db, run, working, out_path)
        print(f"\n  Total: {time.time()-t0:.1f}s  →  {out_path}")

    diag_path = run.diag_path(working)
    Path(diag_path).write_text(json.dumps(diag, indent=2))
    print(f"  Diagnostics → {diag_path}")

    show_top(df, 'S', label=run.label)
    show_top(df, 'U', label=run.label)


if __name__ == '__main__':
    main()
    print("\nFINISHED!")
