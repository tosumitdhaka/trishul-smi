"""Unit tests for errors.py — hierarchy, import safety, instantiation."""
import pytest

from trishul_smi.errors import (
    CircularDependencyError,
    CodeGenError,
    MibCacheError,
    MibNotFoundError,
    MibSizeLimitError,
    ParseError,
    TrishulError,
    WriterError,
)


class TestErrorHierarchy:
    def test_all_inherit_from_trishul_error(self):
        for cls in [
            MibNotFoundError,
            MibSizeLimitError,
            ParseError,
            CircularDependencyError,
            CodeGenError,
            WriterError,
            MibCacheError,
        ]:
            assert issubclass(cls, TrishulError), f"{cls.__name__} must inherit TrishulError"

    def test_trishul_error_inherits_exception(self):
        assert issubclass(TrishulError, Exception)

    def test_raise_and_catch_as_base(self):
        with pytest.raises(TrishulError):
            raise MibNotFoundError("IF-MIB not found")

    def test_messages_are_preserved(self):
        err = ParseError("unexpected token at line 42")
        assert "line 42" in str(err)

    def test_size_limit_error(self):
        err = MibSizeLimitError("IF-MIB exceeds 10MB limit")
        assert isinstance(err, TrishulError)

    def test_circular_dependency_error(self):
        err = CircularDependencyError("A -> B -> A")
        assert "A -> B -> A" in str(err)
