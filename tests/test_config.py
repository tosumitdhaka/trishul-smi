"""Unit tests for CompilerConfig — defaults, isolation, and validators."""

from pathlib import Path

import pytest

from trishul_smi.config import MIB_NAME_PATTERN, CompilerConfig, validate_mib_name


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

    def test_sidecar_flags_default_false(self):
        c = CompilerConfig()
        assert c.emit_manifest is False
        assert c.emit_oid_index is False


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

    def test_pysnmp_format_removed_in_v0_5_0(self):
        """The pysnmp .py output format is gone (breaking v0.5.0 change)."""
        with pytest.raises(ValueError, match="removed in v0.5.0"):
            CompilerConfig(formats=["pysnmp"])
        with pytest.raises(ValueError, match="removed in v0.5.0"):
            CompilerConfig(formats=["json", "pysnmp"], emit_manifest=True)

    def test_sidecar_flags_allowed_with_json_format(self):
        c = CompilerConfig(formats=["json"], emit_manifest=True, emit_oid_index=True)
        assert c.emit_manifest is True
        assert c.emit_oid_index is True


class TestValidateMibName:
    @pytest.mark.parametrize(
        "name",
        [
            "IF-MIB",
            "mib-802.1ap",  # lowercase + dots — real corpus stems must pass
            "SNMPv2-SMI",
            "lower-case.name",
            "A1",
            "a_1.b-c",
            "x" * 100,  # no length cap — only the character set is checked
        ],
    )
    def test_valid_names_accepted(self, name: str):
        validate_mib_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "../../etc/passwd",  # path traversal
            "foo/bar",  # path separator
            "foo?x=1",  # URL query chars
            "@evil",  # URL-special leading char
            ":",  # not alphanumeric
            ".hidden",  # leading dot
            "..",  # relative path
            "foo bar",  # whitespace
            "foo#frag",  # URL fragment
            "",  # empty
        ],
    )
    def test_invalid_names_raise(self, name: str):
        with pytest.raises(ValueError, match=r"Invalid MIB name"):
            validate_mib_name(name)

    def test_invalid_name_message_is_actionable(self):
        with pytest.raises(ValueError, match=r"allowed|must match") as exc_info:
            validate_mib_name("../../etc/passwd")
        assert "../../etc/passwd" in str(exc_info.value)

    def test_pattern_matches_issue_spec(self):
        assert MIB_NAME_PATTERN == r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
