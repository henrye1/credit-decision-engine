import pytest

from projects.loan_scoring.ui_app import (
    WORKFLOW_STEPS,
    app,
    templates,
    workflow_context,
)


def test_workflow_steps_cover_assessment_process_in_order():
    keys = [step["key"] for step in WORKFLOW_STEPS]
    assert keys == ["pull_data", "configure_rules", "score_client", "decision"]


def test_workflow_steps_have_render_fields():
    for step in WORKFLOW_STEPS:
        assert step["label"]
        assert step["caption"]
        assert "href" in step


def test_workflow_context_marks_done_active_and_upcoming():
    ctx = workflow_context("score_client")
    states = {step["key"]: step["state"] for step in ctx["workflow_steps"]}
    assert states == {
        "pull_data": "done",
        "configure_rules": "done",
        "score_client": "active",
        "decision": "todo",
    }
    assert ctx["current_step"] == "score_client"


def test_workflow_context_rejects_unknown_step():
    with pytest.raises(KeyError):
        workflow_context("not_a_step")


def test_data_route_registered():
    paths = {route.path for route in app.routes}
    assert "/data" in paths


@pytest.mark.parametrize(
    ("template_name", "current_step"),
    [
        ("data.html", "pull_data"),
        ("rules.html", "configure_rules"),
        ("index.html", "score_client"),
    ],
)
def test_pages_render_stepper_with_active_step(template_name, current_step):
    ctx = workflow_context(current_step)
    ctx.setdefault("rule_sets", [])
    ctx.setdefault("parsed_rules", [])
    ctx.setdefault("client_configs", [])
    ctx.setdefault("data_fields", [])
    html = templates.get_template(template_name).render(**ctx)

    for step in WORKFLOW_STEPS:
        assert step["label"] in html

    active_label = next(s["label"] for s in WORKFLOW_STEPS if s["key"] == current_step)
    assert 'aria-current="step"' in html
    assert active_label in html.split('aria-current="step"')[1].split("</li>")[0]


def test_result_page_marks_decision_step_active():
    ctx = workflow_context("decision")
    html = templates.get_template("result.html").render(
        payload={"debt": 1.0},
        result={"decision": "approve", "risk_band": "low", "reason_codes": []},
        **ctx,
    )
    assert 'aria-current="step"' in html
    assert "Decision" in html.split('aria-current="step"')[1].split("</li>")[0]
