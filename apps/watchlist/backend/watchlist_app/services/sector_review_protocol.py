"""Bind compact factual-review receipts to one immutable proposed draft.

This module only translates the wire protocol. Financial/source validation stays
in sector_fact_review's canonical checks and the existing publication boundary.
"""
from copy import deepcopy


_RESEARCH_ITEMS = ("modules", "questions", "catalysts", "forecasts", "forecast_reviews", "lessons")
_REFERENCES = ("theme_id", "event_key", "pm_note_id", "pm_note_revision", "forecast_key",
               "forecast_version_id", "related_research_update_id", "module_key")


def _object(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


def _variant(decision, **properties):
    return _object({"decision": {"const": decision}, **properties})


def _inline_schema(schema):
    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return inline(schema["$defs"][value["$ref"].removeprefix("#/$defs/")])
        return {key: inline(item) for key, item in value.items() if key != "$defs"}
    return inline(schema)


def _typed(schema, kind):
    return schema if schema.get("type") == kind else next(item for item in schema["anyOf"] if item.get("type") == kind)


def _patch_fields(original, properties, *, immutable=(), citations=False):
    allowed = set(original) - set(immutable)
    if citations:
        allowed.add("source_ids")
    fields = {key: deepcopy(value) for key, value in properties.items() if key in allowed}
    if citations and "source_ids" not in original:
        fields["source_ids"]["minItems"] = 1
    return fields


def review_receipt_schema(reviewed, canonical_schema, eligible_reflection_sources):
    """The sole provider protocol; canonical full objects are internal only."""
    canonical = _inline_schema(canonical_schema)["properties"]["reviews"]["items"]["properties"]
    reason = {"type": "string", "minLength": 1, "description": "Specific factual reason for this correction or rejection; do not repeat the draft."}

    def scalar(value_schema):
        return {"oneOf": [_variant("accept"), _variant("correct", reason=reason, value=value_schema)]}

    def object_receipt(original, fields, *, reject=None, omissions=False, immutable=(), required=()):
        if original is None:
            return {"type": "null"}
        variants = [_variant("accept")]
        if reject:
            variants.append(_variant(reject, reason=reason))
        patch = _object(fields, [])
        properties = {"reason": reason, "patch": patch}
        if omissions:
            properties["omit_fields"] = {"type": "array", "uniqueItems": True,
                "items": {"enum": sorted(set(original) - set(immutable) - set(required))} if set(original) - set(immutable) - set(required) else False,
                "description": "Reject these proposed fields explicitly. Unmentioned fields retain the bound draft values; this is not a null withdrawal."}
        else:
            patch["minProperties"] = 1
        if fields or omissions and original:
            correction = _variant("correct", **properties)
            if omissions:
                correction["anyOf"] = [{"properties": {"patch": {"minProperties": 1}}},
                                       {"properties": {"omit_fields": {"minItems": 1}}}]
            variants.append(correction)
        return {"oneOf": variants}

    def keyed_receipts(rows, key, properties, immutable, reject, *, research=False, required=()):
        items = [object_receipt(row, _patch_fields(row, properties, immutable=immutable, citations=research),
                    reject=reject, omissions=research, immutable=immutable, required=required) for row in rows]
        bindings = list(range(len(rows))) if key == "index" else [row[key] for row in rows]
        # Equal field contracts can share a schema without repeating it for
        # every fact. Different proposed field sets remain bound separately.
        same_contract = bool(items) and all(item == items[0] for item in items[1:])
        candidates = items[:1] if same_contract else items
        for index, item in enumerate(candidates):
            for variant in item["oneOf"]:
                variant["properties"][key] = {"enum": bindings} if same_contract else {"const": bindings[index]}
                variant["required"].append(key)
        return {"type": "array", "minItems": len(rows), "maxItems": len(rows),
                "items": candidates[0] if len(candidates) == 1 else {"oneOf": candidates} if candidates else False}

    def research_fields(original, properties):
        fields = _patch_fields(original, properties, citations=True)
        for field in ("investment_view", "mandate_update"):
            if field not in original:
                continue
            value = original[field]
            fields[field] = object_receipt(value, _patch_fields(value or {},
                _typed(properties[field], "object")["properties"], citations=field == "investment_view"),
                reject="reject", omissions=True, required=_typed(properties[field], "object").get("required", ()))
        for field in ("facts", *_RESEARCH_ITEMS):
            if field not in original:
                continue
            key = "index" if field == "facts" else "key"
            fields[field] = keyed_receipts(original[field], key, properties[field]["items"]["properties"],
                () if field == "facts" else ("key", *_REFERENCES), "reject", research=True,
                required=properties[field]["items"].get("required", ()))
            fields[field]["description"] = ("Every original record requires exactly one decision. "
                + ("index is the zero-based position in this frozen draft, never the output order. Rejected facts are removed from the new fact list; rejecting all means an empty new fact list."
                   if field == "facts" else "key binds the original proposed item. Reject cancels this item's proposed change, preserving an existing notebook item under that key."))
        return fields

    candidates = []
    event_properties = _typed(canonical["decisions"]["items"]["properties"]["event"], "object")["properties"]
    theme_properties = _typed(canonical["themes"], "array")["items"]["properties"]
    research_properties = _typed(canonical["research"], "object")["properties"]
    reflection_properties = _typed(canonical["reflection"], "object")["properties"]
    for row in reviewed:
        reflection = row.get("reflection")
        fields = _patch_fields(reflection or {}, reflection_properties, immutable=("reviewed_update_ids",))
        # Reflection source eligibility is scoped by the canonical evidence and
        # cutoff predicate, even when the proposed receipt omitted citations.
        allowed = eligible_reflection_sources.get(row["instrument_id"], [])
        fields["source_ids"] = {"type": "array", "items": {"type": "string", "enum": allowed}} if allowed else {"type": "array", "maxItems": 0, "items": {"type": "string"}}
        candidates.append(_object({
            "instrument_id": {"const": row["instrument_id"]},
            **{field: scalar(canonical[field]) for field in ("summary", "change_kind", "coverage")},
            "decisions": keyed_receipts(row.get("events", []), "event_key", event_properties, ("event_key", "action"), "remove"),
            "research": object_receipt(row.get("research"), research_fields(row.get("research") or {}, research_properties), reject="reject", omissions=True),
            "themes": keyed_receipts(row.get("themes", []), "theme_key", theme_properties, ("theme_key", "theme_id"), "reject"),
            "reflection": object_receipt(reflection, fields),
        }))
    return _object({"reviews": {"type": "array", "minItems": len(candidates), "maxItems": len(candidates),
        "items": candidates[0] if len(candidates) == 1 else {"oneOf": candidates} if candidates else False}})


def _exact_fields(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(f"Review receipt requires exactly these fields: {', '.join(sorted(required))}")


def _index_exact(rows, originals, key):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Review receipts require a {key} list")
    keys = [row.get(key) for row in rows]
    expected = list(range(len(originals))) if key == "index" else [row[key] for row in originals]
    kind = int if key == "index" else str
    if (any(type(value) is not kind for value in keys) or len(set(expected)) != len(expected)
            or len(keys) != len(expected) or set(keys) != set(expected)):
        raise ValueError(f"Review must decide every original {key} exactly once")
    return {row[key]: row for row in rows}


def _reason(receipt):
    value = receipt["reason"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Review corrections and rejections require a factual reason")


def expand_review_receipts(reviewed, result):
    """Expand explicit decisions only; never infer acceptance from omission."""
    _exact_fields(result, ("reviews",))
    receipts = _index_exact(result["reviews"], reviewed, "instrument_id")

    def expand(original, receipt, *, key=None, reject=None, immutable=(), research=False, omissions=False, citations=False):
        if original is None:
            if receipt is not None:
                raise ValueError("Review cannot invent an object absent from the draft")
            return None
        if not isinstance(receipt, dict):
            raise ValueError("Every proposed object requires an explicit review receipt")
        binding = (key,) if key else ()
        decision = receipt.get("decision")
        if decision == "accept":
            _exact_fields(receipt, (*binding, "decision"))
            return deepcopy(original)
        if reject and decision == reject:
            _exact_fields(receipt, (*binding, "decision", "reason"))
            _reason(receipt)
            return None
        if decision != "correct":
            raise ValueError("Invalid review receipt decision")
        _exact_fields(receipt, (*binding, "decision", "reason", "patch", *(("omit_fields",) if omissions else ())))
        _reason(receipt)
        patch = receipt["patch"]
        allowed = set(original) - set(immutable)
        if citations or "reviewed_update_ids" in immutable:
            allowed.add("source_ids")
        if not isinstance(patch, dict) or set(patch) - allowed:
            raise ValueError("Review patch cannot change identity or introduce unproposed fields")
        omitted = receipt["omit_fields"] if omissions else []
        if (not isinstance(omitted, list) or any(not isinstance(key, str) for key in omitted)
                or len(set(omitted)) != len(omitted) or not set(omitted).issubset(original)
                or set(omitted).intersection(patch) or set(omitted).intersection(immutable)):
            raise ValueError("Research omitted fields must be distinct proposed fields outside the patch")
        if not patch and not omitted:
            raise ValueError("Correct requires an explicit nonempty correction")
        if citations and "source_ids" in patch and "source_ids" not in original and not patch["source_ids"]:
            raise ValueError("Added research citations must be nonempty")
        if research:
            patch, rejected = research_patch(original, patch)
            omitted = [*omitted, *rejected]
        return {**{key: deepcopy(value) for key, value in original.items() if key not in omitted}, **deepcopy(patch)}

    def research_patch(original, patch):
        result, rejected = deepcopy(patch), []
        for field in ("investment_view", "mandate_update"):
            if field not in patch:
                continue
            value = expand(original[field], patch[field], reject="reject", omissions=True,
                           citations=field == "investment_view")
            if value is None and original[field] is not None:
                result.pop(field)
                rejected.append(field)
            else:
                result[field] = value  # A bound original null remains a proposed withdrawal.
        for field in ("facts", *_RESEARCH_ITEMS):
            if field not in patch:
                continue
            key = "index" if field == "facts" else "key"
            receipts = _index_exact(patch[field], original[field], key)
            retained = []
            for index, item in enumerate(original[field]):
                value = expand(item, receipts[index if key == "index" else item[key]], key=key, reject="reject",
                    immutable=() if field == "facts" else ("key", *_REFERENCES), omissions=True, citations=True)
                if value is not None:
                    retained.append(value)
            result[field] = retained
        return result, rejected

    def scalar(original, receipt, field):
        if not isinstance(receipt, dict):
            raise ValueError("Summary, change kind and coverage require explicit review decisions")
        if receipt.get("decision") == "accept":
            _exact_fields(receipt, ("decision",))
            return deepcopy(original)
        _exact_fields(receipt, ("decision", "reason", "value"))
        if receipt["decision"] != "correct":
            raise ValueError("Invalid scalar review decision")
        _reason(receipt)
        value = receipt["value"]
        if ((field == "summary" and not isinstance(value, str))
                or (field == "change_kind" and value not in ("none", "knowledge", "investment"))
                or (field == "coverage" and (not isinstance(value, list) or any(not isinstance(item, str) for item in value)))):
            raise ValueError("Review correction has the wrong field type")
        return deepcopy(value)

    expanded = []
    for original in reviewed:
        row = receipts[original["instrument_id"]]
        _exact_fields(row, ("instrument_id", "summary", "change_kind", "coverage", "decisions", "research", "themes", "reflection"))
        events = _index_exact(row["decisions"], original.get("events", []), "event_key")
        themes = _index_exact(row["themes"], original.get("themes", []), "theme_key")
        decisions, kept_themes = [], []
        for event in original.get("events", []):
            receipt = events[event["event_key"]]
            value = expand(event, receipt, key="event_key", reject="remove", immutable=("event_key", "action"))
            decisions.append({"event_key": event["event_key"], "decision": "remove" if value is None else "keep",
                "reason": receipt.get("reason", "Explicitly accepted the bound draft event."), "event": value})
        for theme in original.get("themes", []):
            value = expand(theme, themes[theme["theme_key"]], key="theme_key", reject="reject", immutable=("theme_key", "theme_id"))
            if value is not None:
                kept_themes.append(value)
        expanded.append({"instrument_id": original["instrument_id"],
            **{field: scalar(original.get(field, [] if field == "coverage" else "none" if field == "change_kind" else ""), row[field], field)
               for field in ("summary", "change_kind", "coverage")},
            "decisions": decisions, "themes": kept_themes,
            "research": expand(original.get("research"), row["research"], reject="reject", research=True, omissions=True, citations=True),
            "reflection": expand(original.get("reflection"), row["reflection"], immutable=("reviewed_update_ids",))})
    return {"reviews": expanded}
