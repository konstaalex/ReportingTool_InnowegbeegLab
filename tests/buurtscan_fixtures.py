"""Tiny synthetic Buurtscan files mimicking the Oct 2026 shapes.

No real (or even the supplied synthetic) research data is used in tests:
these builders write minimal files with the same structure - merged theme
cells, a two-row survey header with merged question spans, a footer with
totals, and a demografie docx with two tables.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl


def write_expert(path: Path, buurt: str = "Testwijk", stad: str = "Teststad") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Score per indicator"
    ws.append(["Stad", stad, "Buurt", buurt, None, None, None])
    ws.append(["Kwalitatieve beoordeling", None, None, None, None, None, None])
    ws.append(["Score 1 = heel slecht (of niet aanwezig)"])
    ws.append(["Thema's", "Indicatoren", "Stelling nummer", "Stelling", "Score", "Gemiddelde score", "Opmerkingen"])
    ws.append(["Sporten", "Variatie", 1, "Voldoende variatie", 4, None, None])                        # row 5
    ws.append([None, "Onderhoud", 2, "Goed onderhouden", 2, None, "Kapotte basketbalpaal"])            # row 6
    ws.append([None, None, 3, "Goed onderhouden omgeving", 1, None, None])                             # row 7
    ws.append(["Ontmoeten", "Zitgelegenheid", 1, "Genoeg bankjes", 5, None, "Veel bankjes"])           # row 8
    ws.merge_cells("A5:A7")
    ws.merge_cells("B6:B7")
    wb.save(path)
    return path


def write_survey(path: Path, buurt: str = "Testwijk", bad_footer: bool = False) -> Path:
    """Respondents: 4. Questions: woonbuurt (choice), belang sport (likert),
    mist (multi + Anders), sport cijfer (rating + Omdat)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Blad1"
    q_woon = "In welke buurt woont u?"
    q_bel = "Hoe belangrijk zijn de volgende onderdelen in de buurt voor u?"
    q_mist = "Wat mist u in de openbare ruimte in uw buurt? Meerdere antwoorden mogelijk"
    q_cijfer = "Welk cijfer geeft u het thema sporten in de openbare ruimte in uw buurt?"
    q_wens = f"Wat voor sportvoorzieningen zou u graag zien in de openbare ruimte in {buurt}? Meerdere antwoorden mogelijk"
    row1 = ["Respondent", "Vraag", q_woon, None, q_bel, None, None, q_mist, None, None, None,
            q_cijfer, None, None, None, None, None, q_wens, None]
    row2 = [None, "Antwoord", "Onderzoeksbuurt", "Omliggende buurt 1",
            "Openbare sport- en beweegvoorzieningen - Niet belangrijk",
            "Openbare sport- en beweegvoorzieningen - Neutraal",
            "Openbare sport- en beweegvoorzieningen - Belangrijk",
            "Fietspaden", "Speelvoorzieningen (speeltuin)", "Straatverlichting", "Anders, namelijk:",
            1, 2, 3, 4, 5, "Omdat:", "Beweegtuin", "Jeu de Boules baan"]
    ws.append(row1)
    ws.append(row2)
    B = "Openbare sport- en beweegvoorzieningen - "
    ws.append([1, None, "Onderzoeksbuurt", None, None, None, B + "Belangrijk", "Fietspaden", None, None, "Watertappunt",
               None, None, None, 4, None, "Er is een trapveld", "Beweegtuin", None])
    ws.append([2, None, "Onderzoeksbuurt", None, None, B + "Neutraal", None, "Fietspaden", "Speelvoorzieningen (speeltuin)",
               None, None, None, 2, None, None, None, "Niets te doen", "Beweegtuin", "Jeu de Boules baan"])
    ws.append([3, None, None, "Omliggende buurt 1", None, None, B + "Belangrijk", None, None, "Straatverlichting", None,
               None, 2, None, None, None, "Niets te doen", None, None])
    ws.append([4, None, "Onderzoeksbuurt", None, B + "Niet belangrijk", None, None, "Fietspaden", None, None, None,
               None, None, None, None, 5, None, None, None])
    ws.append(["Totaal aantal antwoorden per kolom (n = 4) - SYNTHETISCH"])
    totals = [None, None, 3, 1, 1, 1, 2, 3, 1, 1, 1, 0, 2, 0, 1, 1, 3, 2, 1]
    if bad_footer:
        totals[7] = 9
    ws.append(totals)
    for rng in ("C1:D1", "E1:G1", "H1:K1", "L1:Q1", "R1:S1"):
        ws.merge_cells(rng)
    wb.save(path)
    return path


def write_demografie(path: Path, buurt: str = "Testwijk", gemeente: str = "Teststad") -> Path:
    import docx
    doc = docx.Document()
    doc.add_paragraph("DEMOGRAFIE")
    doc.add_paragraph(f"Buurt {buurt} (wijk {gemeente}-Noord), Gemeente {gemeente}")
    doc.add_paragraph(f"Aantal inwoners Buurt {buurt} (in 2025): 5.010")
    t = doc.add_table(rows=3, cols=4)
    for i, v in enumerate(["Indicator", f"Buurt {buurt}", f"Gemeente {gemeente}", "Nederland"]):
        t.cell(0, i).text = v
    for i, v in enumerate(["Sporters", "47,4%", "44,4%", "54,9%"]):
        t.cell(1, i).text = v
    for i, v in enumerate(["Overgewicht", "57,8%", "57,4%", "51,6%"]):
        t.cell(2, i).text = v
    t2 = doc.add_table(rows=2, cols=3)
    for i, v in enumerate(["Indicator", f"Buurt {buurt}", f"Wijk {gemeente}-Noord"]):
        t2.cell(0, i).text = v
    for i, v in enumerate(["Sport-accommodaties", "42", "41"]):
        t2.cell(1, i).text = v
    doc.save(path)
    return path


def write_case_folder(root: Path, label: str = "Testwijk Teststad", with_duplicate: bool = True,
                      with_docx: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    buurt = label.rsplit(" ", 1)[0]
    stad = label.rsplit(" ", 1)[1]
    write_expert(root / f"Expertwandeling - {label} (SYNTHETISCH).xlsx", buurt, stad)
    write_survey(root / f"Vragenlijst buurtbewoners - {label} (SYNTHETISCH).xlsx", buurt)
    if with_duplicate:  # byte-identical copy, like the '[n]' files in the real delivery
        import shutil
        shutil.copyfile(root / f"Vragenlijst buurtbewoners - {label} (SYNTHETISCH).xlsx",
                        root / f"Vragenlijst buurtbewoners - {label} (SYNTHETISCH)[3].xlsx")
    if with_docx:
        write_demografie(root / f"Data voor Demografie - {label} (SYNTHETISCH).docx", buurt, stad)
    (root / "Onbekend bestand.txt").write_text("x")
    return root


def has_docx() -> bool:
    try:
        import docx  # noqa: F401
        return True
    except ImportError:
        return False
