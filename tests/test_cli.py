import json

from repair_agent.cli import _drive


class FakeAgent:
    def __init__(self, run_dir, resumed):
        self.run_dir = run_dir
        self.resumed = resumed
        self.decisions = []

    def run(self):
        raise AssertionError("A resumed run must not call run() first.")

    def resume(self, decision=None):
        self.decisions.append(decision)
        return self.resumed


def test_resume_reads_saved_approval_and_prompts(tmp_path, monkeypatch):
    paused = {
        "status": "paused",
        "reason": "approval_required",
        "pending_approval": {"edits": [{"path": "calculator.py"}]},
    }
    (tmp_path / "summary.json").write_text(json.dumps(paused), encoding="utf-8")
    completed = {"status": "success", "success": True, "paused": False}
    agent = FakeAgent(tmp_path, completed)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    summary = _drive(agent, resume=True)

    assert summary["success"]
    assert agent.decisions == [True]
