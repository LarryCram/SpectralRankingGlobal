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

# Style Exemplars: [PROJECT NAME]

These are excerpts of my own published or drafted writing. They are the
ground truth for style in this workspace. When drafting or revising, match
their rhythm, sentence architecture, hedging patterns, citation density,
and rhetorical sequencing. Reuse structure, not wording. Do not copy
distinctive phrases verbatim.

If these exemplars conflict with the global style card in `GEMINI.md`,
follow these exemplars.

---

## Exemplar 1: Introduction — Opening / Importance
**Move:** Establish broad importance of the topic.
**Notes:** Note the concrete opening, the absence of "In recent years",
and how the second sentence narrows without hedging.

The research system relies on journals and institutions. Journals filter
works through editorial and peer review, archive the literature, and
create metadata for retrieval and exploration. Institutions fund individual researchers,
implement training, oversee compliance, establish research organisational units
and maintain physical and information infrastructure. The influence hierarchy linking journals and institutions helps to shape the research system \citep{mertonSociologyScienceTheoretical1973, mcnameeStratificationScienceComparison1994, martinTiedKnowledgePower1998}, yet studies that treat journals and institutions symmetrically are infrequent. More commonly, the hierarchy of journals is established first, perhaps by spectral ranking, and the institutional hierarchy is derived from it by aggregation. It is therefore unknown whether institutional spectral rankings yield useful orderings, and how joint ranking changes rankings of either component. This paper proposes a cardinal spectral ranking of journals and institutions treated in parallel, revealing how the recursive exchange of references between research articles helps to form and maintain influence. 


We foreground our choice to view references and citations through the lens of Social Systems Citation Theory \citep[][SSCT]{tahamtanSocialSystemsCitation2022}. It concentrates on the structural features of the network of references and citations, setting aside authorial, editorial and managerial intent. This approach contrasts with actor-network theory \citep[][ANT]{latourReassemblingSocialIntroduction2007}, which foregrounds the intentful roles of heterogeneous agents including authors
in assembling citation networks. ANT will be important in our follow-up studies that relate this paper to the referencing activities of individual researchers, such as the `enclaves' featured in the Discussion. Note also that in the face of potential confusion around the words `reference' and `citation', we prefer `reference.' We say work $w_1$ references a work $w_2$ in its reference list and, if necessary, that $w_2$ is cited by $w_1$ \citep{woutersCitationCulture1999}.

The paper proceeds as follows. In the next section, we construct a framework for representing the steady-state distribution of attention or influence among journal and institution units coupled by the reference lists of the works they publish. Numerical methods for computing the resulting cardinal spectral ranking of influence are presented and their convergence discussed.  We then construct a corpus of works in the fields of economics and business, forming subsets of the corpus to illustrate the interconnections of units, subfields, and time. We present spectral rankings over these subsets, alongside sensitivity and bootstrap studies of some key parameters. The findings are applied to help explain ranking differences between the broad fields of economics and business, and to identify enclaves of highly cited work in low-ranking units.  The paper discusses these results including a critique of our methods, and concludes with some directions for future work.

**What to imitate:**
- Opens with a specific claim, not a platitude
- Sentence 1 is short; sentence 2 expands
- No citations in the first two sentences
- Ends the paragraph on a narrow, testable claim

---

## Exemplar 2: Introduction — Gap Statement
**Move:** Identify the specific gap after synthesizing prior work.
**Notes:** Note the "however" placement mid-paragraph, not at the start,
and the explicit naming of what is missing.

The introduction of the science citation index \citep{garfieldScienceCitationIndex1964} facilitated the exploration of connections between journals and institutions by `inverting' the reference list of each work back to a convenient index of citing works and their targets \citep{woutersCitationCulture1999}. Citation-based journal ranking immediately became a useful means to manage collections and to select outlets for publications. On the other hand, the rapid and widespread misuse of the journal impact factor (JIF) as proxy for the importance of research works exemplified tensions in bibliometric evaluation that remain unresolved \citep{garfieldCitationAnalysisLegitimate1979,bornmannCitationCountsResearch2008, leydesdorffCitationsIndicatorsQuality2016, macrobertsMismeasureScienceCitation2018}. Perhaps due to the complexity of institutions' disciplines and missions, an institution impact factor (IIF) has not emerged. Rather, institutional rankings derived from bibliometric indicators of works, authors or journals are often composed with other indicators to form an institutional score. This is the approach in global rankings such as QS, Times Higher Education (THE), Shanghai (ARWU), Leiden Ranking (CWTS) and SCImago Institutions Rankings, as well as many national performance-based research funding schemes \citep{zacharewiczPerformancebasedResearchFunding2019}. 

There are hundreds of algorithms for extracting information from a citation index. For example, \cite{pendleburyUseMisuseJournal2009} presents a critical survey of around a dozen journal rankings, while \cite {kanellosImpactbasedRankingScientific2019} present a critical comparative study of more than 30 methods for ranking articles over the work-work citation network. \cite{adlerCitationStatisticsReport2009} [see also \cite{lehmannCommentCitationStatistics2009}] offer cautionary advice about these algorithms in general. Their report comments on a `mystical belief in the magic of citation statistics' and reserves a special mention for the eigenvector methods studied in this paper: `Their proponents make claims about their efficacy that are unjustified by the analysis and difficult to assess. Because they are based on more complicated calculations, the (often hidden) assumptions behind them are not easy for most people to discern.' Mindful of this evaluation, we avoid claims of efficacy, find the calculations uncomplicated, and aim to reveal our assumptions and parameters.

**What to imitate:**
- Prior work acknowledged in one clause before the pivot
- Gap stated in one sentence, not three
- Uses "remains unclear" / "has not been tested" style phrasing
- Does not overstate the gap ("no one has ever...")

---

## Exemplar 3: Introduction — Aim / Contribution
**Move:** State the aim and preview the contribution.
**Notes:** Note the "we" voice, past tense for the aim, and the modest
framing of novelty.


The spectral methods explored in this paper can be traced to studies of recursive methods for computing centrality in social networks \citep{vignaSpectralRanking2016}, although these were 
not referenced by \cite{narinEvaluativeBibliometricsUse1976}, \cite{pinskiCitationInfluenceJournal1976}, and \cite{narinStructureBiomedicalLiterature1976} when they proposed a recursive method to construct influence weights of journals within a disciplinary field. To obtain influence weights for institutions, Narin et al. aggregated the institutional publications in a field weighted by the respective journal influence score. Over a sample of several dozen universities, the total institution influence scores were well-correlated with survey-based rankings, but also with the number of publications, suggesting that survey respondents combined influence and size. Narin's work developed into the eigenvector approach now widely used for journal ranking \citep{bollenJournalStatus2006, bergstromEigenfactorMetricsFigure2008, franceschetTenGoodReasons2010, vignaSpectralRanking2016}. 

Eigenvector methods have been also applied to author-level influence weightings in the field of social science by \cite{westAuthorlevelEigenfactorMetrics2013}. This work extended Narin's aggregation approach to ranking institutions and countries, and appended an exploration of spectral ranking over distinct kinds of entities that anticipates the approach taken here. An alternative approach iterates between the article-article network and the corresponding article-author network \citep{ujumBestBothWorlds2015, palCITEXNewCitation2015}. \cite{chenArticlesScientificPrestige2023} present an extensive comparison, within topic clusters, between (a) PageRank ranking over the article reference network and (b) the articles' simple citation count. The formulation by \cite{caoRankingAcademicInstitutions2023} considers the article-article and article-institution network, ranked by a novel and effective iteration over random walks. This is one of the few reported spectral rankings of institutions. The authors note that a limiting case of their approach corresponds to the eigenvector (PageRank) method, while \cite{gellerCitationInfluenceMethodology1978} noted earlier that random walks over Markov chains are closely related to the spectral ranking introduced by Narin et al. 


**What to imitate:**
- "We" for authorial action
- Aim is one sentence; contribution is one sentence
- Novelty claim is hedged ("to our knowledge")
- No promises of impact

---

## Exemplar 4: Discussion — Interpreting the Main Finding
**Move:** Restate and interpret the primary result.
**Notes:** Note the absence of statistics (they live in Results), the
comparison to prior work, and the two-sentence mechanism sketch.

> [PASTE YOUR PARAGRAPH HERE]

**What to imitate:**
- Finding stated in plain language
- Prior work engaged by name, not by citation dump
- Mechanism offered as possibility, not fact
- No "interesting" or "surprising" as evaluative crutches

---

## Exemplar 5: Discussion — Limitations
**Move:** State limitations specifically and without boilerplate.
**Notes:** Note that each limitation names a direction of bias, not just
the existence of a constraint.

> [PASTE YOUR PARAGRAPH HERE]

**What to imitate:**
- Each limitation says what it threatens, not just that it exists
- No "future work should address" without specifying how
- No apology; limitations stated as facts
- One limitation per sentence, no stacking

---

## Exemplar 6: Abstract
**Move:** Full abstract in my voice.
**Notes:** Note the six-move structure (context, gap, aim, result,
implication, close) and the word count.

> [PASTE YOUR ABSTRACT HERE]

**What to imitate:**
- Move structure, in order
- No citations
- Past tense for what was done, present for what it means
- Final sentence is a claim, not a summary

---

## Exemplar 7: Peer Review — Summary of Submission
*(Include only if you write reviews in this workspace.)*
**Move:** Neutral summary of the manuscript under review.

> [PASTE YOUR REVIEW EXCERPT HERE]

**What to imitate:**
- Neutral, descriptive tone
- Author's own framing paraphrased, not evaluated yet
- No recommendation language in the summary section

---

## Exemplar 8: Peer Review — Major Concern
**Move:** State a substantive concern constructively.

> [PASTE YOUR REVIEW EXCERPT HERE]

**What to imitate:**
- Concern stated as a question or a specific gap
- Points to the section where the issue lives
- Suggests a remedy, not just a criticism
- No sarcasm, no rhetorical questions

---

## Anti-Exemplars (What NOT to write)
These are excerpts of generic or AI-flavored academic prose. Do not write
in this register.

> "In recent years, there has been growing interest in X. It is important
> to note that X plays a crucial role in Y. Moreover, several studies have
> delved into this topic. However, despite these efforts, a comprehensive
> understanding remains elusive. In conclusion, this study sheds light on..."

**Why this is wrong:**
- Opener is a platitude
- "It is important to note" and "plays a crucial role" are banned
- "Delve" is banned
- "Moreover" at sentence start
- "Sheds light on" is a cliché
- "In conclusion" as an opener

---

## Usage Notes for the Agent
1. Before drafting any section, identify which exemplar above matches the
   rhetorical move you are writing.
2. Match its sentence length distribution, hedging density, and transition
   choices.
3. After drafting, compare your output side-by-side with the exemplar and
   revise to close any stylistic gap.
4. If no exemplar matches the move you need, say so and ask me for one
   before drafting.