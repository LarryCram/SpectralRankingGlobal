"""
common.py — shared paths, constants and DuckDB setup for the researchers/ subproject.

Every researchers/ script accepts --working and --openalex so it can run against a
snapshot other than the one in config.yaml (this checkout stays on jul26; the
researcher study uses Oct26).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from util import load_config, load_settings  # noqa: E402

WINDOW = '2020_2024'          # window of the spectral rankings that supply v
LABEL = 'baseline'
CITE_YEAR = 2025              # single-year analogue: citing works published in this year
MIN_WORKS = 5                 # Stanford volume baseline (>= 5 papers)
STANFORD_TYPES = ('article', 'review', 'conference-paper')
N_BUCKETS = 32                # cited_idx hash buckets for the self-citation join
TMP_DIR = '/home/lc/m/duckdb_tmp'


@dataclass(frozen=True)
class RPaths:
    working: Path
    openalex: Path
    year_min: int
    year_max: int

    @property
    def out(self) -> Path:
        p = self.working / 'researchers'
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def flat_works(self) -> Path:
        return self.working / f'flat_works_{self.year_min}_{self.year_max}.parquet'

    @property
    def corpus_refs(self) -> Path:
        return self.working / f'corpus_references_{self.year_min}_{self.year_max}.parquet'

    @property
    def work_v(self) -> Path:
        return self.working / f'work_v_{WINDOW}_{LABEL}.parquet'

    @property
    def compact(self) -> Path:
        return self.openalex / 'compact'

    @property
    def authorships_glob(self) -> str:
        return f'{self.compact}/authorships/*.parquet'

    @property
    def works_glob(self) -> str:
        return f'{self.compact}/works/*.parquet'

    @property
    def authors_glob(self) -> str:
        return f'{self.openalex}/authors/*.parquet'

    # researchers/ outputs
    @property
    def author_works(self) -> Path:
        return self.out / f'author_works_{self.year_min}_{self.year_max}.parquet'

    @property
    def author_volume(self) -> Path:
        return self.out / f'author_volume_{self.year_min}_{self.year_max}.parquet'

    @property
    def work_scores(self) -> Path:
        return self.out / f'work_scores_{self.year_min}_{self.year_max}_{LABEL}.parquet'

    @property
    def work_subfields(self) -> Path:
        return self.out / f'work_subfields_{self.year_min}_{self.year_max}.parquet'

    @property
    def author_v(self) -> Path:
        return self.out / f'author_v_{self.year_min}_{self.year_max}_{LABEL}.parquet'

    @property
    def stanford_dir(self) -> Path:
        p = self.out / 'stanford'
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def stanford_match(self) -> Path:
        return self.out / 'stanford_oa_match.parquet'


def add_path_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--working', type=Path, default=None,
                        help='WORKING dir (default: config.yaml)')
    parser.add_argument('--openalex', type=Path, default=None,
                        help='OpenAlex parquet_converted dir (default: config.yaml)')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Rebuild stale output without prompting')


def resolve_paths(args: argparse.Namespace) -> RPaths:
    cfg = load_config()
    st = load_settings()
    return RPaths(
        working=args.working or cfg.working,
        openalex=args.openalex or cfg.openalex,
        year_min=st.year_min,
        year_max=st.year_max,
    )


def connect(memory_limit: str = '24GB', threads: int = 8) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET temp_directory = '{TMP_DIR}'")
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(f'SET threads = {threads}')
    con.execute('SET preserve_insertion_order = false')
    con.execute('SET enable_progress_bar = false')
    return con
