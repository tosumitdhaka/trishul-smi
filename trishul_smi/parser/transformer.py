"""Lark Transformer: converts a parsed Tree into a MibModule dataclass.

Design: every grammar rule method receives already-transformed children.
Syntax types (integer_type, octet_string_type, …) return _SyntaxInfo objects
directly, so assignment methods use typed extractors — no string sniffing.
Description/status/access fields use typed wrappers for the same reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from lark import Transformer, Token, Tree

from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.models.mib_type import MibType
from trishul_smi.parser._constants import SMIv2_MARKERS


# ---------------------------------------------------------------------------
# Internal typed wrapper dataclasses
# ---------------------------------------------------------------------------

@dataclass
class _SyntaxInfo:
    value: str

@dataclass
class _DescriptionInfo:
    value: str

@dataclass
class _StatusInfo:
    value: str

@dataclass
class _AccessInfo:
    value: str

@dataclass
class _IndexInfo:
    columns: list[str]

@dataclass
class _AugmentsInfo:
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

    Named arcs without a number (e.g. 'mib-2') are kept in the dotted
    string but skipped in int_path — full numeric resolution happens later
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

        language = "SMIv2" if any(m in imports for m in SMIv2_MARKERS) else "SMIv1"

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
        return {str(children[1]): children[0]}

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
        return MibObject(name=name, oid=oid_str, oid_path=oid_path,
                         object_type="MODULE-IDENTITY")

    # ------------------------------------------------------------------
    # OBJECT-IDENTITY
    # ------------------------------------------------------------------

    def object_identity_assignment(self, children: list) -> MibObject:
        name = str(children[0])
        oid_str, oid_path = _resolve_oid(self._oid(children))
        return MibObject(name=name, oid=oid_str, oid_path=oid_path,
                         object_type="OBJECT-IDENTITY",
                         status=self._status(children),
                         description=self._description(children))

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
        return MibObject(name=name, oid=oid_str, oid_path=oid_path,
                         object_type="NOTIFICATION-TYPE",
                         status=self._status(children),
                         description=self._description(children))

    # ------------------------------------------------------------------
    # TEXTUAL-CONVENTION
    # ------------------------------------------------------------------

    def textual_convention_assignment(self, children: list) -> MibType:
        name = str(children[0])
        return MibType(name=name, base_type=self._syntax(children) or "",
                       description=self._description(children))

    # ------------------------------------------------------------------
    # Type / Value assignments
    # ------------------------------------------------------------------

    def type_assignment(self, children: list) -> MibType:
        name = str(children[0])
        return MibType(name=name, base_type=self._syntax(children) or "")

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
    # Clause wrappers — typed info objects
    # ------------------------------------------------------------------

    def description_clause(self, children: list) -> _DescriptionInfo:
        return _DescriptionInfo(_unquote(children[0]))

    def max_access_clause(self, children: list) -> _AccessInfo:
        info = next((c for c in children if isinstance(c, _AccessInfo)), None)
        return info if info is not None else _AccessInfo(str(children[0]))

    def index_clause(self, children: list):
        return children[0] if children else None

    def index_part(self, children: list) -> _IndexInfo:
        # children[0] is _IndexInfo produced by index_list, which already
        # holds ALL column names. Bug fix: old code did c.columns[0] which
        # silently dropped columns 2..N in composite indexes.
        info = next((c for c in children if isinstance(c, _IndexInfo)), None)
        return info if info is not None else _IndexInfo([])

    def index_list(self, children: list) -> _IndexInfo:
        # children are strings from index_item rules
        return _IndexInfo([c for c in children if isinstance(c, str)])

    def index_item(self, children: list) -> str:
        # grammar: "IMPLIED"? LOWER_ID — take last child (always the identifier)
        return str(children[-1])

    def augments_part(self, children: list) -> _AugmentsInfo:
        return _AugmentsInfo(str(children[0]))

    def revision(self, _): return None
    def compliance_module(self, _): return None
    def mandatory_groups(self, _): return None
    def compliance_item(self, _): return None
    def compliance_group(self, _): return None
    def compliance_object(self, _): return None
    def capabilities_module(self, _): return None
    def variation(self, _): return None
    def trap_variables_clause(self, _): return None
    def units_clause(self, _): return None
    def reference_clause(self, _): return None
    def display_hint_clause(self, _): return None
    def defval_clause(self, _): return None
    def objects_clause(self, _): return None
    def scalar_value(self, _): return None
    def write_syntax_clause(self, _): return None
    def min_access_clause(self, _): return None
    def access_clause(self, _): return None
    def creation_requires_clause(self, _): return None

    def object_list(self, children: list) -> list[str]:
        return [str(c) for c in children]

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
    # Syntax type rules — each returns _SyntaxInfo
    # ------------------------------------------------------------------

    def syntax_type(self, children: list) -> _SyntaxInfo:
        val = children[0] if children else ""
        return val if isinstance(val, _SyntaxInfo) else _SyntaxInfo(str(val))

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

    # ------------------------------------------------------------------
    # Private typed extractors
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
