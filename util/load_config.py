"""
util/load_config.py — Shared loaders for SpectralRankingGlobal.

  load_config()   → Paths           reads config.yaml        (machine-specific, gitignored)
  load_settings() → GlobalSettings  reads settings.yaml      (version-controlled)
                                    + data/bloc.xlsx for bloc definitions
  load_runs()     → list[Run]       reads params.csv          (version-controlled)
"""

import csv
from dataclasses import dataclass
from pathlib import Path
import yaml

from .runs import GlobalSettings, Run, VALID_M

_CONFIG_PATH   = Path(__file__).parent.parent / 'config.yaml'
_SETTINGS_PATH = Path(__file__).parent.parent / 'settings.yaml'
_RUNS_PATH     = Path(__file__).parent.parent / 'params.csv'
_BLOCS_PATH    = Path(__file__).parent.parent / 'data' / 'bloc.xlsx'


@dataclass(frozen=True)
class Paths:
    project_root: Path
    working:      Path
    openalex:     Path
    data:         Path
    parquet:      Path


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


def _load_blocs(path: Path) -> dict[str, tuple[str, ...]]:
    """
    Read data/bloc.xlsx (columns: Bloc, Country, Abbreviation) and return
    a bloc_name → tuple of country codes mapping.

    Base blocs (OECD, G20) come from the spreadsheet.  Derived blocs:
      OECDG20      = OECD ∪ G20
      OECDG20-CIA  = OECDG20 − {CN, IN, US}   (CIA = China, India, America)
      CIAA         = {AU, CN, IN, US}
      AU           = {AU}
    Returns {} if the file does not exist.
    """
    if not path.exists():
        return {}
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    base: dict[str, set[str]] = {}
    first_row = True
    for row in ws.iter_rows(values_only=True):
        if first_row:
            first_row = False
            continue
        if row[0] and row[2]:
            base.setdefault(str(row[0]), set()).add(str(row[2]).strip())
    wb.close()

    oecd    = base.get('OECD', set())
    g20     = base.get('G20',  set())
    cia     = {'CN', 'IN', 'US'}
    oecdg20 = oecd | g20
    return {
        **{name: tuple(sorted(codes)) for name, codes in base.items()},
        'OECDG20':     tuple(sorted(oecdg20)),
        'OECDG20-CIA': tuple(sorted(oecdg20 - cia)),
        'CIAA':        ('AU', 'CN', 'IN', 'US'),
        'AU':          ('AU',),
    }


def load_settings(settings_path: Path = _SETTINGS_PATH,
                  blocs_path:    Path = _BLOCS_PATH) -> GlobalSettings:
    with open(settings_path) as f:
        cfg = yaml.safe_load(f)
    c  = cfg['corpus']
    d  = cfg['duckdb']
    k  = cfg['katz']
    fi = cfg['fields']
    return GlobalSettings(
        year_min                 = int(c['year_min']),
        year_max                 = int(c['year_max']),
        source_types             = tuple(c['source_types']),
        work_types               = tuple(c['work_types']),
        institution_types        = tuple(c['institution_types']),
        memory_limit             = str(d['memory_limit']),
        preserve_insertion_order = bool(d['preserve_insertion_order']),
        katz_tol                 = float(k['tol']),
        katz_max_iter            = int(k['max_iter']),
        field_first              = int(fi['first']),
        field_last               = int(fi['last']),
        blocs                    = _load_blocs(blocs_path),
    )


def load_runs(runs_path: Path = _RUNS_PATH) -> list[Run]:
    """
    Read params.csv and return one Run per non-skipped row.

    m is validated against VALID_M = {'1000','0001','0110','1111'}.
    epsilon, omega, beta default to 0 if column absent.
    bloc defaults to '' (all countries) if column absent.
    """
    int_cols   = {'skip', 'tc0', 'tc1', 'tt0', 'tt1',
                  'rho', 'omega', 'epsilon', 'beta'}
    float_cols = {'tau_s', 'tau_u', 'chi', 'alpha'}

    runs = []
    with open(runs_path, newline='') as f:
        for lineno, row in enumerate(csv.DictReader(f), start=2):
            for col in int_cols:
                if col in row:
                    row[col] = int(row[col]) if row[col].strip() else 0
            for col in float_cols:
                if col in row:
                    row[col] = float(row[col]) if row[col].strip() else 0.0

            if row.get('skip', 0):
                continue

            m_str = row['m'].strip()
            if m_str not in VALID_M:
                raise ValueError(
                    f"params.csv line {lineno}: invalid m={m_str!r}. "
                    f"Must be one of {sorted(VALID_M)}"
                )

            runs.append(Run(
                tc0      = row['tc0'],
                tc1      = row['tc1'],
                tt0      = row.get('tt0', row['tc0']),
                tt1      = row.get('tt1', row['tc1']),
                tau_s    = row['tau_s'],
                tau_u    = row['tau_u'],
                m        = tuple(int(c) for c in m_str),
                alpha    = row['alpha'],
                rho      = row['rho'],
                chi      = row['chi'],
                mu_type  = row.get('mu_type', '').strip(),
                label    = row['label'].strip(),
                epsilon  = row.get('epsilon', 0),
                omega    = row.get('omega', 0),
                beta     = row.get('beta', 0),
                bloc     = row.get('bloc', '').strip(),
            ))
    return runs
