"""Formatter registry: built-in formatters plus entry-point-discovered plugins.

Third-party packages can register a custom output formatter by exposing a
class that conforms to the structural :class:`FormatterProtocol` under the
``trishul_smi.formatters`` entry-point group::

    [project.entry-points."trishul_smi.formatters"]
    yaml = "my_package.formatters:YamlFormatter"

Format-name resolution checks built-ins first, then discovered plugins — a
plugin may never shadow a built-in. A plugin that fails to import, or whose
loaded object is not a formatter class, is skipped with a logged warning and
never aborts a compile run. This is the escape hatch for the v0.5.0 pysnmp
format removal: anyone who needs the old ``.py`` output can ship it as a
plugin.

Discovery is deliberately uncached: every call re-reads distribution metadata
so tests can monkeypatch ``importlib.metadata.entry_points`` between calls
without stale state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypeGuard

from trishul_smi.output.json_fmt import JsonFormatter

if TYPE_CHECKING:
    from trishul_smi.output.base import FormatterProtocol

logger = logging.getLogger(__name__)

#: Entry-point group under which third-party formatter classes are registered.
ENTRY_POINT_GROUP: str = "trishul_smi.formatters"

#: Built-in formatter classes keyed by format name. Resolution checks this
#: mapping first, so a plugin cannot shadow a built-in name.
BUILTIN_FORMATTERS: dict[str, type[FormatterProtocol]] = {
    "json": JsonFormatter,
}


def _is_formatter_class(obj: object) -> TypeGuard[type[FormatterProtocol]]:
    """True if *obj* is a class structurally conforming to FormatterProtocol."""
    return (
        isinstance(obj, type)
        and isinstance(getattr(obj, "FILE_SUFFIX", None), str)
        and callable(getattr(obj, "format", None))
    )


def discover_plugins() -> dict[str, type[FormatterProtocol]]:
    """Load formatter classes from the ``trishul_smi.formatters`` entry points.

    Broken entry points — import errors, non-class targets, or classes missing
    the structural formatter surface (``FILE_SUFFIX`` + ``format()``) — are
    skipped with a logged warning instead of raising. Entry points whose name
    collides with a built-in format are skipped as well.

    Returns a mapping of format name → formatter class. No caching: every call
    re-reads distribution metadata so monkeypatched tests stay isolated.
    """
    plugins: dict[str, type[FormatterProtocol]] = {}
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover — importlib.metadata is stdlib
        return plugins
    for ep in list(entry_points(group=ENTRY_POINT_GROUP)):
        name = ep.name
        if name in BUILTIN_FORMATTERS or name in plugins:
            continue
        try:
            loaded = ep.load()
        except Exception as exc:  # noqa: BLE001 — a broken plugin must not crash the run
            logger.warning("Skipping broken formatter plugin %r (%s): %s", name, ep.value, exc)
            continue
        if not _is_formatter_class(loaded):
            logger.warning(
                "Skipping formatter plugin %r (%s): loaded object is not a "
                "formatter class (requires a FILE_SUFFIX attribute and a "
                "format(module) method)",
                name,
                ep.value,
            )
            continue
        plugins[name] = loaded
    return plugins


def available_formats() -> list[str]:
    """All format names usable with ``-f``: built-ins then discovered plugins.

    Each group is sorted. Plugin discovery side effects (skipping broken
    plugins with a logged warning) apply.
    """
    plugins = discover_plugins()
    return sorted(BUILTIN_FORMATTERS) + sorted(plugins)


def resolve_formatter(name: str) -> type[FormatterProtocol]:
    """Resolve a format name to its formatter class.

    Built-ins are checked first; entry-point plugins are only discovered for
    names that are not built-in, so a plugin named ``json`` is never used.
    Unknown names raise a ``ValueError`` listing every available format,
    marking which are plugins.
    """
    if name in BUILTIN_FORMATTERS:
        return BUILTIN_FORMATTERS[name]
    plugins = discover_plugins()
    if name in plugins:
        return plugins[name]
    if name == "pysnmp":
        # No plugin registers a `pysnmp` formatter (which is exactly what the
        # v0.5.0 removal promised as the escape hatch), so the name is gone
        # from the built-ins. Point at the JSON bundle output and the plugin
        # route instead of the generic unknown-format listing.
        raise ValueError(
            "the 'pysnmp' output format was removed in v0.5.0 — use the JSON "
            "bundle output instead (--format json, optionally with "
            "--emit-manifest / --emit-oid-index); 'tsmi convert' is unaffected "
            "for reading existing .py files. To keep producing .py output, a "
            "plugin can register a 'pysnmp' formatter under the "
            f"{ENTRY_POINT_GROUP!r} entry-point group — the trishul-smi-pysnmp "
            "package does exactly that."
        )
    available = ", ".join(
        [f"{fmt} (built-in)" for fmt in sorted(BUILTIN_FORMATTERS)]
        + [f"{fmt} (plugin)" for fmt in sorted(plugins)]
    )
    raise ValueError(f"Unknown output format: {name!r}. Available formats: {available}")
