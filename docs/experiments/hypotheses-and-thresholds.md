# Hypotheses and pass/fail thresholds

**Status: draft of 3 November 2026, written before any counted run; updated 4 November: where
the experiments run (sections 1, 5 and 8).** Items marked **(P)** are
proposals that Alex confirms or changes before the first counted run. The git history of this
file is the record of what was fixed when; any change after the first counted run goes in the
deviation log at the end, with the reason.

Research question addressed: SQ3 (which architecture gives traceable, useful drafts at what cost)
and SQ4 (does it run on a 16 GB CPU-only laptop). Brief sections 16 (experiment matrix) and 17
(evaluation).

## 1. What counts as a run

| Item | Rule |
|---|---|
| Test case | `euterpepark-oss`, the only case with a human-written report to score against. All report sections that have evidence. Recommendations are excluded by design: the researcher writes them. |
| Unit | One full report (`unit = "report"` in `config/experiments.toml`). |
| Repeats | Seeds 42, 43 and 44 for every model condition; the template baseline once (it is deterministic). Temperature 0.2, prompt version v2. |
| Counted runs | Untagged runs made after the freeze: git tag `freeze-1`, planned for Friday 13 November. The run record's `code_version` must be that commit without `-dirty`, and its fingerprints must match the frozen prompts and config. |
| Not counted | Runs with a tag (e.g. `pilot-week1`) and runs before the freeze. They size the plan only. |
| Failures | A failed section is kept, never dropped: its chapter scores as missing. A run is repeated only for an infrastructure fault (Ollama crash, power loss, laptop asleep), logged in the deviation log. A poor output is never a reason to repeat. |
| Primary model | qwen2.5:7b for stages 1 and 2; the three models in stage 3. |
| Where quality is compared | Stages 1–3 run on the Mac's GPU (Apple M1 Pro, 16 GB). All conditions of one comparison run on the same setting, never mixed: the same settings give different text on GPU and CPU (`week1-pilot.md`, F5). Whether that matters is tested with V1 (section 5). Decided 4 November. |
| Where time and memory are measured | The end users work on typical corporate laptops (e.g. a ThinkPad with 16 GB) without a GPU that Ollama uses. Time and memory therefore count only from CPU-only runs (`--cpu-only`): one per condition (seed 42), and three for the final configuration (seeds 42–44). |
| Machine | Recorded in every run (`machine`, `runtime.cpu_only`). |

## 2. Measures (all computed by code)

| Measure | Code | Better | Role |
|---|---|---|---|
| **U** = unsupported-claim rate: claims with no valid citation or with a number not in the cited evidence, over all claims | `verification/checks.py` → run totals `unsupported_rate` | lower | primary: traceability (NFR1, FR5, FR6) |
| **S** = coverage of what the data supports: statements of the original report that the evidence can support, and the draft covers | `reports/evaluate.py` → `coverage_of_supportable` | higher | primary: usefulness against the human report |
| Ceiling: share of the original's statements the evidence can support at all | `evaluate.py` → `ceiling` | – | context: separates data limits from model limits |
| Number conflicts: matched statements whose numbers differ from the original | `evaluate.py` → `possible_number_conflicts` | lower | secondary |
| Numbers found back | `evaluate.py` → `numbers_agree / numbers_in_reference` | higher | secondary |
| Grounded and weak rates, citation validity | `checks.py` | – | secondary |
| Time per report; model load, prompt and output speed | run `totals`: `wall_s`, `model_load_s`, `prompt_tokens_per_s`, `output_tokens_per_s` | lower / higher | cost (SQ3), feasibility (SQ4) |
| Peak model memory | run `totals.model_peak_mb` (`llm/resources.py`) | lower | feasibility (NFR5) |
| Failed sections | run `totals.failed_sections` | none | reliability |

## 3. How a difference is judged

For a comparison X versus Y, both on the same seeds and sections:

1. The difference in U and in S, pooled over the three seeds.
2. A 95% bootstrap confidence interval for that difference, resampling claims (for U) or the
   original's statements (for S) within each section, 2,000 resamples **(P)**.
3. The minimum meaningful difference (MMD) is **5 percentage points** for U and S **(P)**.
   It is my judgement of the smallest change a researcher would notice in a full report; no
   published standard exists for this task.

| Outcome | Rule |
|---|---|
| **Better / worse** | the interval excludes 0 **and** the difference is at least the MMD |
| **No meaningful difference** | the whole interval lies within ± MMD |
| **Inconclusive** | anything else: reported as such, never as a win |

With one case and three seeds these are descriptive intervals over statements of one report, not
a test that generalises to other neighbourhoods. The results chapter says so.

## 4. Hypotheses

| Id | Stage | Hypothesis | Measure and threshold |
|---|---|---|---|
| **H1** evidence layer | 1 | A single prompt that gets computed evidence (E1, condition A) produces fewer unsupported claims and fewer wrong numbers than the same prompt given the raw records (E1R, "A without the evidence layer", close to how Copilot is used today). | U lower by ≥ MMD; number conflicts lower; numbers found back higher. If E1R cannot be checked by the same verifier, the number measures from `evaluate.py` decide, because they do not depend on the citation format. |
| **H2** staging | 1 | Staged drafting (E2, B: findings per perspective, then synthesis) covers more of what the data supports than the single prompt (A), without more unsupported claims. | S higher by ≥ MMD, and U not worse by more than the MMD. |
| **H3** verification | 1 | Adding a checker step (E4, D) lowers unsupported claims compared with B, at little cost in coverage. | U lower by ≥ MMD, and S not worse by more than the MMD. Extra time per report is reported. |
| **H4** retrieval | 2 | Selecting evidence before drafting (top-25 salience, BM25, vector, hybrid; all within the theme) lowers U but also lowers S, compared with giving all evidence of the theme. | Judged per variant with the rule in section 3; a variant that lowers U without lowering S by more than the MMD supports retrieval. |
| **H5** models | 3 | No direction expected between qwen2.5:7b, qwen3:8b and llama3.1:8b. Llama 3.1 does not list Dutch among its supported languages; this is recorded, not tested. | Exploratory: U, S, time and memory with intervals. |

Stage-selection rules, fixed now so the winner is not picked after seeing the results:

- **Stage 1 (pipeline):** among A, B and D, the condition with the highest S whose U is not worse
  than the lowest U by more than the MMD. If two are within the MMD on both, the faster one wins.
  E1R is a reference, never a candidate.
- **Stage 2 (selection):** the same rule among all evidence, top-25, BM25, vector and hybrid.
- **Stage 3 (model):** the same rule among the three models, on the stage-2 winner. The final
  configuration must also pass F1 to F3.
- If the week-1 timing shows a full report takes more than about 3 hours, stages 2 and 3 use 3 of
  the 6 themes **(P)**. Which three is decided before stage 2 starts and recorded here.

## 5. Feasibility thresholds (SQ4, pass/fail)

| Id | Threshold | Source |
|---|---|---|
| **F1** memory | Peak model memory ≤ 9.6 GB in every CPU-only run of the final configuration (also reported per condition) | `config/models.toml [hardware]`: 0.6 × 16 GB (NFR5) |
| **F2** time | A full report with the final configuration finishes within 8 hours on CPU only, i.e. overnight **(P)**. If a corporate laptop can be borrowed (F5), its time decides; otherwise the Mac's CPU-only time, reported as a best case | NFR5 asks for "a practical working session" without a number; Scenario C has the work run overnight |
| **F3** reliability | Every section drafted in all three CPU-only runs of the final configuration **(P)** | – |
| **F4** cost | No paid service for local runs (EUR 0) | NFR6 |
| **F5** target laptop | Descriptive: one run of the final configuration on a typical corporate laptop (e.g. a ThinkPad, 16 GB, CPU only), in week 4, if InnoBeweegLab can lend one. Reported: time, peak model memory, and the memory other programs (Teams, browser, Outlook) already used before the run | the end users' hardware; `week1-pilot.md` F3: the Mac already had 11.7–13.5 GB in use before the runs |

**V1. GPU and CPU differ only by chance (P).** The three CPU-only runs of the final configuration
(the same runs F1–F3 use) are compared with its three GPU runs on U and S, by the rule of
section 3. Pass: the outcome is not "better/worse" and the difference is smaller than the MMD.
Then the comparison on the GPU stands for use on a CPU-only laptop. If V1 fails, this is reported,
and the stage-1 winner and runner-up are each run three times CPU-only; the choice of pipeline is
confirmed or changed on those runs. The model, weights, prompts and settings are identical on both;
the difference comes from the order of floating-point arithmetic, which changes the wording but is
not expected to favour either side. V1 tests that expectation instead of assuming it.

## 6. Is the scorer good enough?

S comes from the word-overlap scorer. It agreed with the developer's labels on 89 statements at
κ = 0.38. Before S is used as a primary measure:

1. A second rater labels the same statements on their own; κ between the two raters is reported.
   If it is below 0.61, the label definitions are revised once and the labelling repeated.
2. The two raters agree on one label per statement; that set is the reference.
3. Word overlap is the primary scorer for S if its κ against the agreed labels is **≥ 0.61**
   **(P)**, the lower bound of "substantial" agreement in Landis and Koch (1977). Otherwise the
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
- Quality is compared on the Mac's GPU, not on the end users' laptops; V1 tests whether that
  matters.
- The Mac's CPU is faster than a typical corporate laptop's (it has much faster memory, which sets
  the writing speed), so its CPU-only times are a best case. Only a run on a borrowed laptop (F5)
  shows real times.
- On a corporate 16 GB laptop other programs often use about half the memory. The tool is expected
  to run with them closed, e.g. overnight; F5 records what was open.

## 9. To decide before the freeze (13 November)

Decided on 4 November: quality is compared on the GPU; time and memory count from CPU-only runs;
check V1 and the borrowed-laptop run F5 (sections 1 and 5).

1. Confirm or change every **(P)**.
2. What D's checker does with a claim it rejects: flag only, or send it back once for revision
   with the original kept in the log. If D only flags, U cannot change, so H3 needs the revision
   step or a different measure.
3. How E1R presents raw records (rows and cells as text with their record ids) and how its claims
   are checked.

## Deviation log

| Date | Change | Reason |
|---|---|---|
| | | |
