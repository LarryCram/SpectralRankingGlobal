# LaTeX Manuscripts & Bibliographies

This directory contains the academic manuscripts, bibliography styles, and references for the SpectralRankingGlobal project.

For instructions on setting up and compiling these documents on a Windows workstation (including Zotero and Better BibTeX integration), see [WINDOWS_LATEX_SETUP.md](file:///home/lc/Projects_Antigravity/SpectralRankingGlobal/WINDOWS_LATEX_SETUP.md) in the project root.

## Directory Structure

- `spbasic.bst`: Shared Springer BibTeX style used by all manuscripts (`\bibliographystyle{../spbasic}`).
- `MyLibrary.bib`: Master bibliography file synchronized live from Zotero via Better BibTeX (gitignored).
- `NewERA/`: Active manuscript on national research assessment across Australian universities.
  - `main.tex`: Root document.
  - `sections/`: Modular document sections (01 to 05).
  - `tables/`: Pre-rendered publication tables (tab1 to tab4).
  - `figures/`: Pre-rendered publication vector figures (fig2 to fig5).
  - `references.bib`: Paper-specific citation entries.
- `enclaves/`: Manuscript on citation enclaves in low-ranked venues.
- `PrestigeEsteem/`: Manuscript on prestige vs. esteem dynamics.
