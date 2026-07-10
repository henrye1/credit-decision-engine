import pytest

from projects.loan_scoring.ui_app import (
    DATA_FIELDS,
    app,
    create_client_config,
    create_rule_set,
    delete_client_config,
    derive_schema,
    load_client_configs,
    templates,
    workflow_context,
)


def _rules():
    return [
        {"rule_id": "Pac_001", "description": "Turnover < 100 000", "phase": "Early_rules_1", "action": "Decline"},
        {"rule_id": "Pic_001", "description": "Internal arrears <= 12 months", "phase": "Early_rules_2", "action": "Decline"},
        {"rule_id": "Fac_001", "description": "FICA", "phase": "Late_rules_1", "action": "Decline"},
    ]


def test_data_fields_cover_scoring_inputs():
    fields = {entry["field"] for entry in DATA_FIELDS}
    assert {
        "debt",
        "income",
        "credit_used",
        "credit_limit",
        "bureau_score",
        "adverse_accounts",
        "delinquency_count",
        "default_history",
    } <= fields
    for entry in DATA_FIELDS:
        assert entry["key"] == f"{entry['table']}.{entry['field']}"
        assert entry["label"]
        assert entry["dtype"]


def test_derive_schema_reflects_only_selected_rules():
    schema = derive_schema(
        _rules(),
        selected_rule_ids=["Pac_001", "Pic_001"],
        field_mappings={"Pac_001": "applicants.income", "Pic_001": "internal_records.delinquency_count"},
    )
    assert schema["selected_rules"] == ["Pac_001", "Pic_001"]
    sources = {field["source"] for field in schema["fields"]}
    assert sources == {"applicants.income", "internal_records.delinquency_count"}


def test_derive_schema_updates_when_selection_changes():
    mappings = {"Pac_001": "applicants.income", "Fac_001": "bureau_data.bureau_score"}
    first = derive_schema(_rules(), ["Pac_001"], mappings)
    second = derive_schema(_rules(), ["Pac_001", "Fac_001"], mappings)
    assert {f["source"] for f in first["fields"]} == {"applicants.income"}
    assert {f["source"] for f in second["fields"]} == {"applicants.income", "bureau_data.bureau_score"}


def test_derive_schema_groups_rules_sharing_a_field():
    schema = derive_schema(
        _rules(),
        ["Pac_001", "Pic_001"],
        {"Pac_001": "applicants.income", "Pic_001": "applicants.income"},
    )
    assert len(schema["fields"]) == 1
    assert schema["fields"][0]["rule_ids"] == ["Pac_001", "Pic_001"]


def test_derive_schema_lists_unmapped_rules():
    schema = derive_schema(_rules(), ["Pac_001", "Fac_001"], {"Pac_001": "applicants.income"})
    assert schema["unmapped_rules"] == ["Fac_001"]


def test_derive_schema_rejects_unknown_field():
    with pytest.raises(ValueError):
        derive_schema(_rules(), ["Pac_001"], {"Pac_001": "nowhere.nothing"})


def test_create_client_config_persists_selection_and_schema(tmp_path):
    rule_store = tmp_path / "rule_sets.json"
    config_store = tmp_path / "client_configs.json"
    rule_set = create_rule_set("Retail Policy", "Retail", _rules(), store_path=rule_store)

    config = create_client_config(
        client_name="Acme Lending",
        rule_set_id=rule_set["id"],
        selected_rule_ids=["Pac_001"],
        field_mappings={"Pac_001": "applicants.income"},
        store_path=config_store,
        rule_store_path=rule_store,
    )

    stored = load_client_configs(config_store)
    assert len(stored) == 1
    assert stored[0]["client_name"] == "Acme Lending"
    assert stored[0]["schema"]["fields"][0]["source"] == "applicants.income"
    assert config["rule_set_name"] == "Retail Policy"


def test_create_client_config_rejects_rules_outside_rule_set(tmp_path):
    rule_store = tmp_path / "rule_sets.json"
    rule_set = create_rule_set("Retail Policy", "Retail", _rules(), store_path=rule_store)
    with pytest.raises(ValueError):
        create_client_config(
            client_name="Acme Lending",
            rule_set_id=rule_set["id"],
            selected_rule_ids=["Not_a_rule"],
            field_mappings={},
            store_path=tmp_path / "client_configs.json",
            rule_store_path=rule_store,
        )


def test_delete_client_config(tmp_path):
    rule_store = tmp_path / "rule_sets.json"
    config_store = tmp_path / "client_configs.json"
    rule_set = create_rule_set("Retail Policy", "Retail", _rules(), store_path=rule_store)
    config = create_client_config(
        client_name="Acme Lending",
        rule_set_id=rule_set["id"],
        selected_rule_ids=["Pac_001"],
        field_mappings={"Pac_001": "applicants.income"},
        store_path=config_store,
        rule_store_path=rule_store,
    )
    delete_client_config(config["id"], store_path=config_store)
    assert load_client_configs(config_store) == []


def test_client_config_routes_registered():
    paths = {route.path for route in app.routes}
    assert "/clients/config" in paths
    assert "/clients/delete" in paths
    assert "/api/clients/{config_id}/schema" in paths


def test_rules_page_renders_client_activation_control():
    ctx = workflow_context("configure_rules")
    html = templates.get_template("rules.html").render(
        rule_sets=[{"id": "abc123", "name": "Retail Policy", "category": "Retail", "status": "published", "rules": _rules()}],
        parsed_rules=[],
        client_configs=[
            {
                "id": "cfg1",
                "client_name": "Acme Lending",
                "rule_set_name": "Retail Policy",
                "selected_rule_ids": ["Pac_001"],
                "schema": {"fields": [], "unmapped_rules": [], "selected_rules": ["Pac_001"]},
            }
        ],
        data_fields=DATA_FIELDS,
        **ctx,
    )
    assert 'id="client-activation"' in html
    assert 'id="schema-preview"' in html
    assert "Acme Lending" in html
    assert "rule-sets-data" in html
    assert "data-fields-data" in html
