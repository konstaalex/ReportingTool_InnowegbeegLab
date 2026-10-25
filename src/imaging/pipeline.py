"""Analyse every registered image of a case -> citable image records.

    summary = analyse_case_images(store, "euterpepark-oss", out_dir, vision_provider=None)

Deterministic measurements always run (no model needed). Model readings run
only when a vision provider is given; without one, those images are listed as
'needs a vision model' instead of being skipped silently.

Records produced (perspective 'data', all citable like spreadsheet cells):
  image_measurement   one per map x area ('onderzoeksgebied' or a neighbourhood);
                      measure_kind 'shares' (coloured buildings), 'counts' (markers)
                      or 'coverage' (share of homes inside a distance zone)
  image_reading       one per value read from an infographic (status starts 'unconfirmed')
  image_observation   one per sentence a model wrote about a symbol map (always unconfirmed)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ingestion.config import load_config
from ingestion.models import SourceRecord
from ingestion.store import SourceStore

from . import maps, vision

STUDY_AREA = "onderzoeksgebied"


def _cfg_for(record: SourceRecord) -> dict:
    cfg = load_config("images.toml")
    for img in cfg.get("image", []):
        if img.get("id") == record.extra.get("image_id"):
            return img
    return {}


def _case_cfg(case_id: str) -> dict:
    return load_config("images.toml").get("case", {}).get(case_id, {})


def _measurement_records(rec: SourceRecord, cfg: dict, m: maps.MapMeasurement, kind: str) -> list[SourceRecord]:
    out = []
    study_total, study_only = m.total, kind in ("shares", "coverage")
    if kind == "counts" and m.per_region:
        # markers inside the neighbourhoods only (the map also shows the surroundings)
        study_total = {k: sum(c.get(k, 0) for c in m.per_region.values()) for k in m.total}
        study_only = True
    areas = [(STUDY_AREA, study_total)] + sorted(m.per_region.items())
    for area, counts in areas:
        what = {"shares": "kleuraandeel", "counts": "telling", "coverage": "dekking afstandszone"}[kind]
        locator = f"{cfg.get('title', rec.text)} › {area} ({what})"
        extra = {
            "record_kind": "image_measurement", "measure_kind": kind, "image_id": cfg.get("id"),
            "title": cfg.get("title"), "measure": cfg.get("measure"), "area": area,
            "counts": {k: v for k, v in counts.items() if not k.startswith("_")},
            "themes": cfg.get("themes", []), "rules": cfg.get("rules", []),
            "method": m.method, "params": m.params, "warnings": m.warnings,
            "overlay": m.overlay_path, "image_record_id": rec.id, "extraction_method": "deterministic",
            "study_area_only": study_only if area == STUDY_AREA else True,
        }
        if kind == "shares":
            extra["shares"] = {k: (round(v, 4) if v is not None else None) for k, v in m.shares(counts).items()}
            extra["classified_pixels"] = m.classified_pixels(counts)
        elif kind == "coverage":
            homes_pct, area_pct = maps.coverage_pct(counts)
            extra["coverage"] = {"homes_pct": homes_pct, "area_pct": area_pct, "zone": cfg.get("zone"),
                                 "homes_px": counts.get("homes", 0), "area_px": counts.get("area", 0)}
            extra["reference_map"] = m.params.get("reference")
        out.append(SourceRecord(
            id=SourceRecord.make_id("data", rec.source_file, None, locator), perspective="data",
            source_file=rec.source_file, sheet=None, locator=locator, theme=None,
            text=f"{cfg.get('title')} – {area}", extra=extra, case_id=rec.case_id,
            needs_manual_mapping=bool(m.warnings and area != STUDY_AREA and kind == "counts"),
        ))
    return out


def analyse_case_images(store: SourceStore, case_id: str, out_dir: str | Path,
                        vision_provider=None, samples: int = 3, verbose: bool = False) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    case_cfg = _case_cfg(case_id)
    seeds = {k: tuple(v) for k, v in case_cfg.get("regions", {}).items()}
    images = [r for r in store.all() if r.case_id == case_id and r.extra.get("record_kind") == "image"]
    by_id = {r.extra.get("image_id"): r for r in images}
    template = None
    ref_id = case_cfg.get("region_reference")
    if ref_id and ref_id in by_id:
        ref_cfg = next((c for c in load_config("images.toml").get("image", []) if c.get("id") == ref_id), {})
        template = maps.RegionTemplate.from_map(ref_id, by_id[ref_id].extra["absolute_path"],
                                                ref_cfg.get("legend", []), seeds)

    new_records: list[SourceRecord] = []
    summary: dict = {"case_id": case_id, "images": {}}

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    for rec in images:
        cfg = _cfg_for(rec)
        kind = cfg.get("kind", "unknown")
        path = rec.extra.get("absolute_path")
        item: dict = {"file": rec.source_file, "kind": kind}
        try:
            if kind == "map_classes":
                m = maps.measure_classes(path, cfg["legend"], seeds, overlay_to=out_dir / f"{cfg['id']}_overlay.png")
                new_records += _measurement_records(rec, cfg, m, "shares")
                item.update({"status": "measured", "warnings": m.warnings, "overlay": m.overlay_path,
                             "regions": list(m.per_region)})
                (out_dir / f"{cfg['id']}_measurement.json").write_text(json.dumps(m.to_dict(), indent=2, ensure_ascii=False))
            elif kind == "map_objects":
                m = maps.count_objects(path, cfg["legend"], tuple(cfg.get("overlay", [])), seeds,
                                       region_template=template)
                new_records += _measurement_records(rec, cfg, m, "counts")
                item.update({"status": "counted", "warnings": m.warnings, "regions": list(m.per_region)})
                (out_dir / f"{cfg['id']}_measurement.json").write_text(json.dumps(m.to_dict(), indent=2, ensure_ascii=False))
                if cfg.get("read_with_model"):
                    item["model_reading"] = "needs a vision model" if vision_provider is None else "described"
                    if vision_provider is not None:
                        new_records += _observations(rec, cfg, vision_provider, list(seeds), out_dir)
            elif kind == "map_zones":
                if template is None:
                    item["status"] = "not measured: the case has no region_reference map (config/images.toml)"
                else:
                    m = maps.measure_zone_coverage(path, cfg["legend"], cfg["zone"], template,
                                                   overlay_to=out_dir / f"{cfg['id']}_overlay.png")
                    if m.total:
                        new_records += _measurement_records(rec, cfg, m, "coverage")
                    item.update({"status": "measured" if m.total else "not measured", "warnings": m.warnings,
                                 "overlay": m.overlay_path, "regions": list(m.per_region)})
                    (out_dir / f"{cfg['id']}_measurement.json").write_text(json.dumps(m.to_dict(), indent=2, ensure_ascii=False))
            elif kind == "infographic":
                if vision_provider is None:
                    item["status"] = "needs a vision model (python -m imaging.cli analyse --vision-model ...)"
                else:
                    run = vision.read_infographic(path, vision_provider, cfg.get("title", rec.text), samples=samples,
                                                  n_tiles=int(cfg.get("tiles", 1)))
                    (out_dir / f"{cfg['id']}_reading.json").write_text(json.dumps(run.to_dict(), indent=2, ensure_ascii=False))
                    for rd in run.readings:
                        locator = f"{cfg.get('title')} › {rd.chart} › {rd.area} › {rd.label}"
                        new_records.append(SourceRecord(
                            id=SourceRecord.make_id("data", rec.source_file, None, locator), perspective="data",
                            source_file=rec.source_file, sheet=None, locator=locator, theme=None,
                            text=f"{rd.label} ({rd.area}): {rd.as_printed}", value=rd.value,
                            extra={"record_kind": "image_reading", "image_id": cfg.get("id"), "title": cfg.get("title"),
                                   **rd.to_dict(), "model": run.model.get("model"), "image_record_id": rec.id,
                                   "extraction_method": f"model-assisted ({run.model.get('model')}, {run.samples} samples)",
                                   "themes": cfg.get("themes", [])},
                            needs_manual_mapping=True, case_id=case_id,
                        ))
                    item.update({"status": "read", "values": len(run.readings), "errors": run.errors,
                                 "latency_s": round(run.latency_s, 1)})
            elif kind == "map_vision":
                if vision_provider is None:
                    item["status"] = "needs a vision model"
                else:
                    new_records += _observations(rec, cfg, vision_provider, list(seeds), out_dir)
                    item["status"] = "described"
            elif kind in ("chart_derived", "proposal_map", "superseded"):
                item["status"] = {"chart_derived": f"not read: drawn from {cfg.get('derived_from')}, which is ingested itself",
                                  "proposal_map": "not used as evidence: design proposal, not the current situation",
                                  "superseded": f"ignored: superseded by '{cfg.get('superseded_by')}'"}[kind]
            else:
                item["status"] = "unknown image: add it to config/images.toml"
        except Exception as exc:  # noqa: BLE001 - report per image
            item["status"] = f"failed: {type(exc).__name__}: {exc}"
        summary["images"][cfg.get("id") or rec.source_file] = item
        log(f"[{rec.source_file}] {kind}: {item.get('status')}")

    # replace earlier image-derived records of this case (re-analysis = fresh results)
    derived = {"image_measurement", "image_reading", "image_observation"}
    keep = [r for r in store.all() if not (r.case_id == case_id and r.extra.get("record_kind") in derived)]
    store._records = keep  # noqa: SLF001 - store is a simple list + index
    store._by_id = {r.id: r for r in keep}  # noqa: SLF001
    store.add_many(new_records)
    summary["records_added"] = len(new_records)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def _observations(rec: SourceRecord, cfg: dict, provider, areas: list[str], out_dir: Path) -> list[SourceRecord]:
    res = vision.describe_map(rec.extra["absolute_path"], provider, cfg.get("title", rec.text), cfg.get("measure", ""), areas)
    (out_dir / f"{cfg['id']}_description.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    out = []
    for i, ob in enumerate(res.get("observations", []), start=1):
        locator = f"{cfg.get('title')} › modelbeschrijving {i}"
        out.append(SourceRecord(
            id=SourceRecord.make_id("data", rec.source_file, None, locator), perspective="data",
            source_file=rec.source_file, sheet=None, locator=locator, theme=None, text=str(ob.get("text", "")),
            extra={"record_kind": "image_observation", "image_id": cfg.get("id"), "title": cfg.get("title"),
                   "area": ob.get("area"), "legend_item": ob.get("legend_item"), "model": res["model"].get("model"),
                   "themes": cfg.get("themes", []), "rules": cfg.get("rules", []), "image_record_id": rec.id,
                   "extraction_method": f"model-assisted ({res['model'].get('model')}, description)"},
            needs_manual_mapping=True, case_id=rec.case_id,
        ))
    return out
