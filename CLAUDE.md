# CLAUDE.md — SpectralRankingGlobal

## Project root
`/home/lc/Projects/SpectralRankingGlobal`

## Purpose
Generalised spectral ranking of institutions across all fields, using journal sets
and topic sets as the left-side nodes of a bipartite (left × institution) ranking.
See NEW_PROJECT_DESIGN.md for the full design rationale.

## Python environment
Always use `.venv/bin/python` and `.venv/bin/pip`. Never invoke bare `python` or `pip`.

## Machine-specific config
`config.yaml` (gitignored) — three keys: `PROJECT_ROOT`, `WORKING`, `OPENALEX`.
Read via `util.load_config()` → `Paths`. Never inline yaml loading.

## Set types
Two parallel set types, identical downstream pipeline:
- `journal`: left entities = journals {S}; edge source is `work.source_id`
- `topic`: left entities = OA topics {T} in a subfield; edge source is `work.topic_ids`

## Config files (target — not yet created)
- `sets.csv` — set definitions (set_id, set_type, set_name, discipline_or_subfield_id)
- `runs.csv` — ranking runs (skip, set_id, window, fx, tau_u, tau_s, rho, m, chi, alpha)

## Folder structure
```
SpectralRankingGlobal/
  spectral_ranking/       # ranking engine
    build_csr.py          #   CSR matrix assembly from edge lists
    katz_ranker.py        #   spectral algorithms (Perron/Katz, bipartite)
    run_rankings.py       #   parameter driver; reads runs.csv
    tests/
  prepare_data/           # OpenAlex ingestion pipeline
    build_edge_lists.py   #   builds citation edge lists in DuckDB
    filter_mode_units.py  #   mode-specific SCC unit tables
    load_corpus_entities.py #  extracts corpus parquets from OA snapshot
    verify_edge_lists.py  #   invariant checks
    tests/
  util/
    load_config.py        #   load_config() → Paths, load_runs() → list
  data/
    oa_topic_df.csv       #   OA topic taxonomy (topic_id → subfield_id, …)
  config.yaml             # machine-specific paths (gitignored)
  runs.csv                # run schedule (not yet created)
  sets.csv                # set registry (not yet created)
  requirements.txt
  NEW_PROJECT_DESIGN.md   # design rationale
```

## Data persistence (current — DuckDB, pending migration to parquet)
```
WORKING/
  parquet/              pipeline intermediates from load_corpus_entities.py
  edge_lists.duckdb     edge lists + unit tables (build_edge_lists.py output)
  rankings.duckdb       ranking tables (run_rankings.py output)
```

## Migration status
The pipeline is ported from EconomicsBusiness but not yet migrated to the new design.
Key remaining work:
- Create `sets.csv` and `runs.csv`
- Replace field-based corpus logic (`field_eb`, `fx` letter codes) with set-id logic
- Replace DuckDB edge list / unit storage with parquet files
- Parameterise `build_csr` with `left_col` to support topic sets
