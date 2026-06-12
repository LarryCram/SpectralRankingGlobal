"""
util/load_config.py — Shared loaders for SpectralRankingGlobal.

  load_config() → Paths   reads config.yaml  (machine-specific, gitignored)
  load_runs()   → list    reads runs.csv      (run schedule, version-controlled)
"""

import csv
from dataclasses import dataclass
from pathlib import Path
import yaml

_CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'
_RUNS_PATH   = Path(__file__).parent.parent / 'runs.csv'


@dataclass(frozen=True)
class Paths:
    project_root: Path   # PROJECT_ROOT in config.yaml
    working:      Path   # WORKING  (fast storage: large parquets, DuckDB)
    openalex:     Path   # OPENALEX (OA parquet snapshot)
    data:         Path   # project_root / 'data'    (small ref files, git-tracked)
    parquet:      Path   # working / 'parquet'      (pipeline intermediates)


def load_runs(runs_path: Path = _RUNS_PATH) -> list[dict]:
    """
    Read runs.csv and return one dict per non-skipped run.

    Type conversions:
        skip, tau_u, tau_s, rho, omega, epsilon  → int
        chi, alpha                               → float
        all others                               → str
    """
    int_cols   = {'skip', 'tc0', 'tc1', 'tt0', 'tt1', 'tau_u', 'tau_s', 'rho', 'omega', 'epsilon'}
    float_cols = {'chi', 'alpha'}

    runs = []
    with open(runs_path, newline='') as f:
        for row in csv.DictReader(f):
            for col in int_cols:
                if col in row:
                    row[col] = int(row[col]) if row[col] else 0
            for col in float_cols:
                if col in row:
                    row[col] = float(row[col])
            if row['skip']:
                continue
            runs.append(row)
    return runs


def load_config(config_path: Path = _CONFIG_PATH) -> Paths:
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    project_root = Path(cfg['PROJECT_ROOT'])
    working      = Path(cfg['WORKING'])
    return Paths(
        project_root = project_root,
        working      = working,
        openalex     = Path(cfg['OPENALEX']),
        data         = project_root / 'data',
        parquet      = working / 'parquet',
    )
