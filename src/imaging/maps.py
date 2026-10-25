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


# ------------------------------------------------------------------ registration
#
# Maps of one study area are exported from the same QGIS project, often on the
# same base map. Where a map's own border lines are hidden under translucent
# distance zones, the neighbourhoods (and the homes) are carried over from a
# reference map instead. The two maps are registered on what they share - the
# base map's streets and building edges - not on the borders: edges survive a
# translucent overlay, border lines under it often do not pass a darkness test.
# The fit is then checked independently: the carried-over border network must
# land on dark pixels of the target map ('border_overlap').

BORDER_OK = 0.8        # below this share the transfer is not trusted


def edge_map(img: np.ndarray, scale: int = 1) -> np.ndarray:
    """Gradient magnitude of the luminance, optionally block-averaged by `scale`."""
    lum = img.astype(np.float32).mean(axis=2)
    if scale > 1:
        h, w = lum.shape
        lum = lum[:h // scale * scale, :w // scale * scale].reshape(h // scale, scale, w // scale, scale).mean((1, 3))
    return np.hypot(ndimage.sobel(lum, 1), ndimage.sobel(lum, 0)).astype(np.float32)


def _ncc_search(a: np.ndarray, b: np.ndarray, min_overlap: float = 0.5) -> tuple[float, int, int]:
    """Best placement of `a` over `b` by normalised cross-correlation (FFT).
    Returns (score, dy, dx): a's top-left corner in b's coordinates."""
    from scipy.signal import fftconvolve
    a = a - a.mean()
    b = b - b.mean()
    ones = np.ones_like(a)
    corr = fftconvolve(b, a[::-1, ::-1], mode="full")
    energy = fftconvolve(b * b, ones, mode="full")
    cover = fftconvolve(np.ones_like(b), ones, mode="full") / ones.size
    score = corr / np.sqrt(np.maximum(energy, 1e-6)) / np.sqrt(float((a * a).sum()) or 1.0)
    score[cover < min_overlap] = -1.0
    py, px = np.unravel_index(int(np.argmax(score)), score.shape)
    return float(score[py, px]), int(py - (a.shape[0] - 1)), int(px - (a.shape[1] - 1))


def _overlap(src_shape: tuple, dst_shape: tuple, dy: int, dx: int):
    """Slices of src and dst that overlap when src's (0,0) sits at dst (dy, dx)."""
    ys0, xs0, yt0, xt0 = max(0, -dy), max(0, -dx), max(0, dy), max(0, dx)
    hh, ww = min(src_shape[0] - ys0, dst_shape[0] - yt0), min(src_shape[1] - xs0, dst_shape[1] - xt0)
    if hh <= 0 or ww <= 0:
        return None
    return (slice(ys0, ys0 + hh), slice(xs0, xs0 + ww)), (slice(yt0, yt0 + hh), slice(xt0, xt0 + ww))


def _ncc_at(a: np.ndarray, b: np.ndarray, dy: int, dx: int) -> float:
    sl = _overlap(a.shape, b.shape, dy, dx)
    if sl is None:
        return -1.0
    x, y = a[sl[0]], b[sl[1]]
    x, y = x - x.mean(), y - y.mean()
    den = float(np.sqrt((x * x).sum() * (y * y).sum()))
    return float((x * y).sum() / den) if den else -1.0


def warp(arr: np.ndarray, zoom: float, offset_xy: tuple[int, int] | list[int], shape: tuple[int, int]) -> np.ndarray:
    """Place a reference-frame array into a target frame: target = zoom * ref + offset."""
    a = _resize_nearest(arr, zoom)
    out = np.zeros(shape[:2], dtype=arr.dtype)
    sl = _overlap(a.shape, out.shape, int(offset_xy[1]), int(offset_xy[0]))
    if sl is not None:
        out[sl[1]] = a[sl[0]]
    return out


def align_basemap(ref_img: np.ndarray, target_img: np.ndarray, scale: int = 4,
                  zoom_range: tuple[float, float] = (0.9, 1.1), zoom_step: float = 0.01) -> dict:
    """Register two maps on their shared base map (street and building edges):
    coarse zoom + translation search by FFT cross-correlation at 1/`scale`,
    then translation refinement at 1/2 and full resolution.
    Returns {"zoom", "offset_xy": [dx, dy], "ncc"} with target = zoom * ref + offset."""
    ea, eb = edge_map(ref_img, scale), edge_map(target_img, scale)
    zooms = [1.0] + [float(z) for z in np.arange(zoom_range[0], zoom_range[1] + 1e-9, zoom_step) if abs(z - 1.0) > 1e-9]
    best = (-2.0, 1.0, 0, 0)
    for z in zooms:
        a = ea if z == 1.0 else ndimage.zoom(ea, z, order=1)
        s, dy, dx = _ncc_search(a, eb)
        if s > best[0] + 1e-6:
            best = (s, z, dy, dx)
    _, z, dy, dx = best
    dy, dx = dy * scale, dx * scale
    for sc, radius in ((2, scale // 2 + 1), (1, 2)):
        a, b = edge_map(ref_img, sc), edge_map(target_img, sc)
        if z != 1.0:
            a = ndimage.zoom(a, z, order=1)
        cands = [(_ncc_at(a, b, (dy + ddy * sc) // sc, (dx + ddx * sc) // sc), dy + ddy * sc, dx + ddx * sc)
                 for ddy in range(-radius, radius + 1) for ddx in range(-radius, radius + 1)]
        s, dy, dx = max(cands)
    return {"zoom": round(float(z), 3), "offset_xy": [int(dx), int(dy)], "ncc": round(float(s), 3)}


def border_agreement(ref_img: np.ndarray, info: dict, target_img: np.ndarray, contrast: float = 40.0,
                     window: int = 41, tol: int = 3) -> float:
    """Share of the reference border network that lands on (or within `tol` px
    of) line-dark pixels in the target after transfer. 'Line-dark' is relative:
    at least `contrast` below the local mean luminance. A black line under one
    or two translucent zones is tinted and lighter (luminance ~110) but still
    much darker than its surroundings. On the Oss maps a correct fit scores
    0.95-0.99, a fit 25 px off 0.30-0.47."""
    b = warp(boundary_mask(ref_img).astype(np.uint8), info["zoom"], info["offset_xy"], target_img.shape[:2]).astype(bool)
    lum = target_img.astype(np.float32).mean(axis=2)
    dark = lum < ndimage.uniform_filter(lum, size=window) - contrast
    near = ndimage.binary_dilation(dark, iterations=tol)
    return round(float((b & near).sum() / max(b.sum(), 1)), 3)


def align_regions(ref_img: np.ndarray, ref_regions: np.ndarray, target_img: np.ndarray,
                  scale: int = 4, zoom_range: tuple[float, float] = (0.9, 1.1), zoom_step: float = 0.01
                  ) -> tuple[np.ndarray, dict]:
    """Transfer a named region template from a reference map onto another map
    of the same study area. Returns (region image in the target frame, info:
    zoom, offset_xy, ncc, border_overlap). border_overlap < BORDER_OK means the
    fit is doubtful; callers then report whole-map results only."""
    info = align_basemap(ref_img, target_img, scale, zoom_range, zoom_step)
    info["border_overlap"] = border_agreement(ref_img, info, target_img)
    return warp(ref_regions, info["zoom"], info["offset_xy"], target_img.shape[:2]), info


@dataclass
class RegionTemplate:
    """Neighbourhoods and homes taken from one reference map of a case.
    `homes` = legend-coloured buildings inside the regions: on a Basisbehoefte-
    style map every coloured building is an address the RIVM rules are about."""
    image_id: str
    img: np.ndarray
    regions: np.ndarray
    names: dict[int, str]
    homes: np.ndarray

    @classmethod
    def from_map(cls, image_id: str, path: str | Path, legend: list[str],
                 seeds: Optional[dict[str, tuple[float, float]]] = None, tolerance: float = 30.0,
                 legend_search: tuple = (0.55, 0.6, 1.0, 1.0)) -> "RegionTemplate":
        img = load_rgb(path)
        reg, regions, _ = detect_regions(img, seeds)
        sw, _w = label_swatches(detect_swatches(img, legend_search), legend)
        classes = classify(img, [s for s in sw if s.label], tolerance)
        return cls(image_id, img, reg, {r.region_id: r.name or f"regio {r.region_id}" for r in regions},
                   (classes >= 0) & (reg > 0))


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
                  region_template: Optional[RegionTemplate] = None) -> MapMeasurement:
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
        reg_img, info = align_regions(region_template.img, region_template.regions, img)
        info["reference"] = region_template.image_id
        params["region_alignment"] = info
        if info["border_overlap"] < BORDER_OK:
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
    if not regions and reg_img.max() > 0 and region_template is not None:
        region_names = region_template.names   # names of the template's region ids
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


# ------------------------------------------------------------------ distance zones (coverage)
#
# Sport- en speelkaarten draw the RIVM distance rule itself: a translucent zone
# of 200 m (children up to 12) or 400 m (older than 12) around every play or
# sport location. Whether a home lies inside the zone IS the rule's outcome for
# that home. Homes and neighbourhoods come from the reference map (registered
# on the shared base map); the zone is recognised by its tint.

ZONE_THRESHOLD = 0.5


def tint_score(img: np.ndarray, rgb: tuple[int, int, int] | list[int]) -> np.ndarray:
    """Per pixel: how strongly it carries the tint of a translucent overlay whose
    legend swatch (drawn over white) has colour `rgb`. Chroma (colour minus its
    own grey level) projected on the swatch's chroma: ~1 = tinted like the
    swatch, ~0 = untinted grey/white base map. A translucent layer adds the same
    chroma to any grey, so dark and light buildings score alike."""
    t = np.asarray(rgb, np.float32)
    t = t - t.mean()
    c = img.astype(np.float32)
    c -= c.mean(axis=2, keepdims=True)
    return (c @ t) / float(t @ t)


def measure_zone_coverage(path: str | Path, labels: list[str], zone_label: str, template: RegionTemplate,
                          threshold: float = ZONE_THRESHOLD, legend_search: tuple = (0.55, 0.6, 1.0, 1.0),
                          overlay_to: Optional[str | Path] = None) -> MapMeasurement:
    """Share of homes (and of the area) inside a translucent distance zone,
    for the whole study area and per neighbourhood.

    per region: {"homes_in_zone", "homes", "area_in_zone", "area"} in pixels.
    "Homes" = legend-coloured buildings on the template's reference map; an
    area without coloured buildings (e.g. not built yet) gets area only."""
    img = load_rgb(path)
    sw, warnings = label_swatches(detect_swatches(img, legend_search), labels)
    zone = next((s for s in sw if s.label == zone_label), None)
    params: dict = {"threshold": threshold, "reference": template.image_id}
    method = ("share of homes (coloured buildings on the reference map) inside the translucent distance zone; "
              "maps registered on their shared base map (deterministic)")
    if zone is None:
        warnings.append(f"legend entry '{zone_label}' not found: zone coverage not measured")
        return MapMeasurement(str(path), method, [], {}, {}, [], warnings, params)
    info = align_basemap(template.img, img)
    info["border_overlap"] = border_agreement(template.img, info, img)
    params["alignment"] = info
    if info["border_overlap"] < BORDER_OK:
        warnings.append(f"the reference map could not be matched to this map (border overlap {info['border_overlap']}): "
                        "zone coverage not measured")
        return MapMeasurement(str(path), method, [], {}, {}, [], warnings, params)
    reg = warp(template.regions, info["zoom"], info["offset_xy"], img.shape[:2])
    homes = warp(template.homes.astype(np.uint8), info["zoom"], info["offset_xy"], img.shape[:2]).astype(bool)
    score = tint_score(img, zone.rgb)
    inzone = score >= threshold

    def cov(mask: np.ndarray) -> dict:
        h = mask & homes
        return {"homes_in_zone": int((h & inzone).sum()), "homes": int(h.sum()),
                "area_in_zone": int((mask & inzone).sum()), "area": int(mask.sum())}

    total = cov(reg > 0)
    per_region = {name: cov(reg == rid) for rid, name in template.names.items()}
    ambiguous = float(((score > 0.2) & (score < threshold) & homes).sum() / max(homes.sum(), 1))
    params["ambiguous_home_share"] = round(ambiguous, 4)
    if ambiguous > 0.05:
        warnings.append(f"{ambiguous:.0%} of home pixels have an in-between tint: zone edges uncertain")
    overlay_path = str(write_zone_overlay(img, reg, template.names, homes, inzone, per_region, overlay_to)) if overlay_to else None
    return MapMeasurement(
        image=str(path), method=method,
        classes=[{"label": zone_label, "rgb": list(zone.rgb), "swatch_bbox": list(zone.bbox)}],
        total=total, per_region=per_region, unnamed_regions=[], warnings=warnings, params=params,
        overlay_path=overlay_path,
    )


def coverage_pct(c: dict) -> tuple[Optional[int], Optional[int]]:
    """(homes %, area %) inside the zone; homes None when the area has no homes."""
    homes = int(round(100 * c["homes_in_zone"] / c["homes"])) if c.get("homes") else None
    area = int(round(100 * c["area_in_zone"] / c["area"])) if c.get("area") else None
    return homes, area


def write_zone_overlay(img: np.ndarray, reg: np.ndarray, names: dict[int, str], homes: np.ndarray, inzone: np.ndarray,
                       per_region: dict[str, dict], out: str | Path, max_side: int = 1600) -> Path:
    """Check image: zone faintly blue, homes inside the zone dark blue, homes
    outside the zone red, neighbourhood borders and coverage per neighbourhood."""
    from PIL import ImageDraw, ImageFont
    vis = (img.astype(np.float32) * 0.25 + 191).astype(np.uint8)
    vis[inzone & (reg > 0)] = (vis[inzone & (reg > 0)] * 0.6 + np.array([120, 170, 255]) * 0.4).astype(np.uint8)
    vis[homes & inzone] = (25, 70, 200)
    vis[homes & ~inzone] = (220, 30, 30)
    edges = reg != ndimage.grey_erosion(reg, size=(5, 5))
    vis[edges & (reg > 0)] = (0, 0, 0)
    im = Image.fromarray(vis)
    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=max(18, img.shape[1] // 70))
    except Exception:  # noqa: BLE001 - older Pillow
        font = None
    for rid, name in names.items():
        m = reg == rid
        if not m.any():
            continue
        cy, cx = ndimage.center_of_mass(m)
        h, a = coverage_pct(per_region.get(name, {}))
        label = f"{name}\nwoningen {h}% · gebied {a}%" if h is not None else f"{name}\ngebied {a}% (geen woningen)"
        draw.multiline_text((cx - len(name) * 5, cy - 10), label, fill=(0, 0, 0), font=font,
                            stroke_width=3, stroke_fill=(255, 255, 255))
    im.thumbnail((max_side, max_side))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out
