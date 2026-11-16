import pytest
from conftest import plan_for
from pydantic import ValidationError

from codex_agy.planning import TaskPlan


def test_validation_and_topological_order(tmp_path):
    data = plan_for(tmp_path, 2)
    data["milestones"].reverse()
    data["tasks"].reverse()
    plan = TaskPlan.model_validate(data)
    assert plan.milestone_dependencies() == {"m1": {"m0"}, "m0": set()}
    assert plan.tasks_for("m1")[0].task_id == "t1"


@pytest.mark.parametrize("bad", ["../x", "C:/Windows/x", ".git/config", "x:stream", "a//b", "/abs"])
def test_path_restrictions(tmp_path, bad):
    data = plan_for(tmp_path)
    data["tasks"][0]["expected_files"] = [bad]
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_cross_milestone_cycle(tmp_path):
    data = plan_for(tmp_path, 2)
    data["milestones"][0]["dependencies"] = ["m1"]
    with pytest.raises(ValidationError, match="Circular milestone"):
        TaskPlan.model_validate(data)


def test_hybrid_checkpoint_validation(tmp_path):
    data = plan_for(tmp_path, 2)
    data.update(review_strategy="hybrid", work_size="medium", verification_gate=True)
    data["tasks"][1]["critical_change"] = "authentication"
    assert len(TaskPlan.model_validate(data).milestones) == 2
    data["tasks"][0]["critical_change"] = "database"
    with pytest.raises(ValidationError, match="preceding review"):
        TaskPlan.model_validate(data)


def test_hybrid_small_and_medium_checkpoint_counts(tmp_path):
    data = plan_for(tmp_path)
    data.update(review_strategy="hybrid", verification_gate=True)
    assert TaskPlan.model_validate(data).work_size == "small"
    data["work_size"] = "medium"
    with pytest.raises(ValidationError, match="checkpoint_exception_reason"):
        TaskPlan.model_validate(data)
    data["checkpoint_exception_reason"] = (
        "Existing implementation makes a final-only check sufficient."
    )
    assert len(TaskPlan.model_validate(data).milestones) == 1
