"""Theme resolution: exact matches are trusted, everything else is flagged.

Two sources ingest with an EXPLICIT theme already (the resident coded-data
sheets' column-group headers, and the expert sheets' "Thema's" column) —
those just need normalising against THEMES. A third source (the raw
`Resultaten vragenlijst` survey) has no theme column at all; its columns
are mapped through SURVEY_COLUMN_THEMES below, built up from the columns
actually seen in the report + dataset inventory so far. Anything not in
that table comes back None + flagged, per FR3 — never guessed.
"""
from __future__ import annotations

from typing import Optional

from .models import THEMES

_THEME_LOOKUP = {t.lower(): t for t in THEMES}


def normalise_theme(raw: Optional[str]) -> Optional[str]:
    """Exact (case-insensitive) match against THEMES, else None."""
    if raw is None:
        return None
    return _THEME_LOOKUP.get(str(raw).strip().lower())


# Column-group prefix (the text before " - <scale point>") -> theme, for the
# `Resultaten vragenlijst` sheet. Extend this as more of the sheet's 149
# columns are reviewed; an unlisted prefix is flagged, not guessed.
SURVEY_COLUMN_THEMES: dict[str, str] = {
    "wandel- en fietspaden": "Actieve mobiliteit",
    "natuur/ groen voor recreatie": "Uitstraling",
}


def theme_for_survey_column(column_label: str) -> Optional[str]:
    prefix = column_label.split(" - ", 1)[0].strip().lower()
    return SURVEY_COLUMN_THEMES.get(prefix)


# Map filename keywords -> theme(s). A single theme is applied directly; a
# tuple means the map is genuinely cited under more than one theme in the
# report (e.g. the Sport- en speelkaart, under both Sporten and Formeel
# spelen) — those are flagged for a human to pick rather than assigned
# silently.
MAP_FILENAME_THEMES: dict[str, tuple[str, ...]] = {
    "basisbehoeftekaart": ("Actieve mobiliteit",),
    "beweeggroenkaart": ("Informeel spelen",),
    "sportaccommodatiekaart": ("Sporten",),
    "inspiratie sport- en speelkaart": ("Sporten",),
    "sport- en speelkaart": ("Sporten", "Formeel spelen"),
    "sport en speelkaart": ("Sporten", "Formeel spelen"),
    "beweegstructuurkaart": (),   # supporting map, not tied to one theme
    "voorzieningenkaart": (),     # supporting map, not tied to one theme
}


def theme_for_map_filename(filename: str) -> tuple[Optional[str], bool]:
    """Returns (theme_if_unambiguous, needs_manual_mapping)."""
    name = filename.lower()
    for keyword, themes in MAP_FILENAME_THEMES.items():
        if keyword in name:
            if len(themes) == 1:
                return themes[0], False
            return None, True  # zero or multiple candidate themes
    return None, True
