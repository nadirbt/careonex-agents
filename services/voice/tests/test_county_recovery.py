from nova_sonic.tools import possible_county, normalize_county, intake_next_question_sync


def test_phonetic_candidate_needs_confirmation():
    assert normalize_county("mom mouse") is None
    assert possible_county("mom mouse") == "Monmouth"
    result = intake_next_question_sync({"relationship":"son", "care_recipient_age":"74", "kind_of_help":"bathing", "county":"mom mouse"})
    assert result["field"] == "county"
    assert result["requires_confirmation"] is True
    assert result["ask"] == "Did you mean Monmouth County?"
    assert "county" not in result.get("known", {})


def test_generic_moms_and_state_are_not_counties():
    assert possible_county("my mom's") is None
    assert possible_county("New Jersey") is None
    assert normalize_county("New Jersey") is None


def test_repeating_county_offers_other_way_to_answer():
    result = intake_next_question_sync({"repeat_field":"county", "retry_count":3})
    assert result["field"] == "county"
    assert "spell" in result["ask"].lower()


def test_user_must_explicitly_confirm_county():
    result = intake_next_question_sync({"relationship":"son", "care_recipient_age":"74", "county":"Monmouth"})
    assert result["field"] != "county"


def test_optional_county_can_be_skipped_without_fabricating():
    out = intake_next_question_sync({"relationship":"son", "care_recipient_age":"74", "county":"not sure"})
    assert out["field"] == "kind_of_help"
    assert out["remaining"] > 0
