# Week-1 pilot: time, memory and first scores

**Status: in progress. Last update: 3 November 2026, 14:35.**

These are pilot runs (tag `pilot-week1`). They test the set-up and size the experiment plan;
they do not count in the comparison (`hypotheses-and-thresholds.md`, section 1). Only numbers
are reported here; nothing is quoted from the original report.

## Set-up

| | |
|---|---|
| Machine | MacBook Pro, Apple M1 Pro, 16 GB RAM (the same memory as the target laptop) |
| Model server | Ollama on the same machine (`localhost:11434`); nothing sent over the internet |
| Code | commit `caabbdd`, prompt version v2, temperature 0.2, seed 42 |
| Model settings | qwen2.5:7b, context window (`num_ctx`) 8,192 tokens, output limit 1,800 tokens |
| Case | `euterpepark-oss`, re-imported 3 Nov 13:50 with the current code; maps measured in code |
| Scoring | against the original report: 250 statements (recommendations excluded by design) |
| Start | every model run starts cold: Ollama unloads all models first, so load time is included |

## Results so far

| | E0 template | E1 = A, single prompt | E2 = B, staged |
|---|---|---|---|
| Time for the full report | < 1 s (+3 s Word) | **11.1 min** | **14.5 min** |
| Model calls | 0 | 8 | 26 |
| Tokens in / out | – | 39,053 / 8,986 | 83,872 / 11,480 |
| Peak model memory | – | **5.1 GB** (Ollama reports 4.9 GB), GPU | **5.5 GB** (Ollama reports 4.9 GB), GPU |
| Claims | 72 | 55 | 76 |
| Unsupported claims | 0% | 22% | **7%** |
| Grounded claims | 100% | 71% | 78% |
| Coverage of the original | 15% | 10% | 10% |
| **Coverage of what the data supports** | **27%** | **17%** | **18%** |
| Ceiling (original statements the data can support) | 52% | 52% | 52% |
| Numbers of the original found back | 33 of 73 | 17 of 73 | 17 of 73 |
| Failed sections | none | Conclusies | Conclusies |

Speed on the GPU: reading the prompt at 229 (A) and 299 (B) tokens/s, writing at 23 tokens/s,
loading the model 1–3 s. Writing took most of the model time: 70% in A (395 of 566 s), 64% in B
(489 of 770 s). B makes about three times as many calls as A but took only 30% longer, because
its prompts are shorter and its answers per call are short.

**Read with care.** These are single pilot runs with known problems (F1, F2). Both model reports
lack the Conclusies chapter, which the template does have; that alone lowers their coverage. So
the coverage figures are not yet a comparison of the approaches. What already stands: B has far
fewer unsupported claims than A (7% against 22%), although A worked from partial prompts.

## Findings

### F1. Prompts longer than the context window were cut off without any error

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
window while writing, which also drops the start of the prompt. So **5 of A's 8 model calls
worked from a partial prompt**: the instructions and part of the evidence were missing. A's scores
above are therefore not a valid baseline.

What it means for the research: with all evidence of a theme in one prompt, the single-prompt
approach does not fit an 8K window on the Oss case. B splits the evidence per perspective; its
largest prompt was 5,678 tokens, which fits. Only B's Conclusies (one call over all themes, the same
prompt as in A) was cut off as well. Without detecting this, the A-versus-B
comparison (H2) would have compared a damaged A with B.

### F2. The conclusions did not fit the output limit

Conclusies failed in both A and B: the answer reached the 1,800-token limit and the JSON was cut
off, so no claim could be read (its prompt was also cut, F1). The tool retried once with the same
result, which cost another ~90 s per report.

### F3. Memory: well within the limit on the GPU

qwen2.5:7b with an 8K window used 4.9 GB (Ollama) and 5.1–5.6 GB process memory at its peak,
against the limit of 9.6 GB (60% of 16 GB). The whole Mac already had 11.8–13.5 GB in use before
the runs (other apps open) and peaked at 14.8–14.9 GB. On a 16 GB office laptop the model fits, but only with
few other programs open; this belongs in the feasibility conclusion (SQ4).

### F4. Time

On the GPU a full report took 11 minutes (A) and 15 minutes (B), mostly spent writing. The
CPU-only run (SQ4) is still to come.

## Fixes made after these runs (before the freeze)

1. **A cut-off is never silent.** If a prompt arrives with more than 4.5 characters per token, or
   prompt plus answer exceed the window, or the answer hits the output limit, the stage fails with
   the reason ("prompt cut off", "context window full", "answer cut off at the output limit"). It
   is not retried and the section is recorded as failed, never dropped (`llm/providers.py`).
2. **Context window 16,384 tokens** for the three drafting models (all support at least 32K). The
   largest prompt (~12,200 tokens) plus an answer then fits. Extra memory for the larger window
   (KV cache, estimated): qwen2.5:7b +0.5 GB, qwen3:8b +1.2 GB, llama3.1:8b +1.1 GB; all three stay
   under 9.6 GB on paper. The runs will measure it.
3. **Output limit 3,000 tokens** for Conclusies and Aanleiding (`drafting/pipelines.py`).

The A and B pilots above ran with the old settings; they are superseded by the runs below.

## Still to run

- [x] B (staged, GPU, old settings): 14.5 min
- [ ] B on CPU only (`--cpu-only`), with the fixes: time and memory for the 16 GB laptop question.
      A first CPU-only run with the old settings was started by the script at 14:24 and stopped.
- [ ] A and B again with the fixes (GPU): confirm no prompt is cut off and Conclusies is drafted

## For the run budget

To be completed when the pilots above are in.
