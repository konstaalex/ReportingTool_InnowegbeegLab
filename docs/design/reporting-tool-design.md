# Reporting tool — design (v0.2, updated 4 Nov 2026)

How to read this document: sections 1–9 describe the design of 21 October. Sections 10–13
record the changes and decisions since then, in order, so the reasoning stays visible. Where an
early section is out of date it says so and points to the later one. Section 13 gives the current
state. An overview of all project documents is in `docs/README.md`.

Scope: the Lab-phase prototype that turns one neighbourhood's Buurtscan data into a
traceable, reviewed draft report. Governing context: `CLAUDE_InnoBeweegLab_App_Brief.md`,
the Requirements Engineering doc (UC1–UC6, FR1–FR16, NFR1–NFR12) and Project Plan v0.2.

## 1. Inputs (new data set, received Oct 2026)

| Source | Shape | Perspective | Per case |
|---|---|---|---|
| `Expertwandeling - <buurt> <stad> (SYNTHETISCH).xlsx` | one sheet `Score per indicator`; 50 statements; theme + indicator forward-filled (merged cells); one consensus score 1–5; free-text `Opmerkingen` | expert | 1 |
| `Vragenlijst buurtbewoners - <buurt> <stad> (SYNTHETISCH).xlsx` | survey export, respondent-level; row 1 = question (merged span), row 2 = answer option; one row per respondent; footer row `Totaal aantal antwoorden per kolom (n = …)` + per-column totals | bewoners | 1 |
| `Data voor Demografie - <buurt> <stad> (SYNTHETISCH).docx` | 2 tables (health indicators buurt/gemeente/NL; beweegvriendelijke-omgeving sub-scores buurt/wijk) + inhabitant counts + 8 chart images (age, housing, origin, income) | data | 1 |
| `Vuistregels Bewegen RIVM 18-12-2025.pdf` | 6 rules (25 %, 200 m, 300 m, 400 m, 800 m, 1500 m) | reference norm | shared |
| `Ten textual rules.docx`, `Schrijfwijzer_Communicatie MKP.docx` | writing rules | reference style | shared |

10 cases (Baardwijk Waalwijk … Waterkwartier Zutphen). Files named `… [n].xlsx` are
byte-identical duplicates (9 of them) and are skipped on checksum, with a log line.
The existing Oss/Euterpepark parsers stay; the new shapes get their own parsers.

## 2. Pipeline

```
raw files ──► UC1 ingestion ──► SourceRecord (cell-level, stable id)
                                      │  deterministic Python only
                                      ▼
                              Evidence item (statistic / score / quote / norm)
                              stable readable id e.g. B-SP-03, X-SP-07
                                      │  LLM sees ONLY evidence items
                                      ▼
                     drafting pipeline (template | naive | staged)
                                      │  JSON: claims[{text, type, evidence_ids}]
                                      ▼
                     deterministic verifier  ──► grounded / weak / unsupported
                                      │           + style flags (writing rules)
                                      ▼
                     review session (append-only, hash-chained audit log)
                                      ▼
                     export (blocked until every claim resolved) + evidence map
```

Two-level traceability: **claim → evidence item → source cell(s)**. The LLM never
calculates; every number it may use is pre-computed in an evidence item, and the
verifier checks that each number in a claim appears in its cited evidence.

## 3. Key decisions

| # | Decision | Why | Alternative kept open |
|---|---|---|---|
| D1 | Extend the existing repo, flat packages next to `ingestion/` and `evaluation/` | keeps git history + tests; no rename churn | move to `src/innobeweeglab/` namespace later |
| D2 | Evidence layer between records and LLM | brief §10: "pass compact structured evidence"; makes statistics deterministic and testable | raw-record RAG as an experimental condition |
| D3 | Readable short evidence ids (`B-SP-03`) | small local models copy short ids far more reliably than hashes | — |
| D4 | Verifier is deterministic and only *flags* | brief §12C: no silent rewrites; cheap on 16 GB laptop | LLM-judge / NLI verifier as experiment E4 |
| D5 | Writing rules encoded as config + lint checks, each traceable to its rule document | rules become measurable (style-violation rate) rather than prompt wishes | — |
| D6 | RIVM rules are *reference norms*, not measurements | no maps / distances in this data set → system must not claim compliance | add GIS-derived evidence when maps or layers arrive |
| D7 | Survey question → theme mapping lives in `config/survey_mapping.toml`, marked "proposed" | it is an interpretation; researchers must be able to see and change it | — |
| D8 | TOML configs (stdlib `tomllib`), stdlib HTTP for model calls | NFR10 maintainability, zero extra dependencies | — |
| D9 | Providers: Ollama, OpenAI-compatible (hosted/LM Studio/llama.cpp), Fake | brief §11; Fake makes every pipeline testable offline | — |
| D10 | Privacy guard: free-text resident evidence never goes to a `hosted` provider unless config sets an explicit, logged override; pseudonymisation applied first | FR15/FR16, NFR7 | — |

## 4. Pipelines (experimental variable)

| id | stages | LLM calls / section | experiment |
|---|---|---|---|
| `template` | deterministic: top-salience evidence rendered as claims | 0 | floor for fluency, ceiling for faithfulness |
| `naive` | one prompt: all theme evidence → claims | 1 | E1 |
| `staged` | (a) findings per perspective, (b) cross-perspective synthesis + conclusion | 4 | E2/E3 |
| `staged_verified` | staged + verifier-guided revision pass that keeps the original in history | 4–5 | E4 (next) |

Evidence selection (retrieval variable): `all` theme evidence vs `top_k` by salience; BM25/vector are later conditions.

*Updated in §13:* the conditions are now named by experiment number (E0–E7), E1R (single prompt
on raw records) is added, and E4 (staged + checker step) is still to be built.

## 5. Verifier checks

| check | flags | severity |
|---|---|---|
| no citation | `no_citation` | unsupported |
| cited id not in the pack | `invalid_citation` | unsupported |
| number in text not in cited evidence | `unsupported_number` | unsupported |
| "bewoners/experts" named but no evidence from that perspective | `attribution_mismatch` | weak |
| majority wording ("de meeste", "veel bewoners") on < 50 % share or a single quote | `overgeneralisation` | weak |
| compliance wording about an RIVM rule supported only by the norm itself | `norm_not_measured` | weak |
| cited evidence from another theme | `theme_mismatch` | weak |
| > 20 words, passive voice, empty words, formal words, modal verbs | `style:*` | style (does not block) |

Section-level: **coverage gaps** — salient evidence (expert score ≤ 2, top resident needs) that no claim cites.

## 6. Review and export (UC3–UC6)
- Claim states: `draft → approved | edited | rejected`; approving a weak/unsupported claim needs `confirm_override` and is logged as `override_approve` (FR10).
- Every action is an event `{ts, actor, action, before, after, comment, prev_hash, hash}`; `verify_chain()` detects tampering (NFR11).
- Export is blocked until every claim in every included section is resolved and names the blocking sections (FR11). A clearly watermarked preview export exists for internal reading.
- Output: Markdown + DOCX in the Wijkscan structure, plus `evidence_map.csv` (claim → evidence → file / sheet / cell).

## 7. Experiments

*Out of date: see §13 and `docs/experiments/`. Experiments now produce full reports of the Oss
case, scored against the original report.*

`python -m experiments.cli run --definition config/experiments.toml --id E1 --case passewaaij-tiel`
writes `experiments/runs/<run_id>.json` with config, dataset checksums, prompt version + hash,
per-stage latency and tokens, peak memory, outputs and verifier metrics
(citation-validity rate, unsupported-claim rate, numeric-support rate, style-violation rate, coverage).

## 8. Open questions for InnoBeweegLab (not guessed — see config comments)
1. **Survey → theme mapping.** Is the proposed mapping (`config/survey_mapping.toml`) how researchers read the questionnaire? In particular, "Natuur/groen voor recreatie" → Uitstraling, and "Straatverlichting" / "Sociale veiligheid" as cross-cutting.
2. **Respondents outside the research neighbourhood.** Should "Omliggende buurt 1–3" and "Ik woon niet in deze buurten" be included in the statistics, or reported separately? The current default includes them all and reports the split.
3. **RIVM rules.** In these reports, are the rules used as framing for recommendations only, or will distance/GIS data arrive so the rules can be tested?
4. **Schrijfwijzer scope.** It targets web and social media. Should reports to municipalities use "je"? The current default keeps the general rules (B1, active voice, ≤ 20 words, no empty words) and does not apply "je".
5. **Demographics charts.** Should the values shown only in the chart images (age, housing, origin, income) be typed into a sidecar sheet, or read with a vision model? They are flagged for now.
6. **Reference report.** Is there a human-written Buurtscan report for one of these 10 cases? It's needed as the human reference in the SQ5 comparison.

## 9. Status (21 Oct 2026; current status in §13)

Built and tested (49 unit/integration tests, synthetic fixtures; existing stdlib suites still pass):

| capability | state | verified on |
|---|---|---|
| Buurtscan ingestion, checksum dedup, footer cross-check | done | all 10 cases: 0 count mismatches, 9 duplicate files skipped |
| Evidence layer (6 themes + Algemeen) | done | ~260 evidence items per case |
| Verifier + writing-rule lint | done | unit tests per check |
| Pipelines `template` / `naive` / `staged` | done | template on real data; naive/staged with Fake + Replay providers |
| Ollama + OpenAI-compatible providers | written, **not yet run against a live model** | next step on the MacBook |
| Review sessions, override, audit chain, archive on regenerate | done | tests + UI stub run |
| Export gate, Markdown/DOCX, evidence_map.csv | done | tests |
| Experiment runner + CLI | done | SMOKE and E0 runs |
| Streamlit UI | written; every code path executed against a stub; **not yet opened in a browser** | — |

Next (in order): (1) run E1/E2 with `local_qwen7b` on Passewaaij, Sporten, 3 seeds, and read the drafts; (2) tune prompt v2 only if failure patterns are systematic, then freeze; (3) `staged_verified` (E4); (4) gold claims for 2 cases to connect `src/evaluation/` metrics; (5) the open questions in §8 with InnoBeweegLab.

## 10. Update 21 Oct 2026 (afternoon): Oss case and images

**Oss / Wijkscan connected to the fact layer.**
- `ingestion/wijkscan.py` ingests the perspective-folder layout. The human report PDF is **excluded** (status `excluded_reference_report`) because it is the evaluation reference and must not leak into the inputs.
- **Bug fixed** in the September expert parser. Real Oss sheets number statements per section, but the parser matched on number alone, so every statement got the last theme. It now matches on section k ↔ theme block k and cross-checks the averages in both sheets: 151/151 agree. Section-level remarks are no longer dropped.
- `survey_tally_parser.py` reads "Resultaten vragenlijst". It is the same questionnaire as the Buurtscan survey, stored as totals per group (65+ en toekomstige bewoners; jongvolwassenen), and it reuses `config/survey_mapping.toml`. A merged matrix header is split per item, and "- 1…- 5" labels are recognised as ratings.
- Coded open answers ("Gecategoriseerde data") become evidence with their group. Stakeholders are kept apart from residents, and the verifier flags "bewoners …" claims that rest only on stakeholder answers. "Samengevoegde data" is not used, to avoid double counting (open question).

**Images** (see `docs/research/image-interpretation.md`):

| # | Decision | Why |
|---|---|---|
| D11 | Maps with colour-coded buildings are measured deterministically (legend swatches, border regions, pixel shares per neighbourhood) | VLMs are unreliable at counting and spatial detail; extract-then-reason is the reliable route (MapQA) |
| D12 | Infographics are read by a local vision model with a self-ensemble, sanity checks and mandatory researcher confirmation | VLMs read printed numbers well but not perfectly; uncertainty must be visible |
| D13 | Proposal maps are never findings; counts of locations do not "test" a distance rule; identical legend colours are merged and flagged | no claim may say more than the image shows |
| D14 | Image interpretation lives in `config/images.toml` (kind, legend, themes, rules, region seeds) | researchers can check and correct it |
| D15 | Vision-model accuracy is measured against a hand-checked gold file kept outside the repo | SQ3 comparison of vision models; confidential data stays local |

**New open questions for InnoBeweegLab:** (7) Can we get the QGIS layers behind the maps and the data behind the demographic infographics? (8) What is the difference between "Gecategoriseerde data" and "Samengevoegde data"? (9) Should the "Inspiratie" map feed the recommendations section?

## 11. Update 23 Oct 2026: closing the gap with the human report

A comparison with the human Oss report (`claude/oss-reference-gap-analysis.md` in the project) showed four fixable gaps. All four are now addressed.

| # | Decision | Why |
|---|---|---|
| D16 | **Distance-zone coverage (RIVM R2, R4)** is measured on the sport-and-play maps. They are registered on the base map they share with the Basisbehoeftekaart (normalised cross-correlation of street/building edges, coarse-to-fine). Homes (the coloured buildings) and neighbourhoods are taken over from that map, and a home counts as covered when its pixels carry the zone's tint (chroma projected on the legend swatch). The fit is checked independently: the carried-over borders must land on line-dark pixels (relative to the local mean), ≥ 0.8; a correct fit scores 0.95–0.99 and a fit 25 px off scores 0.30–0.47. | "Within 200 m of every home" is exactly what the zone shows. The borders under the zones fail a fixed darkness test, but the shared base map does not. |
| D17 | **Researcher notes** become an input type: an Excel template with five kinds (walk observation, resident meeting, stakeholder talk, project context, literature). Each note has a source and gets an `O-…` evidence id. Meeting and stakeholder notes carry the privacy flag; project context feeds a new **Aanleiding** section; literature goes to the Bronnenlijst. | About 1 in 6 statements in the human report comes from team knowledge that is in no file. Without a source, the tool must not write them. |
| D18 | A **Conclusies** section gets its own deterministic cross-theme pack: theme rankings (experts, each resident group), the outcome of every tested rule, and factors that recur in ≥ 2 themes. Recurring factors come from a configurable term list (`config/reference/cross_theme_terms.toml`), counted in coded answers, expert remarks and notes. The Samenvatting is assembled from approved cross-theme conclusions. Recommendations stay with the researcher. | The human report has a cross-theme conclusions chapter; per-theme conclusions alone do not make one. Counting the terms keeps the synthesis traceable. |
| D19 | **Prompt v2** (default; v1 kept frozen): house-style order per theme (data/maps → experts → residents and stakeholders → synthesis); RIVM compliance may be stated only from a rule-testing measurement; notes are attributed to the research team; dedicated `conclusions` and `aanleiding` prompts. The staged pipeline now runs data → expert → bewoners. | Matches the structure of the human report. v1 said rules were "not measured", which is false for Oss. |
| D20 | The **Methode** section is generated from the case's own evidence: neighbourhoods walked, statements per neighbourhood, assessors, respondent groups, stakeholders, maps and infographics, and which rules were and were not tested. | The fixed text was Buurtscan-specific and wrong for Oss: it double-counted statements (312), named one group of respondents (37) and said no rule was tested. |

**Oss results (deterministic):** children up to 12 have a play area within 200 m for 53% of homes (Centrum-West 38%; Euterpepark area 6%); everyone older than 12 has one within 400 m for 81% of homes (Centrum-West 72%; Euterpepark area 59%). Per-neighbourhood marker counts on the combined play map now work too, because the same registration is used. The Conclusies pack names netheid (5 themes), groen, onderhoud and veiligheid (4 each), and zitplekken and verlichting (3 each).

**New open questions for InnoBeweegLab:** (10) Are the coloured buildings on the Basisbehoeftekaart all addresses with homes, or also other premises ("huis/pand")? (11) Do the field notes, photos and resident-meeting notes exist in a form that could fill the notes template, and who would record them in future scans? (12) Is the cross-theme term list right, and what is missing from it?


## 12. Update 23 Oct 2026 (evening): full reports per model, and the comparison with the original

The structure of the report, its sources, and the evaluation are described in full in `docs/report-structure.md`.

| # | Decision | Why |
|---|---|---|
| D21 | The **Word report follows the Wijkscan house layout**: A4 landscape, house colours and fonts, cover, colofon, table of contents, chapters, a running "Afbeelding/Figuur n" numbering, maps in the theme text, two full-page infographics, Bijlage 1–2, and an added Bijlage 3 (claim → evidence → cell). The structure is configuration (`config/report_structure.toml`), taken from the Oss report's structure only. **Charts are drawn from survey evidence** (`[[chart]]`), so every bar has an evidence id. On the Oss data they equal the published figures. House figures (BVO model, perspectives) and logos are shown only when InnoBeweegLab supplies the files. | One of the tool's goals is a draft in the organisation's own report format. Drawing the charts from evidence makes figures as traceable as sentences. |
| D22 | **Full-report runs per model.** The three drafting models compared in SQ3 are listed in `[selection]` (qwen2.5:7b, qwen3:8b, llama3.1:8b; all fit 16 GB). A run drafts every section with one model under the same evidence, prompts and pipeline, verifies it, and renders the report. Runs are kept apart from the review sessions; one run can be adopted as the start of the review. A preflight check stops a run when Ollama or the model is missing. | Comparing models needs whole reports under identical conditions, not single sections. Adoption keeps the review trail intact (sessions are archived, never overwritten). |
| D23 | **Evaluation against the original report**: chapter structure; coverage of the original's statements (covered / partial / missing per chapter); the *ceiling*, i.e. the share the evidence can support at all, independent of the model; coverage of that supportable part; numbers found back and possible number conflicts; additions; maps, charts and photos. Recommendations and their inspiration pictures are reported separately ("not generated by design"). The original is read only for evaluation, and the outputs that quote it stay in the git-ignored workspace. | "How well does the output respect the original" has several parts. A model must not be blamed for content the data cannot support, such as field observations or the residents' meeting. |
| D24 | **Matching**: deterministic word overlap (IDF-weighted recall of the original's content words, plus numbers) to find candidate sentences and give a fast score. An optional **local LLM judge** decides the labels from those candidates, with cached answers; hosted judges are refused, because the original is confidential. Every evaluation writes `alignment.csv` with columns for human labels, and `calibrate` reports Cohen's kappa for both methods and suggests overlap thresholds. Initial thresholds come from 89 developer-labelled statements, where word overlap reached κ ≈ 0.38. | Lexical overlap is reproducible but weak on paraphrase. The judge is closer to a human reading. Both must be checked against researcher labels before thesis use. |
| D25 | The original is read from the PDF using **its own style profile** (body size, heading font, captions, chart labels skipped, chart titles taken from above their captions). The same reader handles the tool's own output converted to PDF. A report compared with itself scores above 90% on its generated text (test). | No hand-made copy of the report text is needed, so the measure works for every new Wijkscan. The round trip is a sanity check of the whole measure. |
| D26 | **Re-ingesting a case keeps its map measurements and confirmed readings** when the image file is unchanged (same checksum). | Before, re-ingesting silently dropped all `D-…` evidence until the maps were analysed again. |

**Oss baseline (template pipeline, no model, no researcher notes; word overlap):** 9 of the 11 chapters of the original are present. The Samenvatting needs conclusions, and the template writes none; the Aanleiding needs project-context notes. Coverage of the original's 250 statements is 15%. The ceiling (statements the evidence supports at least partly) is 52%, and coverage of that part is 27%. Numbers found back: 33 of 73. Maps 6/6 and charts 4/4 (titles identical). Photos 0/11, because there are no photo notes yet. The model runs and the judge will be run on the Mac with Ollama.

**New open questions for InnoBeweegLab:** (13) Can the house template files (logo, BVO model, perspectives figure) and the wording of the Bijlage 1 location definitions be shared? (14) Who could be the second rater for the calibration labels?


## 13. Update 3–4 Nov 2026: experiment set-up and the week-1 pilot

Plan, hypotheses and thresholds: `docs/experiments/hypotheses-and-thresholds.md`. Pilot results
and findings P1–P8: `docs/experiments/week1-pilot.md`.

| # | Decision | Why |
|---|---|---|
| D27 | **An experiment run produces full reports.** With `unit = "report"` (`config/experiments.toml`) every case × seed becomes one report run (`reports.runs`), scored against the original report (`reports.evaluate`); the experiment record links the report runs and keeps every number needed to compare. `unit = "section"` remains for quick checks (SMOKE). | The comparison against the human report needs whole reports. Pilots and counted runs now go through one mechanism. |
| D28 | **Test case: `euterpepark-oss`**, the only case with a human-written report; it replaces `passewaaij-tiel` as the default. | Without a reference report a run cannot be scored on coverage. |
| D29 | **Run records measure the model server, not only Python** (`llm/resources.py`): every second the memory of the Ollama processes and what Ollama reports per loaded model (`/api/ps`), plus Python's and the whole machine's memory. The peak is compared with 0.6 × 16 GB. Records also keep the machine, the code version (with `-dirty`), fingerprints of prompts and configuration, and the model's own timings (loading, reading the prompt, writing). | Python's own peak misses the model completely (NFR5, SQ4). Timings show where the time goes on a CPU. |
| D30 | **Cold starts and CPU-only runs.** `--cold` unloads every Ollama model before a run, so the time includes loading and the memory is the run's own. `--cpu-only` tells Ollama to keep the model off the GPU (`num_gpu 0`) and allows 30 minutes per model call. | Comparable times; the closest a Mac gets to a 16 GB laptop without a GPU. |
| D31 | **A cut-off is never silent.** A prompt that arrives as fewer tokens than its text can hold (> 4.5 characters per token), a context window that fills up while writing, or an answer that stops at the output limit makes the stage fail with the reason. It is not retried, and the section is recorded as failed, never dropped. | Pilot P1: Ollama silently cut 5 of the 8 single-prompt calls to the last half of the window. |
| D32 | **Context window 16,384 tokens** for the three drafting models; **output limit 3,000 tokens** for Conclusies and Aanleiding. | The largest prompt is about 12,400 tokens (pilot round 2); the conclusions did not fit 1,800 tokens (P2). Measured cost for qwen2.5:7b: +0.3–0.5 GB. |
| D33 | **Where the experiments run.** Quality is compared on the Mac's GPU; time and memory count only from CPU-only runs, and once on a borrowed corporate laptop if possible. Check V1 compares GPU and CPU runs of the same condition and moves to the start of week 2. | The end users have corporate laptops without a GPU that Ollama uses. In the pilot, CPU drafts covered more than GPU drafts twice (P5), so the choice is tested before it is relied on. |
| D34 | **Current-practice baseline C0: Microsoft 365 Copilot**, which InnoBeweegLab already uses with work accounts. It is run by hand with a fixed protocol (same source files, never the original report, one fixed prompt, three new chats) and scored with the same measures plus a manual source check. A hosted model *inside* the tool (E6) runs only through Azure OpenAI in InnoBeweegLab's own tenant. | Copilot has no interface the tool can call, and its model, settings and instructions cannot be fixed or logged; as a baseline it still answers the stakeholders' question: is the tool better than what we have? |
| D35 | **Names.** Conditions go by their experiment number: E0 template, E1 single prompt (A), E1R single prompt on raw records, E2 staged (B), E3 staged with evidence selection, E4 staged + checker step, E6 hosted, E7 final. E5 (free multi-agent) is not built: E2 and E4 are the role-separated form the brief allows. | The letters C and D meant different things in the brief and in earlier documents. |
| D36 | **Fixed in advance and frozen.** Hypotheses, measures, the decision rule and the thresholds are written down before any counted run; the set-up is frozen with the git tag `freeze-1` (planned 13 Nov). Pilot runs carry a tag and do not count. Any change after the freeze goes in the deviation log. | Differences must come from the conditions, not from changes along the way, and the winner must not be chosen after seeing the results. |

**Week-1 pilot in short** (qwen2.5:7b, seed 42, round 2 with the fixes; details in
`week1-pilot.md`): a full report took 10 min (E1) and 13 min (E2) on the GPU and 77 min (E2) on
the CPU only. Peak model memory was 5.5–5.7 GB, well under 9.6 GB. No prompt was cut off. E2 had
far fewer unsupported claims than E1 (8.5% against 26%). On the CPU, two answers got stuck
numbering invented evidence ids until the output limit (P7); the new check caught both.

**Status (4 Nov 2026).** 107 tests pass (synthetic data only). Built and run on real data:
ingestion, evidence, the template, E1 and E2 pipelines, the verifier, full-report runs with Word
output, scoring against the original, memory and time measurement, CPU-only and cold runs. Still to
build, in order (from `hypotheses-and-thresholds.md` and `week1-pilot.md`):

| Week | What |
|---|---|
| 2, before the freeze | E4 (checker step); E1R (raw records); a cap on evidence ids per claim (P7); only code, prompts and configuration count as "dirty" (P8); an evidence fingerprint in every run record; shorter evidence lines in the prompt (to decide); the Copilot import step; check V1; the freeze |
| 3 | retrieval (BM25, vector, hybrid within the theme); the second-rater workflow (blind labels, κ between raters, agreed labels) |
| 4 | statistics (mean ± sd over seeds, bootstrap intervals, paired per section, the decision rule, V1) with tables and charts from code; tooling for the user sessions (planted errors, evidence panel on/off) |
| if needed | Azure OpenAI adapter (E6); instructions for running on a Windows laptop |

**New open questions for InnoBeweegLab** (in the request drafted on 4 Nov): (15) May the Oss source files be used in
Microsoft 365 Copilot with an InnoBeweegLab work account for baseline C0? (16) Is Azure OpenAI
available in InnoBeweegLab's Microsoft environment (E6)? (17) Can a standard office laptop be
borrowed for one evening in week 4? (18) Is there another past Wijkscan report with its source
data? Question 14 (a second rater) is in the same request.
