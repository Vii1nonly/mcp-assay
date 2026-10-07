"""JSON Schema rules shared by loading and grading.

A suite's schema is checked when the suite loads and used again when a result is
graded. Both stages ask this module which draft the schema uses, which keywords
that draft acts on and which formats it can check, so a schema that loads is
always graded under the same rules.
"""

from functools import cache
from urllib.parse import urljoin

from jsonschema import (
    Draft4Validator,
    Draft6Validator,
    Draft7Validator,
    Draft201909Validator,
    Draft202012Validator,
    validators,
)
from jsonschema_specifications import REGISTRY
from referencing import Specification
from referencing.jsonschema import specification_with

DEFAULT = Draft202012Validator

# Oldest to newest: other_draft() relies on this order to suggest the newest draft.
_NAMES = {
    Draft4Validator: "draft-04",
    Draft6Validator: "draft-06",
    Draft7Validator: "draft-07",
    Draft201909Validator: "draft 2019-09",
    Draft202012Validator: "draft 2020-12",
}

# Listed by the draft's meta-schema for compatibility, but its validator does not
# act on them: a schema relying on them would check nothing.
_IGNORED = {
    Draft201909Validator: {"dependencies"},
    Draft202012Validator: {"dependencies", "$recursiveRef", "$recursiveAnchor"},
}

# Defined by the draft but missing from the meta-schema jsonschema bundles.
_MISSING = {Draft7Validator: {"writeOnly"}}

# In these drafts a `$ref` replaces its siblings: they are never evaluated.
_REF_OVERRIDES_SIBLINGS = {Draft4Validator, Draft6Validator, Draft7Validator}
# Keywords that assert nothing, so they lose nothing when a `$ref` overrides them.
_NON_ASSERTING = {
    "$ref", "$schema", "$id", "id", "definitions", "$comment", "title", "description",
    "default", "examples", "readOnly", "writeOnly", "contentEncoding", "contentMediaType",
}  # fmt: skip

# A keyword that is only evaluated together with another one. On its own it checks
# nothing. Each entry: keyword -> (the keyword it needs, whether that one must be a list).
_ITEMS = ("items", True)
# (Draft-04's own meta-schema already refuses exclusiveMaximum without maximum.)
_NEEDS = {
    Draft4Validator: {"additionalItems": _ITEMS},
    Draft6Validator: {"additionalItems": _ITEMS},
    Draft7Validator: {"additionalItems": _ITEMS, "then": ("if", False), "else": ("if", False)},
    Draft201909Validator: {
        "additionalItems": _ITEMS,
        "then": ("if", False),
        "else": ("if", False),
        "minContains": ("contains", False),
        "maxContains": ("contains", False),
    },
    Draft202012Validator: {
        "then": ("if", False),
        "else": ("if", False),
        "minContains": ("contains", False),
        "maxContains": ("contains", False),
    },
}

# What an author likely meant by a keyword from an older draft.
_REPLACEMENTS = {Draft202012Validator: {"dependencies": "dependentRequired or dependentSchemas"}}


class DialectError(ValueError):
    """A `$schema` value the harness cannot grade under."""


def validator_class(schema) -> type:
    """The validator for the draft named by the schema's `$schema`, default 2020-12."""
    if not isinstance(schema, dict) or "$schema" not in schema:
        return DEFAULT
    uri = schema["$schema"]
    if not isinstance(uri, str):
        raise DialectError("$schema must be a string")
    cls = validators.validator_for(schema, default=None)
    if cls is None:
        raise DialectError(f"'{uri}' is not a JSON Schema draft ({_supported()})")
    if cls not in _NAMES:
        raise DialectError(f"'{uri}' is not supported ({_supported()})")
    return cls


def draft_name(cls: type) -> str:
    return _NAMES[cls]


@cache
def keywords(cls: type) -> frozenset[str]:
    """Every keyword this draft acts on or defines as an annotation."""
    listed = _meta_schema_keywords(_meta_id(cls).rstrip("#"))
    acted_on = set(cls.VALIDATORS) | listed | _MISSING.get(cls, set())
    return frozenset(acted_on - _IGNORED.get(cls, set()))


def other_draft(keyword: str, cls: type) -> type | None:
    """The newest supported draft that acts on `keyword`, when `cls` does not."""
    for other in reversed(list(_NAMES)):
        if other is not cls and keyword in keywords(other):
            return other
    return None


def format_names(cls: type) -> frozenset[str]:
    """Formats this draft's checker can actually check."""
    return frozenset(cls.FORMAT_CHECKER.checkers)


def specification(cls: type) -> Specification:
    """How `$ref`, `$id` and subschemas work in this draft."""
    return specification_with(_meta_id(cls))


def ref_overrides_siblings(cls: type) -> bool:
    """Whether a `$ref` in this draft replaces the keywords beside it."""
    return cls in _REF_OVERRIDES_SIBLINGS


def ignored_beside_ref(node: dict, cls: type) -> list[str]:
    """Asserting keywords beside a `$ref` that this draft never evaluates."""
    if not ref_overrides_siblings(cls) or "$ref" not in node:
        return []
    return [key for key in node if key not in _NON_ASSERTING and key in keywords(cls)]


def unevaluated(node: dict, cls: type) -> list[tuple[str, str]]:
    """(keyword, reason) for each keyword that checks nothing where it sits."""
    found = []
    for keyword, (needed, must_be_list) in _NEEDS.get(cls, {}).items():
        if keyword not in node:
            continue
        if needed not in node:
            found.append((keyword, f"is ignored without '{needed}'"))
        elif must_be_list and not isinstance(node[needed], list):
            found.append((keyword, f"is ignored unless '{needed}' is a list"))
    return found


def replacement(keyword: str, cls: type) -> str | None:
    """What an author likely meant, in this draft, by a keyword from another draft."""
    return _REPLACEMENTS.get(cls, {}).get(keyword)


def validator(schema):
    """A validator for `schema` that also checks `format`."""
    cls = validator_class(schema)
    return cls(schema, format_checker=cls.FORMAT_CHECKER)


def _meta_id(cls: type) -> str:
    # Draft-04 names its meta-schema with `id`; later drafts use `$id`.
    return cls.META_SCHEMA.get("$id") or cls.META_SCHEMA["id"]


def _meta_schema_keywords(uri: str) -> set[str]:
    # 2019-09 and 2020-12 split their meta-schema into vocabularies joined by allOf.
    found, seen, todo = set(), set(), [uri]
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        contents = REGISTRY.contents(current)
        found |= set(contents.get("properties", {}))
        todo += [urljoin(current, part["$ref"]) for part in contents.get("allOf", [])]
    return found


def _supported() -> str:
    return "supported: " + ", ".join(_NAMES.values())
