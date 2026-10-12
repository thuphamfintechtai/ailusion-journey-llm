"""Quy ước key Redis."""

from ailusion.core.redis import agent_log_key


def test_agent_log_key_shape():
    assert agent_log_key("trip-1:A") == "agentlog:trip-1:A"
