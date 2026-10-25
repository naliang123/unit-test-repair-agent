from repair_agent.tools import ToolBox


def test_read_edit_and_path_guard(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("value = 1\nvalue = 1\n", encoding="utf-8")
    tools = ToolBox(tmp_path)

    read = tools.read_file("sample.py", 1, 2)
    assert read["ok"] and "1: value = 1" in read["content"].replace("    ", " ")

    ambiguous = tools.execute(
        "edit_file", {"path": "sample.py", "old_string": "value = 1", "new_string": "value = 2"}
    )
    assert not ambiguous["ok"] and "exactly once" in ambiguous["error"]

    unique = tools.edit_file("sample.py", "value = 1\nvalue = 1", "value = 2\nvalue = 3")
    assert unique["ok"]
    assert source.read_text(encoding="utf-8") == "value = 2\nvalue = 3\n"

    escape = tools.execute("read_file", {"path": "../outside.txt"})
    assert not escape["ok"] and "escapes" in escape["error"]


def test_literal_search_and_test_execution(tmp_path):
    (tmp_path / "operations.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_operations.py").write_text(
        "from operations import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    tools = ToolBox(tmp_path, test_timeout=20)

    found = tools.search_code("return a + b")
    assert found["ok"] and found["matches"][0].startswith("operations.py:2:")

    result = tools.run_tests("tests/test_operations.py")
    assert result["ok"] and result["passed"]
    forbidden = tools.execute(
        "edit_file",
        {
            "path": "tests/test_operations.py",
            "old_string": "assert add(2, 3) == 5",
            "new_string": "assert True",
        },
    )
    assert not forbidden["ok"] and "forbidden" in forbidden["error"]
    assert tools.execute("run_tests", {"target": "--help"})["ok"] is False
