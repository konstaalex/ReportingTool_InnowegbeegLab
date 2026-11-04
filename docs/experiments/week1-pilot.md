# Week-1 pilot: time, memory and first scores

**Status: complete. Last update: 4 November 2026, 16:00.**

These are pilot runs (tags `pilot-week1` and `pilot-week1b`). They test the set-up and size the
experiment plan; they do not count in the comparison (`hypotheses-and-thresholds.md`, section 1).
Only numbers are reported here; nothing is quoted from the original report.

P1–P8 below are the pilot's findings. F1–F5 are the feasibility thresholds in
`hypotheses-and-thresholds.md`, section 5. Memory is given in GB of 1024³ bytes, the same unit as
the 9.6 GB limit.

## Set-up

| | |
|---|---|
| Machine | MacBook Pro, Apple M1 Pro, 16 GB RAM (the same memory as the target laptop) |
| Model server | Ollama on the same machine (`localhost:11434`); nothing sent over the internet |
| Model | qwen2.5:7b, prompt version v2, temperature 0.2, seed 42 |
| Case | `euterpepark-oss`, re-imported 3 Nov 13:50 with the current code; maps measured in code |
| Scoring | against the original report: 250 statements (recommendations excluded by design) |
| Start | every model run starts cold: Ollama unloads all models first, so load time is included |

Two rounds:

| Round | Date | Code | Context window | Output limit |
|---|---|---|---|---|
| 1 (`pilot-week1`) | 3 Nov | `caabbdd` | 8,192 tokens | 1,800 tokens |
| 2 (`pilot-week1b`) | 4 Nov | `c9417ac`, with the fixes below | 16,384 tokens | 1,800; 3,000 for Conclusies and Aanleiding |

## Results

### Round 1: old settings (3 November)

| | E0 template | E1 = A, single prompt (GPU) | E2 = B, staged (GPU) | E2 = B, staged (**CPU only**) |
|---|---|---|---|---|
| Time for the full report | < 1 s (+3 s Word) | 11.1 min | 14.5 min | 58.6 min |
| Model calls | 0 | 8 | 26 | 26 (+2 retries) |
| Tokens in / out | – | 39,053 / 8,986 | 83,872 / 11,480 | 83,940 / 12,020 |
| Peak model memory (Ollama's own figure) | – | 5.1 GB (4.8 GB) | 5.5 GB (4.8 GB) | 5.2 GB (5.1 GB), 0 GB on the GPU |
| Claims | 72 | 55 | 76 | 85 |
| Unsupported claims | 0% | 22% | 7% | 11% |
| Grounded claims | 100% | 71% | 78% | 79% |
| Coverage of the original | 15% | 10% | 10% | 15% |
| **Coverage of what the data supports** | **27%** | **17%** | **18%** | **28%** |
| Ceiling (original statements the data can support) | 52% | 52% | 52% | 52% |
| Numbers of the original found back | 33 of 73 | 17 of 73 | 17 of 73 | 24 of 73 |
| Failed sections | none | Conclusies | Conclusies | none (Conclusies from a cut prompt, P1) |

A's prompts were partly cut off (P1), so A's round-1 scores are not valid.

### Round 2: with the fixes (4 November)

| | E0 template | E1 = A, single prompt (GPU) | E2 = B, staged (GPU) | E2 = B, staged (**CPU only**) |
|---|---|---|---|---|
| Time for the full report | < 1 s | **10.0 min** | **12.9 min** | **77.0 min** |
| Model calls | 0 | 8 | 26 | 26 |
| Tokens in / out | – | 66,515 / 6,350 | 89,899 / 10,361 | 89,967 / 14,041 |
| Largest prompt | – | 12,404 tokens | 10,125 tokens | 10,125 tokens |
| Peak model memory (Ollama's own figure) | – | 5.7 GB (5.1 GB) | 5.7 GB (5.2 GB) | 5.5 GB (5.5 GB), 0 GB on the GPU |
| Claims | 72 | 54 | 82 | 75 |
| Unsupported claims | 0% | **26%** | **8.5%** | 11% |
| Grounded claims | 100% | 61% | 76% | 77% |
| Coverage of the original | 15% | 9% | 10% | 15% |
| **Coverage of what the data supports** | **27%** | **15%** | **18%** | **28%** |
| Ceiling | 52% | 52% | 52% | 52% |
| Numbers of the original found back | 33 of 73 | 14 of 73 | 17 of 73 | 24 of 73 |
| Prompts cut off / retries | – | none / 0 | none / 0 | none / 0 |
| Failed sections | none | none | none | Ontmoeten, Conclusies (P7) |

The template is deterministic: round 2 gives the same report as round 1.

**Read with care.** Each cell is one run with one seed. Differences between conditions are not
results yet (hypotheses, section 3). Two things already point the same way in both rounds, as a
signal for the plan, not as evidence:
- B has far fewer unsupported claims than A (7–8.5% against 22–26%), and in round 2 A had its
  full prompts;
- every model draft covers less of what the data supports than the template (15–18% against 27% on
  the GPU). The template states every evidence item in fixed wording; the models select and
  rephrase.

## Findings

### P1. Prompts longer than the context window were cut off without any error (round 1)

Ollama reports how many tokens of a prompt it read. For 34 prompts that fitted, the text took
2.0 to 2.9 characters per token. Four prompts of A arrived as exactly 4,098 tokens:

| A, section | Prompt (characters) | Tokens Ollama read | Characters per token | Estimated full size |
|---|---|---|---|---|
| Sporten | 28,926 | 4,098 | 7.1 | ~10,500 tokens |
| Actieve mobiliteit | 33,493 | 4,098 | 8.2 | ~12,200 tokens |
| Formeel spelen | 28,734 | 4,098 | 7.0 | ~10,400 tokens |
| Conclusies | 26,717 | 4,098 | 6.5 | ~9,700 tokens |
| Ontmoeten | 22,332 | 8,055 | 2.8 | fitted, but 8,055 + 964 answer tokens > 8,192 |
| Informeel spelen, Uitstraling, Algemeen | | | 2.7–2.8 | fitted |

When a prompt is longer than the context window, Ollama keeps the first few tokens and the last
half of the window (4 + 4,094 = 4,098) and drops the rest, without an error. Ontmoeten filled the
window while writing, which also drops the start of the prompt. So 5 of A's 8 model calls in round
1 worked from a partial prompt: the instructions and part of the evidence were missing. B splits the
evidence per perspective; its largest theme prompt was 5,678 tokens, which fitted. Only B's
Conclusies (one call over all themes, the same prompt as in A) was cut off as well.

**Round 2 confirms the fix:** with a 16,384-token window no prompt was cut off; the largest was
12,404 tokens (A). What it means for the research: with all evidence of a theme in one prompt, the
single-prompt approach does not fit an 8K window on the Oss case. Without detecting this, the
A-versus-B comparison (H2) would have compared a damaged A with B.

### P2. The conclusions did not fit the output limit (round 1)

Conclusies failed in A and B: the answer reached the 1,800-token limit and the JSON was cut off,
so no claim could be read. The tool retried once with the same result, which cost another ~90 s
per report. In round 2, with 3,000 tokens and the full prompt, Conclusies was drafted on the GPU in
both A and B (681 output tokens in B).

### P3. Memory: well within the limit, also with the larger window

| | Round 1 (8K window) | Round 2 (16K window) | Increase |
|---|---|---|---|
| Ollama's own figure, GPU | 4.8 GB | 5.1–5.2 GB | +0.3–0.4 GB |
| Ollama's own figure, CPU only | 5.1 GB | 5.5 GB | +0.5 GB |
| Peak process memory (all runs) | 5.1–5.5 GB | 5.5–5.7 GB | |

The increase matches the estimate for qwen2.5:7b (+0.5 GB for the KV cache). All runs stayed well
under the 9.6 GB limit (F1). The whole Mac already had 10.4–14.4 GB in use before the runs (other
apps open) and peaked at 13.6–14.6 GB. On a 16 GB office laptop the model fits, but only with few
other programs open; this belongs in the feasibility conclusion (SQ4, F5).

### P4. Time: one to one and a quarter hours per report on the CPU

| | GPU (Apple M1 Pro) | CPU only, round 1 (8K) | CPU only, round 2 (16K) |
|---|---|---|---|
| Reading the prompt | 220–300 tokens/s | 72 tokens/s | 58 tokens/s |
| Writing the answer | 21–24 tokens/s | 7.1 tokens/s | 4.6 tokens/s |
| Full B report | 12.9–14.5 min | 58.6 min | 77.0 min |
| Model time reading / writing (B) | 281–331 s / 440–489 s | 1,159 s / 1,705 s | 1,562 s / 3,043 s |

Ollama reported 0 GB on the GPU during both CPU-only runs, so they really ran on the CPU. Round 2
was slower on the CPU: writing dropped from 7.1 to 4.6 tokens/s and the answers were longer
(14,041 against 12,020 tokens, partly the two loops of P7). The causes cannot be separated from one
run: the larger window, the longer answers, heat after more than an hour at full load, and other use
of the Mac during the run all play a part. For planning, assume about 1¼ hours per B report on the
M1 Pro's CPU. A typical office laptop CPU is probably slower, so this is a best case for the target
laptop; the 8-hour threshold (F2) is still far away.

### P5. GPU and CPU give different reports: twice in the same direction

B ran with the same prompts, settings and seed on the GPU and on the CPU, in both rounds:

| B, seed 42 | GPU round 1 | CPU round 1 | GPU round 2 | CPU round 2 |
|---|---|---|---|---|
| Unsupported claims | 7% | 11% | 8.5% | 11% |
| Coverage of what the data supports | 18% | 28% | 18% | 28% |
| Numbers found back | 17 | 24 | 17 | 24 |

The arithmetic on GPU and CPU differs slightly, and a different first word changes everything
after it, so some difference is expected. But both rounds point the same way: the CPU drafts
covered more and found more numbers. That may still be chance, because each round is one seed per
device, but it may also be systematic. This matters for the decision to compare quality on the GPU
(hypotheses, section 1): check V1 tests exactly this, and it should run early (see below), not only
at the end.

Consequences for the comparison:
- all conditions of one comparison run on the same device setting, never mixed;
- one run says little: the three seeds per condition are needed, and differences smaller than the
  run-to-run variation must not be read as effects (hypotheses section 3).

### P6. Retries for broken JSON (round 1)

Two of the 26 calls of the round-1 CPU run returned JSON that could not be read and were repeated
once, at the end of long answers. A retry costs a full call (2–5 minutes on the CPU). Round 2 had no
retries.

### P7. On the CPU the model sometimes counts evidence ids until the output limit (round 2)

In the round-2 CPU run, two answers (the Ontmoeten synthesis and Conclusies) turned into a list of
invented evidence ids numbered one after another (…-152, …-153, …-154 and so on) until the output
limit. These ids do not exist. The new check caught both: the sections failed visibly, with
"answer cut off at the output limit". The same calls on the GPU used 319 and 681 output tokens.

Fix before the freeze: limit the number of evidence ids per claim in the answer format (e.g. at most
6) and say so in the prompt. Ids that do not exist are already marked unsupported by the verifier.

### P8. Editing files during a run marks it "-dirty"

The two B runs of round 2 record code version `c9417ac-dirty`, because documentation files were
edited while they ran. For counted runs nothing may be uncommitted. Proposal: let only changes to
code, prompts and configuration count as dirty, not documentation.

## Fixes made after round 1 (in the code since commit `4673c5a`)

1. **A cut-off is never silent.** If a prompt arrives with more than 4.5 characters per token, or
   prompt plus answer exceed the window, or the answer hits the output limit, the stage fails with
   the reason ("prompt cut off", "context window full", "answer cut off at the output limit"). It
   is not retried and the section is recorded as failed, never dropped (`llm/providers.py`).
   Round 2 shows it working (P7).
2. **Context window 16,384 tokens** for the three drafting models (all support at least 32K).
   Estimated extra memory (KV cache): qwen2.5:7b +0.5 GB (measured: +0.3–0.5 GB, P3), qwen3:8b
   +1.2 GB, llama3.1:8b +1.1 GB; all three stay under 9.6 GB on paper.
3. **Output limit 3,000 tokens** for Conclusies and Aanleiding (`drafting/pipelines.py`).

## Still to do before the freeze (13 November)

- [ ] Cap the evidence ids per claim (P7)
- [ ] Count only code, prompts and configuration as dirty (P8)
- [ ] **Run check V1 early:** B with seeds 43 and 44 on the GPU and on the CPU (about 3 hours of
      CPU, one evening). If GPU and CPU still differ in the same direction, the quality comparison
      should run on the CPU after all, and the run budget below changes.

## For the run budget (from round 2)

| Where the comparison runs | Per report (A / B) | ~30 reports (stages 1–3) |
|---|---|---|
| Mac GPU | 10 / 13 min | ~7–9 hours: two or three evenings |
| Mac CPU only | ~50 / 77 min | ~35–45 hours: five to six nights |

E4 (checker step) and the retrieval variants add calls, so these are lower bounds.

**Decided on 4 November** (`hypotheses-and-thresholds.md`, sections 1 and 5): the quality
comparison (stages 1–3, three seeds) runs on the GPU. Time and memory count only from CPU-only runs,
because the end users' corporate laptops have no GPU that Ollama uses: one CPU-only run per
condition and three for the final configuration, about 13–15 runs of 1–1¼ hours, so about three
nights. One run on a borrowed corporate laptop in week 4 would show real times. Because of P5, this
decision depends on check V1, which now runs first.
