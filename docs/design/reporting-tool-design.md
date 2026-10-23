# Reporting tool — design (v0.1, 21 Oct 2026)

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

## 9. Status (21 Oct 2026)

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
