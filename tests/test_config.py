"""Unit tests for CompilerConfig defaults and field types."""
from pathlib import Path

from trishul_smi.config import CompilerConfig


class TestCompilerConfig:
    def test_defaults(self):
        cfg = CompilerConfig()
        assert isinstance(cfg.sources, list)
        assert len(cfg.sources) == 2
        assert "@mib@" in cfg.sources[0]
        assert cfg.output_dir == Path("./mibs-output")
        assert cfg.formats == ["json"]
        assert cfg.http_timeout == 30.0
        assert cfg.http_retries == 3
        assert cfg.cache_ttl_days == 7
        assert cfg.max_mib_size == 10 * 1024 * 1024

    def test_cache_dir_under_home(self):
        cfg = CompilerConfig()
        assert cfg.cache_dir is not None
        assert cfg.cache_dir == Path.home() / ".cache" / "trishul-smi"

    def test_cache_dir_can_be_none(self):
        cfg = CompilerConfig(cache_dir=None)
        assert cfg.cache_dir is None

    def test_independent_defaults(self):
        a = CompilerConfig()
        b = CompilerConfig()
        a.sources.append("extra")
        assert "extra" not in b.sources  # no shared mutable default

    def test_custom_values(self):
        cfg = CompilerConfig(
            formats=["json", "pysnmp"],
            http_timeout=10.0,
            max_mib_size=1024,
        )
        assert cfg.formats == ["json", "pysnmp"]
        assert cfg.http_timeout == 10.0
        assert cfg.max_mib_size == 1024
