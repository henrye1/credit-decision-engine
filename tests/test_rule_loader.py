from pathlib import Path

from openpyxl import Workbook

from projects.loan_scoring.rule_loader import parse_workbook


def test_parse_workbook_extracts_rule_rows(tmp_path: Path):
    workbook_path = tmp_path / "rules.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Commercial Rules"
    sheet.append(["Phase", "RuleID", "Description", "Action", "Disputable", "Rule Applied"])
    sheet.append(["Early_rules_1", "Pac_001", "Turnover < 100 000", "Decline", "No", "In_decision_engine"])
    workbook.save(workbook_path)

    parsed = parse_workbook(workbook_path)

    assert parsed["sheet_count"] == 1
    assert parsed["rules"][0]["phase"] == "Early_rules_1"
    assert parsed["rules"][0]["rule_id"] == "Pac_001"
    assert parsed["rules"][0]["action"] == "Decline"
    assert parsed["rules"][0]["disputable"] is False
