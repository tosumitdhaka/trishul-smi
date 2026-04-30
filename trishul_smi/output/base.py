"""Shared Protocol for output formatters.

All formatters (JsonFormatter, PysnmpFormatter, any future formatter) must
conform to FormatterProtocol so the compiler can type-check its formatter
registry and future authors know exactly what contract to satisfy.
"""

from __future__ import annotations

from typing import Protocol

from trishul_smi.models.mib_module import MibModule


class FormatterProtocol(Protocol):
    """Contract every output formatter must satisfy.

    Attributes
    ----------
    FILE_SUFFIX:
        File extension including the leading dot (e.g. '.json', '.py').

    Methods
    -------
    format(module):
        Render *module* and return the file content as ``str`` or ``bytes``.
        Returning ``bytes`` is valid (e.g. compressed JSON); the compiler
        handles both via ``isinstance(content, bytes)``.
    """

    FILE_SUFFIX: str

    def format(self, module: MibModule) -> str | bytes:  # noqa: A003
        ...
