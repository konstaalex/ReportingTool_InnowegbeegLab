# ReportingTool InnoBeweegLab — Buurtscan reporting prototype

Research prototype for the InnoBeweegLab master's project: heterogeneous Buurtscan
data (expert walk, resident questionnaire, demographics, RIVM rules) → traceable,
reviewed draft report. Design and decisions: `docs/design/reporting-tool-design.md`.

```
source cell ─► SourceRecord ─► Evidence item (deterministic) ─► drafted claim (LLM)
            ─► verifier flags ─► researcher review (hash-chained log) ─► gated export + evidence map
```

## Quick start (Buurtscan data, Oct 2026 set)

The research data is NOT in this repository and must never be added to it (NFR7).
Point the tool at wherever the data lives on your machine:

```bash
export DATA="$HOME/Desktop/AppliedIT_Masters/Data-Evidence_prj/newDataSender"   # adjust to your machine

python3.11 -m venv .venv && source .venv/bin/activate      # Python >= 3.11
pip install -r requirements.txt
export PYTHONPATH=src

# 1. ingest (dedups the '[n]' copies, cross-checks survey counts with the sheet's own totals)
python -m ingestion.cli ingest-buurtscan "$DATA" --out workspace/ingest

# 2. models (Ollama app must be running)
ollama pull qwen2.5:7b
python -m llm.cli check                                       # which configured models are downloaded
python -m llm.cli smoke local_qwen7b                          # one tiny JSON call: works? how fast?

# 3. experiments (run records in workspace/runs/)
python -m experiments.cli run SMOKE --workspace workspace     # fake model: plumbing only
python -m experiments.cli run E0    --workspace workspace     # template baseline, no model
python -m experiments.cli run E1 --workspace workspace --section Sporten --seed 42
python -m experiments.cli summary --workspace workspace

# 3b. real Oss case (Wijkscan layout) + images: maps measured without a model;
#     infographics read by a local vision model (ollama pull qwen2.5vl:7b) and confirmed in the UI
python -m ingestion.cli ingest-wijkscan "$HOME/Desktop/AppliedIT_Masters/Data-Evidence_prj/Perspectieven Oss" \
    --out workspace --case-id euterpepark-oss --label "Euterpepark Oss"
python -m imaging.cli analyse --workspace workspace --case euterpepark-oss                      # maps only
python -m imaging.cli analyse --workspace workspace --case euterpepark-oss --vision-model vision_qwen25vl
python -m imaging.cli eval-reading --workspace workspace --case euterpepark-oss --gold "$HOME/Desktop/AppliedIT_Masters/Data-Evidence_prj/gold/oss_demografie_readings.toml"   # confidential: never in the repo

# 3c. researcher notes: what the team knows but no file holds (walk observations, the resident
#     meeting, project context for the Aanleiding, literature). Keep the file next to the case data;
#     a file named Onderzoekersnotities*.xlsx in the case folder is read by ingest-wijkscan.
python -m ingestion.cli notes-template "$HOME/Desktop/AppliedIT_Masters/Data-Evidence_prj/Perspectieven Oss/Onderzoekersnotities.xlsx"
python -m ingestion.cli ingest-notes <file.xlsx> --out workspace --case-id euterpepark-oss    # for a file kept elsewhere

# 3d. the sections beyond the six themes (template = no model; naive/staged = prompt v2)
python -m experiments.cli run E0 --workspace workspace --case euterpepark-oss --section Conclusies
python -m experiments.cli run E1 --workspace workspace --case euterpepark-oss --section Conclusies --seed 42

# 3e. full reports per model: the three selected drafting models ([selection] in config/models.toml:
#     qwen2.5:7b, qwen3:8b, llama3.1:8b) each draft every section; the tool renders a Word report
#     in the Wijkscan house layout (maps, charts drawn from the survey data, appendices)
ollama pull qwen2.5:7b && ollama pull qwen3:8b && ollama pull llama3.1:8b
python -m reports.cli models
python -m reports.cli generate --case euterpepark-oss --model local_qwen7b --pdf     # one model
python -m reports.cli compare  --case euterpepark-oss --generate --include-template  # all three + baseline, vs. the original
python -m reports.cli evaluate --case euterpepark-oss --run latest --judge local_qwen7b   # optional LLM judge (local only)
python -m reports.cli calibrate --csv workspace/reports/euterpepark-oss/<run>/evaluation/alignment.csv
python -m reports.cli adopt --case euterpepark-oss --run <run id> --reviewer <name>  # start the review from a run

# 4. review UI
INNO_WORKSPACE=workspace INNO_DATA_DIR="$DATA" streamlit run src/ui/streamlit_app.py

# tests (synthetic fixtures only; no research data needed)
python -m unittest tests.test_buurtscan_ingestion tests.test_evidence_and_verification tests.test_pipelines_review_export tests.test_imaging_and_oss tests.test_notes_zones_conclusions tests.test_reports
python tests/run_stdlib.py && python tests/run_stdlib_evaluation.py
```

The structure of the report, where each part's content comes from, and how a generated
report is compared with the original are described in `docs/report-structure.md`.
The original report is only read for evaluation; the comparison files quote it and stay
in `workspace/reports/` (git-ignored).

`workspace/` holds derived data (scores, resident quotes) and is git-ignored.

## Running in VS Code

Open the repository folder in VS Code (File › Open Folder) and install the recommended
Python extensions when asked. `.vscode/` then provides:

- **Run and Debug** (▷ in the sidebar): one entry per step, numbered in order: ingest,
  notes template, measure maps, check/smoke models, read infographics, score against the
  gold file, experiments E0–E3 (also for Conclusies/Aanleiding), summary, review UI, and
  9–9f for full reports per model and the comparison with the original. Pickers ask for case, section and model. Set
  breakpoints anywhere in `src/` to step through a run.
- **Terminal › Run Task…**: `Setup: install Python packages`, `Setup: download the Ollama
  models`, `Tests: run all`, and `Run all steps that need no model` (also Cmd+Shift+B).
- **Testing** (flask icon): the pytest suite; `pytest.ini` puts `src/` on the path.
- **Terminal**: new terminals use `.venv` and have `PYTHONPATH`, `$DATA`, `$OSS` and
  `$GOLD` set, so the commands above work as `--gold "$GOLD"` etc.

Data paths assume `~/Desktop/AppliedIT_Masters/Data-Evidence_prj/`; change them in
`.vscode/launch.json`, `tasks.json` and `settings.json` if the data lives elsewhere.

## Layout

| package | role | requirement |
|---|---|---|
| `ingestion/` | parsers (Oss + Buurtscan + researcher notes), source registry with checksums, config loader | UC1, FR1–FR3 |
| `evidence/` | deterministic statistics/quotes/scores/norms/map measurements/notes with readable ids (`B-SP-04`, `O-FS-01`); a cross-theme pack for Conclusies | brief §10 |
| `llm/` | provider protocol, Ollama / OpenAI-compatible / Fake / Replay, RAM check, privacy guard | UC5, FR13–FR16 |
| `drafting/` | pipelines `template`, `naive`, `staged` for the six themes; single-call prompts for Conclusies and Aanleiding; versioned prompts in `prompts/` (v2 default, v1 frozen) | UC2, SQ3 |
| `verification/` | citation, number, attribution, overgeneralisation, norm, theme and writing-rule checks | FR5–FR7, NFR1 |
| `review/` | append-only, hash-chained review sessions; override needed for flagged claims | UC3/UC4, FR8–FR10, NFR11 |
| `export/` | export gate, Markdown report, `evidence_map.csv`; Word report in the Wijkscan house layout (`docx_report.py`, structure in `config/report_structure.toml`), charts drawn from survey evidence (`charts.py`), PDF via LibreOffice | UC6, FR11–FR12 |
| `reports/` | full report per model (runs), comparison with the original report (structure, coverage, ceiling, numbers, figures), optional local LLM judge, calibration against human labels | SQ3, evaluation |
| `experiments/` | run records: config, checksums, fingerprints, tokens, latency, memory, metrics | SQ3, brief §15 |
| `app/` | `Workspace` services: the only API the UI calls | brief §7 |
| `imaging/` | map measurement (legend colours per neighbourhood; homes inside RIVM distance zones, via registration on the shared base map), vision-model reading with self-ensemble, confirmation log, reading evaluation | images as evidence |
| `ui/` | Streamlit review interface; tab 5 makes and compares the full reports of the three models | SQ4 |
| `config/` | survey→theme mapping (PROPOSED), demografie mapping, models (+ the three selected), experiments, report structure, RIVM + writing rules | — |

Interpretive choices (survey→theme mapping, demografie→theme/RIVM mapping) live in
`config/*.toml`, marked *proposed* until InnoBeweegLab confirms them.

---

# innoweegbeg-ingestion

First working slice of the Innoweegbeg AI reporting tool: **UC1 — Ingest source data**.

Turns the three raw Wijkscan perspectives (resident survey/coded data, expert
walkthrough scores, QGIS map images) into a flat store of `SourceRecord`s —
individually addressable rows, cells, and images that later drafting and
verification steps (UC2–UC4) can cite by id. This is the foundation the rest
of the requirements doc depends on: nothing downstream can be traceable if
the ingestion step doesn't keep a precise locator for every fact.

## Why this shape

- **FR1** — accepts the real file shapes InnoBeweegLab already produces
  (`Gecategoriseerde/Samengevoegde data`, `Resultaten vragenlijst`,
  `Score <buurt>` + `Score per indicator`, `Totaal scores` + `Opmerkingen`,
  QGIS PNG maps) rather than requiring a reformat.
- **FR2** — detects perspective from folder name AND cross-checks it against
  which sheets a workbook actually contains; the CLI prints what it detected
  for each file rather than applying it silently.
- **FR3** — anything that doesn't match a known shape (an unrecognised sheet,
  a survey column with no theme mapping yet, a map whose filename doesn't
  match a known pattern) is flagged with `needs_manual_mapping=True` instead
  of guessed at.
- No real personal data lives in this repo. Tests build tiny synthetic
  workbooks that mimic the real structure with fake values — see
  `tests/conftest.py`. Real ingestion runs are pointed at the data on disk
  (`../Perspectieven Oss`) and only their *summary counts* get discussed,
  never committed.

## Usage

    pip install -r requirements.txt
    python -m ingestion.cli ingest "../Perspectieven Oss" --out store.json

Prints a per-file detection log plus a summary (counts per perspective,
per theme, and how many records still need manual theme mapping), and
writes every ingested record to `store.json`.

## Status

Covers the shapes observed in the actual Euterpepark dataset as of the
requirements/experiment-design docs (2026-09-29). Known gaps, left as
explicit TODOs rather than silent guesses:

- The `Resultaten vragenlijst` sheet is 149 columns wide; the column→theme
  lookup in `theme_tagger.py` only covers the columns seen so far. Anything
  else is flagged for manual mapping, not dropped.
- Map images whose report role is genuinely dual-theme (e.g. the
  Sport- en speelkaart, cited under both "Sporten" and "Formeel spelen")
  are flagged rather than assigned a single theme.

## Evaluation harness

A second package, `src/evaluation/`, scores draft output against a frozen
gold set — the SQ3/SQ4 evaluation methodology from the "Evaluation Approach
— Supporting Resources" doc, built as real code rather than a one-off
analysis. It mirrors ALCE's citation precision/recall, RAGAS/FActScore's
faithfulness ("supported claims ÷ total claims"), and
PaperTrail/AttributionBench's injected-error catch rate, logging every run
(mean ± stdev across repeats, per "Do Repetitions Matter?") the way Inspect
AI and DeepEval do.

**Important: these metrics are currently structural proxies, not NLP/LLM
judges.** UC2 (draft generation) doesn't exist yet, so there is no real
free-text model output to decompose into claims. Every metric works on
`Claim.cited_ids` — a list of `SourceRecord` ids already attached to a
claim — not on free text. The harness is correct and tested today; what's
missing is the upstream step (an LLM-judge claim/id extractor) that will
eventually populate `cited_ids` from a real model's prose. Swapping that in
later should not require changing `metrics.py`, `runner.py`, or
`results_log.py`.

    python -m evaluation.cli demo --repeats 5 --out runs.json
    python -m evaluation.cli report --log runs.json

`demo` runs two built-in synthetic conditions (`perfect`, always-correct;
`noisy`, a mix of fabricated, misgrounded, and one deliberately
injected-error claim) through the full harness so the plumbing can be
read end to end today. Swap `_perfect_condition`/`_noisy_condition` in
`cli.py` for a real adapter that calls the local model (llama.cpp/Ollama)
once UC2 exists.

Tests: `tests/test_evaluation.py` (pytest) and
`tests/run_stdlib_evaluation.py` (dependency-free unittest mirror — no
openpyxl or network needed, unlike `run_stdlib.py`). 17 cases covering
each metric's edge cases (partial citation, fabrication vs. misgrounding,
unmatched claims, injected-error catch/miss), the runner's repeat handling,
and the results log's append-only behaviour.
