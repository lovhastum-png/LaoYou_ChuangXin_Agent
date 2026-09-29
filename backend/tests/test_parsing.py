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


def test_spoken_phrasing_without_the_word_reminder():
    """口语里不会说"提醒"，会说"叫我""闹钟"；方言归一后也是这种句式。"""
    assert parse_reminder("七点叫我吃药")["time"] == "07:00"
    assert parse_reminder("明天早上八点吃降压片")["time"] == "08:00"
    assert parse_reminder("早上六点半提醒我喝降压药")["time"] == "06:30"


def test_dialect_normalized_sentence_end_to_end():
    """东北话经模型归一化后的句子，应能直接进入提醒提案。"""
    proposal = parse_reminder("你帮我设个闹钟，明天早上七点叫我吃药，吃降压片。")
    assert proposal is not None
    assert proposal["time"] == "07:00"
    assert proposal["medicine"] == "降压片"


def test_medicine_name_prefers_specific_over_generic():
    """"吃药吃降压片"里"药"是泛称，"降压片"才是药名。"""
    assert parse_reminder("你帮我整个闹钟明个早上七点叫我吃药吃降压片")["medicine"] == "降压片"
    # 只有泛称时保留默认值，不把"药"当成具体药名
    assert parse_reminder("七点叫我吃药")["medicine"] == "药物"


def test_non_reminder_speech_is_not_hijacked():
    """放宽触发词后，日常闲聊不能被误认成提醒。"""
    assert parse_reminder("今天天气怎么样") is None
    assert parse_reminder("你好通通") is None
    assert parse_reminder("我想吃火锅") is None
    assert parse_reminder("晚上十点该睡觉了") is None
