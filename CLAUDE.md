# CLAUDE.md — SpectralRankingGlobal

Current-state operating reference only. For stable design/schema/mechanics material
(folder structure, data schemas, parameter semantics, field mappings, spectral gap
tables) see `REFERENCE.md`. For dated incidents, recodes, and bug-fix history see
`CHANGELOG.md`.

## Project root
`/home/lc/Projects/SpectralRankingGlobal`

## Purpose
Generalised spectral ranking of sources and institutions across all fields.
Rankings are always over sources {S} and institutions {I}. See `REFERENCE.md` for
set types, run parameters, and full design details; `NEW_PROJECT_DESIGN.md` for the
rationale.

## Python environment
Always use `.venv/bin/python` and `.venv/bin/pip`. Never invoke bare `python` or `pip`.

## Testing
`.venv/bin/python -m pytest util/tests pipeline/tests analysis/tests
analysis/test_hcr_inst_oax.py enclaves/tests --import-mode=importlib` (389 tests).
Plain `pytest` errors on collection — `pipeline/tests/` has no `__init__.py` while the
other three `tests/` packages do, causing a module-name collision across separately-run
test directories.

## Machine-specific config
`config.yaml` (gitignored) — three keys: `PROJECT_ROOT`, `WORKING`, `OPENALEX`.
Read via `util.load_config()` → `Paths`. Never inline yaml loading.

## Pipeline status — window 2020_2024
All stages ✅ complete and verified fresh as of 2026-07-04 (full rebuild against the
flat_works recode — see CHANGELOG.md). Run
`.venv/bin/python pipeline/pipeline_status.py` for a live freshness report — every
guarded output was 421/421 fresh at last check. See `REFERENCE.md`'s "Pipeline guard
system" section for how staleness is now caught automatically instead of silently.

- ✅ Stage 1a/1b: `build_flat_works.py` — flat_works/corpus_references, recoded schema
  (title, cited_by_count, authors_count, institutions_distinct_count carried verbatim;
  title dedup + authors_count<=29 filter; work_types broadened to 8 types)
- ✅ Stage 2: `build_field_candidacy.py` — all 6 candidacy parquets
- ✅ Stage 3+4: `run_field_bloc.py` / `run_leiden_bloc.py` — baseline + all 4 blocs
  (OECDG20, OECDG20CIA, CIAA, BASELINECIA), 26 OA fields + 5 Leiden groups each
- ✅ Sensitivity suite: `run_leiden_sensitivity.py` — 5 variants × 5 Leiden groups
- ✅ Stage 4c: `build_work_v.py` — work_v_2020_2024_baseline.parquet
- ✅ Stage E1–E3: HCW detection, TF-IDF, NMF — all 26 fields. NMF topic names (the
  26-field AI merge+name pass) will need reapplying after any future from-scratch
  rebuild, since `enclave_nmf_*.parquet` regenerates without them.
- ✅ Stage E4: `network_enclave.py` — enclave network MD reports (26 fields)
- ✅ Stage E5: `researcher_enclaves.py` — 26 researcher MD reports (OA authors schema
  bug fixed this session — see CHANGELOG.md)
- ✅ `enclaves/plot_hca_hcr.py` — 6 PDFs (combined + 5 Leiden group), HCR-overlay included
- ✅ FOR2020/AREA5 classification: `pipeline/run_area5.py` (5 populated AREA5 groups, +
  Indigenous Studies correctly skipped — unreachable from OA data) and
  `pipeline/run_for_division.py` (FOR2020 division-level rankings) — baseline label only, not
  part of the guarded rerun order below (decoupled from `flat_works`/`Run`; see REFERENCE.md's
  "FOR2020/AREA5 classification" section). Reference tables
  (`data/oax_field_to_area5.csv`/`data/oax_subfield_to_for2020.csv`) built once by
  `util/build_for_mapping.py`, re-run only if `ResearchClassification` itself is updated.

## Rerun order
Every stage above is guard-wired (see `REFERENCE.md`): each script checks its own
inputs' and its own (plus its real code dependencies') mtimes before deciding to skip
a rebuild, so manual delete-then-rerun is not required to pick up a code or data change.
To rebuild everything from scratch in dependency order, just run in sequence (each
auto-skips whatever is already fresh):
```
.venv/bin/python pipeline/build_flat_works.py
.venv/bin/python pipeline/build_field_candidacy.py
.venv/bin/python pipeline/run_field_bloc.py
.venv/bin/python pipeline/run_leiden_bloc.py
.venv/bin/python pipeline/run_leiden_sensitivity.py
.venv/bin/python pipeline/build_work_v.py
.venv/bin/python analysis/hca_extract.py
.venv/bin/python analysis/hca_crossfield.py
.venv/bin/python enclaves/build_enclave_hcw.py
.venv/bin/python enclaves/tfidf_enclave.py
.venv/bin/python enclaves/nmf_enclave.py
.venv/bin/python enclaves/network_enclave.py
.venv/bin/python enclaves/researcher_enclaves.py
.venv/bin/python enclaves/plot_hca_hcr.py
```
Append `--yes`/`-y` to any of them to skip the confirmation prompt on an expensive stale
rebuild (safe for unattended/background runs). Run `.venv/bin/python
pipeline/pipeline_status.py` any time to see what's fresh/stale without rebuilding anything.

## TODO — next: spectral_analysis/
- Compute top-k eigenvectors for small-gap fields (Arts 0.117, Business 0.091, Econ 0.149, Vet 0.110, Engineering 0.210, Maths 0.332)
- Embed sources+institutions in eigenvector space → identify co-clusters / subfield structure
- The second eigenvector sign-pattern partitions the field into its two dominant sub-communities

## TODO — Australian HEP heatmap
- `analysis/hep_heatmap.py` produces the heatmap for 26 OA fields with baseline rankings
- Extension: Leiden-level heatmap (5 columns instead of 26)
- Extension: bloc-filtered heatmap (OECDG20CIA, CIAA) — bloc rankings are now complete,
  ready to build against

## TODO — Subfield rankings
- Investigate running spectral ranking at the OA subfield level (below the 26 fields)
  to expose finer disciplinary structure; assess corpus sparsity vs. spectral signal trade-off
