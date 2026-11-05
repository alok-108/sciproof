"""Tests for Jupyter / IPython sciproof integration."""

from __future__ import annotations

from pathlib import Path

from sciproof.jupyter import load_ipython_extension


class DummyIPython:
    def __init__(self):
        self.user_ns = {}
        self.magics = {}

    def register_magic_function(self, func, magic_kind="line", magic_name=None):
        name = magic_name or func.__name__
        self.magics[name] = func


def test_jupyter_magic_registration(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ipython = DummyIPython()

    # Mock register_cell_magic from IPython.core.magic
    import sys
    import types

    fake_magic = types.ModuleType("IPython.core.magic")

    def register_cell_magic(func):
        ipython.magics[func.__name__] = func
        return func

    fake_magic.register_cell_magic = register_cell_magic

    fake_ipython_mod = types.ModuleType("IPython")
    fake_ipython_mod.core = types.ModuleType("IPython.core")
    fake_ipython_mod.core.magic = fake_magic

    monkeypatch.setitem(sys.modules, "IPython", fake_ipython_mod)
    monkeypatch.setitem(sys.modules, "IPython.core", fake_ipython_mod.core)
    monkeypatch.setitem(sys.modules, "IPython.core.magic", fake_magic)

    load_ipython_extension(ipython)
    assert "sciproof" in ipython.magics

    cell_magic = ipython.magics["sciproof"]
    cell_code = "import json; res = {'acc': 0.99}; open('res.json', 'w').write(json.dumps(res))"
    (tmp_path / "res.json").write_text("{}", encoding="utf-8")

    cell_magic("--name nb_exp --output res.json", cell_code)
    assert (tmp_path / "nb_exp.sciproof.json").exists()
    assert ipython.user_ns.get("res") == {"acc": 0.99}
