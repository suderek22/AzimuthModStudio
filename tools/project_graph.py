"""Rebuild the local, deterministic AI navigation index without external services."""
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "project-graph.json"


def project_files(root):
    files = [path for path in root.iterdir()
             if path.is_file() and path.suffix in (".py", ".spec", ".ps1", ".md", ".txt")]
    for folder in ("cps", "tests", "tools", "native", "resources"):
        files.extend(path for path in (root / folder).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts and
                     path.suffix in (".py", ".cpp", ".h", ".rc", ".bat", ".html", ".svg", ".txt"))
    return sorted(set(files) - {root / "PROJECT-GRAPH.md"})


def build_graph(root=ROOT):
    nodes, edges = [], []
    files = project_files(root)
    modules = {str(path.relative_to(root).with_suffix("")).replace("\\", "."): path
               for path in files if path.suffix == ".py"}
    for path in files:
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        nodes.append({"id": relative, "kind": "file", "path": relative,
                      "sha256": hashlib.sha256(data).hexdigest()})
        if path.suffix == ".py":
            tree = ast.parse(data.decode("utf-8-sig"), filename=relative)

            def visit(body, parent, prefix=""):
                for node in body:
                    if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    name = prefix + node.name
                    identifier = relative + "::" + name
                    nodes.append({"id": identifier, "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                                  "path": relative, "name": name, "line": node.lineno,
                                  "end_line": node.end_lineno})
                    edges.append({"from": parent, "to": identifier, "kind": "contains"})
                    visit(node.body, identifier, name + ".")

            visit(tree.body, relative)
            package = str(path.relative_to(root).parent).replace("\\", ".")
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = ".".join(package.split(".")[:len(package.split(".")) - node.level + 1]) if node.level else ""
                    module = ".".join(part for part in (base, node.module) if part)
                    imports = [module] + [module + "." + alias.name for alias in node.names]
                else:
                    continue
                for module in imports:
                    target = modules.get(module) or modules.get(module + ".__init__")
                    if target:
                        edges.append({"from": relative, "to": target.relative_to(root).as_posix(), "kind": "imports"})
        elif path.suffix in (".cpp", ".h"):
            for include in re.findall(r'^\s*#include\s+"([^"]+)"', data.decode("utf-8-sig"), re.M):
                target = path.parent / include
                if target.is_file() and target in files:
                    edges.append({"from": relative, "to": target.relative_to(root).as_posix(), "kind": "includes"})
    return {"schema": 1, "scope": "Authored source, tests, build scripts, documentation and text resources. "
            "Excludes private installations, saves, generated builds and binary assets. "
            "Edges are lexical imports/includes/containment, NOT a dynamic call graph.",
            "entrypoints": ["run.py", "cps/app.py::main", "cps/runtime.py::Runtime",
                           "native/CarPackStudio.cpp", "native/CarPackLoader.cpp", "build-release.ps1"],
            "nodes": sorted(nodes, key=lambda node: node["id"]),
            "edges": sorted({json.dumps(edge, sort_keys=True): edge for edge in edges}.values(),
                            key=lambda edge: (edge["from"], edge["to"], edge["kind"]))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the saved graph is stale.")
    args = parser.parse_args()
    content = json.dumps(build_graph(), indent=2, ensure_ascii=True) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != content:
            parser.exit(1, "Project graph is stale; run python tools\\project_graph.py\n")
    else:
        OUTPUT.write_text(content, encoding="utf-8")
    print("Project graph verified." if args.check else "Wrote project-graph.json")


if __name__ == "__main__":
    main()
