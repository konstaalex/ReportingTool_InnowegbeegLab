"""Deterministic map measurement: legend swatches, neighbourhood regions, colour shares.

Why deterministic and not a vision model? Research on VLM map reading
(MapBench 2025; "Vision language models are blind", ACCV 2024) shows that
current models fail at exactly what these maps require: counting, small
overlapping shapes, and precise spatial relations. MapQA (2022) shows that
the reliable route for choropleth-style maps is "extract the legend, classify
the regions by colour, then reason over the resulting table". These QGIS
maps colour every building by its legend class, so the share of each class
can be measured by counting pixels - reproducibly, and checkable by eye on
the overlay image this module writes.

What the numbers mean (stated on every evidence item): the share of coloured
*map area* (building footprints as drawn), not the number of homes. A large
apartment block weighs more than a terraced house. Indicative, not a GIS count.

Pipeline per map:
1. find legend swatches (solid rectangles) in the legend corner, top -> bottom;
2. pair them with the legend labels from config/images.toml (count must match;
   identical colours -> classes merged and flagged);
3. detect the study-area boundary network (thick near-black lines) and the
   enclosed regions; name regions via seed points from config;
4. per region, count pixels whose colour is within `tolerance` of a swatch.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image
from scipy import ndimage

Image.MAX_IMAGE_PIXELS = None


@dataclass
class Swatch:
    rgb: tuple[int, int, int]
    bbox: tuple[int, int, int, int]      # x0, y0, x1, y1 in full-image pixels
    area: int
    label: Optional[str] = None
    shared_with: list[str] = field(default_factory=list)   # other labels with the identical colour


@dataclass
class Region:
    region_id: int
    name: Optional[str]
    pixels: int
    bbox: tuple[int, int, int, int]
    centroid_rel: tuple[float, float]    # centroid relative to the boundary network bbox (0-1)


def load_rgb(path: str | Path) -> np.ndarray:
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[3])
        im = bg
    return np.asarray(im.convert("RGB"))


# ------------------------------------------------------------------ legend

def detect_swatches(img: np.ndarray, search: tuple[float, float, float, float] = (0.55, 0.6, 1.0, 1.0),
                    min_rel_area: float = 0.0002, max_rel_area: float = 0.004,
                    min_saturation: int = 40) -> list[Swatch]:
    """Solid, saturated, rectangular patches in the legend corner, sorted top -> bottom."""
    h, w, _ = img.shape
    x0, y0, x1, y1 = int(search[0] * w), int(search[1] * h), int(search[2] * w), int(search[3] * h)
    crop = img[y0:y1, x0:x1].astype(np.int32)
    flat = crop.reshape(-1, 3)
    sat = flat.max(1) - flat.min(1)
    keys = (flat[:, 0] << 16) | (flat[:, 1] << 8) | flat[:, 2]
    uniq, counts = np.unique(keys[sat >= min_saturation], return_counts=True)
    total = h * w
    swatches: list[Swatch] = []
    for key, cnt in zip(uniq, counts):
        if cnt < min_rel_area * total:
            continue
        mask = (keys == key).reshape(crop.shape[:2])
        lab, n = ndimage.label(mask)
        for sl, idx in zip(ndimage.find_objects(lab), range(1, n + 1)):
            area = int((lab[sl] == idx).sum())
            bh, bw = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
            if not (min_rel_area * total <= area <= max_rel_area * total):
                continue
            if area / float(bh * bw) < 0.9 or not (0.3 <= bw / max(bh, 1) <= 4):
                continue
            rgb = (int(key >> 16), int((key >> 8) & 255), int(key & 255))
            swatches.append(Swatch(rgb, (x0 + sl[1].start, y0 + sl[0].start, x0 + sl[1].stop, y0 + sl[0].stop), area))
    swatches.sort(key=lambda s: (s.bbox[1], s.bbox[0]))
    return swatches


def label_swatches(swatches: list[Swatch], labels: list[str]) -> tuple[list[Swatch], list[str]]:
    """Pair swatches (top->bottom) with config labels. Returns (swatches, warnings)."""
    warnings: list[str] = []
    if len(swatches) != len(labels):
        warnings.append(f"legend has {len(swatches)} colour swatches, config lists {len(labels)} labels")
    for s, lab in zip(swatches, labels):
        s.label = lab
    by_rgb: dict[tuple, list[Swatch]] = {}
    for s in swatches:
        if s.label:
            by_rgb.setdefault(s.rgb, []).append(s)
    for rgb, group in by_rgb.items():
        if len(group) > 1:
            names = [g.label for g in group]
            for g in group:
                g.shared_with = [n for n in names if n != g.label]
            warnings.append(f"legend classes {', '.join(names)} use the identical colour {rgb}: "
                            "they cannot be told apart on the map and are reported together")
    return swatches, warnings


# ------------------------------------------------------------------ regions

def boundary_mask(img: np.ndarray, dark: int = 90, open_iter: int = 1, bridge: int = 8) -> np.ndarray:
    """Thick near-black lines = study-area / neighbourhood boundaries. Thin
    dark text (street names) is removed by a morphological opening; the
    largest connected component is the boundary network."""
    m = img.max(axis=2) < dark
    m = ndimage.binary_opening(m, structure=np.ones((3, 3)), iterations=open_iter)
    m = ndimage.binary_closing(m, structure=np.ones((3, 3)), iterations=bridge)   # bridge gaps in the lines
    lab, n = ndimage.label(m)
    if n == 0:
        return m
    sizes = ndimage.sum(m, lab, range(1, n + 1))
    # the boundary network can be split into several long pieces; keep every
    # piece that is at least 10% of the largest one (text blobs are far smaller)
    keep = [i + 1 for i, s in enumerate(sizes) if s >= 0.1 * sizes.max()]
    return np.isin(lab, keep)


def detect_regions(img: np.ndarray, seeds: Optional[dict[str, tuple[float, float]]] = None,
                   min_rel_area: float = 0.01) -> tuple[np.ndarray, list[Region], tuple[int, int, int, int]]:
    """Enclosed areas of the boundary network. Returns (label image, regions, network bbox).
    `seeds` maps region name -> (x, y) relative to the network bbox."""
    b = boundary_mask(img)
    ys, xs = np.nonzero(b)
    if len(xs) == 0:
        return np.zeros(img.shape[:2], int), [], (0, 0, 0, 0)
    bx0, by0, bx1, by1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    free = ~ndimage.binary_dilation(b, iterations=2)
    lab, n = ndimage.label(free)
    border_labels = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    hull_area = (bx1 - bx0) * (by1 - by0)
    regions: list[Region] = []
    out = np.zeros_like(lab)
    rid = 0
    for idx, sl in zip(range(1, n + 1), ndimage.find_objects(lab)):
        if idx in border_labels or sl is None:
            continue
        area = int((lab[sl] == idx).sum())
        if area < min_rel_area * hull_area:
            continue
        rid += 1
        out[lab == idx] = rid
        cy, cx = ndimage.center_of_mass(lab == idx)
        regions.append(Region(rid, None, area, (sl[1].start, sl[0].start, sl[1].stop, sl[0].stop),
                              ((cx - bx0) / max(bx1 - bx0, 1), (cy - by0) / max(by1 - by0, 1))))
    for name, (rx, ry) in (seeds or {}).items():
        x, y = int(bx0 + rx * (bx1 - bx0)), int(by0 + ry * (by1 - by0))
        if 0 <= y < out.shape[0] and 0 <= x < out.shape[1] and out[y, x]:
            regions[out[y, x] - 1].name = name
    return out, regions, (bx0, by0, bx1, by1)


def _resize_nearest(arr: np.ndarray, factor: float) -> np.ndarray:
    if abs(factor - 1.0) < 1e-9:
        return arr
    h, w = arr.shape
    nh, nw = max(1, int(round(h * factor))), max(1, int(round(w * factor)))
    yi = np.minimum((np.arange(nh) / factor).astype(int), h - 1)
    xi = np.minimum((np.arange(nw) / factor).astype(int), w - 1)
    return arr[yi][:, xi]


def align_regions(ref_img: np.ndarray, ref_regions: np.ndarray, target_img: np.ndarray,
                  scale: int = 4, zoom_range: tuple[float, float] = (0.8, 1.25), zoom_step: float = 0.01
                  ) -> tuple[np.ndarray, dict]:
    """Transfer a named region template from a reference map onto another map
    of the same study area, for maps whose own border lines are hidden under
    overlays. Searches zoom (scale) and translation by FFT cross-correlation of
    the border masks. Returns (region image in target frame, info with zoom,
    offset and overlap = share of the transferred border landing on dark target
    pixels; < 0.6 means the fit is doubtful and the measurement is flagged)."""
    from scipy.signal import fftconvolve
    ref_b = boundary_mask(ref_img)
    tgt_dark = ndimage.binary_opening(target_img.max(axis=2) < 90, iterations=1)
    near_dark = ndimage.binary_dilation(tgt_dark, iterations=3)
    # correlate border pieces with border pieces (not with every dark pixel:
    # street names and buildings would dominate the correlation)
    tgt_b = ndimage.binary_dilation(boundary_mask(target_img), iterations=2)
    b = tgt_b[::scale, ::scale].astype(np.float32)
    a0 = ndimage.binary_dilation(ref_b, iterations=2)[::scale, ::scale]

    nd_small = ndimage.binary_dilation(tgt_dark[::scale, ::scale], iterations=1)

    def place(z: float) -> tuple[float, int, int]:
        """Best translation for zoom z (cross-correlation), scored by the share of
        the zoomed reference border that lands on dark target pixels."""
        a = _resize_nearest(a0, z)
        corr = fftconvolve(b, a[::-1, ::-1].astype(np.float32), mode="full")
        py, px = np.unravel_index(int(np.argmax(corr)), corr.shape)
        dy_s, dx_s = py - (a.shape[0] - 1), px - (a.shape[1] - 1)
        thin = _resize_nearest(ref_b[::scale, ::scale], z)
        th, tw = nd_small.shape
        h, w = thin.shape
        ys0, xs0, yt0, xt0 = max(0, -dy_s), max(0, -dx_s), max(0, dy_s), max(0, dx_s)
        hh, ww = min(h - ys0, th - yt0), min(w - xs0, tw - xt0)
        if hh <= 0 or ww <= 0:
            return 0.0, dy_s, dx_s
        hit = (thin[ys0:ys0 + hh, xs0:xs0 + ww] & nd_small[yt0:yt0 + hh, xt0:xt0 + ww]).sum()
        return float(hit) / max(thin.sum(), 1), dy_s, dx_s

    if place(1.0)[0] >= 0.9:          # same layout and zoom: no search needed
        best_z = 1.0
    else:
        zooms = np.arange(zoom_range[0], zoom_range[1] + 1e-9, 0.04)
        coarse = max(zooms, key=lambda z: place(z)[0])
        fine = np.arange(coarse - 0.03, coarse + 0.03 + 1e-9, zoom_step)
        best_z = max(fine, key=lambda z: place(z)[0])
    _, dy_s, dx_s = place(best_z)
    dy, dx = dy_s * scale, dx_s * scale

    reg_z = _resize_nearest(ref_regions, best_z)
    b_z = _resize_nearest(ref_b.astype(np.uint8), best_z).astype(bool)
    out = np.zeros(target_img.shape[:2], dtype=ref_regions.dtype)
    shifted_b = np.zeros_like(tgt_dark)
    h, w = reg_z.shape
    th, tw = out.shape
    ys0, xs0, yt0, xt0 = max(0, -dy), max(0, -dx), max(0, dy), max(0, dx)
    hh, ww = min(h - ys0, th - yt0), min(w - xs0, tw - xt0)
    if hh > 0 and ww > 0:
        out[yt0:yt0 + hh, xt0:xt0 + ww] = reg_z[ys0:ys0 + hh, xs0:xs0 + ww]
        shifted_b[yt0:yt0 + hh, xt0:xt0 + ww] = b_z[ys0:ys0 + hh, xs0:xs0 + ww]
    overlap = float((shifted_b & near_dark).sum() / max(shifted_b.sum(), 1))
    return out, {"zoom": round(float(best_z), 3), "offset_xy": [int(dx), int(dy)], "border_overlap": round(overlap, 3)}


# ------------------------------------------------------------------ measurement

def classify(img: np.ndarray, swatches: list[Swatch], tolerance: float = 30.0) -> np.ndarray:
    """Per pixel: index of the nearest swatch colour within tolerance, else -1."""
    flat = img.reshape(-1, 3).astype(np.int32)
    best = np.full(flat.shape[0], -1, dtype=np.int32)
    best_d = np.full(flat.shape[0], tolerance ** 2 + 1, dtype=np.int64)
    for i, s in enumerate(swatches):
        d = ((flat - np.array(s.rgb)) ** 2).sum(1)
        better = d < best_d
        best[better] = i
        best_d[better] = d[better]
    best[best_d > tolerance ** 2] = -1
    return best.reshape(img.shape[:2])


@dataclass
class MapMeasurement:
    image: str
    method: str
    classes: list[dict]                      # label, rgb, shared_with
    total: dict                              # label -> pixels (whole study area)
    per_region: dict[str, dict]              # region name -> {label: pixels, "_classified": n}
    unnamed_regions: list[dict]
    warnings: list[str]
    params: dict
    overlay_path: Optional[str] = None

    def shares(self, counts: dict) -> dict[str, Optional[float]]:
        """Share of classified (legend-coloured) pixels per class; None when the
        area contains no legend-coloured buildings at all (e.g. not built yet)."""
        n = sum(v for k, v in counts.items() if not k.startswith("_"))
        return {k: (v / n if n else None) for k, v in counts.items() if not k.startswith("_")}

    def classified_pixels(self, counts: dict) -> int:
        return sum(v for k, v in counts.items() if not k.startswith("_"))

    def to_dict(self) -> dict:
        return asdict(self)


def measure_classes(path: str | Path, labels: list[str], seeds: Optional[dict[str, tuple[float, float]]] = None,
                    tolerance: float = 30.0, legend_search: tuple = (0.55, 0.6, 1.0, 1.0),
                    overlay_to: Optional[str | Path] = None) -> MapMeasurement:
    img = load_rgb(path)
    sw, warnings = label_swatches(detect_swatches(img, legend_search), labels)
    sw = [s for s in sw if s.label]
    reg_img, regions, net_bbox = detect_regions(img, seeds)
    cls = classify(img, sw, tolerance)
    inside = reg_img > 0
    # identical colours = one reported class, named in legend order ("3 / 4 / 5")
    names = []
    for s in sw:
        group = [x.label for x in sw if x.rgb == s.rgb]
        names.append(" / ".join(group))
    total: dict[str, int] = {}
    per_region: dict[str, dict] = {}
    for i, name in enumerate(names):
        if name in total:
            continue
        total[name] = int(((cls == i) & inside).sum())
    for r in regions:
        m = reg_img == r.region_id
        counts = {}
        for i, name in enumerate(names):
            if name not in counts:
                counts[name] = int(((cls == i) & m).sum())
        counts["_region_pixels"] = r.pixels
        per_region[r.name or f"regio {r.region_id}"] = counts
    unnamed = [{"region_id": r.region_id, "centroid_rel": [round(c, 3) for c in r.centroid_rel], "pixels": r.pixels}
               for r in regions if r.name is None]
    if unnamed:
        warnings.append(f"{len(unnamed)} region(s) without a name: add seed points in config/images.toml")
    if seeds:
        missing = [n for n in seeds if n not in per_region]
        if missing:
            warnings.append(f"seed point(s) fell outside every detected region: {', '.join(missing)}")
    if sum(total.values()) == 0:
        warnings.append("no pixels matched any legend colour inside the study area (check tolerance / legend)")
    overlay_path = None
    if overlay_to:
        overlay_path = str(write_overlay(img, reg_img, regions, cls, sw, overlay_to))
    return MapMeasurement(
        image=str(path), method="legend-colour pixel share per region (deterministic)",
        classes=[{"label": n, "rgb": list(s.rgb), "swatch_bbox": list(s.bbox)} for n, s in zip(names, sw)],
        total=total, per_region=per_region, unnamed_regions=unnamed, warnings=warnings,
        params={"tolerance": tolerance, "legend_search": list(legend_search), "network_bbox": list(net_bbox),
                "n_regions": len(regions)},
        overlay_path=overlay_path,
    )


def write_overlay(img: np.ndarray, reg_img: np.ndarray, regions: list[Region], cls: np.ndarray,
                  swatches: list[Swatch], out: str | Path, max_side: int = 1600) -> Path:
    """Check image for the researcher: classified pixels in their legend colour,
    everything else faded, region outlines and names drawn on top."""
    from PIL import ImageDraw
    vis = (img.astype(np.float32) * 0.25 + 191).astype(np.uint8)
    for i, s in enumerate(swatches):
        vis[(cls == i) & (reg_img > 0)] = s.rgb
    edges = reg_img != ndimage.grey_erosion(reg_img, size=(5, 5))
    vis[edges & (reg_img > 0)] = (0, 0, 0)
    im = Image.fromarray(vis)
    draw = ImageDraw.Draw(im)
    for r in regions:
        x0, y0, x1, y1 = r.bbox
        cy, cx = ndimage.center_of_mass(reg_img == r.region_id)
        try:
            from PIL import ImageFont
            font = ImageFont.load_default(size=max(18, img.shape[1] // 60))
        except Exception:  # noqa: BLE001 - older Pillow
            font = None
        label = r.name or f"regio {r.region_id}"
        draw.text((cx - len(label) * 6, cy), label, fill=(0, 0, 0), font=font, stroke_width=3, stroke_fill=(255, 255, 255))
    for s in swatches:
        draw.rectangle(s.bbox, outline=(255, 0, 255), width=4)
    im.thumbnail((max_side, max_side))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out


def count_objects(path: str | Path, labels: list[str], skip_labels: tuple[str, ...] = (),
                  seeds: Optional[dict[str, tuple[float, float]]] = None, tolerance: float = 25.0,
                  min_pixels: int = 40, legend_search: tuple = (0.55, 0.6, 1.0, 1.0),
                  region_template: Optional[tuple] = None) -> MapMeasurement:
    """Location maps (play/sport spots drawn as coloured dots or areas): count
    connected blobs per legend colour, per region when regions are available.
    `skip_labels` = transparent overlay classes (distance buffers), which are
    not objects. Touching markers of the same colour count as one blob, so
    counts are a lower bound."""
    img = load_rgb(path)
    sw, warnings = label_swatches(detect_swatches(img, legend_search), labels)
    sw = [s for s in sw if s.label and s.label not in skip_labels]
    reg_img, regions, _ = detect_regions(img, seeds)
    params: dict = {"tolerance": tolerance, "min_pixels": min_pixels}
    if not regions and region_template is not None:
        reg_img, info = align_regions(region_template[0], region_template[1], img)
        params["region_alignment"] = info
        if info["border_overlap"] < 0.9:
            warnings.append(f"neighbourhood borders could not be matched to this map (overlap {info['border_overlap']}); "
                            "only whole-map counts are reported")
            reg_img = np.zeros_like(reg_img)
    cls = classify(img, sw, tolerance)
    # exclude the legend box itself from the whole-map count
    frame = np.ones(img.shape[:2], bool)
    for s in sw:
        x0, y0, x1, y1 = s.bbox
        frame[max(0, y0 - 5):y1 + 5, max(0, x0 - 5):x1 + 5] = False
    names = [s.label for s in sw]
    total: dict[str, int] = {}
    per_region: dict[str, dict] = {}
    region_names = {r.region_id: (r.name or f"regio {r.region_id}") for r in regions}
    if not regions and reg_img.max() > 0 and region_template is not None and len(region_template) > 2:
        region_names = region_template[2]   # names of the template's region ids
    for i, name in enumerate(names):
        lab, n = ndimage.label((cls == i) & frame)
        sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1)) if n else []
        keep = [j + 1 for j, sz in enumerate(sizes) if sz >= min_pixels]
        total[name] = len(keep)
        if reg_img.max() > 0 and keep:
            centers = ndimage.center_of_mass(np.ones_like(lab), lab, keep)
            for (cy, cx) in centers:
                rid = int(reg_img[int(cy), int(cx)])
                if rid:
                    rname = region_names.get(rid, f"regio {rid}")
                    per_region.setdefault(rname, {n_: 0 for n_ in names})[name] += 1
    return MapMeasurement(
        image=str(path), method="connected legend-coloured blobs per class (deterministic; lower bound)",
        classes=[{"label": n, "rgb": list(s.rgb), "swatch_bbox": list(s.bbox)} for n, s in zip(names, sw)],
        total=total, per_region=per_region, unnamed_regions=[], warnings=warnings, params=params,
    )
