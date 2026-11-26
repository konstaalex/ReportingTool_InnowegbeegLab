# Stage 1 results, commit 44def27

Numbers only. Rules: docs/experiments/hypotheses-and-thresholds.md, sections 3 and 4. Bootstrap: 2000 resamples, seed 20261126; U by (seed x section) clusters, S by paired statements. MMD: 5 percentage points (pp). Aanleiding and Aanbevelingen are not scored.

## Runs

| Condition | Seed | Run | Claims | U % | S % | Minutes | Peak model memory (GiB) | Failed sections |
|---|---|---|---|---|---|---|---|---|
| E1 | 42 | 20261125-221932-E1-8ce4 | 55 | 12.7 | 22.7 | 98.8 | 7.1 | 0 |
| E1 | 43 | 20261126-055303-E1-0a50 | 45 | 24.4 | 19.5 | 103.6 | 8.8 | 1 |
| E1 | 44 | 20261126-121756-E1-c9a1 | 58 | 20.7 | 20.3 | 94.8 | 8.4 | 0 |
| E2 | 42 | 20261126-001530-E2-3eb4 | 84 | 10.7 | 24.2 | 92.4 | 7.4 | 0 |
| E2 | 43 | 20261126-073724-E2-d3b3 | 87 | 4.6 | 24.6 | 80.5 | 9.5 | 0 |
| E2 | 44 | 20261126-135347-E2-8596 | 85 | 15.3 | 24.2 | 90.7 | 7.6 | 0 |
| E4 | 42 | 20261126-014851-E4-0ffa | 82 | 6.1 | 23.8 | 143.4 | 8.9 | 0 |
| E4 | 43 | 20261126-085844-E4-712f | 87 | 4.6 | 23.8 | 103.7 | 9.5 | 0 |
| E4 | 44 | 20261126-153040-E4-8a14 | 84 | 7.1 | 24.2 | 136.8 | 8.6 | 0 |
| E1R | 42 | 20261126-041312-E1R-1719 | 42 | 38.1 | 16.4 | 98.9 | 8.5 | 0 |
| E1R | 43 | 20261126-104305-E1R-e34d | 46 | 37.0 | 19.5 | 93.7 | 8.7 | 0 |
| E1R | 44 | 20261126-174831-E1R-d5d4 | 39 | 28.2 | 22.7 | 90.1 | 8.6 | 0 |

Ceiling: 128 of 239 statements (53.6%).

E1R has the six theme sections only: its S in these two tables is against the whole original and is not comparable with the others.

## Pooled over the seeds

| Condition | Claims | U % | Weak % | Grounded % | S % | Numbers found back | Number conflicts | Minutes (mean) |
|---|---|---|---|---|---|---|---|---|
| E1 | 158 | 19.0 | 12.7 | 68.4 | 20.8 | 56 of 213 | 0 | 99.0 |
| E2 | 256 | 10.2 | 12.1 | 77.7 | 24.3 | 69 of 213 | 0 | 87.9 |
| E4 | 253 | 5.9 | 13.0 | 81.0 | 24.0 | 69 of 213 | 0 | 128.0 |
| E1R | 127 | 34.6 | 29.1 | 36.2 | 19.5 | 48 of 213 | 0 | 94.2 |

## E4: the checker step (D4, D6)

Over 3 reports: 26 claims sent back, 23 revised, 3 withdrawn (1.2% of all claims), 15 revised but still unsupported. U before revision: 10.2%. Extra: 13 calls, 120.7 minutes.

## Differences (X minus Y, pp, with 95% interval)

| Comparison | Sections | Measure | X | Y | Difference | 95% interval | Verdict for X |
|---|---|---|---|---|---|---|---|
| E2 − E1 (H2) | all | U | 10.2 | 19.0 | -8.8 | -18.0 to +0.1 | inconclusive |
| E2 − E1 (H2) | all | S | 24.3 | 20.8 | +3.5 | +0.5 to +6.6 | inconclusive |
| E2 − E1 (H2) | all | found back | 32.4 | 26.3 | +6.1 | +1.8 to +11.1 | better |
| E2 − E1 (H2) | all | number conflicts (count) | 0 | 0 | +0 | – | – |
| E4 − E2 (H3) | all | U | 5.9 | 10.2 | -4.2 | -11.0 to +2.0 | inconclusive |
| E4 − E2 (H3) | all | S | 24.0 | 24.3 | -0.4 | -0.9 to +0.0 | no meaningful difference |
| E4 − E2 (H3) | all | found back | 32.4 | 32.4 | +0.0 | +0.0 to +0.0 | no meaningful difference |
| E4 − E2 (H3) | all | number conflicts (count) | 0 | 0 | +0 | – | – |
| E4 − E1 | all | U | 5.9 | 19.0 | -13.1 | -21.4 to -5.5 | better |
| E4 − E1 | all | S | 24.0 | 20.8 | +3.1 | +0.1 to +6.3 | inconclusive |
| E4 − E1 | all | found back | 32.4 | 26.3 | +6.1 | +1.8 to +11.1 | better |
| E4 − E1 | all | number conflicts (count) | 0 | 0 | +0 | – | – |
| E1 − E1R (H1) | six themes | U | 17.4 | 34.6 | -17.3 | -30.4 to -3.5 | better |
| E1 − E1R (H1) | six themes | S | 21.6 | 21.1 | +0.5 | -5.2 to +6.3 | inconclusive |
| E1 − E1R (H1) | six themes | found back | 27.8 | 23.3 | +4.4 | -4.2 to +14.0 | inconclusive |
| E1 − E1R (H1) | six themes | number conflicts (count) | 0 | 0 | +0 | – | – |

## Hypotheses

- **H1** (decided by the number measures, R5; six themes): number conflicts lower: no (0 against 0); numbers found back higher: yes (inconclusive). U is secondary and biased against E1R.
- **H2**: S inconclusive; U not worse by more than the MMD: yes. Supported: no.
- **H3**: U inconclusive; S not worse by more than the MMD: yes. Automatic part: not met. H3 also needs the blind human support check (D4), so this is not the final verdict.

## Stage 1 winner by the pre-registered rule

- U within the MMD of the lowest U: E2, E4
- highest S among these: E2; within the MMD of it on both U and S: E2, E4
- **winner: E2** (the fastest of the tied conditions)

S uses the word-overlap scorer (kappa 0.38 on developer labels): provisional until a scorer reaches 0.61 (section 6). One case and three seeds: the intervals describe this report, not other neighbourhoods.
