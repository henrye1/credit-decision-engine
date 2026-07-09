from pathlib import Path

from projects.loan_scoring.ui_app import create_rule_set, load_rule_sets, publish_rule_set


def test_rule_set_can_be_created_and_published(tmp_path: Path):
    store_path = tmp_path / "rule_sets.json"

    rule_set = create_rule_set(
        name="Commercial Policy",
        category="Commercial",
        rules=[
            {
                "phase": "Early_rules_1",
                "rule_id": "Pac_001",
                "description": "Turnover < 100 000",
                "action": "Decline",
                "disputable": False,
                "rule_applied": "In_decision_engine",
                "review_status": "pass",
            }
        ],
        store_path=store_path,
    )

    assert rule_set["status"] == "draft"
    persisted = load_rule_sets(store_path)
    assert persisted[0]["name"] == "Commercial Policy"

    published = publish_rule_set(rule_set["id"], store_path)
    assert published["status"] == "published"
