from datetime import datetime, timezone

from app.rules import evaluate_observation, is_night
from app.schemas import Rules


RULES = {
    "night_start": "22:00",
    "night_end": "06:00",
    "immobility_minutes": 60,
    "sleep_immobility_minutes": 180,
    "away_minutes": 120,
    "heart_rate_low": 50,
    "heart_rate_high": 120,
    "systolic_high": 160,
    "diastolic_high": 100,
}


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 8, hour, minute, tzinfo=timezone.utc)


def test_night_window_crosses_midnight():
    # Inputs are UTC; Asia/Shanghai local times are 23:00, 05:00 and 20:00.
    assert is_night(at(15), "22:00", "06:00")
    assert is_night(at(21), "22:00", "06:00")
    assert not is_night(at(12), "22:00", "06:00")


def test_fall_is_immediate_critical_and_keeps_source_boundary():
    events = evaluate_observation(
        kind="fall",
        value=None,
        duration_minutes=None,
        sleeping=None,
        occurred_at=at(10),
        rules=RULES,
    )
    assert [(event.kind, event.severity) for event in events] == [("fall", "critical")]


def test_sleeping_immobility_uses_sleep_threshold():
    assert evaluate_observation(
        kind="immobility", value=None, duration_minutes=100, sleeping=True, occurred_at=at(10), rules=RULES
    ) == []
    events = evaluate_observation(
        kind="immobility", value=None, duration_minutes=180, sleeping=True, occurred_at=at(10), rules=RULES
    )
    assert len(events) == 1
    assert events[0].severity == "warning"


def test_health_thresholds_only_trigger_outside_configured_bounds():
    assert evaluate_observation(
        kind="heart_rate", value=80, duration_minutes=None, sleeping=None, occurred_at=at(10), rules=RULES
    ) == []
    assert evaluate_observation(
        kind="heart_rate", value=130, duration_minutes=None, sleeping=None, occurred_at=at(10), rules=RULES
    )[0].kind == "heart_rate"
    assert evaluate_observation(
        kind="blood_pressure",
        value={"systolic": 150, "diastolic": 90},
        duration_minutes=None,
        sleeping=None,
        occurred_at=at(10),
        rules=RULES,
    ) == []
    assert evaluate_observation(
        kind="blood_pressure",
        value={"systolic": 160, "diastolic": 90},
        duration_minutes=None,
        sleeping=None,
        occurred_at=at(10),
        rules=RULES,
    )[0].severity == "critical"


def test_settings_reject_an_inverted_heart_rate_range():
    invalid = {**RULES, "heart_rate_low": 130, "heart_rate_high": 50}
    try:
        Rules.model_validate(invalid)
    except ValueError as error:
        assert "heart_rate_low" in str(error)
    else:
        raise AssertionError("inverted heart-rate range must be rejected")
