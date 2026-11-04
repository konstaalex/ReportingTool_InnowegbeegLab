# Design diagrams

One current set of diagrams for the research, the tool and the code (4 November 2026). Every
diagram is a Mermaid text file in [`src/`](src/), rendered to SVG (for reading, zoomable) and PNG
(at 2×, for the thesis) in [`out/`](out/).

- **C4** for the tool as a system: context, containers, components, deployment.
- **UML** for the code: package, class, sequence, state machine, activity and object diagrams.
- Flowcharts for the research design and the architectures under study.

The code diagrams 31–39 are generated from `src/` by [`tools/gen_code_diagrams.py`](tools/gen_code_diagrams.py),
so they show all the code: 69 modules, 54 classes, 205 methods and 303 module functions, checked by
the script on every run. The other diagrams are drawn by hand from the code, the design document
and the experiment plan, and were checked against the code on 4 November.

No research data is shown. Diagram 46 uses made-up values to show the formats.

## The diagrams

### Research

| # | Diagram | Kind | Shows |
|---|---|---|---|
| 01 | [Research design](out/01-research-design.svg) | flowchart | main question, the DOT phases (Library, Field, Design, Lab SQ3/SQ4, Workshop, Showroom), what each delivers, status |
| 02 | [Experiment design](out/02-experiment-design.svg) | flowchart | week-1 pilot, week-2 build and check V1, scorer check, freeze on 13 Nov, stages 1–3 (architecture, selection, models), feasibility and references, user sessions, results on 11 Dec |
| 03 | [Validation framework](out/03-validation-framework.svg) | flowchart | inputs, measures computed in code (U, S, time, memory), validity checks (scorer κ, V1), statistics, outcome per comparison, conclusions per sub-question |

### The tool as a system (C4)

| # | Diagram | Kind | Shows |
|---|---|---|---|
| 10 | [System context](out/10-c4-context.svg) | C4 level 1 | four researcher roles, the tool, the data files, Ollama, the optional hosted API, the original report (scoring only), the municipality, Copilot (baseline C0) |
| 11 | [Containers](out/11-c4-containers.svg) | C4 level 2 | Streamlit UI, command-line tools, core library, workspace files, config and prompts; Ollama, hosted API, LibreOffice, original report |
| 12 | [Components](out/12-c4-components.svg) | C4 level 3 | the components of the core library (one per package) and their main dependencies |
| 13 | [Deployment](out/13-deployment.svg) | deployment | the development laptop, the target office laptop (F5), GitHub, the Microsoft 365 tenant (Copilot, Azure OpenAI in the EU) |

### Architectures under study (SQ3)

| # | Diagram | Kind | Shows |
|---|---|---|---|
| 20 | [Overview](out/20-architectures-overview.svg) | flowchart | every condition (E0–E7, C0) side by side, built or to build, all scored the same way |
| 21 | [Evidence layer (H1)](out/21-h1-evidence-layer.svg) | flowchart | E1 (evidence computed in code) against E1R (raw records), same model and prompt shape |
| 22 | [E2 staged pipeline](out/22-e2-staged-sequence.svg) | UML sequence | per-perspective findings, synthesis, privacy check, verifier |
| 23 | [E4 checker](out/23-e4-checker.svg) | flowchart | the staged pipeline with a checker step (design decision still open) |
| 24 | [E3 selection](out/24-e3-selection.svg) | flowchart | all, top-25, BM25, vector, hybrid selection before drafting |
| 25 | [E6 and C0](out/25-e6-c0.svg) | flowchart | the hosted model run and the Microsoft 365 Copilot baseline |

### The code (UML)

| # | Diagram | Kind | Shows |
|---|---|---|---|
| 30 | [Packages](out/30-code-packages.svg) | package | all 13 packages, entry points, imports (module-level and lazy) |
| 31 | [ingestion](out/31-code-ingestion.svg) | class (generated) | folders and files → SourceDocument, SourceRecord, SourceStore; all nine parsers |
| 32 | [evidence, drafting](out/32-code-evidence-drafting.svg) | class (generated) | Evidence, EvidencePack, the builder; PipelineConfig, the pipelines, DraftClaim, SectionDraft, StageLog |
| 33 | [llm](out/33-code-llm.svg) | class (generated) | LLMProvider, the providers, registry, privacy guard, MemoryMonitor |
| 34 | [verification, review](out/34-code-verification-review.svg) | class (generated) | Flag and the checks; ReviewSession, ReviewEvent, ClaimState |
| 35 | [imaging](out/35-code-imaging.svg) | class (generated) | map measurement, vision reading, confirmation log, scoring |
| 36 | [export](out/36-code-export.svg) | class (generated) | Markdown and evidence map, ReportContent, WijkscanRenderer, charts |
| 37 | [reports, experiments](out/37-code-reports-experiments.svg) | class (generated) | report runs, evaluation against the original, judge, experiment runner, provenance |
| 38 | [app, ui](out/38-code-app-ui.svg) | class (generated) | Workspace (the facade every entry point uses) and the Streamlit app |
| 39 | [evaluation](out/39-code-evaluation.svg) | class (generated) | the standalone gold-set harness (synthetic data) |
| 40 | [Ingest and evidence](out/40-seq-ingest-evidence.svg) | sequence | UC1: a Wijkscan folder to source records, then to evidence packs |
| 41 | [Experiment run](out/41-seq-experiment-run.svg) | sequence | UC9, SQ3/SQ4: one experiment, per seed a full report with memory measurement, then evaluation |
| 42 | [Review and export](out/42-seq-review-export.svg) | sequence | UC2–UC4, UC6: draft, verify, review (with override), export gate, Word report |
| 43 | [Images](out/43-seq-images.svg) | sequence | UC7: maps measured in code, infographics read by a model, readings confirmed |
| 44 | [State machines](out/44-state-claim-and-reading.svg) | state machine | a claim under review; an image reading under confirmation |
| 45 | [Verifier](out/45-activity-verifier.svg) | activity | every check of verify_claim, how the status follows, the section checks |
| 46 | [Traceability](out/46-object-traceability.svg) | object | one approved claim traced through draft, evidence and source record to a cell |

## How to read the code diagrams

- A module is a namespace (box with the module path); namespaces nest by package, so
  `ingestion.parsers.notes_parser` sits inside `ingestion › parsers`.
- A module's functions and constants are in a box with the stereotype «module».
  Classes carry «dataclass», «exception» or «protocol» where that applies.
- `+` public, `-` private (leading underscore); underlined = class or static method.
- Lines: inheritance (hollow triangle); composition (filled diamond, a field holding a list or
  dict of the other class); association (arrow with multiplicity, a field holding one);
  dependency (dashed arrow, the code uses that class, or that module's functions).
- Grey dashed boxes belong to another diagram; they show where a dependency goes.
- Colours in the hand-drawn diagrams: green = built, amber = to build, grey = later or outside.

## Updating

Requires Node and the Mermaid command line, version 11 (`npm install -g @mermaid-js/mermaid-cli`), which
brings the ELK layout the diagrams use. The set was rendered with mmdc 11.16.0.

```bash
cd docs/diagrams
python3 tools/gen_code_diagrams.py ../../src   # regenerate 31–39 from the code
./render.sh                                     # render everything to out/
./render.sh src/41-seq-experiment-run.mmd       # or one diagram
```

The generator prints what it covered and stops with an error when a new package is in no diagram.
After a change to the code, regenerate 31–39 and check the hand-drawn diagrams 30 and 40–46
against the change.
