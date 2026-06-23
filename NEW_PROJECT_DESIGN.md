# New Project: Generalised Spectral Ranking

## Purpose

Generalise the EconomicsBusiness spectral ranking to cover all fields, producing
source/institution rankings for world research output by oax topic/field.

## Core abstraction: seed sets

A **set** defines the corpus of works used in a ranking. The ranking is always
over **sources** {S} and **institutions** {I} — these are the bipartite node sets
in every run. Set type controls only how the corpus is filtered:

| type | corpus filter | work←set join | output |
|------|--------------|---------------|--------|
| `journal` | works published in seed journals {S} | `work.source_id IN {S}` | v_S, v_I |
| `topic` | works tagged with topics in a field | `topics WHERE field_idx = X` | v_S, v_I |

Topics are **corpus filters**, not ranking entities. Both set types produce
source rankings and institution rankings; the bipartite spectral machinery
(build_csr, power iteration, alpha/chi) is identical downstream.

## Scale

- ~2000 institutions
s- ~2000 journals

## OA topic hierarchy

OpenAlex uses a strict four-level hierarchy:

```
domain (4)  →  field (26)  →  subfield (~300)  →  topic (~4,500)
```

We filter at the **field** level, producing one topic-type set per field (26 sets).
`field_idx` runs 11–36 (not 1–26). The full hierarchy is already denormalised in
`parquet/topics/*.parquet` — no auxiliary lookup table needed.

## Data sources

- OpenAlex citation data (existing pipeline)
- `parquet/topics/*.parquet` — OA snapshot table, one row per (work_idx, topic_idx).
  Columns: `work_idx, topic_idx, score, subfield_idx, subfield_name, field_idx, field_name, domain_idx, domain_name`.
  The hierarchy is fully denormalised; no auxiliary join needed.
  `score` is an independent per-topic confidence score (not a probability); values cluster near 1.0.

## Field weights

For each work, the fractional weight assigned to field X is:

```
field_weight(work, X) = SUM(score WHERE field_idx = X)
                      / SUM(score over all topics for this work)
```

Field weights sum to 1 across all 26 fields for every work. A work split equally
between two fields contributes half as much to each field's citation matrix as a
work wholly within one field — conserved in total, downweighted locally.
Because OA scores cluster near 1.0, normalized weights are typically close to
uniform over assigned topics (the score differences carry little information).

## Pipeline stages

### Stage 1 — flat works table (`build_flat_works.py`) ✅

One pass over the OA snapshot produces `WORKING/flat_works_{ymin}_{ymax}.parquet`:
one row per **(work × institution × field)**.

```
work_idx               BIGINT
publication_year       BIGINT
source_idx             BIGINT
institution_idx        BIGINT
inst_weight            DOUBLE   -- author-fractional: SUM(1/n_authors/n_inst_per_author)
direct_inst_weight     DOUBLE   -- institution-fractional: 1/n_qualifying_institutions
field_idx              BIGINT
field_weight           DOUBLE   -- score-normalised, sums to 1 per work across fields
referenced_works_count BIGINT
```

Filters applied: `2016 ≤ publication_year ≤ 2025`, type ∈ {article, review},
`is_paratext = false`, `is_retracted = false`, `referenced_works_count > 0`,
source type ∈ {journal, conference, book series},
institution type ∈ {education, nonprofit, government, other}.

Output: ~110M rows. All downstream stages read from this table.

### Stage 2 — field candidacy (`build_field_candidacy.py`) ✅

Aggregate `flat_works` to get field-weighted work counts per source and institution,
across all 26 fields in a single GROUP BY:

```sql
-- source candidacy
SELECT field_idx, source_idx, SUM(field_weight) AS weighted_works
FROM flat_works WHERE publication_year BETWEEN tc0 AND tc1
GROUP BY field_idx, source_idx

-- institution candidacy
SELECT field_idx, institution_idx,
       SUM(field_weight * inst_weight) AS weighted_works
FROM flat_works WHERE publication_year BETWEEN tc0 AND tc1
GROUP BY field_idx, institution_idx
```

τ threshold is specified as **weighted works per year** (`tau_s`, `tau_u` in `Run`);
actual cutoff = τ × window_years. This makes thresholds window-length-independent.
Output: `WORKING/field_source_cands_{window}.parquet` and
`WORKING/field_inst_cands_{window}.parquet`.

### Stage 3 — edge lists (`build_edge_list_field.py`) ✅

For each field and window, join `flat_works` (citer) × OA references × `flat_works`
(cited), filtered to retained units. Edge weight in field X:

```
edge_field_weight = (citer.field_weight + cited.field_weight) / 2
R_i = SUM(edge_field_weight) over all cited works for a given citer_work
```

Output schema (one row per citer_inst × cited_inst):
```
citer_work_idx, citer_source_idx, citer_inst_idx
cited_work_idx, cited_source_idx, cited_inst_idx
inst_weight, direct_inst_weight
cited_inst_weight, direct_cited_inst_weight
edge_field_weight, R_i
```

Output: `WORKING/el_{field_idx}_{window}_tauS{s}_tauU{u}.parquet` (one per field).

### Stage 4 — rankings (`build_csr_field.py` + `run_rankings.py`) ☐

`build_csr_field.py` reads the edge list parquet and builds CSRData blocks
(C_SS, C_SI, C_IS, C_II) with ρ weighting folded into `edge_field_weight`.
`run_rankings.py` iterates over Run configs, calls `katz_ranker.rank()`, and
writes per-field ranking parquets.

## Run parameters (`util.Run`)

```
window    : '{census_start}_{census_end}', e.g. '2020_2024'
field_idx : OA field index 11–36
tau_s     : source threshold (weighted works/year)
tau_u     : institution threshold (weighted works/year)
m         : block mask (m_SS, m_SI, m_IS, m_II); (0,1,1,0) = bipartite standard
alpha     : 1.0 = Perron eigenvector; <1 = Katz damping
rho       : 0 = fixed-count (R̄/Rᵢ); 1 = full-count
chi       : mixing weight; -1 = chi* = Nu/(Ns+Nu); only for m=(1,1,1,1)
mu_type   : '' for alpha=1; 'uniform' or 'unit_scaled' for alpha<1
```

## Data persistence

All intermediates are parquet files. DuckDB is used for computation; nothing
persists in DuckDB format.

```
WORKING/
  flat_works_{ymin}_{ymax}.parquet                   ✅ stage 1; ~97M rows
  field_source_cands_{window}.parquet                ✅ stage 2
  field_inst_cands_{window}.parquet                  ✅ stage 2
  el_{field_idx}_{window}_tauS{s}_tauU{u}.parquet   stage 3; one per field
  rankings_{field_idx}_{window}.parquet              stage 4; one per field
```

`window` = `{census_start}_{census_end}` (e.g. `2020_2024`).

## Folder structure

```
pipeline/                  — all pipeline stages + ranking math
  build_flat_works.py      stage 1
  build_field_candidacy.py stage 2
  build_edge_list_field.py stage 3
  build_csr_field.py       stage 4a (CSR assembly)
  run_rankings.py          stage 4b (parameter driver)
  katz_ranker.py           spectral algorithms — pure math, no I/O
  summary_flat_works.py    diagnostics
  tests/
spectral_analysis/         future analysis scripts
util/                      load_config, Run dataclass
ZARCHIVE/                  superseded EconomicsBusiness code
```

## What has been jettisoned

- `prepare_data/` and `spectral_ranking/` as separate directories
- Field-specific corpus logic (field_eb, econ/business classification)
- `params.csv` / `runs.csv` schema (replaced by `util.Run` dataclass)
- EconomicsBusiness edge list format (DuckDB tables) → parquet files
- vartau/fixtau distinction (may re-emerge but not a starting assumption)
