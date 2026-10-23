# Interpreting images in the reporting tool — research brief (21 Oct 2026)

**Question.** The Wijkscan input contains images: QGIS maps, demographic infographics and a sunburst chart. How can the tool use them as evidence while keeping every claim traceable, staying local (16 GB laptop, zero budget), and keeping the researcher in control?

## 1. What the images are (Oss case)

| Image | What carries the information | Report use |
|---|---|---|
| Beweeggroen-, Basisbehoefte-, Sportaccommodatiekaart | **every building coloured by legend class** (e.g. "wel/geen groen binnen 300 m"), neighbourhood borders as thick black lines | tests RIVM rules R3, R5, R6 per neighbourhood ("in Centrum-West voldoet de helft niet") |
| Beweegstructuurkaart, Sport- en speelkaarten | coloured **location markers** per type; semi-transparent distance zones (200 m / 400 m) | where play/sport locations are; R2/R4 coverage |
| Voorzieningenkaart | **icons** (huisarts, school, supermarkt, …) | supporting map |
| Demografie infographics | **printed numbers** in donuts, bars, a table | Demografie section (e.g. "overgewicht Dichtersbuurt 62%") |
| Sunburst | drawn from `Totaal scores` | appendix; the spreadsheet is ingested itself |
| Inspiratie-kaart | a **design proposal** | recommendations only — never evidence of the current situation |

## 2. What the literature says

- **Local vision-language models read printed text and numbers well.** Qwen2.5-VL 7B (DocVQA 95.7, ChartQA 87.3) and Qwen3-VL 8B (DocVQA 95+, ChartQA 88+) run in Ollama at about 6 GB (Q4). Gemma 3 4B is lighter but weaker on OCR (DocVQA 75.8). As of July 2026, Qwen 3.5/3.6 vision did not work in Ollama. ([InsiderLLM, upd. Jul 2026](https://insiderllm.com/guides/vision-models-locally/))
- **They are weak exactly where maps need precision.** In "Vision language models are blind" (ACCV 2024), frontier VLMs fail simple geometric tasks: they cannot reliably tell whether lines or circles touch, they miscount overlapping shapes and grid rows, and they trace a path on a subway map only 23–50% correctly. Strong chart-benchmark scores do not imply fine-grained perception. ([arXiv 2407.06581](https://arxiv.org/html/2407.06581v1))
- **Map reasoning is unsolved.** MapBench (2025, 1,649 queries, 100 maps) shows that open-source LVLMs often return no valid answer and that all models struggle with cluttered symbols and spatial relations. ([arXiv 2503.14607](https://arxiv.org/html/2503.14607v1))
- **For choropleth maps, extract first and reason second.** MapQA (2022) shows that the best method reads the legend, classifies each region by colour into a table, and only then answers questions. Its V-MODE extraction reaches 92–99.9% on discrete legends, far above end-to-end VQA models. ([arXiv 2211.08545](https://arxiv.org/pdf/2211.08545))
- **Self-ensembling improves chart extraction.** Sampling the VLM several times, aligning the tables and taking the per-cell median improves accuracy by up to 23% (relative) on hard charts. The spread across samples serves as an uncertainty estimate. (Berkane et al., 2026, arXiv 2605.27298; [leaderboard summary](https://benchmarklist.com/benchmarks/self_ensembling_vision_language_models_for_chart_data_extraction/))
- **Grounding exists, but it is not required.** Qwen2.5-VL can output bounding boxes as JSON in pixel coordinates ([Qwen blog](https://qwenlm.github.io/blog/qwen2.5-vl/)). This could be used later to show the exact image region behind a value (UC3 "highlighted region").
- **Ollama supports structured (JSON-schema) output with vision models.** ([Ollama blog](https://registry.ollama.ai/blog/structured-outputs))

## 3. Design that follows (implemented in `src/imaging/`)

1. **Measure deterministically wherever the map encodes the answer in colour** (`maps.py`). Legend swatches are found automatically and paired with the legend labels in `config/images.toml`. Neighbourhoods are the regions enclosed by the black border lines, named via seed points. Pixels per legend colour are counted per neighbourhood. This is reproducible, needs no model, and writes an overlay image so the researcher can check what was counted. What it measures is the share of coloured *map area*, not the number of homes, and every statement says so.
2. **Read with a vision model only where the information is printed text or icons** (`vision.py`). Each image is read N times (self-ensemble, median, agreement), optionally in overlapping tiles, with deterministic checks: percentages must lie in 0–100 and donut parts must sum to about 100%.
3. **The researcher confirms every model reading** (`confirm.py`, UI tab "Afbeeldingen"). Readings stay *unconfirmed* until confirmed, corrected or rejected, and the log is hash-chained. The verifier marks any claim that relies on an unconfirmed reading as weak (`image_unconfirmed`).
4. **The tool is honest about what an image cannot show.** A proposal map is never used as a finding. Counting play locations is not testing a distance rule (only colour-coded buildings test R3/R5/R6). Classes drawn in identical colours are merged and flagged. A chart drawn from an ingested spreadsheet is not read at all.

## 4. Results on the Oss maps (deterministic, no model)

| Report statement | Measured from the image |
|---|---|
| "het merendeel … beschikt over 1 ha beweeggroen binnen 300 m" | 82% of coloured buildings "wel groen" |
| "in Centrum-West voldoet de helft niet" | Centrum-West: 57% "geen groen" |
| "bijna ieder huis heeft 3 of 4 voorzieningen binnen 800 m" | 89% class 3–4; Vlashoek 61% class 2 |
| "gemiddeld twee sportaccommodaties binnen 1500 m" | 99.7% class 2 |

Neighbourhood detection: 7 regions found on all five building/structure maps, and a template transferred by registration matched the directly detected regions 100%. **Limitation:** on the four sport/play maps the borders are hidden under semi-transparent zones and are drawn differently, so per-neighbourhood zone coverage is not measured. The tool reports this instead of guessing.

## 5. Evaluation (SQ3) — how to compare vision models

`python -m imaging.cli eval-reading --gold <file>` scores model readings against hand-checked values and reports recall, value accuracy, precision, unmatched (possibly hallucinated) values, and how many wrong values the safeguards flagged. A draft gold file with 112 values from the two Oss infographics was transcribed and kept **outside the repository**, because it is confidential. Suggested experiment: `vision_qwen25vl` vs `vision_qwen3vl` vs `vision_gemma3_4b` × samples {1, 3} × tiles {1, 2}. Measure accuracy, flagged-wrong rate, time and RAM.

## 6. Recommendations

- **Ask InnoBeweegLab for the QGIS layers** (GeoPackage or shapefiles) behind the maps. They would turn pixel shares into exact counts of homes, and the buffer coverage per neighbourhood would become measurable. This is the strongest traceability gain available.
- Ask for the **data behind the infographics** (likely Allecijfers / Buurtatlas exports). Each value would then be a spreadsheet cell instead of a model reading.
- Keep model reading as the fallback for images that only exist as pictures, and always pair it with confirmation.
