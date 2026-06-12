# CLAUDE.md — SpectralRankingGlobal

## Project root
`/home/lc/Projects/SpectralRankingGlobal`

## Purpose
Generalised spectral ranking of institutions across all fields, using journal sets
and topic sets as the left-side nodes of a bipartite (left × institution) ranking.
See NEW_PROJECT_DESIGN.md for the full design rationale.

## Python environment
Always use `.venv/bin/python` and `.venv/bin/pip`. Never invoke bare `python` or `pip`.

## Data paths
Machine-specific paths in `config.yaml` (gitignored). Read via `util.load_config()`.

## Set types
Two parallel set types, identical downstream pipeline:
- `journal`: left entities = journals {S}; edge source is `work.source_id`
- `topic`: left entities = OA topics {T} in a subfield; edge source is `work.topic_ids`

## Config files
- `sets.csv` — set definitions (set_id, set_type, set_name, discipline_or_subfield_id)
- `runs.csv` — ranking runs (skip, set_id, window, fx, tau_u, tau_s, rho, m, chi, alpha)

## Folder structure
```
SpectralRankingGlobal/
  spectral_ranking/     # ranking engine (build_csr, power iteration)
  prepare_data/         # OpenAlex ingestion pipeline
  util/                 # load_config, Paths, load_runs
  data/                 # small reference files
  sets.csv              # seed set registry
  runs.csv              # run schedule
  config.yaml           # machine-specific paths (gitignored)
  NEW_PROJECT_DESIGN.md # design rationale
```

## Data persistence
```
data/
  edge_lists/   el_{set_id}_{window}.parquet
  units/        units_{set_id}_{window}.parquet
  rankings/     rankings_all.parquet   (set_id, entity_type, entity_id, v, rank, params)
```
