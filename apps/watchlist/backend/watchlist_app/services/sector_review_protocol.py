"""Bind compact factual-review receipts to one immutable proposed draft.

This module only translates the wire protocol. Financial/source validation stays
in sector_fact_review's canonical checks and the existing publication boundary.
"""
from copy import deepcopy


_RESEARCH_ITEMS = ("questions", "catalysts", "forecasts", "forecast_reviews", "lessons")
_REFERENCES = ("theme_id", "event_key", "pm_note_id", "pm_note_revision", "forecast_key",
               "forecast_version_id", "related_research_update_id")


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


def _research_fields(original, properties):
    fields = _patch_fields(original, properties, citations=True)
    if isinstance(original.get("investment_view"), dict):
        fields["investment_view"] = _object(_patch_fields(original["investment_view"],
            _typed(properties["investment_view"], "object")["properties"], citations=True), [])
    for field in _RESEARCH_ITEMS:
        if field not in original:
            continue
        items = []
        for row in original[field]:
            fields_by_name = _patch_fields(row, properties[field]["items"]["properties"], citations=True)
            for key in ("key", *_REFERENCES):
                if key in row:
                    fields_by_name[key] = {**fields_by_name[key], "const": row[key]}
            # Existing required fields still apply; omitted optional fields stay
            # omitted and never acquire schema defaults during expansion.
            required = [key for key in properties[field]["items"].get("required", []) if key in fields_by_name]
            items.append(_object(fields_by_name, required))
        fields[field] = {"type": "array", "maxItems": len(items),
                         "items": items[0] if len(items) == 1 else {"oneOf": items} if items else False}
    for field, value in fields.items():
        value["description"] = ("Complete replacement of this proposed top-level field. Include every proposed child change you retain; "
            "omitted child fields or keyed rows explicitly reject those proposed changes and preserve the existing notebook. "
            "Other top-level fields outside patch retain this draft's proposal. " + value.get("description", ""))
    return fields


def review_receipt_schema(reviewed, canonical_schema, eligible_reflection_sources):
    """The sole provider protocol; canonical full objects are internal only."""
    canonical = _inline_schema(canonical_schema)["properties"]["reviews"]["items"]["properties"]
    reason = {"type": "string", "minLength": 1, "description": "Specific factual reason for this correction or rejection; do not repeat the draft."}

    def scalar(value_schema):
        return {"oneOf": [_variant("accept"), _variant("correct", reason=reason, value=value_schema)]}

    def object_receipt(original, fields, *, reject=None, research=False):
        if original is None:
            return {"type": "null"}
        variants = [_variant("accept")]
        if reject:
            variants.append(_variant(reject, reason=reason))
        patch = _object(fields, [])
        properties = {"reason": reason, "patch": patch}
        if research:
            properties["omit_fields"] = {"type": "array", "uniqueItems": True,
                "items": {"enum": list(original)} if original else False,
                "description": "Reject these proposed top-level changes, preserving the existing notebook; not a null withdrawal."}
        else:
            patch["minProperties"] = 1
        if fields or research and original:
            correction = _variant("correct", **properties)
            if research:
                correction["anyOf"] = [{"properties": {"patch": {"minProperties": 1}}},
                                       {"properties": {"omit_fields": {"minItems": 1}}}]
            variants.append(correction)
        return {"oneOf": variants}

    def keyed_receipts(rows, key, properties, immutable, reject):
        items = []
        for row in rows:
            item = object_receipt(row, _patch_fields(row, properties, immutable=immutable), reject=reject)
            for variant in item["oneOf"]:
                variant["properties"][key] = {"const": row[key]}
                variant["required"].append(key)
            items.append(item)
        return {"type": "array", "minItems": len(rows), "maxItems": len(rows),
                "items": items[0] if len(items) == 1 else {"oneOf": items} if items else False}

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
            "research": object_receipt(row.get("research"), _research_fields(row.get("research") or {}, research_properties), reject="reject", research=True),
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
    expected = [row[key] for row in originals]
    if (any(not isinstance(value, str) for value in keys) or len(set(expected)) != len(expected)
            or len(keys) != len(expected) or set(keys) != set(expected)):
        raise ValueError(f"Review must decide every original {key} exactly once")
    return {row[key]: row for row in rows}


def _reason(receipt):
    value = receipt["reason"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Review corrections and rejections require a factual reason")


def _check_research_patch(original, patch):
    def item_fields(value, proposed):
        if not isinstance(value, dict) or set(value) - (set(proposed) | {"source_ids"}):
            raise ValueError("Research corrections cannot introduce unproposed fields")
        if "source_ids" in value and "source_ids" not in proposed and not value["source_ids"]:
            raise ValueError("Added research citations must be nonempty")
        for key in _REFERENCES:
            if key in value and value[key] != proposed.get(key):
                raise ValueError("Research corrections cannot retarget original references")
    item_fields(patch, original)
    if "investment_view" in patch:
        proposed, value = original.get("investment_view"), patch["investment_view"]
        if isinstance(proposed, dict):
            if value is None:
                raise ValueError("Reject a proposed investment view with omit_fields, not a new null withdrawal")
            item_fields(value, proposed)
        elif value != proposed:
            raise ValueError("Research corrections cannot replace a proposed withdrawal")
    for field in _RESEARCH_ITEMS:
        if field not in patch:
            continue
        originals = {row["key"]: row for row in original[field]}
        values = patch[field]
        if (not isinstance(values, list) or any(not isinstance(row, dict) or not isinstance(row.get("key"), str) for row in values)
                or len({row["key"] for row in values}) != len(values)):
            raise ValueError("Research item corrections require unique original keys")
        for value in values:
            if value["key"] not in originals:
                raise ValueError("Research corrections cannot invent a keyed item")
            item_fields(value, originals[value["key"]])


def expand_review_receipts(reviewed, result):
    """Expand explicit decisions only; never infer acceptance from omission."""
    _exact_fields(result, ("reviews",))
    receipts = _index_exact(result["reviews"], reviewed, "instrument_id")

    def expand(original, receipt, *, key=None, reject=None, immutable=(), research=False):
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
        _exact_fields(receipt, (*binding, "decision", "reason", "patch", *(("omit_fields",) if research else ())))
        _reason(receipt)
        patch = receipt["patch"]
        allowed = set(original) - set(immutable)
        if research or "reviewed_update_ids" in immutable:
            allowed.add("source_ids")
        if not isinstance(patch, dict) or set(patch) - allowed:
            raise ValueError("Review patch cannot change identity or introduce unproposed fields")
        omitted = receipt["omit_fields"] if research else []
        if (not isinstance(omitted, list) or any(not isinstance(key, str) for key in omitted)
                or len(set(omitted)) != len(omitted) or not set(omitted).issubset(original)
                or set(omitted).intersection(patch)):
            raise ValueError("Research omitted fields must be distinct proposed fields outside the patch")
        if not patch and not omitted:
            raise ValueError("Correct requires an explicit nonempty correction")
        if research:
            _check_research_patch(original, patch)
        return {**{key: deepcopy(value) for key, value in original.items() if key not in omitted}, **deepcopy(patch)}

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
            "research": expand(original.get("research"), row["research"], reject="reject", research=True),
            "reflection": expand(original.get("reflection"), row["reflection"], immutable=("reviewed_update_ids",))})
    return {"reviews": expanded}
