"""Plugin formatter registry tests — entry-point discovery for custom formats.

Entry points are faked with real ``importlib.metadata.EntryPoint`` objects
(``module:attr`` values pointing into this module) and a monkeypatched
``importlib.metadata.entry_points``; no real package installation is needed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from tests.helpers import MockReader
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.models.mib_module import MibModule
from trishul_smi.output.json_fmt import JsonFormatter
from trishul_smi.output.registry import (
    ENTRY_POINT_GROUP,
    available_formats,
    resolve_formatter,
)

MINIMAL_V2 = """
TEST-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, Integer32 FROM SNMPv2-SMI ;
testMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Minimal test MIB."
    ::= { 1 3 }
END
"""


class MarkerFormatter:
    """Test double: renders the module name to a ``.marker`` file."""

    FILE_SUFFIX = ".marker"

    def format(self, module: MibModule) -> str:
        return f"module={module.name}"


NOT_A_FORMATTER = 42  # loads fine but is not a formatter class


def _entry_point(name: str, value: str) -> EntryPoint:
    return EntryPoint(name=name, value=value, group=ENTRY_POINT_GROUP)


@pytest.fixture
def mock_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[list[EntryPoint]], None]:
    """Replace ``importlib.metadata.entry_points`` with the given plugins."""

    def _set(entry_points: list[EntryPoint]) -> None:
        import importlib.metadata

        monkeypatch.setattr(
            importlib.metadata,
            "entry_points",
            lambda group=None: tuple(entry_points),
        )

    return _set


class TestRegistry:
    def test_builtin_wins_over_same_named_plugin(self, mock_entry_points):
        mock_entry_points([_entry_point("json", "tests.test_plugins:MarkerFormatter")])
        assert resolve_formatter("json") is JsonFormatter
        # the shadowed plugin never enters the registry
        assert available_formats() == ["json"]

    def test_plugin_name_resolves_to_plugin_class(self, mock_entry_points):
        mock_entry_points([_entry_point("marker", "tests.test_plugins:MarkerFormatter")])
        assert resolve_formatter("marker") is MarkerFormatter
        assert available_formats() == ["json", "marker"]

    def test_broken_plugin_warns_and_is_skipped(self, mock_entry_points, caplog):
        """A plugin that fails to import must not crash resolution."""
        mock_entry_points(
            [
                _entry_point("broken", "tests.no_such_module:Whatever"),
                _entry_point("marker", "tests.test_plugins:MarkerFormatter"),
            ]
        )
        with caplog.at_level(logging.WARNING, logger="trishul_smi.output.registry"):
            assert resolve_formatter("marker") is MarkerFormatter
        assert any("broken" in r.getMessage() for r in caplog.records)

    def test_non_formatter_object_warns_and_is_skipped(self, mock_entry_points, caplog):
        """A plugin that loads to a non-class/non-formatter object is skipped."""
        mock_entry_points([_entry_point("bad", "tests.test_plugins:NOT_A_FORMATTER")])
        with caplog.at_level(logging.WARNING, logger="trishul_smi.output.registry"):
            with pytest.raises(ValueError, match="Unknown output format"):
                resolve_formatter("bad")
        assert any("not a formatter class" in r.getMessage() for r in caplog.records)
        assert available_formats() == ["json"]

    def test_unknown_name_lists_available_formats(self, mock_entry_points):
        """Unknown names raise with built-ins and discovered plugins marked."""
        mock_entry_points([_entry_point("marker", "tests.test_plugins:MarkerFormatter")])
        with pytest.raises(ValueError) as exc_info:
            resolve_formatter("nope")
        msg = str(exc_info.value)
        assert "Unknown output format" in msg
        assert "json (built-in)" in msg
        assert "marker (plugin)" in msg

    def test_unknown_name_without_plugins(self):
        with pytest.raises(ValueError) as exc_info:
            resolve_formatter("nope")
        assert "json (built-in)" in str(exc_info.value)
        assert "(plugin)" not in str(exc_info.value)


class TestMibCompilerWithPlugin:
    @pytest.mark.asyncio
    async def test_plugin_formatter_used_end_to_end(self, tmp_path: Path, mock_entry_points):
        """A plugin format name flows through MibCompiler and writes output."""
        mock_entry_points([_entry_point("marker", "tests.test_plugins:MarkerFormatter")])
        config = CompilerConfig(
            output_dir=tmp_path / "out", formats=["json", "marker"], cache_dir=None
        )
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")

        compiled = next(r for r in results if r.name == "TEST-MIB")
        assert compiled.status == "compiled"
        marker = tmp_path / "out" / "TEST-MIB.marker"
        assert marker.read_text() == "module=TEST-MIB"
        # the built-in format still works alongside the plugin
        assert (tmp_path / "out" / "TEST-MIB.json").exists()

    def test_unknown_format_raises_at_construction(self, mock_entry_points):
        """Unknown format names now fail at MibCompiler construction."""
        mock_entry_points([_entry_point("marker", "tests.test_plugins:MarkerFormatter")])
        with pytest.raises(ValueError) as exc_info:
            MibCompiler(CompilerConfig(formats=["nope"], cache_dir=None))
        msg = str(exc_info.value)
        assert "json (built-in)" in msg
        assert "marker (plugin)" in msg
