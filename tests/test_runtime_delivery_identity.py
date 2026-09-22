from areal_rsi.modules.research_runtime import scoped_call_id


def test_restarted_mcp_counter_is_unique_per_delivery_but_retry_stable():
    first = scoped_call_id('message-one', 'tool_mcp_1')
    assert first == scoped_call_id('message-one', 'tool_mcp_1')
    assert first != scoped_call_id('message-two', 'tool_mcp_1')
    assert first != scoped_call_id('message-one', 'tool_mcp_2')
    assert scoped_call_id(None, 'legacy') == 'legacy'
