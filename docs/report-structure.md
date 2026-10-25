# The Wijkscan report: structure, sources, and evaluation

This document describes the report the tool produces: what each part of an InnoBeweegLab
Wijkscan report contains, where the tool gets that content, and who is responsible for it
(the data, a model, or the researcher). It also covers how full reports from the three
selected models are made, and how a generated report is compared with the original
human-written report.

The structure follows InnoBeweegLab's Wijkscan report as delivered for Euterpepark Oss
(June 2026). Only the structure was taken from that report: chapter order, where each figure
sits, the pattern within a theme, and the appendices. The report itself is confidential
("NIET OPENBAAR"). None of its findings or prose are in this repository, and it is never an
input for drafting. The structure is encoded in `config/report_structure.toml`. The renderer
(`src/export/docx_report.py`) builds the Word file from that configuration.

## 1. Layout (house style)

| Element | In the report | In the tool |
|---|---|---|
| Page | A4 landscape | same |
| Colours | orange `#FE5C01`, black, greys | `[house] accent`, `text`, `grey` |
| Fonts | Trebuchet MS headings, Arial body text | `heading_font`, `body_font`; charts in `chart_font` |
| Chapter headings | capitals, underlined, each chapter on a new page | Heading 1 style (in the table of contents) |
| Theme headings | `Thema n - <naam>` in orange | Heading 2 |
| Captions | `Afbeelding n: …` for maps, photos and infographics, and `Figuur n` for charts, each numbered through the whole report | the same two counters, with the caption below the figure |
| Cover | orange field, white curve, round photo, title, organisation and municipality | `_cover`: photo only when a cover photo exists, and logos only when the files are configured |
| Colofon / contact | authors, design, contact details, copyright | placeholders from `[house]`, plus "Over deze versie" (the model, pipeline and evidence version used) |
| Table of contents | Word TOC | TOC field; Word fills in the page numbers when the file is opened |
| Footer | page number | page number + organisation; `CONCEPT` header for unreviewed versions |

## 2. Chapters and where their content comes from

"Generated" means that a model or the template pipeline drafts claims from evidence, and that
every claim cites evidence ids. "Deterministic" means the text is assembled from the case
data without a model. "Researcher" means the tool leaves a marked place for the researcher
to fill.

| # | Chapter | What it contains in the report | Source in the tool | Responsible |
|---|---|---|---|---|
| – | Cover, colofon, contents | title, area, organisation, contact | `[house]`, `[case."<id>"]` | configuration |
| 1 | **Samenvatting** | the main conclusions across themes | approved `conclusie` claims of section *Conclusies*; falls back to the per-theme conclusions | generated → researcher approves |
| 2 | **Aanleiding** | project context, assignment, goal of the scan | section *Aanleiding*: researcher notes of type *Projectcontext* | notes → generated → approved (chapter left out when empty) |
| 3 | **Methode** | the six themes; the three perspectives; selection of neighbourhoods; the RIVM rules and which maps test them; expert walk; resident survey and meeting | `methode_parts()` (deterministic, from the case's own files); the rule box; the study-area figure (overlay of the map measurement); optional house figures (BVO model, perspectives) via `[house] method_figures` | deterministic |
| 4 | **Resultaten** – introduction | reading guide, demographics of the area, where the expert results are (sunburst) | section *Algemeen* (demographics, respondents) + two full-page infographics (`image:demografie_1/2`) | generated + embedded images |
| 4.1–4.6 | **Thema 1–6** | per theme: data perspective (maps, RIVM rules), then experts, then residents and stakeholders, then a synthesis; maps, charts and photos in the text | theme sections; figures per `[[theme]]` | generated; figures deterministic |
| 5 | **Conclusies** | overall conclusion, then one paragraph per theme ("Binnen het thema **…**:") | section *Conclusies* (cross-theme evidence pack) + `conclusie` claims of each theme | generated → approved |
| 6 | **Aanbevelingen** | general approaches (co-creation, temporary facilities, nudging and wayfinding), then concrete measures per theme with inspiration pictures | skeleton with the headings from the configuration; approved `aanbeveling` claims are placed under their theme | **researcher** (by design; the brief keeps recommendations human) |
| 7 | **Bronnenlijst** | RIVM rules, data sources, literature | RIVM reference, `[image_sources]`, literature notes, list of input files | deterministic + notes |
| B1 | **Bijlage 1** | one page per map with the definitions or rules it tests | maps from `bijlage1.maps`; rule texts; the tool's own measurement, marked as indicative, with its evidence id | embedded + deterministic |
| B2 | **Bijlage 2** | the sunburst of the expert scores | `image:sunburst` + number of indicators | embedded |
| B3 | **Bijlage 3 · Herleidbaarheid** | *(added by the tool)* | every claim → evidence ids → source file, sheet and cell | deterministic |

### 2.1 The pattern inside a theme

The original report writes each theme in the same order. The `staged` pipeline follows
that order, and so does the template baseline:

1. **Data perspective**: what the maps show and whether the RIVM rule is met. Example: the
   share of homes within 200 m of a play area for children up to 12, per neighbourhood. The
   tool measures this deterministically on the map (`D-…` evidence).
2. **Expert perspective**: averages and notable statements from the expert walk, plus the
   experts' remarks (`X-…`).
3. **Resident perspective**: survey scores and choices per respondent group, plus coded
   needs, barriers and positives of residents and stakeholders (`B-…`).
4. **Synthesis**: where the perspectives agree or differ (`C-…` comparisons). The
   researcher's own observations, meeting notes and literature come in through notes (`O-…`).

The first map of a theme sits next to the first paragraph. Charts follow the resident
paragraph. Photos come after the second paragraph, three in a row, as circles with the note
as caption.

### 2.2 Figures

| Kind | Example | Where it comes from | Traceable through |
|---|---|---|---|
| Map in a theme | Sport- en speelkaart, Basisbehoeftekaart | input image (`config/images.toml`), placed as is | the image record; the measurements on it are `D-…` evidence |
| Study-area figure | neighbourhoods on the base map | the tool's measurement overlay | the measurement record |
| Chart (`Figuur n`) | "Zijn er genoeg sportplekken voor iedere leeftijdsgroep?" | **drawn by the tool** from survey evidence (`[[chart]]`): one bar per answer and respondent group, values printed on the bars | the `B-…` evidence ids of the bars (in the caption and in Bijlage 3) |
| Infographic | two demographics pages | input image, full page | readings by a vision model only after confirmation (`imaging`) |
| Photo | a playground, a path | researcher notes with a `Foto` column; the first *Projectcontext* photo becomes the cover | the note (`O-…`) |
| House figure | BVO model, perspectives | `[house] method_figures`, when supplied | – |
| Inspiration picture | product or project photos in the recommendations | not made by the tool | – |

The four charts of the Oss report are configured in `[[chart]]`. Drawn from the Oss survey
data, the tool's values were identical to the published figures. Adding a chart takes one
`[[chart]]` block: the survey question id (`config/survey_mapping.toml`), the answer options
(matched on the start of the answer text), `count` or `pct`, and the theme it belongs to in
`[[theme]].figures`.

### 2.3 Traceability in the document

- Every generated sentence carries its evidence ids in superscript, coloured by the
  verifier: grey when the claim is grounded, orange when it is weakly grounded, and red when
  it is unsupported.
- Bijlage 3 lists every claim with its evidence and the source file, sheet and cell.
- A reviewed export (tab 4) contains only approved or edited claims, and is blocked while
  claims are unreviewed. A preview or a model run is marked `CONCEPT`.

## 3. Full reports per model

The three drafting models compared in SQ3 are configured in `config/models.toml`:

```toml
[selection]
drafting = ["local_qwen7b", "local_qwen3_8b", "local_llama8b"]   # qwen2.5:7b, qwen3:8b, llama3.1:8b
```

These three all fit the 16 GB target laptop. Change the list to compare other models. A
*run* is one model drafting every section of a case (6 themes, Algemeen, Conclusies,
Aanleiding) with the same evidence, prompts (v2) and pipeline. The run is verified and
rendered as a complete Word report. Runs are stored under `workspace/reports/<case>/<run>/`,
separate from the review sessions. A researcher can make one run the starting point of
the review ("adopt").

| | Command line | Interface |
|---|---|---|
| check the models | `python -m reports.cli models` | tab 5, table of the three models |
| one report | `python -m reports.cli generate --case <case> --model local_qwen7b [--pdf]` | tab 5 › Rapporten maken |
| baseline without a model | `--model template` | "+ template" |
| all three + baseline, compared | `python -m reports.cli compare --case <case> --generate --include-template` | tab 5 › Vergelijk de laatste run per model |
| use a run for review | `python -m reports.cli adopt --case <case> --run <id> --reviewer <name>` | tab 5 › Gebruik als concept voor beoordeling |

Before a run starts, the tool checks that Ollama is running and that the model is
downloaded. It then fails with a clear message, instead of producing a report with nine
failed sections. In tab 2, the model picker shows only the three selected models unless
"Alle modellen tonen" is ticked.

## 4. Comparing a generated report with the original

`reports/evaluate.py` measures how closely a generated report follows the original. The
original PDF is found through the case folder recorded at ingestion (the document has status
`excluded_reference_report`). It is used for evaluation only, and its text never reaches a
prompt.

### 4.1 Reading the original

`load_reference` reads the PDF with pdfplumber and takes the layout from the file itself
(`style_profile`). Text in the body size is body text. Text in the heading font is a heading,
and it sets the chapter or theme. Smaller italic text is a caption. Smaller upright text
(chart labels) is skipped, and so are the table of contents, headers and footers. Chart
titles are taken from the bold text above each `Figuur n` caption. The same reader works on
the tool's own Word output once it is converted to PDF.

### 4.2 Measures

| Measure | What it says | How |
|---|---|---|
| Structure | which chapters of the original the generated report has | per chapter; *Aanbevelingen* is listed as "not generated by design" |
| Coverage | how much of the original's content is in the generated report | every sentence of ≥ 6 words of the original (outside the recommendations) is matched with the generated sentences of the same chapter: covered / partial / missing; coverage = (covered + ½ partial) / all |
| Ceiling | how much of the original the data can support at all | the same match against **all** evidence of the case. Statements from field knowledge, the residents' meeting or literature (unless recorded as notes) are not supportable. This does not depend on the model. |
| Coverage of what the data supports | the fair score for a model | coverage among the supportable statements |
| Numbers | numbers of the original found back; matched statements with *different* numbers | numbers in the statement and in the matched sentence; possible conflicts are listed for checking |
| Additions | generated sentences without a counterpart in that chapter of the original | not errors (the verifier checks grounding), but a different emphasis |
| Figures | maps by name, charts by title, photos and method figures by count | captions of the original vs. the figure list of the run; inspiration pictures in the recommendations are reported separately |
| Verifier (per run) | share of claims grounded / weak / unsupported | from the run itself |

The comparison table (`reports/<case>/comparison/comparison.md`) puts these measures next
to each other for the template baseline and the three models.

### 4.3 Matching, and its reliability

- **Word overlap (default, deterministic).** A statement is scored on the content words it
  shares with the candidate sentence: a weighted recall, where rare words count more (IDF
  over the original and the evidence). Shared numbers are scored too. Dutch stop words are
  removed and words are lightly stemmed. Thresholds: covered ≥ 0.32, partial ≥ 0.26.
- **LLM judge (optional, `--judge <local model>`).** Word overlap finds up to ten
  candidate sentences. A local model then decides covered / partial / missing and flags
  number conflicts. Hosted judge models are refused, because the original is confidential.
  Answers are cached per case and judge model, so a rerun gives the same labels and the
  evidence judgements are made only once per case.
- **Calibration.** Each evaluation writes `alignment.csv`: one row per statement of the
  original, with the automatic labels and the best-matching sentence and evidence. Fill in
  `human_label` (is it in the generated report?) and/or `human_evidence_label` (does the
  data support it?). Then `python -m reports.cli calibrate --csv …` reports agreement,
  Cohen's kappa, the confusion matrix, and the thresholds that would agree best.
- **What is known so far.** On 89 statements from the results chapters of the Oss report,
  labelled by the developer, word overlap agreed with the labels at κ ≈ 0.38 (fair). The
  aggregate number of supportable statements was close to the labelled number, but single
  labels are unreliable for paraphrases. Treat the overlap scores as an approximation for
  comparing models, use the judge for the thesis numbers, and calibrate both with a
  researcher's labels (ideally two raters, see background research §3.7).
- **Sanity check.** A report compared with its own PDF scores above 90% on its generated
  text (asserted in `tests/test_reports.py`). What remains unmatched is fixed renderer text.

### 4.4 Privacy

The evaluation outputs quote the original, so they are written to the workspace, which is
git-ignored. They are never written to the repository. The judge sends one statement of the
original and a few candidate sentences to a **local** model only. The test suite uses
synthetic data only.

## 5. Customising the structure

| To change | Edit |
|---|---|
| chapter order, titles, optional chapters | `[[chapter]]` in `config/report_structure.toml` |
| figures per theme and their order | `[[theme]].figures` (`image:<id>`, `chart:<id>`, `photos`) |
| a new chart | a `[[chart]]` block (question id, options, measure) and a reference in `[[theme]]` |
| names on the cover and in titles | `[case."<case id>"]` (`city`, `area`, `focus`, `municipality`) |
| logos, cover photo, house figures, contact details | `[house]` |
| map definitions in Bijlage 1 | `[definitions]` (InnoBeweegLab's own wording) |
| data sources of images | `[image_sources]` |
| the models compared | `[selection]` in `config/models.toml` |
