# Documentation overview

All project documents of the InnoBeweegLab reporting prototype, what each one contains, and how
current it is (4 November 2026). Documents in this repository are linked; documents kept outside
it are listed at the end.

Research data never enters this repository (NFR7). That includes the original Oss report, which
is confidential: the documents describe its method and structure and report aggregate scores
against it, but quote none of its text.

**Status:** *current* = matches the code and the plan; *partly current* = still right, but
later changes are recorded elsewhere; *earlier version* = kept to show how the design evolved.

## Suggested reading order

1. [Background research](research/InnoBeweegLab_BackgroundResearchReport_LaTeX_v13/) (why)
2. [Analysis artifacts](analysis/InnoBeweegLab_AnalysisArtifacts_v2.md) (what the tool must do)
3. [Design](design/reporting-tool-design.md), [report structure](report-structure.md) and the
   [diagrams](diagrams/) (how it is built)
4. [Hypotheses and thresholds](experiments/hypotheses-and-thresholds.md) and the
   [week-1 pilot](experiments/week1-pilot.md) (how it is tested)

## 1. Research foundation

| Document | Contains | Date | Status |
|---|---|---|---|
| [Background research report v13](research/InnoBeweegLab_BackgroundResearchReport_LaTeX_v13/) (LaTeX; PDF built locally) | 27+ approaches to traceable AI-generated text, factuality evaluation, multi-agent and extraction pipelines, human verification interfaces, evaluation methodology, the stakeholder survey, how a Wijkscan report is made today, reading maps and charts, separating computation from generation (PAL, Program of Thoughts) | 4 Nov | current |
| [Background research report v12](research/InnoBeweegLab_BackgroundResearchReport_LaTeX_v12/) | the submitted version | 25 Oct | submitted, kept unchanged |
| [Image interpretation](research/image-interpretation.md) | which images the input contains, what the literature says about reading them, and the chosen method: maps measured in code, infographics read by a vision model and confirmed by a researcher | 21 Oct | current |

## 2. Requirements and analysis

| Document | Contains | Date | Status |
|---|---|---|---|
| [Analysis artifacts v2.1](analysis/InnoBeweegLab_AnalysisArtifacts_v2.md) (also [PDF](analysis/InnoBeweegLab_AnalysisArtifacts_v2.pdf) and [LaTeX](analysis/InnoBeweegLab_AnalysisArtifacts_LaTeX_v2/)) | 4 personas, 3 scenarios, use cases UC1–UC9 with a use-case diagram, user stories US1–US14, requirements FR1–FR21 and NFR1–NFR12 with implementation status, the traceability workflow, test cases TC1–TC22, the domain model (class diagram), review notes | 4 Nov | current |

## 3. Architecture and design

| Document | Contains | Date | Status |
|---|---|---|---|
| [Reporting tool design](design/reporting-tool-design.md) | inputs, the pipeline from source cell to approved report, design decisions D1–D36 with their reasons, verifier checks, review and export, experiment set-up, open questions for InnoBeweegLab, current status and what is still to build | 4 Nov | current (§13 has the latest state) |
| [Report structure](report-structure.md) | the chapters of a Wijkscan report and where each one's content comes from, figures, traceability in the document, full reports per model, how a generated report is compared with the original | 25 Oct | current |
| [Design diagrams](diagrams/) (Mermaid sources, SVG and PNG; [all on one page](diagrams/all-diagrams.md)) | research design, experiment design and validation; C4 context, containers, components and deployment; every architecture under study; UML of all the code: packages, class diagrams per package (generated from `src/`, all 69 modules), sequences for ingest, experiment run, review and export, images, state machines, the verifier, the traceability chain | 4 Nov | current |

## 4. Experiments and evaluation

| Document | Contains | Date | Status |
|---|---|---|---|
| [Hypotheses and thresholds](experiments/hypotheses-and-thresholds.md) | which runs count, the measures, when a difference counts, hypotheses H1–H9, feasibility thresholds F1–F5, check V1, the Copilot baseline C0, limitations stated in advance, decisions still open before the freeze | 4 Nov | current, before the freeze |
| [Week-1 pilot](experiments/week1-pilot.md) | set-up, two rounds of pilot results, findings P1–P8, the fixes, the run budget | 4 Nov | current |
| [`config/experiments.toml`](../config/experiments.toml) | the experiment conditions as they run | 4 Nov | current |

## 5. Design held in configuration and tests

| File | Holds |
|---|---|
| [`config/models.toml`](../config/models.toml) | the models compared, their context windows, and the memory limit (60% of 16 GB) |
| [`config/report_structure.toml`](../config/report_structure.toml) | chapter order, the place of every map and chart, the charts drawn from survey data |
| [`config/survey_mapping.toml`](../config/survey_mapping.toml) | which survey question belongs to which theme (an interpretation, open to the researchers) |
| [`config/images.toml`](../config/images.toml) | how each map and infographic is read |
| [`config/reference/`](../config/reference/) | writing rules, RIVM rules, cross-theme terms |
| [`prompts/`](../prompts/) | the prompts, versioned (v1 kept unchanged, v2 in use) |
| [`tests/`](../tests/) | 107 tests on synthetic data, linked to test cases TC1–TC22 |

## 6. Outside this repository

Kept in the author's project folder or as private Claude artifacts; available on request.

| Document | Contains | Date | Status |
|---|---|---|---|
| Project plan (Word and PDF) | context, problem, research questions, method, deliverables, risks, planning | 14–22 Sep | current for scope and research questions; the experiment plan has moved on (section 4 above) |
| Requirements Engineering (Word) | versions 1 and 1.1 of the analysis artifacts | 29 Sep – 9 Oct | earlier version |
| GenAI cost comparison and business case; the product owner's cost and GDPR response | hosted and local model options, costs, data residency | 14–18 Sep | background; prices not re-checked since |
| Stakeholder survey (questionnaire and export) and first-meeting questions | field data analysed in the background research | 8–29 Sep | current |
| Architecture diagrams (PNG, SVG, editable DOT) | stakeholder overview, technical architecture, traceability and review flow, experiment roadmap | 23 Oct | earlier version: replaced by [the diagrams folder](diagrams/) |
| Reporting pipeline architecture (PNG) | the pipeline as planned early October | 2 Oct | earlier version |
| Standup deck and Standup Q&A (Claude artifacts) | the chosen architecture against the alternatives; code-architecture diagrams (package layers, traceability chain, map measurement, evaluation design); model choice | 26–27 Oct | partly current: they use the old letters A–D; the diagrams folder has the current versions |
| Evidence Engine Architecture, System Design (data flow and class diagrams), Experiment Design, Evaluation Approach (Claude artifacts) | first designs and evaluation plans | 10 Sep – 2 Oct | earlier versions |

## 7. Known gaps

- **The review interface is not documented** outside the code (`src/ui/streamlit_app.py`, seven
  tabs). Needed: annotated screenshots, the design reasons linked to the HCI literature in the
  background research, and the usability test plan (H6).
- **Diagrams in the analysis artifacts** (use-case diagram, domain model) are from the analysis
  phase; the [diagrams folder](diagrams/) shows the code as it is. Where they differ, the diagrams
  folder is current.
