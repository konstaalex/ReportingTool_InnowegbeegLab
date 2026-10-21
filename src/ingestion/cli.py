"""UC1 entrypoint: ingest a Wijkscan folder, print what was found, save the store.

    python -m ingestion.cli ingest "<folder>" --out store.json

For every file under the folder: detect its perspective from its parent
folder name, detect which parser its actual sheet structure matches, parse
it if a parser matches, and record it as unparsed (with a reason) if not —
never silently skipped. Ends by printing SourceStore.summary() so the actor
gets exactly the "what was ingested" confirmation UC1 asks for.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import openpyxl

from .perspective_detector import detect_perspective_from_path
from .store import SourceStore
from .parsers import resident_parser, expert_parser, map_parser

SKIP_SUFFIXES = {".ds_store", ".psd"}
UNSUPPORTED_BUT_KNOWN = {".pdf": "PDF ingestion not implemented yet (reference-report / route PDFs)"}


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.name.startswith("~$"):
            yield path


def ingest_folder(root: str) -> tuple[SourceStore, list[dict]]:
    root_path = Path(root)
    store = SourceStore()
    unparsed: list[dict] = []

    for path in _iter_files(root_path):
        rel = str(path.relative_to(root_path))
        suffix = path.suffix.lower()
        if suffix in SKIP_SUFFIXES:
            continue

        detected_perspective = detect_perspective_from_path(rel)

        if suffix == ".xlsx":
            try:
                wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
                sheet_names = wb.sheetnames
            except Exception as exc:  # noqa: BLE001 - report, don't crash the whole run
                unparsed.append({"file": rel, "reason": f"could not open workbook: {exc}"})
                continue

            if resident_parser.detect(sheet_names):
                print(f"[{rel}] detected perspective={detected_perspective!r} -> resident_parser (sheets: {sheet_names})")
                store.add_many(resident_parser.parse_file(str(path), source_file_label=rel))
            elif expert_parser.detect(sheet_names):
                print(f"[{rel}] detected perspective={detected_perspective!r} -> expert_parser (sheets: {sheet_names})")
                store.add_many(expert_parser.parse_file(str(path), source_file_label=rel))
            else:
                unparsed.append({"file": rel, "reason": f"no known sheet shape among {sheet_names}"})

        elif suffix in map_parser.IMAGE_SUFFIXES:
            print(f"[{rel}] detected perspective={detected_perspective!r} -> map_parser")
            store.add_many(map_parser.parse_file(str(path), source_file_label=rel))

        elif suffix in UNSUPPORTED_BUT_KNOWN:
            unparsed.append({"file": rel, "reason": UNSUPPORTED_BUT_KNOWN[suffix]})

        else:
            unparsed.append({"file": rel, "reason": f"unrecognised file type {suffix!r}"})

    return store, unparsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_cmd = sub.add_parser("ingest", help="Ingest a Wijkscan folder into a SourceStore")
    ingest_cmd.add_argument("folder")
    ingest_cmd.add_argument("--out", default="store.json")

    bs_cmd = sub.add_parser("ingest-buurtscan", help="Ingest a Buurtscan folder (expert walk / resident survey / demografie per neighbourhood)")
    bs_cmd.add_argument("folder")
    bs_cmd.add_argument("--out", default="workspace/ingest", help="output directory (store.json, sources.json, ingestion_report.json)")
    bs_cmd.add_argument("--case", action="append", help="limit to case id(s), e.g. passewaaij-tiel")

    args = parser.parse_args(argv)

    if args.command == "ingest-buurtscan":
        from .buurtscan import ingest_buurtscan_folder
        result = ingest_buurtscan_folder(args.folder, cases=args.case, verbose=True)
        out = result.save(args.out)
        print("\n--- ingestion summary ---")
        print(json.dumps(result.summary(), indent=2, ensure_ascii=False))
        print(f"\nSaved {len(result.store)} records to {out}")
        return 0

    if args.command == "ingest":
        store, unparsed = ingest_folder(args.folder)
        store.save(args.out)

        summary = store.summary()
        summary["unparsed_files"] = unparsed
        print("\n--- ingestion summary ---")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        print(f"\nSaved {len(store)} records to {args.out}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
