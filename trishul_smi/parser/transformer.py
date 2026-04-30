"""Lark Transformer: converts a parsed Tree into a MibModule dataclass.

Design: every grammar rule method receives already-transformed children.
Syntax types (integer_type, octet_string_type, …) return typed strings directly,
so assignment methods receive a clean _SyntaxInfo object — no string sniffing.
Description fields are extracted from named _DescriptionInfo wrappers — no
positional offset fragility.
"""
from __future__ import annotations

from dataclasses import dataclass
from lark import Transformer, Token, Tree

from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.models.mib_type import MibType


# ---------------------------------------------------------------------------
# Internal wrapper types — tag transformer output so assignment methods
# can reliably distinguish syntax from description from status/access.
# ---------------------------------------------------------------------------

@dataclass
class _SyntaxInfo:
    """Carries the resolved syntax type string from a syntax_type rule."""
    value: str

@dataclass
class _DescriptionInfo:
    """Carries the unquoted DESCRIPTION string."""
    value: str

@dataclass
class _StatusInfo:
    """Carries the STATUS value string."""
    value: str

@dataclass
class _AccessInfo:
    """Carries the MAX-ACCESS / ACCESS value string."""
    value: str

@dataclass
class _IndexInfo:
    """Carries the INDEX column list."""
    columns: list[str]

@dataclass
class _AugmentsInfo:
    """Carries the AUGMENTS row name."""
    row: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unquote(token: Token | str) -> str:
    s = str(token)
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('\\"', '"')
    return s


def _resolve_oid(components: list) -> tuple[str, list[int]]:
    """Convert oid_component list → (dotted_string, int_path).

    Named arcs without a number (e.g. 'mib-2') are preserved in the dotted
    string but skipped in the int path — full numeric resolution happens later
    in resolver/ once all modules are loaded.
    """
    parts_str: list[str] = []
    parts_int: list[int] = []
    for comp in components:
        if not isinstance(comp, Tree):
            continue
        if comp.data == "named_arc":
            num = int(str(comp.children[1]))
            parts_str.append(str(num))
            parts_int.append(num)
        elif comp.data == "name_arc":
            parts_str.append(str(comp.children[0]))
        elif comp.data == "number_arc":
            num = int(str(comp.children[0]))
            parts_str.append(str(num))
            parts_int.append(num)
    return ".".join(parts_str), parts_int


# ---------------------------------------------------------------------------
# Transformer
# ---------------------------------------------------------------------------

class MibTransformer(Transformer):
    """Walks the Lark parse tree and builds a MibModule."""

    # ------------------------------------------------------------------
    # Module-level
    # ------------------------------------------------------------------

    def start(self, children: list) -> MibModule:
        return children[0]

    def module_definition(self, children: list) -> MibModule:
        module_name: str = ""
        imports: dict[str, list[str]] = {}
        objects: dict[str, MibObject] = {}
        types: dict[str, MibType] = {}
        notifications: dict[str, MibObject] = {}

        for child in children:
            if isinstance(child, str):
                module_name = child
            elif isinstance(child, dict) and "__imports__" in child:
                imports = child["__imports__"]
            elif isinstance(child, MibObject):
                if child.object_type == "NOTIFICATION-TYPE":
                    notifications[child.name] = child
                else:
                    objects[child.name] = child
            elif isinstance(child, MibType):
                types[child.name] = child
            # None from unhandled assignments — skipped

        language: str = "SMIv1"
        smiv2_markers = {"SNMPv2-SMI", "SNMPv2-TC", "SNMPv2-CONF"}
        if any(m in imports for m in smiv2_markers):
            language = "SMIv2"

        return MibModule(
            name=module_name,
            language=language,  # type: ignore[arg-type]
            imports=imports,
            objects=objects,
            types=types,
            notifications=notifications,
        )

    def module_name(self, children: list) -> str:
        return str(children[0])

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def imports(self, children: list) -> dict:
        result: dict[str, list[str]] = {}
        for clause in children:
            if isinstance(clause, dict):
                result.update(clause)
        return {"__imports__": result}

    def import_clause(self, children: list) -> dict[str, list[str]]:
        symbols: list[str] = children[0]
        module: str = str(children[1])
        return {module: symbols}

    def symbol_list(self, children: list) -> list[str]:
        return [str(c) for c in children]

    def symbol(self, children: list) -> str:
        return str(children[0])

    def module_ref(self, children: list) -> str:
        return str(children[0])

    # ------------------------------------------------------------------
    # MODULE-IDENTITY
    # ------------------------------------------------------------------

    def module_identity_assignment(self, children: list) -> MibObject:
        name = str(children[0])
        oid_str, oid_path = _resolve_oid(self._oid(children))
        return MibObject(
            name=name, oid=oid_str, oid_path=oid_path,
            object_type="MODULE-IDENTITY",
        )

    # ------------------------------------------------------------------
    # OBJECT-IDENTITY
    # ------------------------------------------------------------------

    def object_identity_assignment(self, children: list) -> MibObject:
        name = str(children[0])
        oid_str, oid_path = _resolve_oid(self._oid(children))
        return MibObject(
            name=name, oid=oid_str, oid_path=oid_path,
            object_type="OBJECT-IDENTITY",
            status=self._status(children),
            description=self._description(children),
        )

    # ------------------------------------------------------------------
    # OBJECT-TYPE
    # ------------------------------------------------------------------

    def object_type_assignment(self, children: list) -> MibObject:
        name = str(children[0])
        oid_str, oid_path = _resolve_oid(self._oid(children))
        index_info = next((c for c in children if isinstance(c, _IndexInfo)), None)
        augments_info = next((c for c in children if isinstance(c, _AugmentsInfo)), None)
        return MibObject(
            name=name, oid=oid_str, oid_path=oid_path,
            object_type="OBJECT-TYPE",
            syntax=self._syntax(children),
            max_access=self._access(children),
            status=self._status(children),
            description=self._description(children),
            index=index_info.columns if index_info else None,
            augments=augments_info.row if augments_info else None,
        )

    # ------------------------------------------------------------------
    # NOTIFICATION-TYPE
    # ------------------------------------------------------------------

    def notification_type_assignment(self, children: list) -> MibObject:
        name = str(children[0])
        oid_str, oid_path = _resolve_oid(self._oid(children))
        return MibObject(
            name=name, oid=oid_str, oid_path=oid_path,
            object_type="NOTIFICATION-TYPE",
            status=self._status(children),
            description=self._description(children),
        )

    # ------------------------------------------------------------------
    # TEXTUAL-CONVENTION
    # ------------------------------------------------------------------

    def textual_convention_assignment(self, children: list) -> MibType:
        name = str(children[0])
        return MibType(
            name=name,
            base_type=self._syntax(children) or "",
            description=self._description(children),
        )

    # ------------------------------------------------------------------
    # Type assignment  TypeName ::= SomeBaseType
    # ------------------------------------------------------------------

    def type_assignment(self, children: list) -> MibType:
        name = str(children[0])
        return MibType(name=name, base_type=self._syntax(children) or "")

    # ------------------------------------------------------------------
    # Value assignment
    # ------------------------------------------------------------------

    def value_assignment(self, children: list) -> MibObject | None:
        name = str(children[0])
        oid_list = self._oid(children)
        if oid_list:
            oid_str, oid_path = _resolve_oid(oid_list)
            return MibObject(name=name, oid=oid_str, oid_path=oid_path,
                             object_type="OBJECT IDENTIFIER")
        return None

    # ------------------------------------------------------------------
    # GROUP / COMPLIANCE / CAPABILITIES
    # ------------------------------------------------------------------

    def object_group_assignment(self, children: list) -> MibObject:
        return self._simple_oid_object(children, "OBJECT-GROUP")

    def notification_group_assignment(self, children: list) -> MibObject:
        return self._simple_oid_object(children, "NOTIFICATION-GROUP")

    def module_compliance_assignment(self, children: list) -> MibObject:
        return self._simple_oid_object(children, "MODULE-COMPLIANCE")

    def agent_capabilities_assignment(self, children: list) -> MibObject:
        return self._simple_oid_object(children, "AGENT-CAPABILITIES")

    def trap_type_assignment(self, children: list) -> MibObject:
        name = str(children[0])
        number = next(
            (str(c) for c in children if isinstance(c, Token) and c.type == "NUMBER"), "0"
        )
        return MibObject(name=name, oid=number, oid_path=[int(number)],
                         object_type="TRAP-TYPE")

    def assignment(self, children: list):
        return children[0] if children else None

    # ------------------------------------------------------------------
    # Clause wrappers — produce typed info objects (no string sniffing)
    # ------------------------------------------------------------------

    def description_clause(self, children: list) -> _DescriptionInfo:
        return _DescriptionInfo(_unquote(children[0]))

    def max_access_clause(self, children: list) -> _AccessInfo:
        return _AccessInfo(str(children[0]))

    def units_clause(self, children: list):
        return None  # not stored in model v1.0

    def reference_clause(self, children: list):
        return None  # not stored in model v1.0

    def display_hint_clause(self, children: list):
        return None

    def defval_clause(self, children: list):
        return None  # not stored in model v1.0

    def objects_clause(self, children: list):
        return None

    def index_clause(self, children: list):
        return children[0] if children else None

    def index_part(self, children: list) -> _IndexInfo:
        cols = [str(c.columns[0]) if isinstance(c, _IndexInfo) else str(c)
                for c in children if not isinstance(c, type(None))]
        return _IndexInfo(cols)

    def index_list(self, children: list) -> _IndexInfo:
        return _IndexInfo([c for c in children if isinstance(c, str)])

    def index_item(self, children: list) -> str:
        # children may be ["IMPLIED", name] or just [name]
        return str(children[-1])

    def augments_part(self, children: list) -> _AugmentsInfo:
        return _AugmentsInfo(str(children[0]))

    def revision(self, children: list):
        return None

    def compliance_module(self, children: list):
        return None

    def mandatory_groups(self, children: list):
        return None

    def compliance_item(self, children: list):
        return None

    def compliance_group(self, children: list):
        return None

    def compliance_object(self, children: list):
        return None

    def capabilities_module(self, children: list):
        return None

    def variation(self, children: list):
        return None

    def trap_variables_clause(self, children: list):
        return None

    def object_list(self, children: list) -> list[str]:
        return [str(c) for c in children]

    def scalar_value(self, children: list):
        return None

    # ------------------------------------------------------------------
    # OID passthrough
    # ------------------------------------------------------------------

    def oid_value(self, children: list) -> list:
        return children

    def named_arc(self, children: list) -> Tree:
        return Tree("named_arc", children)

    def name_arc(self, children: list) -> Tree:
        return Tree("name_arc", children)

    def number_arc(self, children: list) -> Tree:
        return Tree("number_arc", children)

    # ------------------------------------------------------------------
    # Syntax type rules — each returns a _SyntaxInfo (typed, not a plain string)
    # ------------------------------------------------------------------

    def syntax_type(self, children: list) -> _SyntaxInfo:
        val = children[0] if children else ""
        if isinstance(val, _SyntaxInfo):
            return val
        return _SyntaxInfo(str(val))

    def integer_type(self, _):           return _SyntaxInfo("INTEGER")
    def octet_string_type(self, _):      return _SyntaxInfo("OCTET STRING")
    def oid_type(self, _):               return _SyntaxInfo("OBJECT IDENTIFIER")
    def null_type(self, _):              return _SyntaxInfo("NULL")
    def ip_address_type(self, _):        return _SyntaxInfo("IpAddress")
    def counter32_type(self, _):         return _SyntaxInfo("Counter32")
    def counter64_type(self, _):         return _SyntaxInfo("Counter64")
    def gauge32_type(self, _):           return _SyntaxInfo("Gauge32")
    def unsigned32_type(self, _):        return _SyntaxInfo("Unsigned32")
    def timeticks_type(self, _):         return _SyntaxInfo("TimeTicks")
    def opaque_type(self, _):            return _SyntaxInfo("Opaque")
    def integer32_type(self, _):         return _SyntaxInfo("Integer32")
    def network_address_type(self, _):   return _SyntaxInfo("NetworkAddress")
    def counter_type(self, _):           return _SyntaxInfo("Counter")
    def gauge_type(self, _):             return _SyntaxInfo("Gauge")
    def bits_type(self, _):              return _SyntaxInfo("BITS")
    def sequence_type(self, _):          return _SyntaxInfo("SEQUENCE")
    def choice_type(self, _):            return _SyntaxInfo("CHOICE")

    def named_type(self, children: list) -> _SyntaxInfo:
        return _SyntaxInfo(str(children[0]))

    def sequence_of_type(self, children: list) -> _SyntaxInfo:
        return _SyntaxInfo(f"SEQUENCE OF {children[0]}")

    def status_value(self, children: list) -> _StatusInfo:
        return _StatusInfo(str(children[0]))

    def smiv1_status_value(self, children: list) -> _StatusInfo:
        return _StatusInfo(str(children[0]))

    def access_value(self, children: list) -> _AccessInfo:
        return _AccessInfo(str(children[0]))

    def syntax_clause(self, children: list) -> _SyntaxInfo | None:
        return next((c for c in children if isinstance(c, _SyntaxInfo)), None)

    def write_syntax_clause(self, children: list):
        return None

    def min_access_clause(self, children: list):
        return None

    def access_clause(self, children: list):
        return None

    def creation_requires_clause(self, children: list):
        return None

    # ------------------------------------------------------------------
    # Private typed extractors — no string sniffing anywhere
    # ------------------------------------------------------------------

    def _oid(self, children: list) -> list:
        for child in children:
            if isinstance(child, list) and child and isinstance(child[0], Tree):
                return child
        return []

    def _syntax(self, children: list) -> str | None:
        info = next((c for c in children if isinstance(c, _SyntaxInfo)), None)
        return info.value if info else None

    def _description(self, children: list) -> str | None:
        info = next((c for c in children if isinstance(c, _DescriptionInfo)), None)
        return info.value if info else None

    def _status(self, children: list) -> str | None:
        info = next((c for c in children if isinstance(c, _StatusInfo)), None)
        return info.value if info else None

    def _access(self, children: list) -> str | None:
        info = next((c for c in children if isinstance(c, _AccessInfo)), None)
        return info.value if info else None

    def _simple_oid_object(self, children: list, object_type: str) -> MibObject:
        name = str(children[0])
        oid_str, oid_path = _resolve_oid(self._oid(children))
        return MibObject(name=name, oid=oid_str, oid_path=oid_path, object_type=object_type)
