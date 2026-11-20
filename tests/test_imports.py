"""Every module imports on its own (catches broken imports after a move)."""
import importlib
import pkgutil

import pytest

import ris_kpi

MODULES = [m.name for m in pkgutil.walk_packages(ris_kpi.__path__, prefix="ris_kpi.")]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)
