from app.services import parse_reminder


def test_parse_numeric_evening_reminder():
    proposal = parse_reminder("每天晚上八点提醒我吃降压药")
    assert proposal is not None
    assert proposal["time"] == "20:00"
    assert proposal["medicine"] == "降压药"
    assert proposal["dose"] == ""


def test_parse_colon_reminder():
    proposal = parse_reminder("每天 07:30 提醒我服用维生素")
    assert proposal is not None
    assert proposal["time"] == "07:30"


def test_parse_half_and_quarter_without_silent_rounding():
    assert parse_reminder("每天晚上八点半提醒我吃降压药")["time"] == "20:30"
    assert parse_reminder("每天早上七点一刻提醒我吃维生素")["time"] == "07:15"
    assert parse_reminder("每天早上七点零五分提醒我吃维生素")["time"] == "07:05"
    assert parse_reminder("每天提醒我吃降压药") is None
