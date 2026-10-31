"""Executable dependency rules for the hexagonal boundary."""

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1] / "src"
PACKAGE = ROOT / "garmin_local_sync"
INFRASTRUCTURE = {
    "requests",
    "osgeo",
    "fitdecode",
    "sqlite3",
    "pathlib",
    "os",
    "subprocess",
    "socket",
    "urllib",
    "http",
    "zipfile",
}


def test_inner_layers_cannot_import_adapters_or_infrastructure():
    for layer in ("domain", "application"):
        for source in (PACKAGE / layer).glob("*.py"):
            for node in ast.walk(ast.parse(source.read_text())):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imports = [
                        importlib.util.resolve_name(
                            "." * node.level + (node.module or ""), f"garmin_local_sync.{layer}"
                        )
                        if node.level
                        else node.module
                    ]
                for name in imports:
                    root = name.split(".")[0]
                    assert root not in INFRASTRUCTURE, (source, name)
                    if root == "garmin_local_sync":
                        allowed = ["garmin_local_sync.domain"]
                        if layer == "application":
                            allowed.append("garmin_local_sync.application")
                        assert any(name == x or name.startswith(x + ".") for x in allowed), (source, name)
                    else:
                        assert root in sys.stdlib_module_names, (source, name)
            assert ".db" not in source.read_text(), source
            assert "-service/" not in source.read_text(), source


def test_core_imports_and_geography_work_without_installed_infrastructure():
    script = """
import sys
import importlib.abc
class NoInfrastructure(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'requests', 'osgeo', 'fitdecode', 'sqlite3'}:
            raise AssertionError('Core tried to import infrastructure: ' + fullname)
sys.meta_path.insert(0, NoInfrastructure())
from garmin_local_sync.application.sync import SyncService
from garmin_local_sync.domain.geography import build_geography
from garmin_local_sync.domain.models import Sample
shape = build_geography([Sample(0, latitude=10, longitude=179), Sample(1, latitude=12, longitude=-179)])
assert len(shape.segments) == 2
assert shape.segments[0][-1].longitude == 180
assert shape.segments[1][0].longitude == -180
print('Core works without infrastructure')
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r})\n" + script],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Core works without infrastructure" in result.stdout
