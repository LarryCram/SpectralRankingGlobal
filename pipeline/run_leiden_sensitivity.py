"""
run_leiden_sensitivity.py — One-at-a-time parameter sensitivity for Leiden groups.

Baseline (already computed): τ=10, ρ=0, ε=0, ω=0, β=0  label='baseline'

Variants:
  tau20  τ_s=τ_u=20/yr  new edge list (fewer retained units)
  rho1   ρ=1             symlink to baseline edge list
  eps1   ε=1             new edge list (adds sentinel cross-field edges)
  om1    ω=1             symlink to baseline edge list
  beta1  β=1             symlink to baseline edge list

m stays at (0,1,1,0) bipartite throughout.
Labels encode parameter values for downstream plotting in leiden_facets.py.

Usage:
  .venv/bin/python pipeline/run_leiden_sensitivity.py
"""

import sys
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from util import load_config, load_settings, load_runs
from build_edge_list_field import build_edge_list
from run_rankings import rank_field

# Variants: params to override from baseline, and whether a new edge list is needed.
# needs_el=False → symlink to baseline edge list (rho/omega/beta don't affect edges).
VARIANTS = [
    dict(label='tau20',  tau_s=20.0, tau_u=20.0, needs_el=True),
    dict(label='rho1',   rho=1,                  needs_el=False),
    dict(label='eps1',   epsilon=1,               needs_el=True),
    dict(label='om1',    omega=1,                 needs_el=False),
    dict(label='beta1',  beta=1,                  needs_el=False),
]


def _symlink_el(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        return
    dst.symlink_to(src)
    print(f"  edge list: symlink → {src.name}")


def main():
    paths    = load_config()
    settings = load_settings()
    runs     = load_runs()
    working  = paths.working
    working_s = str(working)

    fw_path = str(working / f'flat_works_{settings.year_min}_{settings.year_max}.parquet')
    cr_path = str(working / f'corpus_references_{settings.year_min}_{settings.year_max}.parquet')

    for p in [fw_path, cr_path]:
        if not Path(p).exists():
            raise FileNotFoundError(f"{p} not found — run build_flat_works.py first")

    base_run = runs[0]

    def _db():
        db = duckdb.connect()
        db.execute(f"SET temp_directory = '{working}/.tmp'")
        db.execute(f"SET memory_limit = '{settings.memory_limit}'")
        db.execute(f"SET preserve_insertion_order = {str(settings.preserve_insertion_order).lower()}")
        return db

    for v in VARIANTS:
        label    = v['label']
        overrides = {k: val for k, val in v.items() if k not in ('label', 'needs_el')}
        needs_el = v['needs_el']

        # sc/ic paths come from a leiden run (field_idx=1 triggers is_leiden)
        leiden_base = replace(base_run, field_idx=1)
        sc_path = leiden_base.sc_path(working_s)
        ic_path = leiden_base.ic_path(working_s)

        print(f"\n{'='*72}")
        print(f"Variant: {label}  {overrides}  needs_el={needs_el}")
        print(f"{'='*72}")
        t_var = time.time()

        for lid in settings.all_leiden_fields:
            run = replace(base_run, field_idx=lid, label=label, **overrides)
            baseline_run = replace(base_run, field_idx=lid, label='baseline')

            el_path  = Path(run.el_path(working_s))
            rk_path  = run.rankings_path(working_s)
            baseline_el = Path(baseline_run.el_path(working_s))

            print(f"\n--- leiden {lid} ---")
            t_field = time.time()

            # Fresh connection per leiden group — prevents memory accumulation
            # across large edge list builds (leiden 3/4 are 5–9 GB).
            with _db() as db:
                if needs_el:
                    if not el_path.exists():
                        t0 = time.time()
                        n_el = build_edge_list(
                            db, fw_path, cr_path, sc_path, ic_path,
                            run, str(el_path),
                        )
                        print(f"  edge list: {n_el:,} rows  [{time.time()-t0:.1f}s]")
                    else:
                        print(f"  edge list: cached")
                else:
                    _symlink_el(baseline_el, el_path)

                if Path(rk_path).exists():
                    print(f"  ranked: cached")
                else:
                    df, diag = rank_field(db, run, working_s, rk_path, verbose=True)
                    Path(run.diag_path(working_s)).write_text(json.dumps(diag, indent=2))
                    print(f"  ranked: {len(df):,} units  [{time.time()-t_field:.1f}s]")

        print(f"\nVariant {label} complete  [{time.time()-t_var:.0f}s total]")


if __name__ == '__main__':
    t0 = time.time()
    main()
    print(f"\nFINISHED  [{time.time()-t0:.0f}s total]")
