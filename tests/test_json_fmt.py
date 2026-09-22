"""Focused tests for ``trishul_smi.output.json_fmt`` output-layer nits.

Covers the ``_compact_int_arrays`` byte-rewrite invariant (v0.4.7-review L5):
the regex collapses multi-line integer arrays but must never corrupt string
values that merely *look* like indented arrays. Also pins that ``orjson``
escapes newlines inside strings — the invariant the rewrite depends on.
"""

from __future__ import annotations

import json

import orjson

from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.output.json_fmt import JsonFormatter, _compact_int_arrays

# Array-like DESCRIPTION text: brackets, digit runs, literal newlines that
# _norm_desc collapses, and escaped-newline (backslash-n) sequences that
# survive normalization and are re-escaped by orjson.
_ARRAY_LIKE_DESC = (
    "This DESCRIPTION looks like indented arrays:\n"
    "  [\n"
    "    1,\n"
    "    3,\n"
    "    5\n"
    "  ]\n"
    "  [ 1,\n"
    "    7 ]\n"
    "escaped newlines: [\\n  11,\\n  13\\n]\n"
    "bare digit runs on their own lines:\n"
    "  42,\n"
    "  43\n"
)


def _adversarial_module() -> MibModule:
    return MibModule(
        name="ADV-MIB",
        language="SMIv2",
        description=_ARRAY_LIKE_DESC,
        objects={
            "advScalar": MibObject(
                name="advScalar",
                oid="1.3.6.1.4.1.99999.1",
                oid_path=[1, 3, 6, 1, 4, 1, 99999, 1],
                object_type="OBJECT-TYPE",
                syntax="Integer32",
                max_access="read-only",
                status="current",
                description="object desc with [\n  2,\n  4\n] inside",
            ),
            "advIndex": MibObject(
                name="advIndex",
                oid="1.3.6.1.4.1.99999.2",
                oid_path=[1, 3, 6, 1, 4, 1, 99999, 2],
                object_type="OBJECT-TYPE",
                syntax="Integer32",
                max_access="read-only",
                status="current",
                index=["advScalar", "advIndex"],
            ),
        },
    )


class TestCompactIntArrays:
    def test_integer_arrays_collapsed_to_single_line(self):
        m = _adversarial_module()
        raw = JsonFormatter().format(m)
        text = raw.decode()

        # The real numeric array is collapsed onto one line…
        assert '"oid_path": [1, 3, 6, 1, 4, 1, 99999, 1]' in text
        # …and its multi-line form is gone.
        assert '"oid_path": [\n' not in text

    def test_round_trip_preserves_all_content(self):
        """The rewrite must never change JSON semantics — only whitespace."""
        m = _adversarial_module()
        raw = JsonFormatter().format(m)
        data = json.loads(raw)

        assert data["objects"]["advScalar"]["oid_path"] == [1, 3, 6, 1, 4, 1, 99999, 1]
        assert data["objects"]["advIndex"]["index"] == ["advScalar", "advIndex"]
        # Description survives byte-for-byte after _norm_desc's whitespace collapse.
        assert data["module_metadata"]["description"] == " ".join(_ARRAY_LIKE_DESC.split())
        assert data["objects"]["advScalar"]["description"] == "object desc with [ 2, 4 ] inside"

    def test_bytes_unchanged_when_no_integer_arrays(self):
        payload = {
            "index": ["ifIndex", "ifDescr"],
            "nested": {"members": ["m1", "m2"]},
            "desc": "no arrays here",
        }
        data = orjson.dumps(payload, option=orjson.OPT_INDENT_2)
        assert _compact_int_arrays(data) == data


class TestOrjsonEscapingInvariant:
    """Pin the invariant the regex rewrite depends on.

    ``_compact_int_arrays`` rewrites the *serialized bytes*; it is safe only
    because orjson escapes every raw newline inside string values to the two
    bytes ``b"\\\\n"``, so ``b"[\\n"`` (bracket + LF) can only ever occur at a
    structural array boundary, never inside a string.
    """

    def test_raw_newline_inside_string_is_escaped_by_orjson(self):
        payload = {"desc": "array-like [\n  1,\n  3\n] text"}
        data = orjson.dumps(payload, option=orjson.OPT_INDENT_2)

        assert b"[\\n" in data  # bracket + backslash + n (escaped newline)
        assert b"[\n" not in data  # bracket + raw LF must never occur in a string

    def test_array_like_string_survives_compact_byte_identical(self):
        payload = {
            "oid_path": [1, 3, 6, 1],
            "desc": "array-like [\n  1,\n  3\n] text",
        }
        data = orjson.dumps(payload, option=orjson.OPT_INDENT_2)
        out = _compact_int_arrays(data)
        text = out.decode()

        assert '"oid_path": [1, 3, 6, 1]' in text
        # The description bytes are untouched (orjson's \\n escapes preserved).
        assert "array-like [\\n  1,\\n  3\\n] text" in text
        # Semantics unchanged: the rewrite only removed the array's indentation.
        assert json.loads(out) == json.loads(data)


class TestArtifactMetadataContract:
    def test_metadata_is_construction_time_only(self):
        """set_artifact_metadata was deleted (v0.4.9-review L5): the metadata
        contract is supplied at construction and cannot be mutated afterwards."""
        assert not hasattr(JsonFormatter, "set_artifact_metadata")
