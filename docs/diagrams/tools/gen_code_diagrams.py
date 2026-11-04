#!/usr/bin/env python3
"""Generate the UML class diagrams of the code (Mermaid) straight from src/.

Every module in src/ ends up in exactly one diagram, so together the diagrams show all the
code: each module is drawn as a namespace (nested by package, so
ingestion.parsers.notes_parser sits inside ingestion > parsers) that holds its classes (fields and methods) and,
when it has them, a «module» box with its functions and constants. Relations are read from
the code, not drawn by hand:

  A <|-- B        B inherits from A (only classes of this project)
  A *-- "*" B     a field of A holds a list/dict/tuple of B
  A --> B         a field of A holds one B ("0..1" when Optional)
  m ..> B         the functions of module m (or the methods of class m) use class B
  m ..> n         module m uses functions or constants of module n (also lazy imports)

Classes from another diagram are drawn as grey stubs (name only).

Usage:  python3 tools/gen_code_diagrams.py <repo>/src  [out_dir]
Re-run it after a code change and render with ./render.sh; nothing in the output is edited by
hand (manual notes are in NOTES below).
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

# (file stem, title, packages); every package must appear once, checked in main()
GROUPS = [
    ("31-code-ingestion", "Code · ingestion: files → source records (all modules)", ["ingestion"]),
    ("32-code-evidence-drafting", "Code · evidence and drafting: source records → evidence → cited claims",
     ["evidence", "drafting"]),
    ("33-code-llm", "Code · llm: model gateway, providers, privacy guard, resource measurement", ["llm"]),
    ("34-code-verification-review", "Code · verification and review: checks, review log", ["verification", "review"]),
    ("35-code-imaging", "Code · imaging: maps measured in code, infographics read by a model", ["imaging"]),
    ("36-code-export", "Code · export: Markdown, evidence map and the Word report", ["export"]),
    ("37-code-reports-experiments", "Code · reports and experiments: full runs, scoring, experiment records",
     ["reports", "experiments"]),
    ("38-code-app-ui", "Code · app and ui: the Workspace facade and the Streamlit interface", ["app", "ui"]),
    ("39-code-evaluation", "Code · evaluation: gold-set harness (standalone, synthetic)", ["evaluation"]),
]

DIRECTION = {"37-code-reports-experiments": "TB", "38-code-app-ui": "TB"}

EXTERNAL_STEREOTYPES = {"Exception": "exception", "RuntimeError": "exception", "ValueError": "exception",
                        "Protocol": "protocol"}

# relations Python does not state in the code (structural typing), drawn dashed with a label
EXTRA_RELATIONS = {
    "33-code-llm": ['LLMProvider <|.. _Base : implements (structural)'],
}
NOTES = {
    "38-code-app-ui": [
        ('m_ui_streamlit_app', 'Top-level Streamlit script: seven tabs, 1 · Data, 1b · Afbeeldingen, '
                             '2 · Concept maken, 3 · Beoordelen, 4 · Exporteren, 5 · Rapport per model, '
                             '6 · Experimenten. Every tab calls Workspace.'),
    ],
}


def esc(s: str) -> str:
    """Mermaid reads __x__ as bold and ~ as a generic marker; keep names literal."""
    return s.replace("_", "#95;").replace("~", "#126;").replace('"', "'")


def ann(node) -> str:
    return ast.unparse(node) if node is not None else ""


def args_of(fn: ast.FunctionDef, drop_self: bool) -> str:
    a = fn.args
    names = [x.arg for x in a.posonlyargs + a.args]
    if drop_self and names and names[0] in ("self", "cls"):
        names = names[1:]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    names += [x.arg for x in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    return ", ".join(names)


@dataclass
class Cls:
    name: str
    module: str
    bases: list[str]
    decorators: list[str]
    fields: list[tuple[str, str]]
    methods: list[tuple[str, str, str, bool]]      # name, args, return, static
    uses: set[str] = field(default_factory=set)


@dataclass
class Mod:
    name: str
    doc: str
    classes: list[Cls]
    functions: list[tuple[str, str, str]]          # name, args, return
    constants: list[str]
    uses: set[str] = field(default_factory=set)
    imports: list[tuple[str, list[str]]] = field(default_factory=list)   # (module, names); [] = whole module

    @property
    def has_box(self) -> bool:
        return bool(self.functions or self.constants)


def names_in(node) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.isidentifier():
            out.add(n.value)                      # string annotations such as "ReviewSession"
    return out


def parse_module(path: Path, modname: str, is_package: bool = False) -> Mod:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mod = Mod(modname, (ast.get_docstring(tree) or "").split("\n")[0], [], [], [])
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            c = Cls(node.name, modname, [ann(b) for b in node.bases],
                    [ann(d).split("(")[0] for d in node.decorator_list], [], [])
            for b in node.body:
                if isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name):
                    c.fields.append((b.target.id, ann(b.annotation)))
                elif isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    decs = [ann(d) for d in b.decorator_list]
                    static = any(d in ("staticmethod", "classmethod") for d in decs)
                    c.methods.append((b.name, args_of(b, not any(d == "staticmethod" for d in decs)),
                                      ann(b.returns), static))
                    c.uses |= names_in(b)
            mod.classes.append(c)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod.functions.append((node.name, args_of(node, False), ann(node.returns)))
            mod.uses |= names_in(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    mod.constants.append(t.id)
    for node in ast.walk(tree):                      # module-level and lazy (inside a function) imports
        if isinstance(node, ast.ImportFrom):
            if node.level:                           # relative: from .x import y / from . import y
                base = modname.split(".") if is_package else modname.split(".")[:-1]
                base = base[:len(base) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module
            mod.imports.append((target, [a.name for a in node.names]))
        elif isinstance(node, ast.Import):
            mod.imports += [(a.name, []) for a in node.names]
    return mod


def function_imports(mod: Mod, mods: dict[str, Mod]) -> set[str]:
    """Project modules whose functions or constants `mod` uses (imports of classes only are left
    out: the class relations already show those)."""
    out = set()
    for target, names in mod.imports:
        if target not in mods:
            continue
        if not names:
            out.add(target)
        for n in names:
            if f"{target}.{n}" in mods:
                out.add(f"{target}.{n}")              # from package import module
            elif n in {f for f, _, _ in mods[target].functions} or n in mods[target].constants:
                out.add(target)
    out.discard(mod.name)
    return {t for t in out if mods[t].has_box}


def load(src: Path) -> dict[str, Mod]:
    mods = {}
    for p in sorted(src.rglob("*.py")):
        rel = p.relative_to(src).with_suffix("")
        parts = list(rel.parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        mods[".".join(parts)] = parse_module(p, ".".join(parts), is_package)
    return mods


def mid(modname: str) -> str:
    """id of a module's «module» box (must differ from the namespace name)"""
    return "m_" + modname.replace(".", "_")


def field_relation(owner: str, fname: str, annotation: str, known: set[str]) -> list[str]:
    try:
        tree = ast.parse(annotation, mode="eval")
    except SyntaxError:
        return []
    out = []
    for target in sorted(names_in(tree) & known - {owner}):
        many = any(k in annotation for k in ("list[", "dict[", "tuple[", "set[", "List[", "Dict["))
        if many:
            out.append(f'{owner} "1" *-- "*" {target} : {esc(fname)}')
        elif "Optional[" in annotation or "None" in annotation:
            out.append(f'{owner} --> "0..1" {target} : {esc(fname)}')
        else:
            out.append(f'{owner} --> "1" {target} : {esc(fname)}')
    return out


def render_group(stem: str, title: str, packages: list[str], mods: dict[str, Mod]) -> str:
    inside = {m: mods[m] for m in mods if m.split(".")[0] in packages}
    all_classes = {c.name: c for m in mods.values() for c in m.classes}
    known = set(all_classes)
    here = {c.name for m in inside.values() for c in m.classes}
    lines = ["---", "config:", "  layout: elk", f'title: "{title}"', "---", "classDiagram",
             f"  direction {DIRECTION.get(stem, 'LR')}"]
    rels: list[str] = []
    stubs: set[str] = set()
    for modname, mod in inside.items():
        if not mod.classes and not mod.functions and not mod.constants:
            continue                                   # empty package __init__
        lines.append(f"  namespace {modname} {{")
        for c in mod.classes:
            lines.append(f"    class {c.name} {{")
            ext = [EXTERNAL_STEREOTYPES[b] for b in c.bases if b in EXTERNAL_STEREOTYPES]
            if ext:
                lines.append(f"      <<{ext[0]}>>")
            elif "dataclass" in c.decorators:
                lines.append("      <<dataclass>>")
            for fname, fann in c.fields:
                lines.append(f"      +{esc(fname)}: {esc(fann)}")
                rels += field_relation(c.name, fname, fann, known)
            for name, args, ret, static in c.methods:
                vis = "-" if name.startswith("_") and not name.startswith("__") else "+"
                lines.append(f"      {vis}{esc(name)}({esc(args)}){'$' if static else ''}"
                             + (f" {esc(ret)}" if ret else ""))
            lines.append("    }")
            for b in c.bases:
                if b in known:
                    rels.append(f"{b} <|-- {c.name}")
        if mod.functions or mod.constants:
            lines.append(f'    class {mid(modname)}["{modname}"] {{')
            lines.append("      <<module>>")
            for k in mod.constants:
                lines.append(f"      {'-' if k.startswith('_') else '+'}{esc(k)}")
            for name, args, ret in mod.functions:
                vis = "-" if name.startswith("_") else "+"
                lines.append(f"      {vis}{esc(name)}({esc(args)})" + (f" {esc(ret)}" if ret else ""))
            lines.append("    }")
        lines.append("  }")
    # dependencies: module functions / class methods using a class (not already a field relation)
    linked = {(r.split()[0], r.split(" : ")[0].split()[-1]) for r in rels}
    for modname, mod in inside.items():
        if mod.functions or mod.constants:
            for t in sorted(mod.uses & known):
                if all_classes[t].module != modname:      # same module: the namespace already shows it
                    rels.append(f"{mid(modname)} ..> {t}")
        for c in mod.classes:
            for t in sorted(c.uses & known - {c.name}):
                if (c.name, t) in linked or (t, c.name) in linked:
                    continue
                if any(t == b for b in c.bases):
                    continue
                # a subclass inherits its base's dependencies; drawing them again only adds lines
                if any(b in all_classes and t in all_classes[b].uses for b in c.bases):
                    continue
                rels.append(f"{c.name} ..> {t}")
    # module → module: functions or constants of another module used ("imports")
    stub_mods: set[str] = set()
    for modname, mod in inside.items():
        if not mod.has_box:
            continue
        for t in sorted(function_imports(mod, mods)):
            rels.append(f"{mid(modname)} ..> {mid(t)}")
            if t not in inside:
                stub_mods.add(t)
    for r in rels:
        for token in (r.split()[0], r.split(" : ")[0].split()[-1]):
            if token in known and token not in here:
                stubs.add(token)
    by_mod: dict[str, list[str]] = {}
    for s in sorted(stubs):
        by_mod.setdefault(all_classes[s].module, []).append(f"    class {s}")
    for m in sorted(stub_mods):
        by_mod.setdefault(m, []).append(f'    class {mid(m)}["{m}"] {{\n      <<module>>\n    }}')
    for m, entries in sorted(by_mod.items()):
        lines.append(f"  namespace {m} {{")
        lines += entries
        lines.append("  }")
    seen = set()
    for r in rels + EXTRA_RELATIONS.get(stem, []):
        if r not in seen:
            seen.add(r)
            lines.append("  " + r)
    for target, text in NOTES.get(stem, []):
        lines.append(f'  note for {target} "{text}"')
    for s in sorted(stubs) + [mid(m) for m in sorted(stub_mods)]:
        lines.append(f"  style {s} fill:#f4f4f4,stroke:#9a9a9a,color:#555555,stroke-dasharray: 4 3")
    return "\n".join(lines) + "\n"


def main() -> None:
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / "src"
    mods = load(src)
    covered = {p for _, _, ps in GROUPS for p in ps}
    missing = {m.split(".")[0] for m in mods} - covered
    if missing:
        sys.exit(f"packages not in any diagram: {sorted(missing)}")
    written = ""
    for stem, title, packages in GROUPS:
        text = render_group(stem, title, packages, mods)
        (out / f"{stem}.mmd").write_text(text, encoding="utf-8")
        written += text.replace("#95;", "_")
        n = sum(1 for m in mods if m.split(".")[0] in packages)
        print(f"{stem}: {n} modules")
    # completeness check: every class, method and module function must be in one of the diagrams
    classes = [c for m in mods.values() for c in m.classes]
    methods = [(c.name, x[0]) for c in classes for x in c.methods]
    functions = [(m.name, f[0]) for m in mods.values() for f in m.functions]
    missing = [c.name for c in classes if f"class {c.name}" not in written]
    missing += [f"{a}.{b}" for a, b in methods + functions if f"{b}(" not in written]
    print(f"covered: {len(mods)} modules, {len(classes)} classes, {len(methods)} methods, "
          f"{len(functions)} functions; missing: {missing or 'none'}")


if __name__ == "__main__":
    main()
