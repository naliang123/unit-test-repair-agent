import pytest

from repair_agent.mcp import parse_mcp_servers


def test_parse_mcp_servers_empty():
    assert parse_mcp_servers("") == []
    assert parse_mcp_servers(None) == []


def test_parse_mcp_servers_stdio():
    specs = parse_mcp_servers(
        '[{"name":"memory","command":"python","args":["-m","examples.demo_mcp_server"]}]'
    )
    assert len(specs) == 1
    assert specs[0].name == "memory"
    assert specs[0].transport == "stdio"
    assert specs[0].command == "python"
    assert specs[0].args == ["-m", "examples.demo_mcp_server"]


def test_parse_mcp_servers_rejects_bad_transport():
    with pytest.raises(ValueError):
        parse_mcp_servers('[{"name":"x","transport":"bogus","command":"python"}]')
