from repair_agent.benchmark import load_tasks, validate_benchmark


def test_benchmark_contains_twenty_unique_mutations():
    tasks = load_tasks()
    assert len(tasks) == 20
    assert len({task.id for task in tasks}) == 20
    assert all(task.old != task.new for task in tasks)


def test_clean_fixture_passes_and_selected_mutants_fail():
    results = validate_benchmark(["M01", "M05"])
    assert len(results) == 3
    assert all(result["valid"] for result in results), results
