"""Tests for the top-level package import surface (issue #23 item 7).

``HttpReader`` stays importable from the package root while being a lazy
import: ``import trishul_smi`` must NOT pull ``httpx`` into sys.modules.
The lazy resolution happens in both ``trishul_smi.__init__`` and
``trishul_smi.reader.__init__`` via PEP 562 module ``__getattr__``.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


class TestLazyHttpImport:
    def test_import_package_does_not_import_httpx(self):
        """Fresh interpreter: importing trishul_smi leaves httpx unloaded;
        accessing trishul_smi.HttpReader loads it lazily and resolves to the
        real class."""
        code = "\n".join(
            [
                "import sys",
                "import trishul_smi",
                "assert 'httpx' not in sys.modules, 'httpx was imported eagerly'",
                "from trishul_smi import HttpReader",
                "import trishul_smi.reader.httpclient",
                "assert HttpReader is trishul_smi.reader.httpclient.HttpReader",
                "assert 'httpx' in sys.modules, 'httpx not loaded after HttpReader access'",
                "print('ok')",
            ]
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "ok"

    def test_httpreader_resolves_to_class(self):
        import trishul_smi
        from trishul_smi.reader.httpclient import HttpReader as _HttpReader

        assert hasattr(trishul_smi, "HttpReader")
        assert trishul_smi.HttpReader is _HttpReader

    def test_reader_package_lazy_httpreader(self):
        from trishul_smi.reader import FileReader, HttpReader
        from trishul_smi.reader.httpclient import HttpReader as _HttpReader

        assert FileReader is not None
        assert HttpReader is _HttpReader

    def test_getattr_missing_name_raises(self):
        import trishul_smi

        missing = "NoSuchReader"
        with pytest.raises(AttributeError):
            getattr(trishul_smi, missing)
