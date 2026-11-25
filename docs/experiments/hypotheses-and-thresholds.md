# Hypotheses and pass/fail thresholds

**Status: frozen with git tag `freeze-1` (25 November 2026).** Written as a draft on 3 November,
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
| Test case | `euterpepark-oss`, the only case with a human-written report to score against. All report sections that have evidence: with the frozen inputs these are eight (six themes, Algemeen, Conclusies). Aanleiding needs researcher notes, which are not in the inputs, so no condition drafts it. E1R runs the six theme sections only (R6). Recommendations are excluded by design: the researcher writes them. |
| Unit | One full report (`unit = "report"` in `config/experiments.toml`). |
| Repeats | Seeds 42, 43 and 44 for every model condition; the template baseline once (it is deterministic). Temperature 0.2. Prompt version v3 (E1, E2, E3) or v4 (E4 and E1R; v4 is v3 unchanged plus the prompts these two need). |
| Counted runs | Untagged runs made after the freeze: git tag `freeze-1`, 25 November (planned: 13 November). The run record's `code_version` must be that commit without `-dirty`, and its fingerprints must match the frozen prompts and config. |
| Not counted | Runs with a tag (e.g. `pilot-week1`) and runs before the freeze. They size the plan only. |
| Failures | A failed section is kept, never dropped: its chapter scores as missing. A run is repeated only for an infrastructure fault (Ollama crash, killed process, power loss, laptop asleep), logged in the deviation log. A poor output is never a reason to repeat. |
| Primary model | qwen2.5:7b for stages 1 and 2; the three models in stage 3. |
| Where the runs are made | All counted runs, for quality, time and memory, on the Windows laptop (Intel i7-1165G7, 16 GB RAM), CPU-only (`--cpu-only`) and from a cold start (`--cold`), on mains power. Nothing runs on a GPU. One model call may take one hour (`timeout_s = 3600`). |
| Order | Per seed: E1, E2, E4, E1R; first seed 42, then 43, then 44. |
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
| **S** = coverage of what the data supports: statements of the original report that the evidence can support, and the draft covers | `reports/evaluate.py` → `coverage_of_supportable` | higher | primary: usefulness against the human report |
| Ceiling: share of the original's statements the evidence can support at all | `evaluate.py` → `ceiling` | – | context: separates data limits from model limits |
| Number conflicts: matched statements whose numbers differ from the original | `evaluate.py` → `possible_number_conflicts` | lower | secondary |
| Numbers found back | `evaluate.py` → `numbers_agree / numbers_in_reference` | higher | secondary |
| Grounded and weak rates, citation validity | `checks.py` | – | secondary |
| Time per report; model load, prompt and output speed | run `totals`: `wall_s`, `model_load_s`, `prompt_tokens_per_s`, `output_tokens_per_s` | lower / higher | cost (SQ3), feasibility (SQ4) |
| Peak model memory | run `totals.model_peak_mb` (`llm/resources.py`): the summed memory of all Ollama processes, sampled during the run. What Ollama itself reports is lower and is kept as context | lower | feasibility (NFR5) |
| Failed sections | run `totals.failed_sections` | none | reliability |

## 3. How a difference is judged

For a comparison X versus Y, both on the same seeds and sections:

1. The difference in U and in S, pooled over the three seeds.
2. A 95% bootstrap confidence interval for that difference, resampling claims (for U) or the
   original's statements (for S) within each section, 2,000 resamples (confirmed 19 November; 1,000
   to 2,000 is the usual minimum for percentile intervals: Cowles 2017; resampling tests are the
   standard advice for NLP metrics: Dror et al. 2018).
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
- The sections are the strata. Claims are resampled separately per condition, because they are
  different claims. The original's statements are resampled once per draw for both conditions,
  because they are the same statements.
- Percentile interval; random seed of the bootstrap 20261126.
- "Not worse by more than the MMD" and the selection rules of section 4 use the pooled values;
  "better" and "worse" use the table above.
- For H1, S, U and the number measures of E1 and E1R are computed on the six theme sections and the
  six theme chapters of the original.
- The script refuses to compare if a run is missing, double, dirty, not CPU-only, not cold, or from
  another commit, or if inputs or ceiling differ.

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
- **Stage 3 (model):** the same rule among the three models, on the stage-2 winner. The final
  configuration must also pass F1 to F3.
- Stages 2 and 3 run after 3 December. BM25 is not built at this freeze; building it changes the
  code, so stages 2 and 3 get their own frozen commit before they start, logged below. Measures,
  thresholds and rules stay as they are here.
- Rule of 3 November: if a full report takes more than about 3 hours, stages 2 and 3 use 3 of the 6
  themes. The runs of 24 and 25 November took 1.5 to 2.2 hours per report, so they use all six.

## 4a. The checker step (E4) and the raw-records baseline (E1R)

Accepted on 19 November, built and accepted on 25 November. Reasoning and the options not chosen:
`e4-e1r-design-decisions.md`.

| Id | Rule | Basis |
|---|---|---|
| **D1** feedback | The existing deterministic verifier (`verification/checks.py`), not a model judging itself. | Self-correction without reliable external feedback does not work (Huang et al. 2024; Kamoi et al. 2024); tool feedback drives the gain (Gou et al. 2024); models prefer their own text (Panickssery et al. 2024). A separate judge per claim (Tang et al. 2024) is further work. |
| **D2** what goes back | Only claims with status *unsupported*. Grounded and weak claims are never touched. | Avoids changing correct text into wrong text (Huang et al. 2024). |
| **D3** what the model may do | One revision round per section, all its unsupported claims in one call. Per claim: fix the citation, fix the number, reword to what the cited evidence says, or withdraw. Revised claims are verified again and kept even if still flagged. Original and revision are both in the run record. | Revise what is wrong and preserve the rest (Gao et al. 2023). |
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
hidden. Two raters judge per claim: supported, partly, or not supported by the cited evidence. The
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
fixed or logged. So C0 is run by hand, with a protocol fixed in advance:

| Item | Rule |
|---|---|
| Account | An InnoBeweegLab work account with Microsoft 365 Copilot (enterprise data protection), not a personal or student account. |
| Input | The same Oss source files the tool reads (survey and expert spreadsheets, maps, researcher notes). Never the original report: it is the reference. Never the tool's evidence or drafts. |
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
| **F5** target laptop | Descriptive: one run of the final configuration on the stakeholder's or another typical corporate laptop (16 GB, CPU only). For the hand-over on 3 December this is the stage-1 winner, seed 42. Reported: time, peak model memory, and the memory other programs (Teams, browser, Outlook) already used before the run | the end users' hardware; `week1-pilot.md` P3 |

**V1 (GPU against CPU) is dropped:** no GPU run remains to compare with (deviation log).

## 6. Is the scorer good enough?

S comes from the word-overlap scorer. It agreed with the developer's labels on 89 statements at
κ = 0.38. Before S is used as a primary measure:

1. A second rater labels the same statements on their own; κ between the two raters is reported.
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
| **H7** usefulness | The final configuration is rated useful for report writing. | Median ≥ 4 on a 5-point scale **(P)**; the brief section 17 rubric, rated blind to the configuration where feasible. |
| **H8** time saved | Researchers estimate that working from the draft saves at least 25% of their writing time **(P)**. | Self-estimate. Survey: 3 of 5 respondents wanted 25–50%, 2 of 5 wanted 10–25%. |

## 8. Known limitations, stated in advance

- One case with a human-written report; results describe that case.
- The pipeline is chosen on one model; the best pipeline may differ per model.
- Until the second rater has labelled, S rests on the developer's own labels.
- E4 was repaired with the verifier that measures U; only the human check, S and the number measures
  are independent of it.
- The same seed does not give the same text twice: E1 and E2 with seed 42 had other output lengths on
  19 and 24 November (cause not known). E4's first draft is therefore not E2's draft of the same seed,
  and the comparison is between conditions, not within one draft.
- In the pilot (one seed) the interval for a difference in U was about ± 11 percentage points. With
  three seeds it is expected to be about ± 6 to 7, wider than the MMD, so a true difference near the
  MMD will often come out as inconclusive.
- The laptop resembles the end users' laptops but is one machine; F5 records another.
- On a corporate 16 GB laptop other programs often use about half the memory. The tool is expected
  to run with them closed, e.g. overnight; F5 records what was open.

## 9. Decided before the freeze, and still open

Decided: the five **(P)** values of 19 November (MMD, resamples, κ, F2, the Copilot sample); what E4
does with a rejected claim and how E1R presents records (section 4a); stage-2 scope; all runs CPU-only.

Still open, and not affecting stage 1: the **(P)** values in section 4b (H9), F3 and sections 7 (H7,
H8), which were not among the five; the hosted model (E6), only if InnoBeweegLab gives access to Azure
OpenAI in its own Microsoft tenant (EU region), otherwise it is not run and the discussion explains
why; whether the synthetic cases join.

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

## Sources

- Huang et al. (2024). Large Language Models Cannot Self-Correct Reasoning Yet. ICLR 2024. https://arxiv.org/abs/2310.01798
- Kamoi et al. (2024). When Can LLMs Actually Correct Their Own Mistakes? A Critical Survey of Self-Correction of LLMs. TACL 12. https://aclanthology.org/2024.tacl-1.78/
- Gou et al. (2024). CRITIC: Large Language Models Can Self-Correct with Tool-Interactive Critiquing. ICLR 2024. https://arxiv.org/abs/2305.11738
- Dhuliawala et al. (2024). Chain-of-Verification Reduces Hallucination in Large Language Models. Findings of ACL 2024. https://aclanthology.org/2024.findings-acl.212.pdf
- Gao et al. (2023). RARR: Researching and Revising What Language Models Say, Using Language Models. ACL 2023. https://arxiv.org/pdf/2210.08726
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
- Landis & Koch (1977). The Measurement of Observer Agreement for Categorical Data. Biometrics 33(1), 159–174.
