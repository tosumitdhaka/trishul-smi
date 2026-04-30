"""Unit tests for CompilerConfig — defaults, isolation, and validators."""
import pytest
from pathlib import Path

from trishul_smi.config import CompilerConfig


class TestDefaults:
    def test_default_construction(self):
        c = CompilerConfig()
        assert c.http_timeout == 30.0
        assert c.http_retries == 3
        assert c.max_mib_size == 10 * 1024 * 1024
        assert c.cache_ttl_days == 7
        assert len(c.sources) > 0
        assert "json" in c.formats

    def test_cache_dir_under_home(self):
        c = CompilerConfig()
        assert c.cache_dir is not None
        assert "trishul-smi" in str(c.cache_dir)

    def test_independent_instances(self):
        a = CompilerConfig()
        b = CompilerConfig()
        a.sources.append("http://example.com/@mib@")
        assert len(a.sources) != len(b.sources)

    def test_custom_output_dir(self):
        c = CompilerConfig(output_dir=Path("/tmp/mibs"))
        assert c.output_dir == Path("/tmp/mibs")

    def test_zero_retries_allowed(self):
        c = CompilerConfig(http_retries=0)
        assert c.http_retries == 0


class TestValidators:
    def test_negative_max_mib_size_raises(self):
        with pytest.raises(ValueError, match="max_mib_size"):
            CompilerConfig(max_mib_size=-1)

    def test_zero_max_mib_size_raises(self):
        with pytest.raises(ValueError, match="max_mib_size"):
            CompilerConfig(max_mib_size=0)

    def test_negative_timeout_raises(self):
        with pytest.raises(ValueError, match="http_timeout"):
            CompilerConfig(http_timeout=-5.0)

    def test_negative_retries_raises(self):
        with pytest.raises(ValueError, match="http_retries"):
            CompilerConfig(http_retries=-1)

    def test_negative_ttl_raises(self):
        with pytest.raises(ValueError, match="cache_ttl_days"):
            CompilerConfig(cache_ttl_days=-1)

    def test_zero_ttl_allowed(self):
        c = CompilerConfig(cache_ttl_days=0)
        assert c.cache_ttl_days == 0

    def test_empty_sources_raises(self):
        with pytest.raises(ValueError, match="sources"):
            CompilerConfig(sources=[])

    def test_empty_formats_raises(self):
        with pytest.raises(ValueError, match="formats"):
            CompilerConfig(formats=[])
