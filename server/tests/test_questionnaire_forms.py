import pytest

from app.modules.questionnaire import FORM_VERSION, get_form, list_forms, validate_answers


MORNING_ANSWERS = {
    "bedtime": "23:30",
    "wake_time": "07:00",
    "alertness": "rather_alert",
    "sleep_quality": 1,
    "sleep_recovery": 2,
    "sleep_continuity": 0,
    "sleep_sufficiency": -1,
    "sleep_onset_ease": 3,
    "wake_ease": -2,
    "sleep_influences": ["environment", "work_or_life"],
}

EVENING_ANSWERS = {
    "alertness": "somewhat_sleepy",
    "day_energy": 1,
    "day_mood": 2,
    "day_activation": -1,
    "nap_duration": "within_30_minutes",
    "device_status": {"gps": "normal", "lighting": "brief_interruption"},
}


def test_form_registry_is_external_and_complete():
    assert FORM_VERSION == "formal_v1"
    assert [form["key"] for form in list_forms()] == ["morning", "evening"]
    assert len(get_form("morning")["questions"]) == 10
    assert len(get_form("evening")["questions"]) == 6
    assert validate_answers(get_form("morning"), MORNING_ANSWERS) == MORNING_ANSWERS
    assert validate_answers(get_form("evening"), EVENING_ANSWERS) == EVENING_ANSWERS
    assert not {"participant_id", "date_local"}.intersection(MORNING_ANSWERS)


@pytest.mark.parametrize(
    ("form_key", "answers"),
    [
        ("morning", {**MORNING_ANSWERS, "bedtime": "25:00"}),
        ("morning", {**MORNING_ANSWERS, "sleep_quality": 4}),
        ("morning", {**MORNING_ANSWERS, "sleep_influences": ["none", "environment"]}),
        ("evening", {**EVENING_ANSWERS, "device_status": {"gps": "normal"}}),
    ],
)
def test_form_validation_rejects_invalid_answers(form_key, answers):
    with pytest.raises(ValueError):
        validate_answers(get_form(form_key), answers)

