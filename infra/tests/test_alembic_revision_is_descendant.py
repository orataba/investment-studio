from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = (
    REPOSITORY_ROOT / "infra" / "scripts" / "alembic_revision_is_descendant.py"
)
REGISTRY_MIGRATION_ROOT = REPOSITORY_ROOT / "infra" / "instrument_registry"


@pytest.fixture()
def helper_module() -> ModuleType:
    module_name = "portfolio_ops_test_alembic_revision_is_descendant"
    spec = importlib.util.spec_from_file_location(module_name, HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "current_revision",
    (
        "20260715_0012",
        "20260804_0017",
        "20260806_0018",
        "20260807_0019",
    ),
)
def test_registry_nav_contract_descendants_are_recognized(
    helper_module: ModuleType,
    current_revision: str,
) -> None:
    assert helper_module.revisions_are_descendants(
        REGISTRY_MIGRATION_ROOT,
        f"{current_revision} (head)",
        "20260715_0012",
    )


@pytest.mark.parametrize(
    "current_output",
    (
        "20260715_0011",
        "",
        "not-a-known-revision (head)",
        "20260807_0019 (head)\n20260715_0011 (head)",
    ),
)
def test_registry_nav_contract_check_fails_closed(
    helper_module: ModuleType,
    current_output: str,
) -> None:
    assert not helper_module.revisions_are_descendants(
        REGISTRY_MIGRATION_ROOT,
        current_output,
        "20260715_0012",
    )
