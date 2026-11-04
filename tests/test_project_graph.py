import unittest
from pathlib import Path

from tools.project_graph import build_graph


class ProjectGraphTests(unittest.TestCase):
    def test_graph_has_complete_file_and_python_symbol_nodes_with_valid_edges(self):
        root = Path(__file__).resolve().parents[1]
        graph = build_graph(root)
        nodes = {node["id"]: node for node in graph["nodes"]}
        self.assertIn("run.py", nodes)
        self.assertIn("cps/app.py::MainWindow", nodes)
        self.assertIn("cps/dependencies.py::import_dependencies", nodes)
        self.assertIn("native/CarPackStudio.cpp", nodes)
        self.assertIn("native/studio_spawn.h", nodes)
        for path in (root / "cps").glob("*.py"):
            self.assertIn(path.relative_to(root).as_posix(), nodes)
        for edge in graph["edges"]:
            self.assertIn(edge["from"], nodes)
            self.assertIn(edge["to"], nodes)
        self.assertIn({"from": "cps/runtime.py", "to": "cps/dependencies.py", "kind": "imports"}, graph["edges"])
        self.assertFalse(any(node["path"].startswith(("runtime/", "dist/", "build/", "save_template/"))
                             for node in nodes.values()))
        self.assertEqual(graph, build_graph(root))
