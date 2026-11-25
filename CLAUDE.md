# CLAUDE.md — project briefing for Claude Code

Read this first. It is the hand-over from the claude.ai project "Innoweegbeg" (planning,
research and documents) to Claude Code on Alex's laptop (running things). Last updated
25 November 2026 (freeze-2). When something here changes, update this file in the same commit.

## 1. The project in five lines

- **Who:** Alex Constantinescu, Master of Applied IT (Fontys ICT), capstone project for
  **InnoBeweegLab**, a small Dutch research lab that writes "Wijkscan"/"Buurtscan" reports
  for municipalities about how movement-friendly a neighbourhood is.
- **What:** a prototype that turns heterogeneous research data (expert walk scores, resident
  surveys, demographics, QGIS maps, RIVM rules of thumb) into a **traceable draft report**:
  every claim cites evidence ids that trace back to a cell, row or map. The researcher reviews
  every claim before anything leaves the tool.
- **Main research question:** within InnoBeweegLab's zero-budget, standard-office-hardware
  setting, what is the most efficient, cost-effective and highest-value way to turn this data
  into a verifiably traceable draft report, with the researcher in control?
- **Sub-questions (DOT framework):** Library (literature, done), Field (current workflow),
  **SQ3 Lab** (which pipeline architecture, evidence selection and model gives the best balance
  of traceability, usefulness, speed and cost), **SQ4 Lab** (does it run on a 16 GB laptop
  without a GPU), Workshop (review interface with researchers), Showroom (expert validation).
- **Now:** the experiment phase. Only pilots have run; the counted runs have not started.

## 2. Machine and environment

- **Laptop:** Windows, Intel i7-1165G7 (4 cores), 16 GB RAM, Intel Iris Xe (Ollama does not
  use it): **everything runs CPU-only.** It matches the end users' office laptops, so it is
  also the dress rehearsal for the stakeholder's laptop on **3 December 2026**.
- The MacBook (M1 Pro) died on 17 Nov. Its raw pilot run records (`workspace/runs/*.json`)
  are lost; the pilot numbers survive in `docs/experiments/` and `pilot-runs-summary.csv`.
- **Shell:** PowerShell. Python 3.11 in `.venv` (`.\.venv\Scripts\python.exe`), all of
  `requirements.txt` installed. Ollama with `qwen2.5:7b`. Set `PYTHONPATH=src` and
  `PYTHONUTF8=1` (Dutch file names) before running modules.
- **Setup scripts:** `scripts/setup_windows.ps1` (installs everything, idempotent) and
  `scripts/windows_timing.ps1` (the speed test, see §7). The `.sh` scripts are macOS only.
- `.vscode/settings.json` still points at the Mac venv (`.venv/bin/python`) and only sets
  terminal env vars for macOS: fix it for Windows (`.venv/Scripts/python.exe` and a
  `terminal.integrated.env.windows` block with `PYTHONPATH`, `PYTHONUTF8`, `DATA`, `OSS`, `GOLD`).
- Git: the repo stores LF; the Windows working tree has CRLF. Commit before any run (§8).

## 3. Data: where it is and the rules

All research data lives OUTSIDE the repo, under
`%USERPROFILE%\Desktop\AppliedIT_Masters\Data-Evidence_prj\`:

| Folder | What | Notes |
|---|---|---|
| `Perspectieven Oss\` | The real Euterpepark Oss case: report PDF, resident xlsx, 10 QGIS maps + 2 demografie jpegs, 4 expert xlsx, route PDF, sunburst | **Confidential.** The only case with a human-written report, so the only one S can be scored on |
| `newDataSender\Textual_Rules_and_more_inspiration\` | 10 **synthetic** Buurtscan cases (file names say SYNTHETISCH), plus Schrijfwijzer and "Ten textual rules" | 10 `[n]` duplicates; the ingester skips them by checksum. No reference reports, so they give U, time, memory and failed sections, not S. They join the counted runs after 3 December |
| `gold\oss_demografie_readings.toml` | Hand-checked values of the two demografie images, for `imaging.cli eval-reading` | **Draft** recreated 17 Nov (149 values). Alex must verify it and fill `verified_by`/`verified_on` before it counts |

Rules, never broken:

- **Never commit research data or anything derived from it** (NFR7). `workspace/`, `*.json`,
  `*.xlsx`, `*.pdf`, `*.png` etc. are git-ignored for this reason; keep it that way.
- **Never quote the original Oss report** in docs, commits or chat. It is the evaluation
  reference only; the ingester excludes it from the drafting inputs
  (`excluded_reference_report`). Report numbers only.
- **No hosted model sees the data.** The privacy guard refuses resident free text to hosted
  providers and the hosted judge is refused by a test. Only logged exception: C0 (Copilot by
  hand, with InnoBeweegLab's written OK). E6 (Azure EU) runs only if
  InnoBeweegLab gives a tenant and a DPA.
- Tests use synthetic fixtures only.

## 4. How the system works

```
source cell -> SourceRecord -> Evidence item (computed in code) -> drafted claim (LLM, cites evidence ids)
            -> verifier flags -> researcher review (hash-chained log) -> gated export + evidence map
```

Packages and their requirements: `README.md` (Layout). Design and every decision so far:
`docs/design/reporting-tool-design.md`. Report structure and how a run is compared with the
original: `docs/report-structure.md`. Analysis artifacts (personas, use cases, requirements,
test cases): `docs/analysis/`.

Key facts:

- Pipelines (`src/drafting/pipelines.py`): `template` (E0, no model), `naive` (one prompt per
  section), `staged` (findings per perspective: data, expert, residents; then a synthesis),
  `staged_checked` (E4) and `naive_raw` (E1R, `src/drafting/raw_records.py`).
  Conclusies and Aanleiding always use their own single-call prompt.
- The verifier (`src/verification/checks.py`) is deterministic. Per claim: grounded / weak /
  unsupported, with named flags (no_citation, invalid_citation, unsupported_number,
  attribution_mismatch, overgeneralisation, norm_not_measured, image_unconfirmed,
  theme_mismatch). It flags, never rewrites.
- Prompts are versioned files in `prompts/<version>/`. **v3 is the default** (v2 + at most 6
  evidence ids per claim, also as `maxItems` in the JSON schema). v1 and v2 are frozen for
  the reproducibility of earlier runs. v4 = v3 unchanged + the E4 and E1R prompts; only E4 and E1R
  use it. **Never edit a released prompt version: make a new one.**
- Models (`config/models.toml`): `local_qwen7b` (qwen2.5:7b, primary), `local_qwen3_8b`,
  `local_llama8b`; context window 16,384 tokens, output 1,800 (3,000 for Conclusies and
  Aanleiding). Memory limit 0.6 × 16 GB = 9.6 GB.
- Every run record stores code version, prompt and config fingerprints, dataset checksums,
  machine and device. `-dirty` is added only when `src/`, `prompts/`, `config/` or
  `requirements.txt` have uncommitted changes (`src/experiments/provenance.py`).

## 5. The experiment framework (fixed in advance)

Full text: `docs/experiments/experimental-design-and-evaluation-framework.md` and
`docs/experiments/hypotheses-and-thresholds.md` (the pre-registration; its git history is the
record of what was fixed when). Short version:

**Measures.** **U** = unsupported-claim rate (claims with no valid citation or with a number
not in the cited evidence; lower is better), from `checks.py`. **S** = coverage of what the
data supports, against the original report (higher is better), from `reports/evaluate.py`.
Ceiling = share of the original's statements the data can support at all: 128 of 239 (53.6%) for Oss,
without Aanleiding. Pilot results below still have the old basis (131 of 250, 52%).
Secondary: number conflicts, numbers found back, grounded/weak rates, time, peak model memory,
failed sections.

**Conditions.** E0 template · E1 (A) single prompt · E1R single prompt on raw records ·
E2 (B) staged · E3 staged with evidence selection (top-25, BM25) ·
E4 (C) staged + checker step · E5 multi-agent, not built · E6 hosted model, only with access ·
E7 final configuration · C0 Microsoft 365 Copilot used by hand (protocol in the hypotheses
doc §4b).

**Hypotheses.** H1 evidence layer (E1 vs E1R) · H2 staging (E2 vs E1) · H3 checker (E4 vs E2)
· H4 retrieval · H5 models (exploratory) · H6–H8 user sessions · H9 tool vs Copilot.
Feasibility: F1 peak memory ≤ 9.6 GB · F2 full report ≤ 8 h on CPU · F3 no failed sections
in 3 runs · F4 €0 · F5 one complete report (stage-1 winner, seed 42) on the
InnoBeweegLab laptop (HP ProBook, Core 5 120U, 16 GB), no failed sections, peak ≤ 9.6 GiB.

**Judging a difference.** Pooled over seeds 42, 43, 44; 95% bootstrap CI (2,000 resamples;
U by (seed x section) clusters, S by paired statements);
minimum meaningful difference (MMD) 5 percentage points. Better/worse = CI excludes 0 and the
difference ≥ MMD; no meaningful difference = whole CI within ± MMD; else inconclusive.
S is trusted only if a scorer reaches κ ≥ 0.61 against two raters' agreed labels (word overlap
is at 0.38 on 89 developer labels).

**Stages.** Stage 1 pipeline (E1, E2, E4; E1R reference) → stage 2 evidence selection on the
winner → stage 3 models on that winner. Rule: highest S among candidates whose U is within the
MMD of the best U; ties → the faster one. If a full report takes more than ~3 hours, stages 2
and 3 use 3 of the 6 themes (already fixed; choose which three before stage 2).

**What counts.** Only untagged runs after the git tag `freeze-2`, with `code_version` equal to
that commit (no `-dirty`). Tagged runs (`pilot-week1`, `pilot-windows`, …) never count. A
failed section is kept and scored as missing. A run is repeated only for an infrastructure
fault (crash, power loss, sleep), logged in the deviation log — never because the output was
poor.

## 6. Decisions accepted on 19 Nov (build E4 and E1R exactly like this)

Full reasoning and sources: `docs/experiments/e4-e1r-design-decisions.md`.

E4 (checker step) = E2 → verifier → one revision call → verifier again:

- D1 feedback = the existing deterministic verifier, not a model judging itself.
- D2 only claims with status *unsupported* go back; grounded and weak claims are untouched.
- D3 one revision round per section, all its unsupported claims in one call. Allowed outcomes
  per claim: fix the citation, fix the number, reword to what the cited evidence says, or
  withdraw. Revised claims are verified again and kept even if still flagged. The original and
  the revision both go into the run record.
- D4 H3 is judged on U **plus** checks E4 never saw: a blind human support check of a sample of
  E2 and E4 claims (part of the second rater's session), S, number conflicts, withdrawal rate,
  and still-flagged revisions. H3 holds only if U drops by ≥ MMD and human support does not get
  worse.
- D5 same model and settings; factored context: the flagged claim, its flag messages and the
  section's evidence list only (not the rest of the draft). Same JSON schema with the 6-id cap.
- D6 report extra calls and minutes per report.

E1R (raw-records baseline) = E1 with the evidence layer removed:

- R1 input = exactly the source records behind the section's evidence items (union of their
  `source_record_ids`); same "not available" list and quote setting as E1.
- R2 one Markdown pipe table per source sheet: file + sheet as a heading, original column
  headers, record id as the first column.
- R3 the v3 prompts with exactly two changes: the data block is "brongegevens" and claims cite
  record ids; system rule 3 becomes "if you calculate, say what you calculated from".
- R4 count E1R prompt tokens per section before the freeze (script, no model). Run at 16K like
  E1; a section that doesn't fit fails via the existing truncation check and counts as a result
  for H1. If any fails, add one 32K sensitivity run on seed 42.
- R5 H1 is decided by number conflicts and numbers found back (format-independent); U for E1R
  is secondary, with its bias against correctly computed numbers stated.
- R6 the six theme sections only, for both E1 and E1R.

Also accepted: the five (P) values as written (MMD 5 pp, 2,000 resamples, κ ≥ 0.61, F2 8 h,
30 sampled statements for C0); all counted runs CPU-only on this laptop; check V1 (GPU vs CPU)
dropped. Stage 2 compares all, top-25 and BM25; vector and hybrid are dropped (25 Nov),
except one exploratory hybrid run that never counts.

Deviation-log entries to add at the freeze are listed in the decisions doc.

## 7. Status and next steps (25 Nov)

Done:

- Tool works end to end (ingestion, evidence, staged drafting, verifier, review screen with
  audit log, export gate). Not built: the screen for resolving unknown survey mappings; E3's BM25.
- Pilot (Mac, qwen2.5:7b, seed 42, round 2 with the 16K window): E1 26% unsupported, E2 8.5%;
  S 15% (E1), 18% (E2, GPU), 28% (E2, CPU), 27% (E0); E2 on CPU took 77 min, peak 5.5 GB.
  Findings P1-P8 in `docs/experiments/week1-pilot.md`; P7 (invented ids) and P8 (dirty flag)
  are fixed (`tests/test_pilot_fixes.py`).
- Windows speed test, 19 Nov (tag `pilot-windows2`): E1 68.9 min with 4 of 8 sections failed, E2
  82.3 min with 1 failed. Every failure was the 600 s per-call timeout; now 3600 s. Model memory
  peaked at 8.1 GiB (E1) and 8.7 GiB (E2) as the sum of the Ollama processes (Ollama itself reports
  5.7): under the 9.6 limit with little margin. System memory peaked at 15.5 and 15.4 of 15.7 GiB.
  (The first version of this file mixed GB and GiB: "15.9 of 15.7".)
- Check run, 24 Nov (tag `pilot-windows3`, commit `e1a27b4`, `--cold --cpu-only`): no failed
  sections; E1 111.7 min (its time is unusable: about 80 min on battery), E2 92.1 min; peak 7.6 and
  7.1 GiB. The same seed gave other output lengths than on 19 Nov (cause unknown).
- E4 and E1R built per section 6 (`7aa977e`), accepted 25 Nov. Verifier and scorer no longer read the
  digits of ids as numbers (`565eb45`, `7428245`); U of E2 in the check run 19.5% -> 12.2%, E1 unchanged.
- Smoke runs, 25 Nov (tag `pilot-e4e1r`, commit `7428245`, seed 42, no failed sections): E4 130.5 min
  (U 10.7% before and 6.1% after revision, S 23.3%, 42 min extra for 4 revision calls, peak
  8.3 GiB); E1R 97.7 min (six themes, U 38.1%, peak 7.8 GiB). E1R prompts 7,157-12,615 tokens: all fit
  in 16K. E1R's number conflicts and numbers found back were not looked at before the freeze.
- Aanleiding is out of scope for all counted runs: researcher-written, no condition gets researcher
  notes, not scored. A report has 8 sections (E1R: 6).
- `freeze-1` (`d56ac62`) was superseded by `freeze-2` before any counted run.
- The 10 synthetic cases ingest cleanly (checked 25 Nov).

Plan to 3 December (stop at every GATE and ask Alex):

1. Wed 25 (planned Thu 26): freeze. `hypotheses-and-thresholds.md` and its deviation log updated. GATE, then commit,
   tag, push the tag. Done: `freeze-2`.
2. Wed 25 night - Sun 29: stage 1 counted runs, untagged, `--cold --cpu-only`: E1, E2, E4, E1R on Oss;
   seed 42 for all four, then 43, then 44 (12 reports, about 7 h per seed). After each report one line
   in `workspace\stage1-progress.md`. **No commit on the checkout that runs until all 12 are done**
   (a new commit changes `code_version`). Then
   `python scripts/compare_conditions.py --workspace workspace --freeze freeze-2 --out workspace/stage1-results.md`.
   GATE: results and the winner by the pre-registered rule.
3. Mon 30 - Tue 1 Dec: offline package in `%USERPROFILE%\Desktop\InnoBeweegLab_offline\` (Python 3.11
   installer, wheels, Ollama installer, model files, git archive of the freeze commit,
   `install_offline.ps1`, `DEMO.md`), without research data; dry run in a fresh folder plus the F5
   command once (stage-1 winner, seed 42). GATE.
4. 3 Dec: stakeholder laptop (specs, written agreement that data stays local and pseudonymised, F5).

After 3 December: E2 and E4, seed 42, on the 10 synthetic cases (20 reports, at Oss speed about 37 h), in
their own workspace `workspace\synthetic`, from the freeze-2 commit; build BM25 and freeze again for stage 2 (all, top-25, BM25; six themes) and stage 3
(models); F1-F3 runs of the final configuration; Copilot C0; one labelling session with the second
rater (89 statements, 50 verifier verdicts, blind E2/E4 support check); user sessions with 4-5
InnoBeweegLab researchers, 7-18 Dec (rubric, SUS, NASA-TLX, interview guide, consent forms, editing
time). Analysis in January.

Open decisions: the remaining (P) values (H7 rubric, H9, F3); build the unknown-mapping screen or scope it out; E6 (Azure EU) or drop it.

## 8. How to work in this repo

- Run tests before every commit: `.\.venv\Scripts\python.exe -m pytest -q` (194 tests).
- Commit code, prompts and config **before** starting a run, so its record is not `-dirty`.
- Long runs: laptop on power, power mode "Best performance", sleep off. **At least 9 GB free
  memory at the start**: close VS Code, browsers, Teams, Outlook (the script warns below 9 GB).
  Every model run starts cold (`--cold`) and **CPU-only (`--cpu-only`)**, so the run record says
  CPU-only; counted runs without it do not count as SQ4 evidence.
- A run takes 1.5–2 h; that is longer than one command timeout, so start it in the background
  with its output in a log file and poll the log.
- Change only what a task needs. Interpretive choices live in `config/*.toml`, marked proposed
  until InnoBeweegLab confirms them.
- Documentation style (docs/, commit messages): plain English, short sentences, numbers with
  units, no research data quoted.
- Useful commands (PowerShell, from the repo root, venv active, `$env:PYTHONPATH="src"`):

```powershell
python -m ingestion.cli ingest-wijkscan "$env:USERPROFILE\Desktop\AppliedIT_Masters\Data-Evidence_prj\Perspectieven Oss" --out workspace --case-id euterpepark-oss --label "Euterpepark Oss"
python -m imaging.cli analyse --workspace workspace --case euterpepark-oss
python -m llm.cli check
python -m llm.cli smoke local_qwen7b
python -m experiments.cli run E2 --workspace workspace --seed 42 --cold --cpu-only --tag <tag>
python -m experiments.cli summary --workspace workspace --tag <tag>
python scripts/pilot_summary.py
streamlit run src/ui/streamlit_app.py
```

## 9. Documents outside this repo

The claude.ai project "Innoweegbeg" holds the research notes (literature reviews on
traceability and verification interfaces, OSS and cost comparisons, the Oss dataset inventory,
the image-interpretation research, the status hand-over). Claude Code cannot read them; ask
Alex to paste what is needed. The E4/E1R decisions also exist as a claude.ai doc; the repo copy
in `docs/experiments/` is the one that gets frozen.
