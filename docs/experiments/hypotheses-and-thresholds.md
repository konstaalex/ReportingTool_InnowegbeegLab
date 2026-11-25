# Hypotheses and pass/fail thresholds

**Status: frozen with git tag `freeze-2` (25 November 2026).** Written as a draft on 3 November,
before any counted run; updated 4 November (where the experiments run, the Copilot baseline) and
25 November (the decisions of 19 November, the move to CPU-only runs, the stage-2 scope). The git
history of this file is the record of what was fixed when. Every change since the draft, and any
change after the freeze, is in the deviation log at the end, with the reason. Where this file and
`experimental-design-and-evaluation-framework.md` (version 1.0, 6 November) differ, this file
holds.

Sources are named in short form, e.g. (Lakens 2017); the full references are at the end.

Research question addressed: SQ3 (which architecture gives traceable, useful drafts at what cost)
and SQ4 (does it run on a 16 GB CPU-only laptop). Brief sections 16 (experiment matrix) and 17
(evaluation).

## 1. What counts as a run

| Item | Rule |
|---|---|
| Test case | `euterpepark-oss`, the only case with a human-written report to score against. All report sections that have evidence: with the frozen inputs these are eight (six themes, Algemeen, Conclusies). Aanleiding is out of scope for all counted runs: the researcher writes it by design, no condition gets researcher notes (C0 neither), no condition drafts it and it is not scored. E1R runs the six theme sections only (R6). Recommendations are excluded by design: the researcher writes them. |
| Unit | One full report (`unit = "report"` in `config/experiments.toml`). |
| Repeats | Seeds 42, 43 and 44 for every model condition; the template baseline once (it is deterministic). Temperature 0.2. Prompt version v3 (E1, E2, E3) or v4 (E4 and E1R; v4 is v3 unchanged plus the prompts these two need). |
| Counted runs | Untagged runs made after the freeze: git tag `freeze-2`, 25 November (planned: 13 November). The earlier tag `freeze-1` does not count (deviation log). The run record's `code_version` must be that commit without `-dirty`, and its fingerprints must match the frozen prompts and config. |
| Not counted | Runs with a tag (e.g. `pilot-week1`) and runs before the freeze. They size the plan only. |
| Failures | A failed section is kept, never dropped: its chapter scores as missing. A run is repeated only for an infrastructure fault (Ollama crash, killed process, power loss, laptop asleep), logged in the deviation log. A poor output is never a reason to repeat. |
| Primary model | qwen2.5:7b for stages 1 and 2; the three models in stage 3. |
| Where the runs are made | All counted runs, for quality, time and memory, on the Windows laptop (Intel i7-1165G7, 16 GB RAM), CPU-only (`--cpu-only`) and from a cold start (`--cold`), on mains power. Nothing runs on a GPU. One model call may take one hour (`timeout_s = 3600`). |
| Order | Per seed: E1, E2, E4, E1R; first seed 42, then 43, then 44. |
| Synthetic cases | They join. After 3 December: E2 and E4, seed 42, all 10 synthetic Buurtscan cases (20 reports), from the same frozen commit, cold and CPU-only, in their own workspace so that the Oss inputs do not change. Measures: U, time, peak model memory, failed sections. No S: these cases have no human-written report. Reported per case and as median and range of E4 minus E2; described, not tested, and no part of H1–H4 or of the stage 1 winner. Checked on 25 November: all 10 ingest cleanly (30 source files and 2 reference files parsed, 10 duplicates skipped, no unreadable file, no unmapped survey question; every case has its three perspectives and evidence for all eight sections; 8 chart images per case are not read and are flagged as such). |
| Machine | Recorded in every run (`machine`, `runtime.cpu_only`). |

**Names used in this file.** Conditions go by their E-number (brief section 16;
`config/experiments.toml`): E0 template, E1 single prompt, E1R single prompt on raw records, E2
staged, E3 staged with evidence selection, E4 staged with a checker step, E6 hosted model, E7 final
configuration; E5 (free multi-agent) is not built. A and B are kept as short names for E1 and E2,
the same letters as in the brief's section 12; the checker variant is the brief's C. H = hypothesis,
F = feasibility threshold, V = validity check, C0 = the Copilot baseline. The findings of the
week-1 pilot are P1–P8 in `week1-pilot.md`. D1–D6 (E4) and R1–R6 (E1R) are the design decisions of
19 November (section 4a; full reasoning in `e4-e1r-design-decisions.md`).

## 2. Measures (all computed by code)

| Measure | Code | Better | Role |
|---|---|---|---|
| **U** = unsupported-claim rate: claims with no valid citation or with a number not in the cited evidence, over all claims | `verification/checks.py` → run totals `unsupported_rate` | lower | primary: traceability (NFR1, FR5, FR6). Evidence and record ids written in a claim are not numbers. For E4 it is "the measure E4 was optimised on" (D4); for E1R it is secondary (R5) |
| **S** = coverage of what the data supports: statements of the original report that the evidence can support, and the draft covers; without Aanleiding and Aanbevelingen | `reports/evaluate.py` labels, added up in `scripts/compare_conditions.py` (the `coverage_of_supportable` in a run record still includes Aanleiding) | higher | primary: usefulness against the human report |
| Ceiling: share of the original's statements the evidence can support at all | `scripts/compare_conditions.py`, from the labels of `evaluate.py` (the `ceiling` in a run record still includes Aanleiding) | – | context: separates data limits from model limits. For Oss, without Aanleiding and Aanbevelingen: 128 of 239 statements, 53.6% (recomputed 25 November; with Aanleiding it was 131 of 250, 52.4%) |
| Number conflicts: matched statements whose numbers differ from the original | `evaluate.py` → `possible_number_conflicts` | lower | secondary |
| Numbers found back | `evaluate.py` → `numbers_agree / numbers_in_reference` | higher | secondary |
| Grounded and weak rates, citation validity | `checks.py` | – | secondary |
| Time per report; model load, prompt and output speed | run `totals`: `wall_s`, `model_load_s`, `prompt_tokens_per_s`, `output_tokens_per_s` | lower / higher | cost (SQ3), feasibility (SQ4) |
| Peak model memory | run `totals.model_peak_mb` (`llm/resources.py`): the summed memory of all Ollama processes, sampled during the run. What Ollama itself reports is lower and is kept as context | lower | feasibility (NFR5) |
| Failed sections | run `totals.failed_sections` | none | reliability |

## 3. How a difference is judged

For a comparison X versus Y, both on the same seeds and sections:

1. The difference in U and in S, pooled over the three seeds.
2. A 95% bootstrap confidence interval for that difference: for U a cluster bootstrap over
   (seed × section) clusters within each section (Field & Welsh 2007), for S the original's
   statements, paired, within each section. 2,000 resamples (confirmed 19 November; 1,000 to 2,000
   is the usual minimum for percentile intervals: Cowles 2017; resampling tests are the standard
   advice for NLP metrics: Dror et al. 2018).
3. The minimum meaningful difference (MMD) is **5 percentage points** for U and S (confirmed
   19 November; a smallest effect of interest fixed before the runs is the core of equivalence
   testing: Lakens 2017). It is my judgement of the smallest change a researcher would notice in a full report; no
   published standard exists for this task.

| Outcome | Rule |
|---|---|
| **Better / worse** | the interval excludes 0 **and** the difference is at least the MMD |
| **No meaningful difference** | the whole interval lies within ± MMD |
| **Inconclusive** | anything else: reported as such, never as a win |

"Whole interval within ± MMD" with a 95% interval is stricter than the usual 90% equivalence test, so
"no meaningful difference" is a conservative verdict.

Details of the calculation, in `scripts/compare_conditions.py` (proposed, confirmed with this freeze):

- *Pooled* means: all claims of the three seeds together (U); per statement of the original, the
  credit (covered 1, partial ½, missing 0) averaged over the three seeds (S).
- The sections are the strata, for U and for S.
- U: claims of one run are not independent. The claims of one section are written in the same few
  model calls on the same evidence, so one bad answer makes several claims wrong together.
  Resampling single claims would treat them as independent and can give intervals that are too
  narrow. So each draw takes, within each section, the (seed × section) clusters with replacement
  (three clusters, one per seed) and then the claims within each drawn cluster with replacement;
  separately per condition, because they are different claims. A section that failed in a run is an
  empty cluster. The cluster bootstrap is consistent for clustered data under a random-effect model
  (Field & Welsh 2007); drawing claims within the clusters as well makes the interval wider. With
  three clusters per section it stays an approximation.
- S: the original's statements are resampled once per draw for both conditions, because they are the
  same statements.
- Percentile interval; random seed of the bootstrap 20261126.
- "Not worse by more than the MMD" and the selection rules of section 4 use the pooled values;
  "better" and "worse" use the table above.
- For H1, S, U and the number measures of E1 and E1R are computed on the six theme sections and the
  six theme chapters of the original.
- The script refuses to compare if a run is missing, double, dirty, not CPU-only, not cold, or from
  another commit, or if inputs or ceiling differ.

**Smallest detectable difference.** With one case and three seeds, the smallest difference in U that
this design can detect is about 10 percentage points (a simulation with pilot-like numbers of claims
and rates: half-width of the interval about 9 to 11 points). That is more than the MMD of 5 points. A
smaller difference in U will usually come out as inconclusive, for H2 and H3 alike. H3 therefore also
rests on the blind support check and on S (D4).

With one case and three seeds these are descriptive intervals over statements of one report, not
a test that generalises to other neighbourhoods. The results chapter says so.

## 4. Hypotheses

| Id | Stage | Hypothesis | Measure and threshold |
|---|---|---|---|
| **H1** evidence layer | 1 | A single prompt that gets computed evidence (E1, condition A) produces fewer unsupported claims and fewer wrong numbers than the same prompt given the raw records (E1R, "A without the evidence layer", close to how Copilot is used today). Both on the six theme sections (R6). | Number conflicts lower and numbers found back higher (`evaluate.py`): these decide, because they do not depend on the citation format (R5). U (E1 lower by ≥ MMD) is reported as secondary: the verifier accepts only numbers that literally appear in the cited records, so a correctly computed share counts as unsupported, which biases U against E1R. In the pilot, number conflicts were 0 for E0, E1 and E2, so this measure may not separate the two conditions. A section of E1R that does not fit the context window is failed and counts as a result for H1 (R4). |
| **H2** staging | 1 | Staged drafting (E2, B: findings per perspective, then synthesis) covers more of what the data supports than the single prompt (A), without more unsupported claims. | S higher by ≥ MMD, and U not worse by more than the MMD. |
| **H3** verification | 1 | Adding a checker step (E4) lowers unsupported claims compared with B, at little cost in coverage. | U lower by ≥ MMD, **and** the share of claims that people judge supported is not lower for E4 than for E2 in the blind check (section 4a, D4), **and** S not worse by more than the MMD. Number conflicts, withdrawal rate, still-flagged revisions, extra calls and extra minutes per report are reported (D4, D6). |
| **H4** retrieval | 2 | Selecting evidence before drafting (top-25 salience, BM25; both within the theme) lowers U but also lowers S, compared with giving all evidence of the theme. | Judged per variant with the rule in section 3; a variant that lowers U without lowering S by more than the MMD supports retrieval. |
| **H5** models | 3 | No direction expected between qwen2.5:7b, qwen3:8b and llama3.1:8b. Llama 3.1 does not list Dutch among its supported languages; this is recorded, not tested. | Exploratory: U, S, time and memory with intervals. |

Stage-selection rules, fixed now so the winner is not picked after seeing the results:

- **Stage 1 (pipeline):** among E1, E2 and E4, the condition with the highest S whose U is not worse
  than the lowest U by more than the MMD. If two are within the MMD on both, the faster one wins.
  E1R is a reference, never a candidate.
- **Stage 2 (selection):** the same rule among all evidence, top-25 and BM25. Vector and hybrid are dropped (deviation log).
- **Exploratory hybrid run (stage 2):** one run with hybrid selection (BM25 plus a local embedding
  model, top-25 per theme), seed 42, on the themes Actieve mobiliteit, Sporten and Formeel spelen:
  the three with the most evidence items (119, 103 and 101), where selection can matter most. After
  the counted stage 2 runs, if time allows. It is tagged, reported as exploratory, and never counts
  for H4 or for a selection rule. The embedding model is fixed in the stage 2 freeze.
- **Stage 3 (model):** the same rule among the three models, on the stage-2 winner. The final
  configuration must also pass F1 to F3.
- Stages 2 and 3 run after 3 December. BM25 is not built at this freeze; building it changes the
  code, so stages 2 and 3 get their own frozen commit before they start, logged below. Measures,
  thresholds and rules stay as they are here.
- Rule of 3 November: if a full report takes more than about 3 hours, stages 2 and 3 use 3 of the 6
  themes. The runs of 24 and 25 November took 1.5 to 2.2 hours per report, so they use all six.

**Literature basis per hypothesis** (short; H3 is in section 4a).

| Id | Basis | Counter-evidence and limits |
|---|---|---|
| **H1** | Calculation handed to code instead of the model is more accurate (PAL, Gao et al. 2023b). How well models read tables depends on the table format (Sui et al. 2024). Statements that models write from statistical tables may not follow from the table; the largest model tested was the most faithful (STAT-TO-TEXT, Eida et al. 2026). | PAL is about mathematical, symbolic and algorithmic tasks, not reports, and there the model writes the code; here the code is written beforehand. |
| **H2** | Gathering material per perspective before writing gave articles that were more often broad (+10 points) and well organised (+25 points) than an outline-based baseline (STORM, Shao et al. 2024). | A multi-agent system for automated engineering reports scored below a naive single pass overall (3.60 against 3.78 on a 7-point scale) and was better only on factuality (Chalmers/Volvo master's thesis, Nihlstrand & Lundberg 2025, larger hosted models). STORM itself reports over-association of unrelated facts as a new problem. With equal token budgets single-agent systems match or beat multi-agent ones on multi-hop questions, and many reported gains come from more computation (Tran & Kiela 2026). E2 uses more calls than E1, so its time is reported with it. **H2 is pilot-informed:** the pilot of 4 November (one seed; U 8.5% for E2 against 26% for E1) was known when the thresholds were confirmed. Pilot runs do not count. |
| **H4** | Models use information in the middle of a long context less well (Lost in the Middle, Liu et al. 2024), so a shorter, selected list may give fewer errors. | What is left out cannot be covered, so S may drop; H4 expects both. |
| **H6** | Explanations did not by themselves make people and AI together better, and they raised the chance that people accept the AI's answer whether or not it is right (Bansal et al. 2021). H6 therefore counts planted errors found, not trust. | The same result is the reason H6 may fail. |
| **H7** | SUS ≥ 68 as the usability bar (Bangor, Kortum & Miller 2008; Sauro 2011): the average over many systems is about 68. | 68 is an average, not "good". With 4–5 people it is described, not tested. |
| **H8** | Writing with an AI model cut measured time by 40% in a controlled experiment (Noy & Zhang 2023). | Those tasks did not need precise facts. A self-estimate is not a measured time, so it is secondary. |

## 4a. The checker step (E4) and the raw-records baseline (E1R)

Accepted on 19 November, built and accepted on 25 November. Reasoning and the options not chosen:
`e4-e1r-design-decisions.md`.

| Id | Rule | Basis |
|---|---|---|
| **D1** feedback | The existing deterministic verifier (`verification/checks.py`), not a model judging itself. | Self-correction without reliable external feedback does not work (Huang et al. 2024; Kamoi et al. 2024); tool feedback drives the gain (Gou et al. 2024); models prefer their own text (Panickssery et al. 2024). A separate judge per claim (Tang et al. 2024) is further work. |
| **D2** what goes back | Only claims with status *unsupported*. Grounded and weak claims are never touched. | Avoids changing correct text into wrong text (Huang et al. 2024). |
| **D3** what the model may do | One revision round per section, all its unsupported claims in one call. Per claim: fix the citation, fix the number, reword to what the cited evidence says, or withdraw. Revised claims are verified again and kept even if still flagged. Original and revision are both in the run record. | Revise what is wrong and preserve the rest (Gao et al. 2023a). |
| **D4** fair measurement | U stays as fixed, labelled "the measure E4 was optimised on", plus four checks E4 never saw: the blind human support check, S, number conflicts, and the withdrawal rate with the number of still-flagged revisions. | A measure that becomes a target (Manheim & Garrabrant 2018); over-evaluated self-correction (Kamoi et al. 2024); support judged per claim against a fixed source (Min et al. 2023). |
| **D5** what the reviser sees | Same model and settings. Only the flagged claim, its flag messages and the section's full evidence list; not the other claims. Same JSON schema with at most 6 ids. | Checking without the draft in view avoids repeating errors (Dhuliawala et al. 2024). |
| **D6** cost | Extra calls and minutes per report. | – |
| **R1** which records | Exactly the source records behind the section's evidence items (union of their `source_record_ids`); same "not available" list and quote setting as E1. | One factor at a time (Meyes et al. 2019). |
| **R2** format | One Markdown pipe table per source sheet, file and sheet as a heading, original column headers, record id as the first column. | The format changes the result, so it is fixed and standard (Sui et al. 2024). Limitation: another format might do better. |
| **R3** prompt | The v3 prompts with two changes: the data block is called "brongegevens" and claims cite record ids; rule 3 becomes "if you calculate, say what you calculated from". | Rule 3 ("do not calculate") cannot apply to raw rows. |
| **R4** window | 16,384 tokens, like E1. Counted before the freeze: the six prompts are 7,157 to 12,615 tokens, 8,957 to 14,415 with a full answer. All fit, so the 32K sensitivity run is not needed. | The existing truncation check (pilot P1). |
| **R5** scoring | Number conflicts and numbers found back decide H1; U is secondary. | See H1. |
| **R6** sections | The six theme sections only, for both E1R and E1 in this comparison. | Conclusies and Aanleiding have no raw equivalent. |

**Blind support check (D4), in the same labelling session as the 50-verdict audit of the verifier
and the 89 statements for the S scorer.** Proposed, confirmed with this freeze: 50 claims, 25 from E2
and 25 from E4, drawn at random over the three seeds with a fixed random seed, mixed, condition
hidden. The two raters of section 6 judge per claim: supported, partly, or not supported by the cited evidence. The
share "supported" of E4 must not be lower than that of E2. With 25 claims each this can only show a
large loss; it is reported as counts, not tested.

**As built (details the decisions left open; accepted 25 November).**

1. E4 sends back only the messages of the flags that make a claim unsupported. Claim type and
   paragraph stay as they were.
2. The revision schema adds the claim id and the chosen outcome. A claim that gets no answer keeps
   its original text.
3. An id that exists but belongs to another section is marked weak by the verifier, as in every
   other condition; it does not make a claim unsupported.
4. E1R's tables show the RIVM rule of thumb as E1 sees it, decimal commas, and at most 4 decimals.
5. The renaming in R3 also touched system rules 1, 2 and 6, one definition line and one line of the
   section prompt. The content of these rules is unchanged.

## 4b. Current practice: Microsoft 365 Copilot (C0)

InnoBeweegLab already uses Microsoft 365 Copilot with work accounts. A draft made the way a
researcher could make it today is the most relevant reference for the stakeholders: is the tool
better than what they already have? Copilot cannot be a condition inside the tool: it has no
interface the tool can call, and its model, temperature, seed and system instructions cannot be
fixed or logged. So C0 is run by hand, with a protocol fixed in advance.

C0 is run only with InnoBeweegLab's written OK, given before the first chat. It is a logged exception
to NFR7 (data stays local unless explicitly and separately authorised) and to the project rule that no
hosted model sees the data. It covers these three C0 runs, in InnoBeweegLab's own tenant, and nothing
else. Without the written OK, C0 is not run and H9 is reported as not tested.


| Item | Rule |
|---|---|
| Account | An InnoBeweegLab work account with Microsoft 365 Copilot (enterprise data protection), not a personal or student account. |
| Input | The same Oss source files the tool reads (survey and expert spreadsheets, maps). No researcher notes, as in every condition. Never the original report: it is the reference. Never the tool's evidence or drafts. |
| Prompt | One fixed Dutch prompt, written before the first C0 run and kept in `prompts/copilot_c0_v1.txt`: the same report chapters (six themes, Algemeen, Conclusies), the same writing rules, and the request to name for every fact the file, sheet and cell or row it comes from. Copilot gets the fairest chance at traceability that a researcher could give it. |
| Repeats | Three, each in a new chat, within one week. Recorded: date, the model Copilot names if it shows one, and the minutes of the researcher's own effort. |
| Scoring | The text is imported per chapter and scored with `reports/evaluate.py` like any run (S, ceiling, numbers found back, number conflicts, additions). The verifier cannot check claims without evidence ids, so traceability is checked by hand on a random sample of 30 factual statements per run (confirmed 19 November): is a source named, and does that source support the statement? The same check is applied to the same number of statements of the final configuration. |

| Id | Hypothesis | Measure and threshold |
|---|---|---|
| **H9** current practice | The final configuration produces more traceable drafts than Copilot: a smaller share of factual statements without a supporting source, and fewer number conflicts, at a coverage of what the data supports that is not lower by more than the MMD. | Share of sampled statements without a supporting source, lower by ≥ MMD **(P)**; number conflicts; S. Copilot's model can change without notice: the result describes Copilot as used on those dates. |

C0 is a reference, not a candidate for the final configuration. E1R (a local single prompt on raw
records) remains the controlled version of the same idea: same model and pipeline, only the
evidence layer removed.

## 5. Feasibility thresholds (SQ4, pass/fail)

| Id | Threshold | Source |
|---|---|---|
| **F1** memory | Peak model memory ≤ 9.6 GB in every run of the final configuration (also reported per condition). Measured as the sum of the Ollama processes. | `config/models.toml [hardware]`: 0.6 × 16 GB (NFR5) |
| **F2** time | A full report with the final configuration finishes within 8 hours on CPU only, i.e. overnight (confirmed 19 November), on this laptop. | NFR5 asks for "a practical working session" without a number; Scenario C has the work run overnight |
| **F3** reliability | Every section drafted in all three runs of the final configuration **(P)** | – |
| **F4** cost | No paid service for local runs (EUR 0) | NFR6 |
| **F5** target laptop | Pass = one complete CPU-only report of the stage-1 winner, seed 42, on the InnoBeweegLab laptop (HP ProBook, Core 5 120U, 16 GB), with no failed sections and peak model memory ≤ 9.6 GiB (the limit of F1 as the code computes it). If the visit on 3 December ends first, the run continues with their OK and the record is collected later. Otherwise two themes are run (Sporten and Actieve mobiliteit, the first two in the report) and F5 is reported as partial, not as a pass. Also reported: time, and the memory other programs (Teams, browser, Outlook) already used before the run | the end users' hardware; `week1-pilot.md` P3 |

**V1 (GPU against CPU) is dropped:** no GPU run remains to compare with (deviation log).

## 6. Raters, and is the scorer good enough?

**Raters.** Two: Alex (the developer), and the product owner of InnoBeweegLab as second rater. A
colleague rates too if available and is then reported as a third rater. Who rated, what they saw, the
definitions, the sample sizes and the agreement are all reported (van der Lee et al. 2021).

- *Training:* a short session on the label definitions, with a few examples that are not in the
  samples. One written guideline for all tasks.
- *Independence:* the raters label alone and compare only after κ has been computed.
- *Blinding:* items in random order; hidden are the condition, the seed, the verifier's status and
  flags, the automatic label and the other rater's label.
- *Samples:* 89 statements (scorer for S), 50 verifier verdicts, 50 claims for the blind support check
  (25 E2, 25 E4; section 4a), and 30 statements per C0 run with the same number of the final
  configuration.
- *Limits:* Alex built the tool and may recognise a condition; the product owner is also the expert of
  the Showroom session. Both are stated with the results.

**Scorer.**

S comes from the word-overlap scorer. It agreed with the developer's labels on 89 statements at
κ = 0.38. Before S is used as a primary measure:

1. The second rater labels the same statements on their own; κ between the two raters is reported.
   If it is below 0.61, the label definitions are revised once and the labelling repeated.
2. The two raters agree on one label per statement; that set is the reference.
3. Word overlap is the primary scorer for S if its κ against the agreed labels is **≥ 0.61**
   (confirmed 19 November), the lower bound of "substantial" agreement in Landis and Koch (1977). Otherwise the
   local LLM judge is used if it reaches 0.61. Otherwise S is reported from the human-labelled
   statements only, with the automatic score as secondary.

## 7. Human evaluation (weeks 5 and 6, descriptive)

Four to five InnoBeweegLab researchers, one session each. With this many people, results are
described, not tested.

| Id | Expectation | Measure |
|---|---|---|
| **H6** verification interface | Reviewers catch more planted errors with the evidence panel than without (NFR3). | Share of planted errors found and minutes per section, per person and condition. The order of the conditions alternates. |
| **H7** usefulness | The tool is usable and its draft is rated useful for report writing. | Primary: mean SUS ≥ 68. Secondary: median ≥ 4 on the 5-point rubric of brief section 17 **(P)**, rated blind to the configuration where feasible. |
| **H8** time saved | Working from the draft saves at least 25% of the writing time. | Primary, within-subject: each researcher writes one short section from scratch and edits one tool draft of a comparable section. The order is counterbalanced over the researchers, and so is which of the two sections is written from scratch. Measured minutes, from start to the researcher's "done". H8 holds if the draft takes at least 25% less time: the median over the researchers of 1 − (minutes with the draft / minutes from scratch) is ≥ 25%. Secondary: the researcher's own estimate. Survey: 3 of 5 respondents wanted 25–50%, 2 of 5 wanted 10–25%. |

**Showroom.** The Showroom (expert validation) is the product owner's expert session on the final
configuration plus the comparison with current practice, C0 (section 4b).

## 8. Known limitations, stated in advance

- One case with a human-written report; results describe that case.
- The pipeline is chosen on one model; the best pipeline may differ per model.
- The second rater belongs to the client organisation.
- Until the second rater has labelled, S rests on the developer's own labels.
- E4 was repaired with the verifier that measures U; only the human check, S and the number measures
  are independent of it.
- The same seed does not give the same text twice: E1 and E2 with seed 42 had other output lengths on
  19 and 24 November (cause not known). E4's first draft is therefore not E2's draft of the same seed,
  and the comparison is between conditions, not within one draft.
- In the pilot (one seed) the interval for a difference in U was about ± 11 percentage points. With
  three seeds and the cluster bootstrap it is expected to be about ± 9 to 11 (a simulation with
  pilot-like numbers of claims and rates, and different amounts of variation between seeds;
  resampling single claims would give about ± 7). Most differences in U smaller than about 10 points
  will come out as inconclusive, and "no meaningful difference" is practically out of reach for U.
  The stage 1 rule uses the pooled values and still gives a winner.
- The laptop resembles the end users' laptops but is one machine; F5 records another.
- On a corporate 16 GB laptop other programs often use about half the memory. The tool is expected
  to run with them closed, e.g. overnight; F5 records what was open.

## 9. Decided before the freeze, and still open

Decided: the five **(P)** values of 19 November (MMD, resamples, κ, F2, the Copilot sample); what E4
does with a rejected claim and how E1R presents records (section 4a); stage-2 scope; all runs CPU-only;
Aanleiding out of scope; the synthetic cases join; F5; the raters; the Showroom; H8.

Still open, and not affecting stage 1: the **(P)** values in section 4b (H9), F3 and section 7 (the H7
rubric median); the hosted model (E6), only if InnoBeweegLab gives access to Azure
OpenAI in its own Microsoft tenant (EU region), otherwise it is not run and the discussion explains
why.

## Deviation log

| Date | Change | Reason |
|---|---|---|
| 2026-11-19 | All counted runs CPU-only (`--cpu-only`) on the Windows laptop (i7-1165G7, 16 GB); quality is no longer compared on a GPU | The MacBook failed on 17 November; the CPU-only laptop matches the end users' hardware |
| 2026-11-19 | Check V1 (GPU against CPU) dropped | No GPU run remains to compare with |
| 2026-11-19 | Prompt v3 (v2 + at most 6 evidence ids per claim, also in the JSON schema) | Pilot finding P7: on the CPU the model listed invented ids until the output limit |
| 2026-11-19 | "-dirty" only for uncommitted changes to code, prompts or configuration | Pilot finding P8: documentation edits during a run marked it dirty |
| 2026-11-19 | H3 judged on U plus a blind human support check, S, number conflicts and withdrawals | E4 is repaired with the verifier that measures U (D4) |
| 2026-11-19 | E1R on the six theme sections only, with the two prompt changes of R3 | No raw equivalent for Conclusies and Aanleiding; rule 3 cannot apply to raw rows |
| 2026-11-19 | One-hour model timeout (`timeout_s = 3600`; it was 600 s) | In the speed test every failed section had hit the 600 s limit; a call can take longer on the CPU |
| 2026-11-19 | F1 measured as the sum of the Ollama processes | Ollama's own report (5.7 GB) is lower than what its processes use; the higher value is the safer one for a pass/fail limit |
| 2026-11-25 | Verifier and scorer: evidence and record ids in a claim's text are not numbers | The digits in an id such as "X-AB-01" were read as a number. Fixed after the pilot and before any counted run. Re-checking the pilot drafts of 24 November: U of E2 19.5% to 12.2%, U of E1 unchanged at 20.8%; S, numbers found back and number conflicts unchanged |
| 2026-11-25 | Freeze moved from 13 November to 25 November (the plan of 24 November said the 26th) | The MacBook failed on 17 November and the work moved to a Windows laptop; E4 and E1R were finished on 25 November |
| 2026-11-25 | Stage 2 compares all, top-25 and BM25; vector and hybrid dropped; stages 2 and 3 run after 3 December, with their own frozen commit | Five variants are the most expensive stage, and vector and hybrid also need an embedding model; BM25 is not built yet |
| 2026-11-25 | Details fixed that were open: the calculation in section 3, the size and rule of the blind support check, and the section 4a details "as built" | Written down before any counted run |
| 2026-11-25 | U: cluster bootstrap over (seed × section) clusters within each section instead of resampling single claims | Claims of one run are not independent (Field & Welsh 2007); the first version of the script treated them as independent |
| 2026-11-25 | Literature basis per hypothesis added (H1, H2, H4, H6, H7, H8), with counter-evidence; H2 marked as pilot-informed | The basis was not in this file |
| 2026-11-25 | H7: mean SUS ≥ 68 primary, rubric median secondary | An established usability bar (Bangor, Kortum & Miller 2008; Sauro 2011) |
| 2026-11-25 | H8: measured editing time primary, within-subject (one section from scratch, one tool draft, counterbalanced, ≥ 25% less time); self-estimate secondary | A measured time is stronger evidence than an estimate (Noy & Zhang 2023); the same person in both conditions, because writers differ in speed |
| 2026-11-25 | One exploratory hybrid run added to stage 2 (seed 42, three themes, after the counted runs, if time allows) | Hybrid was dropped for cost; one run shows whether it is worth further work. It never counts for H4 |
| 2026-11-25 | Aanleiding out of scope for all counted runs; not scored; C0 gets no researcher notes | Written by the researcher by design. Leaving out its 11 statements (3 supportable) changes the ceiling from 131 of 250 (52.4%) to 128 of 239 statements (53.6%). Pilot results keep the old basis |
| 2026-11-25 | The 10 synthetic cases join: E2 and E4, seed 42, after 3 December; U, time, memory, failed sections | More than one case for what needs no human-written report; all 10 ingest cleanly |
| 2026-11-25 | F5 has a pass rule and a named laptop (HP ProBook, Core 5 120U, 16 GB); it was descriptive | The end users' hardware is known; the rule also says what happens if the visit ends first |
| 2026-11-25 | Raters named (Alex, product owner), with training, blinding and sample sizes | Reporting advice for human evaluation (van der Lee et al. 2021) |
| 2026-11-25 | C0 only with InnoBeweegLab's written OK, as a logged exception to NFR7 | Copilot is a hosted model; NFR7 asks for explicit, separate authorisation |
| 2026-11-25 | Showroom = the product owner's expert session plus C0 | It was the three reference points of the framework document |
| 2026-11-25 | Smallest detectable difference in U (about 10 points) stated in section 3 | The cluster bootstrap gives wider intervals than expected; stated before any counted run |
| 2026-11-25 | freeze-1 (d56ac62) superseded before any counted run; counted runs come from `freeze-2` | Reasons: cluster bootstrap, Aanleiding out of scope, literature basis, F5 rule, raters, C0 exception, Showroom |

## Sources

- Huang et al. (2024). Large Language Models Cannot Self-Correct Reasoning Yet. ICLR 2024. https://arxiv.org/abs/2310.01798
- Kamoi et al. (2024). When Can LLMs Actually Correct Their Own Mistakes? A Critical Survey of Self-Correction of LLMs. TACL 12. https://aclanthology.org/2024.tacl-1.78/
- Gou et al. (2024). CRITIC: Large Language Models Can Self-Correct with Tool-Interactive Critiquing. ICLR 2024. https://arxiv.org/abs/2305.11738
- Dhuliawala et al. (2024). Chain-of-Verification Reduces Hallucination in Large Language Models. Findings of ACL 2024. https://aclanthology.org/2024.findings-acl.212.pdf
- Gao et al. (2023a). RARR: Researching and Revising What Language Models Say, Using Language Models. ACL 2023. https://arxiv.org/pdf/2210.08726
- Tang, Laban & Durrett (2024). MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents. EMNLP 2024. https://aclanthology.org/2024.emnlp-main.499
- Min et al. (2023). FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation. EMNLP 2023. https://arxiv.org/pdf/2305.14251
- Panickssery, Bowman & Feng (2024). LLM Evaluators Recognize and Favor Their Own Generations. NeurIPS 2024. https://papers.nips.cc/paper_files/paper/2024/hash/7f1f0218e45f5414c79c0679633e47bc-Abstract-Conference.html
- Sui et al. (2024). Table Meets LLM: Can Large Language Models Understand Structured Table Data? WSDM 2024. https://www.microsoft.com/en-us/research/publication/table-meets-llm-can-large-language-models-understand-structured-table-data-a-benchmark-and-empirical-study/
- Manheim & Garrabrant (2018). Categorizing Variants of Goodhart's Law. https://arxiv.org/abs/1803.04585
- Lakens (2017). Equivalence Tests: A Practical Primer for t Tests, Correlations, and Meta-Analyses. https://pmc.ncbi.nlm.nih.gov/articles/PMC5502906
- Dror et al. (2018). The Hitchhiker's Guide to Testing Statistical Significance in Natural Language Processing. ACL 2018. https://aclanthology.org/P18-1128/
- Cowles (2017). More on the Bootstrap, lecture notes, University of Iowa. https://homepage.divms.uiowa.edu/~kcowles/STAT5400_2017/lect12.pdf
- Meyes et al. (2019). Ablation Studies in Artificial Neural Networks. https://alphaxiv.org/abs/1901.08644
- llama.cpp. GBNF guide: JSON schemas to grammars (maxItems in schemas, behind the P7 fix). https://raw.github.com/ggerganov/llama.cpp/master/./grammars/README.md
- Gao et al. (2023b). PAL: Program-aided Language Models. ICML 2023, PMLR 202. https://proceedings.mlr.press/v202/gao23f.html
- Eida et al. (2026). From Tables to Quantified Statements: Evaluating LLM Inference Generation through Executable Verification (STAT-TO-TEXT). arXiv preprint. https://arxiv.org/abs/2609.23966
- Shao et al. (2024). Assisting in Writing Wikipedia-like Articles From Scratch with Large Language Models (STORM). NAACL 2024. https://aclanthology.org/2024.naacl-long.347
- Nihlstrand & Lundberg (2025). Automatic Engineering Report Generation Using Multi-Agent Systems and Large Language Models. Master's thesis, Chalmers University of Technology and University of Gothenburg, with Volvo Trucks (reviewed in the background research report, reference 28).
- Tran & Kiela (2026). Single-Agent LLMs Outperform Multi-Agent Systems on Multi-Hop Reasoning Under Equal Thinking Token Budgets. arXiv preprint. https://arxiv.org/abs/2604.02460
- Liu et al. (2024). Lost in the Middle: How Language Models Use Long Contexts. TACL 12. https://aclanthology.org/2024.tacl-1.9/
- Bansal et al. (2021). Does the Whole Exceed its Parts? The Effect of AI Explanations on Complementary Team Performance. CHI 2021. https://doi.org/10.1145/3411764.3445717
- Bangor, Kortum & Miller (2008). An Empirical Evaluation of the System Usability Scale. International Journal of Human-Computer Interaction 24(6), 574–594. https://doi.org/10.1080/10447310802205776
- Sauro (2011). A Practical Guide to the System Usability Scale: Background, Benchmarks & Best Practices. Measuring Usability LLC.
- Noy & Zhang (2023). Experimental evidence on the productivity effects of generative artificial intelligence. Science 381(6654), 187–192. https://doi.org/10.1126/science.adh2586
- van der Lee et al. (2021). Human evaluation of automatically generated text: Current trends and best practice guidelines. Computer Speech & Language 67, 101151. https://doi.org/10.1016/j.csl.2020.101151
- Field & Welsh (2007). Bootstrapping clustered data. Journal of the Royal Statistical Society, Series B 69(3), 369–390. https://doi.org/10.1111/j.1467-9868.2007.00593.x
- Landis & Koch (1977). The Measurement of Observer Agreement for Categorical Data. Biometrics 33(1), 159–174.
