import pytest

from ris_kpi import pipelines


@pytest.fixture
def fake_steps(monkeypatch):
    calls = []

    def step(name, fail=False):
        def run(**kwargs):
            calls.append(name)
            if fail:
                raise RuntimeError(f"{name} broke")
        return run

    def install(fail=()):
        monkeypatch.setattr(pipelines, "STEPS", {
            "extract": (step("extract", "extract" in fail), []),
            "ris_view": (step("ris_view", "ris_view" in fail), ["extract"]),
            "ris_trends": (step("ris_trends", "ris_trends" in fail), []),
        })
        return calls
    return install


def status(results):
    return {name: r[0] for name, r in results.items()}


def test_all_steps_in_order(fake_steps):
    calls = fake_steps()
    assert status(pipelines.run_full()) == {"extract": "OK", "ris_view": "OK", "ris_trends": "OK"}
    assert calls == ["extract", "ris_view", "ris_trends"]


def test_stops_after_failure(fake_steps):
    calls = fake_steps(fail={"extract"})
    assert status(pipelines.run_full()) == {"extract": "FAILED", "ris_view": "SKIPPED", "ris_trends": "SKIPPED"}
    assert calls == ["extract"]


def test_keep_going_still_skips_dependents(fake_steps):
    calls = fake_steps(fail={"extract"})
    result = status(pipelines.run_full(keep_going=True))
    assert result == {"extract": "FAILED", "ris_view": "SKIPPED", "ris_trends": "OK"}
    assert calls == ["extract", "ris_trends"]


def test_only_treats_unselected_dependency_as_done(fake_steps):
    calls = fake_steps()
    assert status(pipelines.run_full(only=["ris_view"])) == {"ris_view": "OK"}
    assert calls == ["ris_view"]
