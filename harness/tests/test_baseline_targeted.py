from harness.engine import ANAPHORIC, OVERVIEW, RULE, Index, from_batches, source_documents


def index():
    messages = from_batches("c", [
        {"request_id": "r", "session_id": "s", "messages": [
            {"role": "user", "content": "Where is the Atlas meeting?"},
            {"role": "assistant", "content": "It中文 is in Dublin."},
        ]},
        {"request_id": "r2", "session_id": "s2", "messages": [
            {"role": "user", "content": "old milestone"},
            {"role": "user", "content": "new milestone"},
        ]},
    ])
    return Index(messages, source_documents("c", messages))


def test_intent_boundaries_match_javascript_ascii_word_boundary_behavior():
    assert OVERVIEW.search("summary中文")
    assert RULE.search("rules中文")
    assert ANAPHORIC.search("It中文 is in Dublin.")
    assert not OVERVIEW.search("presummary")
    assert not RULE.search("overrules")


def test_chinese_literal_intents_remain_active_and_anaphoric_context_retrieves_answer():
    idx = index()
    try:
        assert [h.document.message_id for h in idx.apply("Atlas meeting")][:2] == ["r:0", "r:1"]
        assert len(idx.apply("请概述变化趋势。")) == 4
    finally:
        idx.close()
