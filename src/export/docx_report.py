"""Render a ReportContent as a Wijkscan report in Word (and PDF when LibreOffice is present).

The structure comes from config/report_structure.toml (see docs/report-structure.md):
cover, colofon, table of contents, Samenvatting, Aanleiding, Methode (with the RIVM
rule box and the study-area map), Resultaten (demographic infographics, six themes
with their maps, charts and photos), Conclusies, Aanbevelingen (researcher), Bronnenlijst,
Bijlage 1 (maps with the rule each tests), Bijlage 2 (sunburst), Bijlage 3 (traceability).

Everything placed in the document is either reviewed text, an input image, a chart
drawn from evidence, a check image of a measurement, or a clearly marked placeholder
for the researcher. Each claim keeps its evidence ids (small grey superscript), each
figure its source; Bijlage 3 lists claim -> evidence -> file.

    path = render_docx(content, out_dir)          # -> <out_dir>/<kind>_<case>[_CONCEPT].docx
    pdf  = to_pdf(path)                           # None when LibreOffice is not installed
"""
from __future__ import annotations

import datetime as _dt
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_UNDERLINE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image, ImageDraw

from evidence.models import AANLEIDING, CONCLUSIES, GENERAL, THEME_CODES
from ingestion.config import config_dir, load_config
from ingestion.models import THEMES

from .charts import chart_data, render_chart
from .content import Item, ReportContent
from .report import methode_parts

Image.MAX_IMAGE_PIXELS = None
PAGE_W, PAGE_H = Cm(29.7), Cm(21.0)
TEXT_W_CM = 29.7 - 2.2 - 3.4


def _rgb(hex_: str) -> RGBColor:
    h = hex_.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


@dataclass
class Figure:
    kind: str                 # Afbeelding | Figuur
    number: int
    caption: str
    source: str               # image file / chart id / photo note
    evidence_ids: list[str] = field(default_factory=list)
    chapter: str = ""         # the chapter (Heading 1) the figure is placed in


class WijkscanRenderer:
    def __init__(self, content: ReportContent, out_dir: str | Path, structure: Optional[dict] = None):
        self.c = content
        self.cfg = structure or load_config("report_structure.toml")
        self.house = self.cfg["house"]
        self.accent = _rgb(self.house["accent"])
        self.grey = _rgb(self.house.get("grey", "#5F5F5F"))
        self.case_cfg = self.cfg.get("case", {}).get(content.case_id, {})
        self.fmt = {"city": self.case_cfg.get("city", ""), "area": self.case_cfg.get("area", content.case_label),
                    "focus": self.case_cfg.get("focus", "het onderzoeksgebied"), "case": content.case_label}
        self.out_dir = Path(out_dir)
        self.media = self.out_dir / "_media"
        self.media.mkdir(parents=True, exist_ok=True)
        self.figures: list[Figure] = []
        self.counter = {"Afbeelding": 0, "Figuur": 0}
        self.chapter = ""
        self.images = {r.extra.get("image_id"): r for r in content.store.all()
                       if r.case_id == content.case_id and r.extra.get("record_kind") == "image" and r.extra.get("image_id")}
        self.img_cfg = {c.get("id"): c for c in load_config("images.toml").get("image", [])}
        self.doc = Document()

    # ------------------------------------------------------------------ setup
    def _setup(self) -> None:
        sec = self.doc.sections[0]
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = PAGE_W, PAGE_H
        sec.left_margin, sec.right_margin = Cm(2.2), Cm(3.4)
        sec.top_margin, sec.bottom_margin = Cm(1.8), Cm(1.8)
        sec.different_first_page_header_footer = True
        styles = self.doc.styles
        body, head = self.house.get("body_font", "Arial"), self.house.get("heading_font", "Trebuchet MS")

        def font(style, name, size, bold=False, colour=None, italic=False):
            style.font.name = name
            style.font.size = Pt(size)
            style.font.bold = bold
            style.font.italic = italic
            if colour is not None:
                style.font.color.rgb = colour
            rpr = style.element.get_or_add_rPr()
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is None:
                rfonts = OxmlElement("w:rFonts")
                rpr.append(rfonts)
            for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
                rfonts.set(qn(a), name)

        font(styles["Normal"], body, 10)
        styles["Normal"].paragraph_format.space_after = Pt(6)
        styles["Normal"].paragraph_format.line_spacing = 1.15
        h1 = styles["Heading 1"]
        font(h1, head, 16, bold=True, colour=RGBColor(0, 0, 0))
        h1.font.all_caps = True
        h1.font.underline = WD_UNDERLINE.THICK
        self._underline_colour(h1.element.get_or_add_rPr())
        h1.paragraph_format.page_break_before = True
        h1.paragraph_format.space_after = Pt(12)
        h2 = styles["Heading 2"]
        font(h2, head, 12, bold=True, colour=self.accent)
        h2.paragraph_format.space_before = Pt(10)
        h2.paragraph_format.space_after = Pt(3)
        h3 = styles["Heading 3"]
        font(h3, head, 10.5, bold=True, colour=self.accent)
        h3.paragraph_format.space_before = Pt(8)
        h3.paragraph_format.space_after = Pt(2)
        from docx.enum.style import WD_STYLE_TYPE
        cap = styles.add_style("Bijschrift", WD_STYLE_TYPE.PARAGRAPH)
        cap.base_style = styles["Normal"]
        font(cap, body, 8, italic=True, colour=RGBColor(0x40, 0x40, 0x40))
        cap.paragraph_format.space_after = Pt(8)
        plain = styles.add_style("Kop zonder inhoud", WD_STYLE_TYPE.PARAGRAPH)   # same look as H1, not in the TOC
        plain.base_style = styles["Normal"]
        font(plain, head, 16, bold=True)
        plain.font.all_caps = True
        plain.font.underline = WD_UNDERLINE.THICK
        self._underline_colour(plain.element.get_or_add_rPr())
        plain.paragraph_format.space_after = Pt(12)
        # Word refreshes the table of contents (page numbers) when the file is opened
        upd = OxmlElement("w:updateFields")
        upd.set(qn("w:val"), "true")
        self.doc.settings.element.append(upd)
        self._footer(sec)
        if self.c.status == "concept":
            hp = sec.header.paragraphs[0]
            hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            r = hp.add_run(f"CONCEPT – niet voor verspreiding · {self.c.provenance.get('label', '')}".rstrip(" ·"))
            r.font.size, r.font.bold, r.font.color.rgb = Pt(8), True, self.accent

    def _underline_colour(self, rpr) -> None:
        u = rpr.find(qn("w:u"))
        if u is None:
            u = OxmlElement("w:u")
            rpr.append(u)
        u.set(qn("w:val"), "thick")
        u.set(qn("w:color"), self.house["accent"].lstrip("#"))

    def _field(self, paragraph, instr: str, cached: str = "", bold=False, colour=None, size=None):
        def fld(t):
            e = OxmlElement("w:fldChar")
            e.set(qn("w:fldCharType"), t)
            return e
        r = paragraph.add_run()
        r._r.append(fld("begin"))
        r2 = paragraph.add_run()
        it = OxmlElement("w:instrText")
        it.set(qn("xml:space"), "preserve")
        it.text = instr
        r2._r.append(it)
        r3 = paragraph.add_run()
        r3._r.append(fld("separate"))
        lines = cached.split("\n") if cached else [""]
        for i, line in enumerate(lines):
            rr = paragraph.add_run(line)
            rr.bold = bold
            if colour is not None:
                rr.font.color.rgb = colour
            if size:
                rr.font.size = Pt(size)
            if i < len(lines) - 1:
                rr.add_break()
        r4 = paragraph.add_run()
        r4._r.append(fld("end"))

    def _footer(self, sec) -> None:
        p = sec.footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._field(p, "PAGE", "1", bold=True, colour=self.accent, size=10)
        p2 = sec.footer.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        logo = self.house.get("logo")
        if logo and Path(logo).exists():
            p2.add_run().add_picture(str(logo), height=Cm(0.6))
        else:
            r = p2.add_run(self.house.get("organisation", ""))
            r.font.size, r.font.color.rgb = Pt(7), self.grey

    # ------------------------------------------------------------------ helpers
    def _no_borders(self, table) -> None:
        tbl_pr = table._tbl.tblPr
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            e = OxmlElement(f"w:{edge}")
            e.set(qn("w:val"), "nil")
            borders.append(e)
        tbl_pr.append(borders)

    def _box_border(self, cell) -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        borders = OxmlElement("w:tcBorders")
        for edge in ("top", "left", "bottom", "right"):
            e = OxmlElement(f"w:{edge}")
            e.set(qn("w:val"), "single")
            e.set(qn("w:sz"), "12")
            e.set(qn("w:color"), self.house["accent"].lstrip("#"))
            borders.append(e)
        tc_pr.append(borders)
        mar = OxmlElement("w:tcMar")
        for edge in ("top", "left", "bottom", "right"):
            e = OxmlElement(f"w:{edge}")
            e.set(qn("w:w"), "220")
            e.set(qn("w:type"), "dxa")
            mar.append(e)
        tc_pr.append(mar)

    @staticmethod
    def _widths(table, cols_cm: list[float]) -> None:
        """Column widths that Word AND LibreOffice respect: grid, cells and a fixed layout."""
        tbl = table._tbl
        layout = OxmlElement("w:tblLayout")
        layout.set(qn("w:type"), "fixed")
        tbl.tblPr.append(layout)
        for gc, w in zip(tbl.tblGrid.findall(qn("w:gridCol")), cols_cm):
            gc.set(qn("w:w"), str(int(w * 567)))
        for row in table.rows:
            for cell, w in zip(row.cells, cols_cm):
                cell.width = Cm(w)

    def _table(self, cols_cm: list[float], borders: bool = False):
        t = self.doc.add_table(rows=1, cols=len(cols_cm))
        t.alignment = WD_TABLE_ALIGNMENT.LEFT
        t.autofit = False
        if not borders:
            self._no_borders(t)
        self._widths(t, cols_cm)
        return t

    def _placeholder(self, container, text: str) -> None:
        p = container.add_paragraph()
        r = p.add_run(f"[{text}]")
        r.italic, r.font.color.rgb = True, self.grey

    def _prep(self, src: str | Path, max_px: int = 1800, name: Optional[str] = None) -> Path:
        src = Path(src)
        out = self.media / ((name or src.stem) + ".jpg")
        if out.exists():
            return out
        im = Image.open(src)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[3])
            im = bg
        im = im.convert("RGB")
        im.thumbnail((max_px, max_px))
        im.save(out, quality=88)
        return out

    def _circle(self, src: str | Path, size: int = 700) -> Path:
        out = self.media / (Path(src).stem + "_circle.png")
        if out.exists():
            return out
        im = Image.open(src).convert("RGB")
        s = min(im.size)
        im = im.crop(((im.width - s) // 2, (im.height - s) // 2, (im.width + s) // 2, (im.height + s) // 2)).resize((size, size))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        canvas = Image.new("RGB", (size, size), (255, 255, 255))
        canvas.paste(im, mask=mask)
        canvas.save(out)
        return out

    def _caption(self, container, kind: str, caption: str, source: str, evidence_ids=()) -> Figure:
        self.counter[kind] += 1
        fig = Figure(kind, self.counter[kind], caption, source, list(evidence_ids), self.chapter)
        self.figures.append(fig)
        p = container.add_paragraph(style="Bijschrift")
        p.add_run(f"{kind} {fig.number}: {caption}")
        if self.c.show_ids:
            r = p.add_run(f"  [{', '.join(evidence_ids) if evidence_ids else source}]")
            r.font.size, r.font.color.rgb = Pt(6.5), self.grey
        return fig

    def _picture(self, container, path: Path, width_cm: float) -> None:
        p = container.add_paragraph() if hasattr(container, "add_paragraph") else container
        p.add_run().add_picture(str(path), width=Cm(width_cm))

    def _claims(self, container, items: list[Item]) -> None:
        """One paragraph per paragraph number; evidence ids as small grey superscript,
        orange/red when the verifier flagged the claim."""
        paras: dict[int, list[Item]] = {}
        for it in items:
            paras.setdefault(it.paragraph, []).append(it)
        for _, its in sorted(paras.items()):
            p = container.add_paragraph()
            for it in its:
                p.add_run(it.text.strip() + " ")
                if self.c.show_ids and it.evidence_ids:
                    r = p.add_run(f"[{', '.join(it.evidence_ids)}] ")
                    r.font.superscript = True
                    r.font.size = Pt(7)
                    r.font.color.rgb = {"weak": RGBColor(0xE0, 0x8A, 0x00), "unsupported": RGBColor(0xC0, 0x00, 0x00)}.get(
                        it.verification or "", self.grey)

    # ------------------------------------------------------------------ cover, colofon, contents
    def _cover_art(self) -> Path:
        out = self.media / "cover_art.png"
        w, h = 1000, 1500
        accent = tuple(int(self.house["accent"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        im = Image.new("RGB", (w, h), accent)
        d = ImageDraw.Draw(im)
        d.ellipse((-2600, -700, 380, h + 700), fill=(255, 255, 255))     # white page edge curving into the orange
        photo = self.house.get("cover_photo") or next((n.extra.get("photo_path") for n in self._notes() if n.extra.get("cover")), None)
        if photo and Path(photo).exists():
            circ = Image.open(self._circle(photo, 640))
            mask = Image.new("L", circ.size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0, circ.width - 1, circ.height - 1), fill=255)
            im.paste(circ, (120, 430), mask)
        im.save(out)
        return out

    def _cover(self) -> None:
        t = self._table([14.5, 9.6])
        left, right = t.rows[0].cells
        left.paragraphs[0].add_run().add_break()
        for line in (self.c.report_kind.upper(), self.fmt["city"].upper()):
            p = left.add_paragraph()
            r = p.add_run(line)
            r.bold, r.font.size, r.font.name = True, Pt(34), self.house.get("heading_font")
            p.paragraph_format.space_after = Pt(0)
        left.add_paragraph()
        p = left.add_paragraph()
        r = p.add_run((self.fmt["area"] or self.c.case_label).upper())
        r.bold, r.font.size, r.font.name = True, Pt(22), self.house.get("heading_font")
        r.font.underline = WD_UNDERLINE.THICK
        self._underline_colour(r._r.get_or_add_rPr())
        left.add_paragraph()
        p = left.add_paragraph()
        for logo in (self.house.get("logo"), self.house.get("partner_logo")):
            if logo and Path(logo).exists():
                p.add_run().add_picture(str(logo), height=Cm(1.6))
                p.add_run("     ")
        if not any(self.house.get(k) for k in ("logo", "partner_logo")):
            r = p.add_run(self.house.get("organisation", ""))
            r.bold, r.font.size = True, Pt(14)
            if self.case_cfg.get("municipality"):
                r2 = p.add_run(f"      {self.case_cfg['municipality']}")
                r2.font.size, r2.font.color.rgb = Pt(14), self.grey
        if self.c.status == "concept":
            p = left.add_paragraph()
            r = p.add_run("CONCEPT")
            r.bold, r.font.size, r.font.color.rgb = True, Pt(14), self.accent
        right.paragraphs[0].add_run().add_picture(str(self._cover_art()), width=Cm(9.4))

    def _colofon(self) -> None:
        p = self.doc.add_paragraph(style="Kop zonder inhoud")
        p.paragraph_format.page_break_before = True
        p.add_run("Colofon")
        year = _dt.date.today().year
        self.doc.add_paragraph(f"Copyright © {year}\n{self.house.get('organisation', '')}")
        for label, value in (("Auteurs", self.house.get("authors")), ("Vormgeving en lay-out", self.house.get("layout_by"))):
            q = self.doc.add_paragraph()
            r = q.add_run(f"{label}: ")
            r.bold, r.font.color.rgb = True, self.accent
            q.add_run(value or "")
            q.paragraph_format.space_after = Pt(0)
        self.doc.add_paragraph()
        self.doc.add_paragraph(self.house.get("copyright_note", ""))
        p = self.doc.add_paragraph(style="Kop zonder inhoud")
        p.add_run("Contactgegevens")
        self.doc.add_paragraph("\n".join(self.house.get("contact", [])))
        prov = self.c.provenance
        p = self.doc.add_paragraph(style="Heading 3")
        p.add_run("Over deze versie")
        lines = [f"Status: {'CONCEPT – nog niet (volledig) beoordeeld door een onderzoeker' if self.c.status == 'concept' else 'beoordeeld en goedgekeurd'}",
                 f"Gemaakt met ReportingTool InnoBeweegLab op {_dt.date.today():%d-%m-%Y}"]
        for k, label in (("model", "Taalmodel"), ("pipeline", "Pijplijn"), ("prompt_version", "Promptversie"),
                         ("run_id", "Run"), ("source", "Bron van de tekst")):
            if prov.get(k):
                lines.append(f"{label}: {prov[k]}")
        lines.append("Elke bewering verwijst naar bewijsstukken (grijze codes); Bijlage 3 en evidence_map.csv koppelen "
                     "ze aan de bronbestanden.")
        q = self.doc.add_paragraph("\n".join(lines))
        for r in q.runs:
            r.font.size, r.font.color.rgb = Pt(8), self.grey

    def _contents(self, headings: list[tuple[int, str]]) -> None:
        p = self.doc.add_paragraph(style="Kop zonder inhoud")
        p.paragraph_format.page_break_before = True
        p.add_run("Inhoudsopgave")
        cached = "\n".join(("      " if lvl == 2 else "") + text for lvl, text in headings)
        self._field(self.doc.add_paragraph(), 'TOC \\o "1-2" \\h \\z \\u', cached)

    # ------------------------------------------------------------------ chapters
    def _h1(self, text: str) -> None:
        self.chapter = text
        self.doc.add_heading(text, level=1)

    def _h2(self, text: str) -> None:
        self.doc.add_heading(text, level=2)

    def _summary(self) -> None:
        self._h1("Samenvatting")
        cross = [i for i in self.c.items(CONCLUSIES) if i.claim_type == "conclusie"]
        per_theme = [(t, i) for t in THEMES for i in self.c.items(t) if i.claim_type == "conclusie"]
        if cross:
            self._claims(self.doc, [Item(i.claim_id, i.text, i.claim_type, i.evidence_ids, 1, i.status, i.verification)
                                    for i in cross])
        elif per_theme:
            for t, i in per_theme:
                p = self.doc.add_paragraph()
                r = p.add_run(f"{t}: ")
                r.bold, r.font.color.rgb = True, self.accent
                self._claims_inline(p, i)
        else:
            self._placeholder(self.doc, "Nog geen goedgekeurde conclusies; de samenvatting volgt uit de conclusies")

    def _claims_inline(self, p, it: Item) -> None:
        p.add_run(it.text.strip() + " ")
        if self.c.show_ids and it.evidence_ids:
            r = p.add_run(f"[{', '.join(it.evidence_ids)}]")
            r.font.superscript, r.font.size, r.font.color.rgb = True, Pt(7), self.grey

    def _aanleiding(self) -> None:
        items = self.c.items(AANLEIDING)
        if not items:
            return
        self._h1("Aanleiding")
        self._claims(self.doc, items)
        self._photos(AANLEIDING)

    def _methode(self) -> None:
        self._h1("Methode")
        if self.house.get("method_intro"):
            self.doc.add_paragraph(self.house["method_intro"])
        parts = methode_parts(self.c.evidence, self.c.store, self.c.case_id, self.c.report_kind, self.c.case_info)
        kind = self.c.report_kind
        self.doc.add_paragraph(f"Met de {kind} is de beweegvriendelijkheid van het onderzoeksgebied in kaart gebracht. "
                               f"De {kind} kijkt naar 6 thema's die relevant zijn voor een beweegvriendelijke leefomgeving:")
        t = self._table([6.5, 6.5, 6.5])
        for k, theme in enumerate(THEMES):
            cell = t.rows[0].cells[k // 2]
            p = cell.paragraphs[0] if k % 2 == 0 and not cell.paragraphs[0].text else cell.add_paragraph()
            r = p.add_run("•  ")
            r.font.color.rgb = self.accent
            p.add_run(theme)
            p.paragraph_format.space_after = Pt(0)
        self.doc.add_paragraph()
        self.doc.add_paragraph(parts["intro"])
        self._house_figures("method_figures")
        note = self.doc.add_paragraph()
        r = note.add_run("De feiten in dit hoofdstuk zijn automatisch samengesteld uit de ingelezen bronbestanden.")
        r.italic, r.font.size, r.font.color.rgb = True, Pt(8), self.grey
        self._h2("Dataperspectief")
        ov = self.c.images_dir / "basisbehoefte_overlay.png"
        t = self._table([15.4, 8.4])
        left, right = t.rows[0].cells
        left.paragraphs[0].add_run(parts["data"] or "")
        self._rule_box(parts, left)
        if ov.exists():
            self._picture(right, self._prep(ov, 1600, "studiegebied"), 8.2)
            self._caption(right, "Afbeelding", "Onderzoeksgebied en buurten (controlebeeld van de kaartmeting)",
                          "metingsoverlay basisbehoeftekaart")
        self._h2("Expertperspectief")
        self.doc.add_paragraph(parts["expert"] or "")
        self._h2("Bewonersperspectief")
        self.doc.add_paragraph(parts["bewoners"] or "")
        if parts["notes"]:
            self.doc.add_paragraph(parts["notes"])

    def _house_figures(self, key: str) -> None:
        """Fixed figures of the house template (e.g. the BVO model), when InnoBeweegLab supplies the files:
        [[house.method_figures]] path = "...", caption = "...". Relative paths are read from config/."""
        figs = [f for f in self.house.get(key, []) if f.get("path")]
        files = [(f, Path(f["path"]) if Path(f["path"]).is_absolute() else config_dir() / f["path"]) for f in figs]
        files = [(f, path) for f, path in files if path.is_file()]
        if not files:
            return
        t = self._table([23.8 / len(files)] * len(files))
        for cell, (f, path) in zip(t.rows[0].cells, files):
            self._picture(cell, self._prep(path, 1600, path.stem), min(11.0, 22.0 / len(files)))
            self._caption(cell, "Afbeelding", f.get("caption", path.stem), f"huisstijl: {path.name}")

    def _maps_for_rule(self, rule_id: str) -> list[str]:
        return [c.get("title") for c in self.img_cfg.values()
                if rule_id in c.get("rules", []) and c.get("kind") in ("map_classes", "map_zones")]

    def _rule_box(self, parts: dict, container=None) -> None:
        container = container or self.doc
        t = container.add_table(rows=1, cols=1)
        t.autofit = False
        self._no_borders(t)
        self._widths(t, [15.0 if container is not self.doc else 18.0])
        cell = t.rows[0].cells[0]
        self._box_border(cell)
        tested = parts["tested_rules"]
        cell.paragraphs[0].add_run(
            f"De RIVM-vuistregels (2025) voor een beweegvriendelijke omgeving die in deze {self.c.report_kind} zijn getoetst:"
            if tested else "De RIVM-vuistregels (2025) voor een beweegvriendelijke omgeving (in deze gegevens niet getoetst):")
        for n, rule in enumerate(tested or parts["untested_rules"], start=1):
            p = cell.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.6)
            p.paragraph_format.space_after = Pt(2)
            r = p.add_run(f"{n}.  ")
            r.bold, r.font.color.rgb = True, self.accent
            p.add_run(rule["text"] + " ").font.size = Pt(9)
            maps = self._maps_for_rule(rule["id"]) if tested else []
            if maps:
                r = p.add_run(" / ".join(maps))
                r.bold, r.italic, r.font.color.rgb, r.font.size = True, True, self.accent, Pt(9)
        if tested and parts["untested_rules"]:
            p = cell.add_paragraph()
            r = p.add_run("Niet getoetst (geen gegevens): " + "; ".join(r_["title"].lower() for r_ in parts["untested_rules"]) + ".")
            r.font.size, r.font.color.rgb = Pt(8), self.grey
        container.add_paragraph()

    def _resultaten(self) -> None:
        self._h1("Resultaten")
        self.doc.add_paragraph(f"In dit hoofdstuk staan de belangrijkste resultaten van de {self.c.report_kind}, "
                               f"geordend naar de 6 thema's voor een beweegvriendelijke leefomgeving.")
        chap = next((c for c in self.cfg["chapter"] if c["id"] == "resultaten"), {})
        self._claims(self.doc, self.c.items(GENERAL))
        for ref in chap.get("full_page_images", []):
            iid = ref.split(":", 1)[1]
            rec = self.images.get(iid)
            if rec is None or not Path(rec.extra.get("absolute_path") or "/nonexistent").is_file():
                continue
            p = self.doc.add_paragraph()
            p.add_run().add_break(WD_BREAK.PAGE)
            self._picture(self.doc, self._prep(rec.extra["absolute_path"], 2600), 21.0)
            self._caption(self.doc, "Afbeelding", self.img_cfg.get(iid, {}).get("title", rec.text), rec.source_file)
        if chap.get("expert_sunburst_note") and "sunburst" in self.images:
            p = self.doc.add_paragraph()
            p.paragraph_format.page_break_before = True
            r = p.add_run("De resultaten van het expertperspectief staan in het sunburstdiagram in bijlage 2. "
                          "Alle beoordeelde indicatoren binnen de 6 thema's zijn daarin weergegeven.")
            r.bold = True
        for n, theme_cfg in enumerate(self.cfg.get("theme", []), start=1):
            self._theme(n, theme_cfg)

    def _theme(self, n: int, theme_cfg: dict) -> None:
        name = theme_cfg["name"]
        self._h2(f"Thema {n} - {name}")
        items = [i for i in self.c.items(name) if i.claim_type != "aanbeveling"]
        figs = theme_cfg.get("figures", [])
        maps = [f for f in figs if f.startswith(("image:", "overlay:"))]
        charts = [f for f in figs if f.startswith("chart:")]
        paras = sorted({i.paragraph for i in items})
        first = [i for i in items if i.paragraph == paras[0]] if paras else []
        rest = [i for i in items if paras and i.paragraph != paras[0]]
        if maps and first:
            t = self._table([14.2, 9.6])
            cell = t.rows[0].cells[0]
            cell.paragraphs[0].text = ""
            self._claims(cell, first)
            for ref in maps:
                self._map_figure(t.rows[0].cells[1], ref, 9.2)
            self._split_figures(rest, charts, name)
        else:
            if maps:
                for ref in maps:
                    self._map_figure(self.doc, ref, 12.0)
            self._split_figures(items, charts, name)
        if not items:
            self._placeholder(self.doc, f"Nog geen (goedgekeurde) tekst voor {name.lower()}")

    def _split_figures(self, items: list[Item], charts: list[str], section: str) -> None:
        """Expert/observation paragraphs, then photos, then resident paragraphs with the charts, then the rest."""
        paras = sorted({i.paragraph for i in items})
        if not paras:
            for ref in charts:
                self._chart(ref)
            self._photos(section)
            return
        resident_para = 3 if 3 in paras else paras[-1]
        photos_done = False
        for k in paras:
            self._claims(self.doc, [i for i in items if i.paragraph == k])
            if k >= 2 and not photos_done:
                self._photos(section)
                photos_done = True
            if k == resident_para:
                for ref in charts:
                    self._chart(ref)
                charts = []
        for ref in charts:
            self._chart(ref)
        if not photos_done:
            self._photos(section)

    def _map_figure(self, container, ref: str, width_cm: float) -> None:
        kind, iid = ref.split(":", 1)
        if kind == "overlay":
            path = self.c.images_dir / f"{iid}_overlay.png"
            src = f"metingsoverlay {iid}"
            title = f"{self.img_cfg.get(iid, {}).get('title', iid)} (controlebeeld)"
        else:
            rec = self.images.get(iid)
            path = Path(rec.extra.get("absolute_path", "")) if rec else Path("")
            src = rec.source_file if rec else iid
            title = self.img_cfg.get(iid, {}).get("title", iid)
        if not path.is_file():
            return
        self._picture(container, self._prep(path), width_cm)
        rec_id = self.images[iid].id if iid in self.images else None
        ev = [e.evidence_id for e in self.c.evidence.values() if e.kind == "map_measurement"
              and e.data.get("area") == "onderzoeksgebied" and rec_id in e.source_record_ids][:2]
        self._caption(container, "Afbeelding", f"{title} onderzoeksgebied" if kind == "image" else title, src, ev)

    def _chart(self, ref: str) -> None:
        cid = ref.split(":", 1)[1]
        spec = next((c for c in self.cfg.get("chart", []) if c["id"] == cid), None)
        if spec is None:
            return
        data = chart_data(spec, self.c.evidence.values(), self.cfg.get("chart_groups", {}), self.fmt)
        if data.empty:
            return
        path = render_chart(data, self.media / f"chart_{cid}.png", accent=self.house["accent"],
                            font=self.house.get("chart_font", "Calibri"), vertical_labels=spec.get("vertical_labels", False))
        self._picture(self.doc, path, 14.0)
        self._caption(self.doc, "Figuur", data.title, f"grafiek {cid}", data.evidence_ids)

    def _notes(self):
        return [r for r in self.c.store.all() if r.case_id == self.c.case_id and r.extra.get("record_kind") == "researcher_note"]

    def _photos(self, section: str) -> None:
        """Photos attached to researcher notes of this section, in a row of circles (as in the report)."""
        notes = [n for n in self._notes() if n.extra.get("photo_path") and Path(n.extra["photo_path"]).is_file()
                 and (section in (n.extra.get("themes") or []) or (section == AANLEIDING and n.extra.get("note_type") == "context"))]
        if not notes:
            return
        ev_by_record = {rid: e.evidence_id for e in self.c.evidence.values() if e.kind == "note" for rid in e.source_record_ids}
        for row_start in range(0, len(notes), 3):
            row = notes[row_start:row_start + 3]
            t = self._table([7.6] * len(row))
            for cell, n in zip(t.rows[0].cells, row):
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                cell.paragraphs[0].add_run().add_picture(str(self._circle(n.extra["photo_path"])), width=Cm(5.6))
                caption = n.extra.get("photo_caption") or (f"{n.text} ({n.extra['place']})" if n.extra.get("place") else n.text)
                self._caption(cell, "Afbeelding", caption, f"{n.source_file} › {n.locator}",
                              [ev_by_record[n.id]] if n.id in ev_by_record else [])

    def _conclusies(self) -> None:
        self._h1("Conclusies")
        items = [i for i in self.c.items(CONCLUSIES) if i.claim_type != "aanbeveling"]
        if items:
            self._claims(self.doc, items)
        per_theme = [(t, i) for t in THEMES for i in self.c.items(t) if i.claim_type == "conclusie"]
        for t, i in per_theme:
            p = self.doc.add_paragraph()
            p.add_run("Binnen het thema ")
            r = p.add_run(t.lower())
            r.bold, r.font.color.rgb = True, self.accent
            p.add_run(": ")
            self._claims_inline(p, i)
        if not items and not per_theme:
            self._placeholder(self.doc, "Nog geen (goedgekeurde) conclusies")

    def _aanbevelingen(self) -> None:
        chap = next((c for c in self.cfg["chapter"] if c["id"] == "aanbevelingen"), {})
        self._h1("Aanbevelingen")
        recs = {sec: [i for i in self.c.items(sec) if i.claim_type == "aanbeveling" and i.status in ("approved", "edited")]
                for sec in self.c.sections}
        general = recs.get(CONCLUSIES, []) + recs.get(GENERAL, [])
        if general:
            self._claims(self.doc, general)
        else:
            self._placeholder(self.doc, "In te vullen door de onderzoeker: de aanbevelingen volgen uit de goedgekeurde "
                                        "bevindingen en conclusies (de tool schrijft geen aanbevelingen)")
        for h in chap.get("intro_headings", []):
            self.doc.add_paragraph(style="Heading 3").add_run(h)
            self._placeholder(self.doc, "in te vullen")
        for n, theme in enumerate(THEMES, start=1):
            self._h2(f"Thema {n} - {theme}")
            self.doc.add_paragraph(style="Heading 3").add_run(chap.get("per_theme_heading", "Concreet").format(case=self.fmt["area"]))
            if recs.get(theme):
                for i in recs[theme]:
                    p = self.doc.add_paragraph(style="List Bullet")
                    self._claims_inline(p, i)
            else:
                self._placeholder(self.doc, "in te vullen")

    def _bronnen(self) -> None:
        self._h1("Bronnenlijst")
        rivm = load_config("reference/rivm_vuistregels.toml")
        refs = [rivm["citation"]]
        for iid, sources in self.cfg.get("image_sources", {}).items():
            if iid in self.images:
                refs += [s for s in sources if s not in refs]
        for e in self.c.evidence.values():
            if e.kind == "note" and e.data.get("note_type") == "literatuur" and e.data.get("source") and e.data["source"] not in refs:
                refs.append(e.data["source"])
        for n, ref in enumerate(refs, start=1):
            p = self.doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.8)
            p.paragraph_format.first_line_indent = Cm(-0.6)
            p.paragraph_format.space_after = Pt(2)
            r = p.add_run(f"{n}.  ")
            r.font.color.rgb = self.accent
            p.add_run(ref)
        p = self.doc.add_paragraph(style="Heading 3")
        p.add_run("Bronbestanden van deze analyse")
        for d in self.c.documents:
            if d.get("status") == "parsed" and d.get("case_id") in (self.c.case_id, None):
                q = self.doc.add_paragraph(f"{d['filename']} (sha256 {d['checksum'][:12]}…)")
                q.paragraph_format.space_after = Pt(0)
                for r in q.runs:
                    r.font.size = Pt(8)

    def _bijlage1(self) -> None:
        chap = next((c for c in self.cfg["chapter"] if c["id"] == "bijlage1"), {})
        rivm = {r["id"]: r for r in load_config("reference/rivm_vuistregels.toml")["rule"]}
        first = True
        for iid in chap.get("maps", []):
            rec = self.images.get(iid)
            if rec is None or not Path(rec.extra.get("absolute_path") or "/nonexistent").is_file():
                continue
            if first:
                self._h1("Bijlage 1")
                first = False
            else:
                self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            cfg = self.img_cfg.get(iid, {})
            t = self._table([14.2, 9.6])
            left, right = t.rows[0].cells
            self._picture(left, self._prep(rec.extra["absolute_path"]), 12.6)
            self._caption(left, "Afbeelding", cfg.get("title", rec.text), rec.source_file)
            if iid == chap.get("definitions_for"):
                for term, text in self.cfg.get("definitions", {}).items():
                    p = right.add_paragraph()
                    r = p.add_run(f"{term}: ")
                    r.bold, r.font.color.rgb = True, self.accent
                    if text:
                        p.add_run(text)
                    else:
                        r2 = p.add_run("[definitie aanvullen in config/report_structure.toml]")
                        r2.italic, r2.font.color.rgb = True, self.grey
            rules = [rivm[r] for r in cfg.get("rules", []) if r in rivm]
            if rules and cfg.get("kind") in ("map_classes", "map_zones", "map_objects"):
                p = right.add_paragraph(style="Heading 3")
                p.add_run("RIVM-vuistregel voor bewegen")
                for r in rules:
                    right.add_paragraph(r["text"])
                meas = [e for e in self.c.evidence.values() if e.kind == "map_measurement"
                        and e.data.get("area") == "onderzoeksgebied" and e.data.get("tests_rule")
                        and any(r in e.data.get("rules", []) for r in cfg.get("rules", []))]
                seen = set()
                for e in meas:
                    key = tuple(e.source_record_ids)
                    if key in seen:
                        continue
                    seen.add(key)
                    q = right.add_paragraph()
                    r = q.add_run("Meting door de tool (indicatief): " + e.statement.split(": ", 1)[-1].split(" (aandeel")[0] + ". ")
                    r.font.size, r.font.color.rgb = Pt(8), self.grey
                    r2 = q.add_run(f"[{e.evidence_id}]")
                    r2.font.size, r2.font.color.rgb = Pt(6.5), self.grey

    def _bijlage2(self) -> None:
        rec = self.images.get("sunburst")
        if rec is None or not Path(rec.extra.get("absolute_path") or "/nonexistent").is_file():
            return
        self._h1("Bijlage 2")
        t = self._table([15.6, 8.2])
        left, right = t.rows[0].cells
        self._picture(left, self._prep(rec.extra["absolute_path"], 2000), 14.6)
        self._caption(left, "Afbeelding", "Sunburst resultaten expertperspectief", rec.source_file)
        n_ind = len({(tuple(e.themes), e.data.get("indicator")) for e in self.c.evidence.values()
                     if e.kind == "expert_statement" and e.data.get("indicator")})
        right.add_paragraph(f"Het onderzoeksgebied is beoordeeld aan de hand van {n_ind} indicatoren, verdeeld over "
                            f"6 thema's. Samen vormen zij een totaalbeoordeling van de onderzochte buurten.")
        right.add_paragraph("Het diagram wordt van binnen naar buiten gelezen: een score van 1 ligt aan de binnenste ring, "
                            "een score van 5 aan de buitenste ring, ter hoogte van de naam van de indicator.")

    def _bijlage3(self) -> None:
        self._h1("Bijlage 3 · Herleidbaarheid")
        self.doc.add_paragraph("Elke bewering in dit rapport verwijst naar een of meer bewijsstukken. Een bewijsstuk is een "
                               "vooraf berekend feit uit de bronbestanden (een cel, een telling, een kaartmeting of een "
                               "onderzoekersnotitie). Onderstaande tabel koppelt de beweringen aan hun bron; "
                               "evidence_map.csv bevat de exacte cellen.")
        rows = []
        for sec in [*THEMES, GENERAL, AANLEIDING, CONCLUSIES]:
            for it in self.c.items(sec):
                files = sorted({r.source_file for eid in it.evidence_ids
                                for rid in (self.c.evidence[eid].source_record_ids if eid in self.c.evidence else [])
                                for r in [self.c.store.get(rid)] if r is not None})
                rows.append((sec, it.claim_id, it.text, ", ".join(it.evidence_ids) or "(geen)",
                             "; ".join(Path(f).name for f in files[:3]) + (" …" if len(files) > 3 else ""),
                             it.verification or "", it.status))
        if not rows:
            self._placeholder(self.doc, "geen beweringen")
            return
        table = self.doc.add_table(rows=1, cols=6)
        table.style = "Table Grid"
        widths = [3.2, 10.8, 2.8, 4.6, 1.3, 1.3]
        for cell, h in zip(table.rows[0].cells, ("Onderdeel", "Bewering", "Bewijsstukken", "Bronbestanden",
                                                 "Controle", "Beoordeling")):
            r = cell.paragraphs[0].add_run(h)
            r.bold, r.font.size = True, Pt(7)
        for sec, cid, text, ids, files, ver, status in rows:
            cells = table.add_row().cells
            for cell, val in zip(cells, (f"{sec} · {cid}", text[:180] + ("…" if len(text) > 180 else ""), ids, files,
                                         ver, status)):
                cell.paragraphs[0].paragraph_format.space_after = Pt(0)
                r = cell.paragraphs[0].add_run(val)
                r.font.size = Pt(6.5)
        self._widths(table, widths)

    # ------------------------------------------------------------------ build
    def _headings(self) -> list[tuple[int, str]]:
        out = [(1, "Samenvatting")]
        if self.c.items(AANLEIDING):
            out.append((1, "Aanleiding"))
        out += [(1, "Methode"), (2, "Dataperspectief"), (2, "Expertperspectief"), (2, "Bewonersperspectief"), (1, "Resultaten")]
        out += [(2, f"Thema {n} - {t['name']}") for n, t in enumerate(self.cfg.get("theme", []), start=1)]
        out += [(1, "Conclusies"), (1, "Aanbevelingen")]
        out += [(2, f"Thema {n} - {t}") for n, t in enumerate(THEMES, start=1)]
        out += [(1, "Bronnenlijst"), (1, "Bijlage 1"), (1, "Bijlage 2"), (1, "Bijlage 3 · Herleidbaarheid")]
        return out

    def build(self) -> Document:
        self._setup()
        self._cover()
        self._colofon()
        self._contents(self._headings())
        self._summary()
        self._aanleiding()
        self._methode()
        self._resultaten()
        self._conclusies()
        self._aanbevelingen()
        self._bronnen()
        self._bijlage1()
        self._bijlage2()
        self._bijlage3()
        return self.doc


def render_docx(content: ReportContent, out_dir: str | Path, filename: Optional[str] = None,
                structure: Optional[dict] = None) -> tuple[Path, list[Figure]]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    r = WijkscanRenderer(content, out_dir, structure)
    doc = r.build()
    name = filename or f"{content.report_kind.lower()}_{content.case_id}{'_CONCEPT' if content.status == 'concept' else ''}.docx"
    path = out_dir / name
    doc.save(path)
    return path, r.figures


def to_pdf(docx_path: str | Path, timeout: int = 240) -> Optional[Path]:
    """PDF via LibreOffice when installed (macOS: /Applications/LibreOffice.app); else None
    (Word: File > Save as > PDF gives the same result, with the contents page filled in)."""
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    mac = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if exe is None and mac.exists():
        exe = str(mac)
    if exe is None:
        return None
    docx_path = Path(docx_path)
    subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", str(docx_path.parent), str(docx_path)],
                   check=False, capture_output=True, timeout=timeout)
    pdf = docx_path.with_suffix(".pdf")
    return pdf if pdf.exists() else None
