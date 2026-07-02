# Plan: HCR overlay on enclave scatter plots

## Goal
Add matched-HCR authors as black circles to `enclaves/plot_enclave.py`.
First: run plot_enclave.py and inspect the density of the existing scatter before coding.

## What the plot shows
Each point = one HCW (top-1%-cited work, one row per work × field).
- Left panel:  x = log10(source_v) of HCW journal,   y = log10(mean_citer_v)
- Right panel: x = log10(mean_inst_v) of HCW author institution, y = log10(mean_citer_v)
Five PDFs: one per Leiden group (coloured by OA field), plus one combined (coloured by Leiden).
Outputs: `enclaves/plots/enclave_citer_v_{window}_{label}_{tag}.pdf`

## Density note
~49K enclave_hcw rows exist; user confirmed ~1,000 HCR-authored HCW.
Heavy overplotting already present → need to see the dense plots FIRST before
choosing individual-circle vs summary-marker approach.

**Blocked on**: running plot_enclave.py to inspect existing density visually.
Run: `.venv/bin/python enclaves/plot_enclave.py`

## ⟨v⟩ and ⟨⟨v⟩⟩ definitions

For matched-HCR persons in a Leiden group:

- **⟨v⟩**   = pool mean of log10(source_v) across all HCW by any matched-HCR person
               in that group. Weights toward prolific authors.
- **⟨⟨v⟩⟩** = per-person mean first (mean over a person's HCW), then mean across persons.
               Treats each matched HCR person equally.

Both yield one (x, y) coordinate pair per panel per Leiden group.

## Data join chain

```
hcr_hca_map.parquet         → cluster_hash  (unique + inst_resolved only)
  ↓  via hca_clusters.parquet (cluster_hash → author_idx)
author_idx set
  ↓  via hcw_authorships.parquet (author_idx → work_idx)
work_idx set
  ↓  filter enclave_hcw_{window}_{label}.parquet on work_idx
(work_idx, field_idx, source_v, mean_inst_v, mean_citer_v)
  + carry author_idx through so ⟨⟨v⟩⟩ can be computed per person
  + add leiden_group via _LEIDEN_GROUP dict (already in plot_enclave.py)
```

All four input files already exist in WORKING/.
Join is small enough for pandas (no DuckDB needed).

## Code changes — all in `enclaves/plot_enclave.py`

### 1. New function `load_hcr_works(working, window, label) → pd.DataFrame | None`

Returns one row per (author_idx, work_idx, field_idx) with columns:
  leiden_group, source_v, mean_inst_v, mean_citer_v

Steps:
- Load hcr_hca_map.parquet, filter match_status ∈ {unique, inst_resolved}, extract cluster_hash set
- Load hca_clusters.parquet, filter to those hashes, extract author_idx set
- Load hcw_authorships.parquet, filter to those author_idxes, extract work_idx set
- Load enclave_hcw_{window}_{label}.parquet, filter to those work_idxes
- Merge author_idx back in (via hcw_authorships join on work_idx)
- Add leiden_group via _LEIDEN_GROUP
- Return None gracefully if any upstream file is missing

### 2. New function `_draw_hcr_overlay(ax, hcr_sub, x_col)`

Given HCR-work rows for one Leiden group and one panel:
- Drop nulls/zeros on x_col and mean_citer_v
- **Pending density inspection**: choose between:
  - Option A (rich): individual hollow black circles per HCW point
    `scatter(lx, ly, s=12, facecolors='none', edgecolors='k', linewidths=0.6, zorder=6)`
  - Option B (clean): only summary markers ⟨v⟩ and ⟨⟨v⟩⟩
- Compute ⟨v⟩ (pool mean) → large filled black circle `(s=80, marker='o')`
- Compute ⟨⟨v⟩⟩ (person-weighted mean) → large black diamond `(marker='D')`
- Annotate with text labels "⟨v⟩" and "⟨⟨v⟩⟩" near the markers

### 3. Thread `hcr_works` into existing plot functions

```python
def plot_leiden_group(df, g, out_path, hcr_works=None):
    ...
    # after _draw_panels:
    if hcr_works is not None:
        hcr_sub = hcr_works[hcr_works['leiden_group'] == g]
        for ax, x_col in zip(axes, ['source_v', 'mean_inst_v']):
            _draw_hcr_overlay(ax, hcr_sub, x_col)

def plot_combined(df, out_path, hcr_works=None):
    ...
    # same pattern, no leiden_group filter
```

### 4. `main()` changes

```python
hcr_works = load_hcr_works(paths.working, args.window, args.label)
if hcr_works is not None:
    print(f'  {len(hcr_works):,} HCR-authored HCW rows  |  '
          f'{hcr_works["author_idx"].nunique()} distinct matched-HCR authors')

plot_combined(df, ..., hcr_works=hcr_works)
for g in range(1, 6):
    plot_leiden_group(df, g, ..., hcr_works=hcr_works)
```

## Decision pending after density inspection

Run `plot_enclave.py` first. Then decide:
- If the existing scatter is so dense that 1,000 extra hollow circles blend in
  → use only the ⟨v⟩ / ⟨⟨v⟩⟩ summary markers (Option B)
- If distinct regions are visible
  → add individual hollow circles AND summary markers (Option A)
