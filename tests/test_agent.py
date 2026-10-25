import json
import subprocess
from pathlib import Path

from repair_agent.agent import RepairAgent
from repair_agent.config import Settings
from repair_agent.models import ModelResponse, ToolCall


class ScriptedModel:
    def __init__(self, responses):
        self._responses = iter(responses)

    def complete(self, messages, tools):
        return next(self._responses)


def scripted_call(step, name, arguments):
    call_id = f"call-{step}"
    raw = json.dumps(arguments)
    return ModelResponse(
        assistant_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": raw},
                }
            ],
        },
        content="",
        tool_calls=[ToolCall(call_id, name, arguments)],
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


def scripted_stop():
    return ModelResponse(
        assistant_message={"role": "assistant", "content": "I stopped."},
        content="I stopped.",
        usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
    )


def make_repo(root: Path) -> Path:
    root.mkdir()
    (root / "calculator.py").write_text(
        "def add(left, right):\n    return left - right\n", encoding="utf-8"
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@localhost"], check=True
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "baseline"], check=True)
    return root


def settings(approve_edits=False):
    return Settings(
        api_key="offline",
        model="scripted",
        max_steps=5,
        test_timeout=20,
        approve_edits=approve_edits,
    )


def test_agent_repairs_in_worktree_and_emits_patch(tmp_path):
    repo = make_repo(tmp_path / "repo")
    model = ScriptedModel(
        [
            scripted_call(
                1,
                "edit_file",
                {
                    "path": "calculator.py",
                    "old_string": "return left - right",
                    "new_string": "return left + right",
                },
            ),
            scripted_call(2, "run_tests", {"target": "tests/test_calculator.py::test_add"}),
        ]
    )
    run_dir = tmp_path / "run"
    summary = RepairAgent(
        model, settings(), repo, "tests/test_calculator.py::test_add", run_dir
    ).run()

    assert summary["success"] and summary["reason"] == "all_tests_passed"
    assert summary["steps"] == 2 and summary["tokens"]["total_tokens"] == 30
    assert "return left + right" in (run_dir / "final.patch").read_text(encoding="utf-8")
    assert "return left - right" in (repo / "calculator.py").read_text(encoding="utf-8")
    assert not (run_dir / "worktree").exists()
    events = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
    assert any(event["event"] == "full_suite_verification" for event in events)


def test_failed_attempt_is_rolled_back(tmp_path):
    repo = make_repo(tmp_path / "repo")
    model = ScriptedModel(
        [
            scripted_call(
                1,
                "edit_file",
                {
                    "path": "calculator.py",
                    "old_string": "return left - right",
                    "new_string": "return left * right",
                },
            ),
            scripted_stop(),
        ]
    )
    run_dir = tmp_path / "run"
    summary = RepairAgent(
        model, settings(), repo, "tests/test_calculator.py::test_add", run_dir
    ).run()

    assert not summary["success"] and summary["rollback_performed"]
    assert "return left * right" in (run_dir / "attempt.patch").read_text(encoding="utf-8")
    assert (run_dir / "final.patch").read_text(encoding="utf-8") == ""
    assert "return left - right" in (repo / "calculator.py").read_text(encoding="utf-8")


def test_edit_approval_pauses_and_resume_continues(tmp_path):
    repo = make_repo(tmp_path / "repo")
    model = ScriptedModel(
        [
            scripted_call(
                1,
                "edit_file",
                {
                    "path": "calculator.py",
                    "old_string": "return left - right",
                    "new_string": "return left + right",
                },
            ),
            scripted_call(2, "run_tests", {"target": "tests/test_calculator.py::test_add"}),
        ]
    )
    run_dir = tmp_path / "run"
    agent = RepairAgent(
        model, settings(approve_edits=True), repo, "tests/test_calculator.py::test_add", run_dir
    )

    paused = agent.run()
    assert paused["status"] == "paused" and paused["reason"] == "approval_required"
    assert paused["resumable"] and (run_dir / "checkpoints.sqlite").exists()
    assert (run_dir / "worktree").exists()
    assert paused["pending_approval"]["edits"][0]["path"] == "calculator.py"

    summary = agent.resume(decision=True)
    assert summary["success"] and summary["reason"] == "all_tests_passed"
    assert not (run_dir / "worktree").exists()
    context = json.loads((run_dir / "run_context.json").read_text(encoding="utf-8"))
    assert context["active"] is False
    events = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
    assert any(event["event"] == "human_approval" and event["approved"] for event in events)


def test_rejected_edit_is_rolled_back_after_resume(tmp_path):
    repo = make_repo(tmp_path / "repo")
    model = ScriptedModel(
        [
            scripted_call(
                1,
                "edit_file",
                {
                    "path": "calculator.py",
                    "old_string": "return left - right",
                    "new_string": "return left * right",
                },
            )
        ]
    )
    run_dir = tmp_path / "run"
    agent = RepairAgent(
        model, settings(approve_edits=True), repo, "tests/test_calculator.py::test_add", run_dir
    )

    assert agent.run()["status"] == "paused"
    summary = agent.resume(decision=False)

    assert not summary["success"] and summary["reason"] == "edit_rejected_by_user"
    assert summary["rollback_performed"]
    assert (run_dir / "final.patch").read_text(encoding="utf-8") == ""
    assert "return left - right" in (repo / "calculator.py").read_text(encoding="utf-8")
