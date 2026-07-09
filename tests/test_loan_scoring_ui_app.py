from projects.loan_scoring.ui_app import score_applicant


def test_score_applicant_returns_expected_fields():
    result = score_applicant(
        {
            "debt": 25000.0,
            "income": 50000.0,
            "credit_used": 4000.0,
            "credit_limit": 10000.0,
            "bureau_score": 650.0,
            "adverse_accounts": 2,
            "delinquency_count": 1,
            "default_history": 0,
        }
    )

    assert result["dti_ratio"] == 0.5
    assert result["utilization_rate"] == 0.4
    assert result["credit_score_estimate"] == 660.0
    assert result["decision"] == "refer"
    assert result["risk_band"] == "medium"
    assert "bureau_score_low" in result["reason_codes"]
