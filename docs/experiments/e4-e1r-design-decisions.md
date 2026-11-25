# E4 and E1R: design decisions before the freeze

**Status: all recommendations accepted by Alex on 19 November 2026.** They go into
`hypotheses-and-thresholds.md` and its deviation log at the freeze (`freeze-2`).
A copy of this record also exists as a claude.ai doc; this file is the one that is frozen.

Twelve decisions had to be fixed before E4 and E1R are built. The most important one is D4:
E4 learns from the same verifier that measures U, so H3 needs an outcome that E4 never saw.

## Summary

| Id | Decision | Accepted option |
| --- | --- | --- |
| D1 | What tells E4 a claim is wrong | The existing deterministic verifier (citation and number checks), not a model judging itself |
| D2 | Which claims go back | Only claims marked *unsupported*; grounded and weak claims are left alone |
| D3 | What the model may do | One revision round per section: fix the citation, fix the number, reword to what the evidence says, or withdraw; the original stays in the run record |
| D4 | How H3 is judged fairly | U plus a blind human check of a sample of E2 and E4 claims, S, number conflicts and the withdrawal rate |
| D5 | What the reviser sees | Same model and settings; only the flagged claim, its flags and the section's evidence, not the rest of the draft |
| D6 | Cost | Extra calls and minutes reported per report; about one extra call per section with a flag |
| R1 | Which raw data E1R gets | Exactly the source records behind that section's evidence items |
| R2 | How the rows are written | One Markdown table per sheet, every row starting with its record id, headers kept |
| R3 | Prompt | The v3 prompts with two changes: "brongegevens" instead of "bewijsstukken", and rule 3 allows computing |
| R4 | Too long for the window | Measure first; run at 16K like E1; a section that does not fit counts as failed; optional 32K check on seed 42 |
| R5 | How E1R is scored | Number conflicts and numbers found back decide H1, as already fixed; U is reported with its known bias |
| R6 | Which sections | The six theme sections only, for both E1 and E1R |

Cross-cutting: confirm the five (P) values, log the move to CPU-only runs (V1 falls away), and
decide the stage-2 scope once the speed test is in.

## Why these decisions matter

Both conditions test a claim about the architecture, so each must change exactly one thing. E4
adds a checker step to E2 (H3: fewer unsupported claims at little cost in coverage). E1R
removes the evidence layer from E1 (H1: computed evidence gives fewer unsupported claims and
fewer wrong numbers than raw rows).

What the literature says about a checker step:

- **Self-correction without outside feedback does not work.** Huang et al. (ICLR 2024) found
  that models correcting their own reasoning without external feedback often got worse. Kamoi
  et al. (TACL 2024) surveyed the field: no study showed success with feedback from a prompted
  model, except on unusually suitable tasks, while self-correction works well when reliable
  external feedback is available. They also warn that many studies over-evaluate
  self-correction through unfair set-ups.
- **Tools are the reliable feedback.** CRITIC (Gou et al., ICLR 2024) lets a model revise after
  checking its text with an external tool, and stresses that the external feedback is what
  drives the gain.
- **Check without the draft in view.** Chain-of-Verification (Dhuliawala et al., Findings of
  ACL 2024) found that models checking against their own context tend to repeat their
  hallucinations, and that answering each check separately ("factored") works better.
- **Fix, don't rewrite.** RARR (Gao et al., ACL 2023) revises unsupported content while
  preserving the rest, and scores both attribution and preservation, because one can be bought
  with the other.
- **A model judging its own text is biased.** Panickssery et al. (NeurIPS 2024) show that
  models rate their own outputs higher than humans do.

What the literature says about a raw-data baseline:

- **The table format changes the result.** Sui et al. (WSDM 2024) found that table
  understanding depends on the input format, content order and partition marks, and that even
  simple structure tasks were far from perfect. A fair E1R therefore needs a standard,
  documented format, not a dump.
- **One factor at a time.** An ablation removes or replaces one component and keeps everything
  else fixed; changing two things at once makes the effect impossible to attribute.

## E4: the checker step

E4 = E2, then the verifier, then one revision call for the claims it rejects, then the verifier
again.

**D1. Source of the feedback.** Options: (a) the deterministic verifier in
`verification/checks.py`; (b) a separate LLM call that judges each claim against its evidence
(entailment, as in MiniCheck or CoVe); (c) both.
*Accepted: (a).* The verifier's flags are reliable external feedback with a named reason (no
citation, invalid id, number not in the cited evidence), which is the condition under which
self-correction works. Option (b) is feedback from a prompted model, which Kamoi et al. found
does not work in general, and it adds self-preference bias, because only one 7B model fits
within the 9.6 GB limit. It would also add one call per claim, about 80 per report, on the CPU.
Recorded as further work.

**D2. Which claims go back.** Options: unsupported only; unsupported and weak; all.
*Accepted: unsupported only.* That is what U counts, so H3 tests exactly what it claims. Claims
that already pass are never touched, which avoids Huang et al.'s failure mode of changing
correct answers into wrong ones. Weak flags (attribution, over-generalisation) stay for the
human reviewer.

**D3. What the reviser may do.** Options: flag only (the current verifier); revise once; revise
until clean; delete.
*Accepted: one revision round, with four allowed outcomes per claim: fix the citation, fix the
number, reword to what the cited evidence says, or withdraw.* Flag-only cannot change U, so H3
would be untestable (already noted in the hypotheses, section 9.2). Repeated rounds cost CPU
time and give more chances to drift. Withdrawal is allowed because in real use a researcher
prefers no sentence to a false one, but it is counted (D4). Revised claims are verified again
and kept even if still flagged; the original and the revision are both kept in the run record.

**D4. Fair measurement of H3.** The problem: if E4 is repaired with the verifier's own flags and
then scored with the same verifier, U falls almost by construction. That is a Goodhart effect,
and the over-evaluation Kamoi et al. warn about.
*Accepted: keep U as pre-registered, label it "the measure E4 was optimised on", and add four
checks E4 never saw:*

1. **Blind human support check.** Draw a random sample of claims from E2 and E4 (same seeds,
   mixed, condition hidden). Two raters judge per claim: supported, partly, not supported by
   the cited evidence (support judged per claim against a fixed source, as in FActScore). Fold
   this into the planned 50-verdict audit of the claim checker, so it also gives κ for the
   verifier.
2. **S** (coverage of what the data supports): catches a lower U bought by withdrawing claims.
3. **Number conflicts** with the original report.
4. **Withdrawal rate** per report, and how many revised claims are still flagged.

H3 is then supported only if U drops by at least the MMD *and* the human support rate does not
get worse.

**D5. What the reviser sees.** *Accepted: the same model and settings (seed, temperature, prompt
version), with a factored context: the flagged claim, its flag messages and the section's full
evidence list, not the other claims or the rest of the draft.* This follows CoVe's finding that
checking without the draft in view avoids repeating errors. All flagged claims of one section
go in one call, with the same JSON schema and the 6-id cap. Evidence ids outside the section
are rejected as now.

**D6. Cost.** E4 adds one call per section that has an unsupported claim. In the four pilot runs
E2 had 7 to 11% unsupported claims out of 75 to 85, so about 5 to 9 claims spread over at most
8 sections. Report the extra calls and minutes per report beside U and S; the stage-1 rule
already breaks ties on speed.

## E1R: the raw-records baseline

E1R = E1 with the evidence layer removed: the same single prompt per section, given the source
rows and cells instead of computed evidence. It is also the controlled version of how Copilot
is used today (C0).

**R1. Which records.** Options: (a) the source records behind the section's evidence items (the
union of their `source_record_ids`); (b) every record tagged to the theme; (c) whole sheets.
*Accepted: (a).* Then E1 and E1R see the same information and only its form differs, which is
the one-factor rule. Option (b) would also change what is included; (c) would mostly test the
context window. The "not available" list and the quotes setting stay as in E1.

**R2. Format of the rows.** Options: Markdown table, CSV, HTML, "key: value" lines.
*Accepted: one Markdown (pipe) table per source sheet, with the file and sheet name as a
heading, the original column headers, and the record id as the first column.* Sui et al. found
the format matters (HTML did best for GPT-3.5/4), but HTML costs far more tokens on a CPU-only
16K window. Markdown is the common, compact choice; state it as a limitation that another
format might do better. Keep survey question text in the headers so the rows are readable.

**R3. Prompt.** The system prompt's rule 3 says "copy numbers, do not calculate", which cannot
work on raw rows: a share or an average has to be computed.
*Accepted: the v3 prompts with exactly two changes, both written down: the data block is called
"brongegevens" and claims cite record ids; rule 3 becomes "if you calculate, say what you
calculated from".* Both changes belong to removing the evidence layer, so they are part of the
manipulated factor, not a second one.

**R4. Context window.** Raw rows are much longer than evidence lines; the resident sheet alone is
149 columns wide.
*Accepted: count the tokens of every E1R prompt before the freeze (a small script on the
ingested workspace, no model needed).* The primary run uses 16K, like E1. A section that does
not fit is recorded as failed through the existing P1 check, and that counts as a result for
H1: the evidence layer is what makes it fit. If any section fails, add one sensitivity run at
32K on seed 42 and report its memory.

**R5. Scoring.** The verifier's number check accepts only numbers that literally appear in the
cited records. A correctly computed share would be marked unsupported, which biases U against
E1R.
*Accepted: keep the rule already fixed in the hypotheses (section 4): number conflicts and
numbers found back, from `reports/evaluate.py`, decide H1, because they do not depend on the
citation format.* Report U for E1R as secondary, with this bias stated. Optional extra: let the
number check also accept a count, share or mean of the cited records' values, within rounding.

**R6. Sections.** Conclusies and Aanleiding are built from cross-theme computed evidence and
notes, with no raw equivalent.
*Accepted: run E1R on the six theme sections only, and compare it with E1 on those same six.*

## Cross-cutting decisions

The five open (P) values are confirmed as written; each has a standard basis.

| (P) value | Value | Basis | Note |
| --- | --- | --- | --- |
| Minimum meaningful difference | 5 percentage points | A smallest effect of interest fixed before the runs is the core of equivalence testing (Lakens 2017) | The "whole interval within ± MMD" rule with a 95% interval is stricter than the usual 90% TOST interval, so "no meaningful difference" is a conservative verdict |
| Bootstrap resamples | 2,000 | 1,000 to 2,000 is the usual minimum for percentile intervals; resampling tests are the standard advice for NLP metrics (Dror et al., ACL 2018) | Fix the random seed of the bootstrap too |
| Scorer agreement | κ ≥ 0.61 | Lower bound of "substantial" (Landis & Koch 1977) | — |
| Time limit F2 | 8 hours on CPU | NFR5 and the overnight scenario | Now applies to this laptop directly |
| Copilot sample | 30 statements per run | Hand check; same number for the final configuration | — |

**All runs CPU-only (deviation).** The Mac and its GPU are gone. Quality and cost now come from
the same CPU-only runs on hardware like the end users' laptop. Check V1 (GPU against CPU) no
longer applies.

**Stage-2 scope.** Five selection variants (all, top-25, BM25, vector, hybrid) is the most
expensive stage; vector and hybrid also need an embedding model. Decide after the speed test:
if a staged report takes more than about 3 hours, the rule already fixed (3 of 6 themes)
applies; dropping vector and hybrid would be a new deviation, so decide it before the freeze or
not at all.

**One human audit, three uses.** The second rater's work is planned once: the 89 statements for
the S scorer (κ), the 50 verifier verdicts, and the blind E2/E4 support check from D4, in one
labelling session with one guideline.

## Deviation log entries to add at the freeze

| Change | Reason |
| --- | --- |
| All counted runs CPU-only on the Windows laptop (i7-1165G7, 16 GB); quality is no longer compared on a GPU | The MacBook failed on 17 Nov; the CPU-only laptop matches the end users' hardware |
| Check V1 (GPU against CPU) dropped | No GPU run remains to compare with |
| Prompt v3 (v2 + at most 6 evidence ids per claim, also in the JSON schema) | Pilot finding P7: on the CPU the model listed invented ids until the output limit |
| "-dirty" only for uncommitted changes to code, prompts or configuration | Pilot finding P8: documentation edits during a run marked it dirty |
| H3 judged on U plus a blind human support check, S, number conflicts and withdrawals | E4 is repaired with the verifier that measures U (D4) |
| E1R on the six theme sections only, with the two prompt changes of R3 | No raw equivalent for Conclusies and Aanleiding; rule 3 cannot apply to raw rows |

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
