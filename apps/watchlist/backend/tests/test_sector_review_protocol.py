"""Compact receipts preserve the existing sparse financial-review boundary."""
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator

from watchlist_app.services import sector_fact_review as review
from watchlist_app.services.sector_review_protocol import expand_review_receipts


SOURCE = {"source_id": "original", "source_type": "public_source", "instrument_id": "gold",
          "published_at": "2026-09-01", "text": "A retained dated original."}


def proposed():
    return [{"instrument_id": "gold", "summary": "", "change_kind": "knowledge", "coverage": ["Known gap"],
        "events": [{"event_key": "disclosure", "action": "new", "direction": "uncertain", "title": "Disclosure",
            "body": "The retained original discloses a fact.", "confidence": "confirmed", "information_type": "fact",
            "recording_type": "new", "source_ids": ["original"], "theme_ids": ["question"]}],
        "research": {"investment_view": {"direction": "Conditional outlook", "risk": "Uncertain transmission"},
            "questions": [{"key": "demand", "question": "Does demand persist?", "assessment": "Need evidence",
                           "next_check": "Next disclosure", "pm_note_id": "pm-original", "pm_note_revision": 2}],
            "source_ids": ["original"]},
        "themes": [{"theme_key": "question", "title": "Demand", "question": "Does demand persist?", "source_ids": ["original"]}],
        "reflection": {"status": "reviewed", "summary": "Checked the original judgment", "reviewed_update_ids": ["research:old"],
                       "source_ids": ["original"]}}]


def accepted(rows):
    return {"reviews": [{"instrument_id": row["instrument_id"],
        **{field: {"decision": "accept"} for field in ("summary", "change_kind", "coverage")},
        "decisions": [{"event_key": item["event_key"], "decision": "accept"} for item in row.get("events", [])],
        "themes": [{"theme_key": item["theme_key"], "decision": "accept"} for item in row.get("themes", [])],
        **{field: {"decision": "accept"} if row.get(field) is not None else None for field in ("research", "reflection")}}
        for row in rows]}


def checked(rows, receipts):
    schema = review._review_schema(rows, [SOURCE])
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(receipts)
    value = expand_review_receipts(rows, receipts)
    review._Checks.model_validate(value)
    return review._apply_checks({"reviews": rows}, value, [SOURCE])["reviews"]


def test_explicit_accept_preserves_every_sparse_field_without_alias_or_schema_defaults():
    rows = proposed(); before = deepcopy(rows)
    result = checked(rows, accepted(rows))
    assert result == rows == before
    result[0]["research"]["investment_view"]["risk"] = "mutated caller copy"
    result[0]["events"][0]["source_ids"].append("mutated caller copy")
    assert rows == before
    assert len(json.dumps(accepted(rows))) < len(json.dumps(rows))


@pytest.mark.parametrize("field", ["summary", "change_kind", "coverage", "decisions", "research", "themes", "reflection"])
def test_no_omitted_instrument_section_defaults_to_accept(field):
    rows = proposed(); receipts = accepted(rows); del receipts["reviews"][0][field]
    with pytest.raises(ValueError):
        expand_review_receipts(rows, receipts)


@pytest.mark.parametrize("section,key", [("reviews", "instrument_id"), ("decisions", "event_key"), ("themes", "theme_key")])
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "invented"])
def test_every_original_identity_requires_exactly_one_receipt(section, key, mutation):
    rows = proposed(); receipts = accepted(rows)
    items = receipts["reviews"] if section == "reviews" else receipts["reviews"][0][section]
    if mutation == "missing": items.clear()
    elif mutation == "duplicate": items.append(deepcopy(items[0]))
    else: items[0][key] = "invented"
    with pytest.raises(ValueError, match="exactly once"):
        expand_review_receipts(rows, receipts)


@pytest.mark.parametrize("receipt", [
    {"decision": "accept", "patch": {"title": "ignored"}},
    {"decision": "remove", "reason": "unsupported", "patch": {"title": "ignored"}},
    {"decision": "correct", "reason": "unsupported", "patch": {}},
    {"decision": "correct", "reason": " ", "patch": {"title": "Correction"}},
    {"decision": "correct", "reason": "identity", "patch": {"event_key": "invented"}},
    {"decision": "correct", "reason": "identity", "patch": {"action": "resolved"}},
    {"decision": "correct", "reason": "unproposed", "patch": {"next_watch": "new proposal"}},
])
def test_ambiguous_or_unbound_event_decisions_are_rejected(receipt):
    rows = proposed(); receipts = accepted(rows)
    receipts["reviews"][0]["decisions"] = [{"event_key": "disclosure", **receipt}]
    with pytest.raises(ValueError): expand_review_receipts(rows, receipts)


def test_correction_only_changes_proposed_fields_and_old_source_checks_still_reject_unknown_evidence():
    rows = proposed(); receipts = accepted(rows)
    decision = receipts["reviews"][0]["decisions"][0]
    decision.update(decision="correct", reason="Attribute accurately", patch={"confidence": "reported"})
    result = checked(rows, receipts)
    assert result[0]["events"] == [{**rows[0]["events"][0], "confidence": "reported"}]
    decision["patch"]["source_ids"] = ["invented"]
    with pytest.raises(ValueError, match="source"): checked(rows, receipts)


def test_theme_rejection_keeps_existing_alias_cleanup_and_does_not_remove_supported_event():
    rows = proposed(); receipts = accepted(rows)
    receipts["reviews"][0]["themes"][0].update(decision="reject", reason="No supported continuing question")
    result = checked(rows, receipts)[0]
    assert result["themes"] == []
    assert result["events"][0]["theme_ids"] == []
    assert result["events"][0]["body"] == rows[0]["events"][0]["body"]


@pytest.mark.parametrize("receipt", [None, {"decision": "reject", "reason": "skip"},
    {"decision": "correct", "reason": "retarget", "patch": {"reviewed_update_ids": []}}])
def test_reflection_cannot_be_rejected_omitted_or_retargeted(receipt):
    rows = proposed(); receipts = accepted(rows); receipts["reviews"][0]["reflection"] = receipt
    with pytest.raises(ValueError): expand_review_receipts(rows, receipts)


def test_reflection_can_mark_insufficient_without_repeating_original_ids():
    rows = proposed(); receipts = accepted(rows)
    receipts["reviews"][0]["reflection"] = {"decision": "correct", "reason": "Known coverage gap",
        "patch": {"status": "insufficient_evidence", "summary": "Cannot verify the relevant update"}}
    result = checked(rows, receipts)[0]["reflection"]
    assert result == {**rows[0]["reflection"], "status": "insufficient_evidence", "summary": "Cannot verify the relevant update"}


def test_investment_field_patch_preserves_other_proposals_unless_explicitly_omitted():
    from watchlist_app.services.research_notebook import retain_notebook
    rows = proposed(); receipts = accepted(rows)
    previous = retain_notebook(review.ResearchNotebook(investment_view={"direction": "Prior direction", "risk": "Prior risk"}),
        None, {"original": SOURCE}, "old", "2026-09-01T00:00:00+00:00")
    view = {"decision": "correct", "reason": "Qualify only direction",
            "patch": {"direction": "Corrected direction"}, "omit_fields": []}
    receipts["reviews"][0]["research"] = {"decision": "correct", "reason": "A supported narrow correction",
        "patch": {"investment_view": view}, "omit_fields": []}
    result = checked(rows, receipts)[0]["research"]
    assert result["investment_view"] == {"direction": "Corrected direction", "risk": "Uncertain transmission"}
    view["omit_fields"] = ["risk"]
    result = checked(rows, receipts)[0]["research"]
    assert result["investment_view"] == {"direction": "Corrected direction"}
    saved = retain_notebook(review.ResearchNotebook.model_validate(result), previous, {"original": SOURCE},
        "new", "2026-09-20T00:00:00+00:00")
    assert saved["investment_view"]["direction"] == "Corrected direction"
    assert saved["investment_view"]["risk"] == "Prior risk"
    receipts["reviews"][0]["research"]["patch"]["investment_view"] = {"decision": "reject", "reason": "Reject this view proposal"}
    assert "investment_view" not in checked(rows, receipts)[0]["research"]


def test_research_omit_reject_and_proposed_null_withdrawal_remain_distinct():
    rows = proposed(); receipts = accepted(rows)
    receipts["reviews"][0]["research"] = {"decision": "correct", "reason": "Unsupported view",
        "patch": {}, "omit_fields": ["investment_view"]}
    assert "investment_view" not in checked(rows, receipts)[0]["research"]
    receipts["reviews"][0]["research"] = {"decision": "reject", "reason": "Unsupported revision"}
    assert checked(rows, receipts)[0]["research"] is None
    rows[0]["research"] = {"investment_view": None}
    assert checked(rows, accepted(rows))[0]["research"] == {"investment_view": None}


@pytest.mark.parametrize("patch", [
    {"investment_view": None}, {"fundamental_view": "unproposed"},
    {"investment_view": {"decision": "correct", "reason": "test", "patch": {"attractiveness": "unproposed"}, "omit_fields": []}},
    {"questions": [{"key": "invented", "decision": "accept"}]},
    {"questions": [{"key": "demand", "decision": "correct", "reason": "test", "patch": {"pm_note_id": "different"}, "omit_fields": []}]},
    {"questions": [{"key": "demand", "decision": "correct", "reason": "test", "patch": {"pm_note_revision": 3}, "omit_fields": []}]},
    {"questions": [{"key": "demand", "decision": "accept"}, {"key": "demand", "decision": "accept"}]},
])
def test_research_patch_cannot_invent_fields_items_withdrawals_or_change_pm_binding(patch):
    rows = proposed(); receipts = accepted(rows)
    receipts["reviews"][0]["research"] = {"decision": "correct", "reason": "Correction", "patch": patch, "omit_fields": []}
    with pytest.raises(ValueError): expand_review_receipts(rows, receipts)


def test_compact_research_citation_exception_still_uses_canonical_evidence_checks():
    rows = proposed(); rows[0]["research"] = {"investment_view": {"risk": "Observed risk"}}
    receipts = accepted(rows)
    receipts["reviews"][0]["research"] = {"decision": "correct", "reason": "Bind observed premise to original",
        "patch": {"source_ids": ["original"], "investment_view": {"decision": "correct", "reason": "Bind original", "patch": {"source_ids": ["original"]}, "omit_fields": []}}, "omit_fields": []}
    assert checked(rows, receipts)[0]["research"]["source_ids"] == ["original"]
    receipts["reviews"][0]["research"]["patch"]["source_ids"] = ["invented"]
    with pytest.raises(ValueError): checked(rows, receipts)


def test_provider_acceptance_does_not_bypass_canonical_reflection_instrument_scope():
    rows = proposed(); rows[0]["reflection"]["source_ids"] = ["other-instrument"]
    result = expand_review_receipts(rows, accepted(rows))
    wrong = {**SOURCE, "source_id": "other-instrument", "instrument_id": "other"}
    with pytest.raises(ValueError): review._apply_checks({"reviews": rows}, result, [SOURCE, wrong])


def test_schema_inlining_keeps_business_titles_and_accepts_catalyst_title_corrections():
    rows = proposed()
    rows[0]["research"]["catalysts"] = [{"key": "release", "title": "Original title", "scheduled_at": "2026-09-22",
        "status": "scheduled", "relevance": "May clarify outlook", "scenarios": ["Conditional"], "next_check": "Read release", "source_ids": ["original"]}]
    receipts = accepted(rows); row = receipts["reviews"][0]
    row["decisions"][0].update(decision="correct", reason="Avoid overstatement", patch={"title": "Supported event title"})
    row["themes"][0].update(decision="correct", reason="Clarify question", patch={"title": "Supported theme title"})
    row["research"] = {"decision": "correct", "reason": "Use the original schedule name", "omit_fields": [],
        "patch": {"catalysts": [{"key": "release", "decision": "correct", "reason": "Original title",
            "patch": {"title": "Supported release title"}, "omit_fields": []}]}}
    value = checked(rows, receipts)[0]
    assert value["events"][0]["title"] == "Supported event title"
    assert value["themes"][0]["title"] == "Supported theme title"
    assert value["research"]["catalysts"][0]["title"] == "Supported release title"


def facts():
    return [{"subject": "gold", "metric": "price", "period": day, "value": value,
             "unit": "USD/oz", "comparison": "", "uncertainty": "", "source_ids": ["original"]}
            for day, value in (("2026-09-01", "100"), ("2026-09-02", "101"))]


def record_correction(patch, *, omit_fields=()):
    return {"decision": "correct", "reason": "Specific correction", "patch": patch, "omit_fields": list(omit_fields)}


def test_facts_and_keyed_receipts_follow_original_order_and_preserve_untouched_fields():
    rows = proposed(); rows[0]["research"]["facts"] = facts()
    rows[0]["research"]["questions"].append({"key": "second", "question": "Other?", "assessment": "Unknown", "next_check": "Later"})
    receipts = accepted(rows)
    receipts["reviews"][0]["research"] = record_correction({
        "facts": [{"index": 1, **record_correction({"uncertainty": "Revised"})}, {"index": 0, "decision": "accept"}],
        "questions": [{"key": "second", "decision": "accept"}, {"key": "demand", **record_correction({"assessment": "Check interpretation"})}]})
    value = checked(rows, receipts)[0]["research"]
    assert value["facts"] == [facts()[0], {**facts()[1], "uncertainty": "Revised"}]
    assert value["questions"] == [{**rows[0]["research"]["questions"][0], "assessment": "Check interpretation"}, rows[0]["research"]["questions"][1]]


@pytest.mark.parametrize("index", [True, False, -1, 2, 0.0, "0", None])
def test_fact_indices_must_be_exact_integers_bound_to_the_frozen_draft(index):
    rows = proposed(); rows[0]["research"]["facts"] = facts(); receipts = accepted(rows)
    receipts["reviews"][0]["research"] = record_correction({"facts": [
        {"index": index, "decision": "accept"}, {"index": 1, "decision": "accept"}]})
    with pytest.raises(ValueError, match="exactly once"): expand_review_receipts(rows, receipts)


@pytest.mark.parametrize("entries", [[], [{"index": 0, "decision": "accept"}],
    [{"index": 0, "decision": "accept"}, {"index": 0, "decision": "accept"}]])
def test_fact_receipts_cannot_omit_or_duplicate_originals(entries):
    rows = proposed(); rows[0]["research"]["facts"] = facts(); receipts = accepted(rows)
    receipts["reviews"][0]["research"] = record_correction({"facts": entries})
    with pytest.raises(ValueError, match="exactly once"): expand_review_receipts(rows, receipts)


@pytest.mark.parametrize("omitted", ["key", "pm_note_id", "pm_note_revision"])
def test_record_omission_cannot_remove_identity_or_pm_version(omitted):
    rows = proposed(); receipts = accepted(rows)
    receipts["reviews"][0]["research"] = record_correction({"questions": [
        {"key": "demand", **record_correction({}, omit_fields=[omitted])}]})
    with pytest.raises(ValueError): expand_review_receipts(rows, receipts)


def test_record_omission_is_explicit_disjoint_and_still_subject_to_canonical_required_fields():
    rows = proposed(); rows[0]["research"]["facts"] = facts(); receipts = accepted(rows)
    first = {"index": 0, **record_correction({"value": "Corrected"}, omit_fields=["value"])}
    receipts["reviews"][0]["research"] = record_correction({"facts": [first, {"index": 1, "decision": "accept"}]})
    with pytest.raises(ValueError): expand_review_receipts(rows, receipts)
    first["patch"] = {}; first["omit_fields"] = ["metric"]
    expanded = expand_review_receipts(rows, receipts)
    with pytest.raises(ValueError): review._Checks.model_validate(expanded)


def test_fact_rejection_replaces_the_observation_set_but_whole_field_omission_preserves_it():
    from watchlist_app.services.research_notebook import retain_notebook
    rows = proposed(); rows[0]["research"]["facts"] = facts(); receipts = accepted(rows)
    old = retain_notebook(review.ResearchNotebook(facts=facts()), None, {"original": SOURCE}, "old", "2026-09-01T00:00:00+00:00")
    receipts["reviews"][0]["research"] = record_correction({"facts": [
        {"index": i, "decision": "reject", "reason": "Unsupported observation"} for i in range(2)]})
    delta = checked(rows, receipts)[0]["research"]
    assert delta["facts"] == []
    saved = retain_notebook(review.ResearchNotebook.model_validate(delta), old, {"original": SOURCE}, "new", "2026-09-20T00:00:00+00:00")
    assert saved["facts"] == []
    receipts["reviews"][0]["research"] = record_correction({}, omit_fields=["facts"])
    delta = checked(rows, receipts)[0]["research"]
    assert "facts" not in delta
    saved = retain_notebook(review.ResearchNotebook.model_validate(delta), old, {"original": SOURCE}, "new", "2026-09-20T00:00:00+00:00")
    assert saved["facts"] == old["facts"]


def test_keyed_item_rejection_keeps_previous_notebook_item_and_optional_omission_keeps_previous_field():
    from watchlist_app.services.research_notebook import retain_notebook
    rows = proposed(); receipts = accepted(rows); question = rows[0]["research"]["questions"][0]
    old = retain_notebook(review.ResearchNotebook(questions=[{**question, "tracking_reason": "Previous reason"}]),
        None, {"original": SOURCE}, "old", "2026-09-01T00:00:00+00:00")
    question["tracking_reason"] = "Proposed reason"
    receipts["reviews"][0]["research"] = record_correction({"questions": [
        {"key": "demand", **record_correction({"assessment": "Corrected"}, omit_fields=["tracking_reason"])}]})
    delta = checked(rows, receipts)[0]["research"]
    saved = retain_notebook(review.ResearchNotebook.model_validate(delta), old, {"original": SOURCE}, "new", "2026-09-20T00:00:00+00:00")
    assert saved["questions"][0]["tracking_reason"] == "Previous reason"
    receipts["reviews"][0]["research"]["patch"]["questions"] = [{"key": "demand", "decision": "reject", "reason": "Reject proposed revision"}]
    delta = checked(rows, receipts)[0]["research"]
    saved = retain_notebook(review.ResearchNotebook.model_validate(delta), old, {"original": SOURCE}, "new", "2026-09-20T00:00:00+00:00")
    assert saved["questions"] == old["questions"]


def test_shared_record_schema_does_not_extend_the_fields_of_a_different_proposal():
    rows = proposed()
    rows[0]["research"]["questions"].append({"key": "second", "question": "Other?", "assessment": "Unknown", "next_check": "Later"})
    receipts = accepted(rows)
    receipts["reviews"][0]["research"] = record_correction({"questions": [
        {"key": "demand", "decision": "accept"},
        {"key": "second", **record_correction({"pm_note_id": "pm-original"})}]})
    validator = Draft202012Validator(review._review_schema(rows, [SOURCE]))
    assert not validator.is_valid(receipts)
    with pytest.raises(ValueError): expand_review_receipts(rows, receipts)
