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
from trishul_smi.output.json_fmt import JsonFormatter, _compact_int_arrays, _enum_map

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


def _value_metadata_module(**overrides: dict) -> MibModule:
    """Module whose objects are value-metadata fixtures for the enrichment tests."""
    base = dict(
        oid="1.3.6.1.2.1.1.1",
        oid_path=[1, 3, 6, 1, 2, 1, 1, 1],
        object_type="OBJECT-TYPE",
        max_access="read-only",
        status="current",
    )
    objects: dict[str, MibObject] = {
        "ifOperStatus": MibObject(
            **base,
            name="ifOperStatus",
            syntax="INTEGER",
            constraints={"kind": "enum", "data": [["up", 1], ["down", 2], ["testing", 3]]},
        ),
        "portFlags": MibObject(
            **base,
            name="portFlags",
            syntax="BITS",
            constraints={"kind": "bits", "data": [["up", 0], ["down", 1]]},
        ),
        "ifSpeed": MibObject(**base, name="ifSpeed", syntax="Gauge32", units="bits/second"),
        "ifMtu": MibObject(
            **base,
            name="ifMtu",
            syntax="Integer32",
            constraints={"kind": "range", "data": [[64, 65535]]},
        ),
        "ifDescr": MibObject(
            **base,
            name="ifDescr",
            syntax="OCTET STRING",
            constraints={"kind": "size", "data": [[0, 255]]},
        ),
        "plain": MibObject(**base, name="plain"),
    }
    return MibModule(name="VM-MIB", language="SMIv2", objects=objects)


class TestObjectValueMetadata:
    """Lane B (issue #35): additive object-level enums / units / constraints."""

    def test_enums_derived_from_enum_constraint(self):
        data = json.loads(JsonFormatter().format(_value_metadata_module()))
        obj = data["objects"]["ifOperStatus"]
        assert obj["enums"] == {"up": 1, "down": 2, "testing": 3}
        assert list(obj["enums"]) == ["up", "down", "testing"]  # insertion order preserved

    def test_enums_derived_from_bits_constraint(self):
        data = json.loads(JsonFormatter().format(_value_metadata_module()))
        assert data["objects"]["portFlags"]["enums"] == {"up": 0, "down": 1}

    def test_enums_absent_for_range_and_size_constraints(self):
        data = json.loads(JsonFormatter().format(_value_metadata_module()))
        assert "enums" not in data["objects"]["ifMtu"]
        assert "enums" not in data["objects"]["ifDescr"]

    def test_enums_absent_when_no_constraints(self):
        data = json.loads(JsonFormatter().format(_value_metadata_module()))
        assert "enums" not in data["objects"]["plain"]

    def test_enums_absent_when_constraints_unrelated(self):
        # A constraints dict that is present but not an enum/bits mapping.
        m = _value_metadata_module()
        m.objects["plain"].constraints = {"kind": "union", "data": []}
        out = json.loads(JsonFormatter().format(m))
        assert "enums" not in out["objects"]["plain"]

    def test_units_emitted_when_present(self):
        data = json.loads(JsonFormatter().format(_value_metadata_module()))
        assert data["objects"]["ifSpeed"]["units"] == "bits/second"

    def test_units_absent_when_none(self):
        data = json.loads(JsonFormatter().format(_value_metadata_module()))
        assert "units" not in data["objects"]["ifOperStatus"]

    def test_constraints_emitted_for_range_and_size(self):
        data = json.loads(JsonFormatter().format(_value_metadata_module()))
        assert data["objects"]["ifMtu"]["constraints"] == {"kind": "range", "data": [[64, 65535]]}
        assert data["objects"]["ifDescr"]["constraints"] == {"kind": "size", "data": [[0, 255]]}

    def test_constraints_absent_when_none(self):
        data = json.loads(JsonFormatter().format(_value_metadata_module()))
        assert "constraints" not in data["objects"]["plain"]

    def test_emission_is_deterministic(self):
        m = _value_metadata_module()
        first = JsonFormatter().format(m)
        second = JsonFormatter().format(m)
        assert first == second


class TestEnumMapHelper:
    def test_none_and_empty_are_none(self):
        assert _enum_map(None) is None
        assert _enum_map({}) is None

    def test_non_enum_kind_is_none(self):
        assert _enum_map({"kind": "range", "data": [[0, 10]]}) is None
        assert _enum_map({"kind": "size", "data": [[0, 255]]}) is None
        assert _enum_map({"kind": "union", "data": []}) is None

    def test_malformed_data_items_are_skipped(self):
        result = _enum_map({"kind": "enum", "data": [["ok", 1], ["bad"], "nope"]})
        assert result == {"ok": 1}

    def test_empty_mapping_returns_none(self):
        assert _enum_map({"kind": "enum", "data": []}) is None
