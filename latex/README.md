# Intelligent Computing submission package

Revision audit (13 November 2026): the manuscript is now **Auditing Foundation Model Grounding in Design and Manufacturing**. The original archive is retained; the revised analysis separates export success, reference agreement and external geometric safety. Earlier claims of an isolated action-space effect, all-model 30/30 success and a harmless point-probe explanation for finite-cutter failures are withdrawn. The pre-revision package is preserved in `../build/submission_before_revision_20261113.zip`.

Built from the journal's LaTeX template (`template.txt`) and the author guidelines at
`https://spj.science.org/page/icomputing/for-authors`. Target: Research Article,
Special Issue *Foundation Models Beyond Text: Towards General Multimodal Intelligence*
(submission deadline 30 June 2027).

## Files

| File | Purpose |
|---|---|
| `main.tex` | Manuscript root: preamble, title, abstract, four main sections, statements |
| `sections/01-introduction.tex` | INTRODUCTION (includes related work; the journal allows only four Level-1 headings) |
| `sections/02-methods.tex` | MATERIALS AND METHODS |
| `sections/03-results.tex` | RESULTS AND DISCUSSION |
| `sections/04-conclusion.tex` | CONCLUSION (includes the required limitations paragraph) |
| `sections/05-backmatter.tex` | Acknowledgments, Author Contributions, Funding, Competing Interests, Data Availability |
| `refs.bib` | References (21 entries, well under the 60 limit) |
| `figures/fig1.pdf` ... `fig6.pdf` | Copies of `reports/figures/*.pdf`; refresh with `PYTHONPATH=src python scripts/figures.py --sync-latex` |
| `cover-letter.tex` | Cover letter, including special-issue and AI-use disclosures |
| `build.sh` | Compile both PDFs |

Compile with `bash build.sh` (Git Bash; TeXLive 2026 with `pdflatex`, `biber`, `latexmk` required).
Outputs `main.pdf` and `cover-letter.pdf`. Submit the whole `latex/` directory as the LaTeX
source, not just `main.tex`.

## Format compliance

| Journal rule | Where it is handled |
|---|---|
| Exactly four numbered main-body sections with the specified titles only | Four `\section` commands, one per section file |
| One column, line numbering | `main.tex` preamble (`\linenumbers`, no `\twocolumn`) |
| Title <= 100 characters, no abbreviations, title case | 63 characters, "Auditing Foundation Model Grounding in Design and Manufacturing" |
| Abstract <= 250 words, single paragraph, plain language, no citations or undefined abbreviations | `main.tex`; acronyms expanded, no `\cite`, no figures/tables |
| <= 10 figures and/or tables | 6 figures + 4 tables = 10 |
| Every figure/table cited in numerical order | Fig 1 in Methods, Figs 2-6 in Results; Tables 1-4 in Results |
| Figure legends start with a short title, < 200 words | All six captions |
| No vertical rules in tables | `booktabs` only |
| <= 60 references, full author list, DOI where available, numbered by first citation | `refs.bib`; `maxnames=99, minnames=99`; `sorting=none` |
| Author Contributions, Funding, Competing Interests, Data Availability | `sections/05-backmatter.tex` |
| AI use disclosed in cover letter and Acknowledgments | Both files, independently worded |
| Cover letter states special-issue intent | First paragraph of `cover-letter.tex` |
| Cover letter: significance, novelty, prior-publication statement, ORCID, corresponding author | `cover-letter.tex` |
| Subheadings either all phrases or all sentences, < 10 words, no full stop | All phrase-form throughout |
| Limitations paragraph in Conclusion | Penultimate group of paragraphs in `04-conclusion.tex` |
| No claims of priority | Checked in Conclusion |

The template requirements recorded here include `Experimental Design`, `Statistical
Analysis` and a human/animal research statement when people took part. All are present.
The revision reports exploratory paired bootstrap intervals, not confirmatory hypothesis
tests or model repeated-sampling uncertainty. Final author-guideline compliance and ethics
wording still require author confirmation.

## Replace before submission

Every bracketed placeholder must be filled by the authors:

1. `main.tex`: author names, affiliation, corresponding-author email.
2. `05-backmatter.tex`: general acknowledgments, author contributions (initial + full surname),
   funding statement or the required no-funding sentence, repository URL, Zenodo DOI.
3. `05-backmatter.tex`: confirm the informed-consent wording in `Human and Animal Research`
   in `02-methods.tex` before deleting that bracketed note.
4. `cover-letter.tex`: date of submission, author names, emails, ORCID IDs, corresponding
   author, potential referees, and any related papers in press or under consideration
   (journal checklist).
5. `refs.bib`: check all 20 references against primary sources before submission; do not
   rely on the previous blanket verification claim. This revision corrected ABC from
   CVPR Workshops to CVPR and pages 9601--9609 to 9601--9611 using the CVF record.
   `SolidCraft2026` is a software citation; confirm the journal's software-reference style.
   Confirm the expanded AI-assistance disclosure, including code and analysis assistance,
   in both the manuscript and cover letter; author review has not been assumed.
6. Guest-editor and deadline details if the special issue page changes.
7. Current APC and the relationship between the two proposals (no duplicate publication),
   both still open external conditions. The deadline is confirmed as 30 June 2027.

## Content discipline (do not reintroduce)

- No Siemens NX, no commercial CAD/CAM backend, and no apology about a missing backend.
  The user's decision is that this option simply does not exist in this work.
- "Zero gouging" is scoped everywhere to a single-valued grid height field with one shared
  discrete disc. Never write continuous-geometry zero gouging.
- Pass counts are written as `k/n`. Keep export counts, reference-agreement counts and
  internal/external safety outcomes separate. Third-party point rates have correlated
  samples; also report part-level failures and equal-part means. Nominal Wilson intervals
  do not establish population reliability for curated parts or repeated stock variants.
- Autonomy is *relocated*, not reduced. The model does not write low-level control codes;
  the system generates all G-code automatically and without human intervention.
- No physical cutting trials are claimed. Verification is simulation-based and cross-checked
  by two independent open-source kernels.
- The human study is a sanity check only, because a colour legend made the binary task trivial.

## English style

The prose was written to avoid the usual generated-text register: no "delve", "leverage",
"robust" as filler, "pivotal", "furthermore", "moreover", "paradigm shift", "pave the way",
"shed light", "seamless", "comprehensive", no three-part parallel triads, no "It is worth
noting that", and no empty concluding sentences. Claims are tied to specific numbers, and
negative results are stated as results rather than smoothed away. Sentence length is
deliberately uneven. A final language edit by a native scientific editor is still advisable.
