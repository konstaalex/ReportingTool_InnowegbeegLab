"""Synthetic fixture builders — small, fake workbooks that mimic the real
Wijkscan shapes without containing any real resident/expert data. Tests
must never read from the actual Perspectieven Oss folder; that data stays
on the researcher's machine, never in this repo or its test suite.
"""
from __future__ import annotations

import openpyxl
import pytest


@pytest.fixture
def categorized_workbook(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gecategoriseerde data"
    ws.append([None, "Sporten", None, None, None, None, None, "Ontmoeten", None, None, None, None, None])
    ws.append([None, "Behoefte", "Aantal", "Belemmering", "Aantal", "Positieve bevinding", "Aantal",
               "Behoefte", "Aantal", "Belemmering", "Aantal", "Positieve bevinding", "Aantal"])
    ws.append(["Testbuurt", "Jeu de boules", 3, None, None, "Urban Ox park leuk", 1,
               "Samen tuinieren", 1, "Te druk", 1, None, None])
    ws.append([None, "Sportveldjes", 2, None, None, None, None, None, None, None, None, None, None])
    path = tmp_path / "Gecategoriseerde Resultaten Test.xlsx"
    wb.save(path)
    return path


@pytest.fixture
def survey_workbook(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Resultaten vragenlijst"
    ws.append(["65+ en toekomstige bewoners"])
    ws.append(["In welke buurt woont u?", None, "Hoe belangrijk?"])
    ws.append(["Testbuurt", "Andere buurt", "Wandel- en fietspaden - Belangrijk", "Onbekende kolom - Belangrijk"])
    ws.append([4, 2, 14, 5])
    path = tmp_path / "Survey Test.xlsx"
    wb.save(path)
    return path


@pytest.fixture
def expert_neighbourhood_workbook(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Score Testbuurt"
    ws.append(["Vraag", "A", "E", "F", "L", "R", None, "Gemiddelde", "Definitief", "Opmerkingen"])
    ws.append(["Sportplekken", None, None, None, None, None, None, None, None, "Geen sportplekken"])
    ws.append([4, 3, 3, 3, 3, 3, None, 3, None, "Twee velden aanwezig"])
    ws.append([5, 3, 4, 3, 3, 4, None, 3.4, None, "Prullenbak bij bankjes"])

    ws2 = wb.create_sheet("Score per indicator")
    ws2.append(["Thema's", "Indicatoren", "Stelling nummer", "Stelling", "Gemiddelde score stelling", "Gemiddelde score indicator"])
    ws2.append(["Sporten", "Variatie", 4, "test stelling 4", 3, 3])
    ws2.append([None, "Doelgroep", 5, "test stelling 5", 3.4, 3.4])

    path = tmp_path / "Expertwandeling resultaten Test Testbuurt.xlsx"
    wb.save(path)
    return path


@pytest.fixture
def expert_totaal_workbook(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Totaal scores"
    ws.append([None, "Indicator", "Stelling nummer", "Stelling", "Testbuurt A", "Testbuurt B", "Testbuurt C", None, "Gemiddelde score 3 buurten", None, "Score per indicator"])
    ws.append(["Sporten", "Variatie", 4, "test stelling", 3, 3, 4.6, None, 3.53, None, 3.53])
    ws.append([None, "Doelgroep", 5, "test stelling 2", 3.4, 2.6, 4.8, None, 3.6, None, 3.6])

    ws2 = wb.create_sheet("Opmerkingen")
    ws2.append(["Testbuurt A", None, "Testbuurt B"])
    ws2.append(["Sporten", None, "Sporten"])
    ws2.append(["Twee velden, weinig licht", None, "Verouderd materiaal"])

    path = tmp_path / "Totaal resultaten Expertwandeling Test.xlsx"
    wb.save(path)
    return path
