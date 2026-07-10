import os
import json
import sys
import uuid
from pathlib import Path
from typing import Dict, Any, List

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
import polars as pl

from projects.loan_scoring.rule_loader import parse_workbook, save_rules_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("Decider_config__type", "file:json")
os.environ.setdefault("Decider_config__basepath", str(Path(__file__).resolve().parent / "configs"))
os.environ.setdefault("Decider_api__root_module", "main")
os.environ.setdefault("Decider_ext__extension_path", str(Path(__file__).resolve().parent / "decider_extensions"))

from decider.initialization import initialize_decider
from decider.modules import GraphModule
from decider.config.file import JsonFileConfigManager

initialize_decider(extension_path=os.environ["Decider_ext__extension_path"])

app = FastAPI(title="Loan Scoring UI")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

# Assessment workflow shown as the stepper at the top of every page.
# To add a stage, append an entry here and give its page a route + template.
WORKFLOW_STEPS: List[Dict[str, Any]] = [
    {"key": "pull_data", "label": "Pull Data", "caption": "Applicant & bureau inputs", "href": "/data"},
    {"key": "configure_rules", "label": "Configure Rules", "caption": "Rule sets & policies", "href": "/rules"},
    {"key": "score_client", "label": "Score Client", "caption": "Run the decision engine", "href": "/"},
    {"key": "decision", "label": "Decision", "caption": "Outcome & reason codes", "href": None},
]


def workflow_context(current_step: str) -> Dict[str, Any]:
    keys = [step["key"] for step in WORKFLOW_STEPS]
    if current_step not in keys:
        raise KeyError(f"unknown workflow step: {current_step!r}")
    current_index = keys.index(current_step)
    steps = []
    for index, step in enumerate(WORKFLOW_STEPS):
        state = "done" if index < current_index else "active" if index == current_index else "todo"
        steps.append({**step, "state": state})
    return {"workflow_steps": steps, "current_step": current_step}


def page_ctx(request: Request, current_step: str, **extra: Any) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"request": request, **workflow_context(current_step)}
    ctx.update(extra)
    return ctx


def rules_ctx(request: Request, **extra: Any) -> Dict[str, Any]:
    extra.setdefault("rule_sets", load_rule_sets())
    extra.setdefault("parsed_rules", [])
    extra.setdefault("client_configs", load_client_configs())
    extra.setdefault("data_fields", DATA_FIELDS)
    return page_ctx(request, "configure_rules", **extra)


RULE_STORE_PATH = Path(__file__).resolve().parent / "configs" / "rule_sets.json"
CLIENT_CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "client_configs.json"

# Where scoring inputs live in the database. Rules are linked to these fields
# on the Configure Rules screen; extend this list as new sources come online.
DATA_FIELDS: List[Dict[str, str]] = [
    {"key": "applicants.debt", "table": "applicants", "field": "debt", "label": "Applicant debt", "dtype": "float"},
    {"key": "applicants.income", "table": "applicants", "field": "income", "label": "Applicant income", "dtype": "float"},
    {"key": "credit_accounts.credit_used", "table": "credit_accounts", "field": "credit_used", "label": "Credit used", "dtype": "float"},
    {"key": "credit_accounts.credit_limit", "table": "credit_accounts", "field": "credit_limit", "label": "Credit limit", "dtype": "float"},
    {"key": "bureau_data.bureau_score", "table": "bureau_data", "field": "bureau_score", "label": "Bureau score", "dtype": "float"},
    {"key": "bureau_data.adverse_accounts", "table": "bureau_data", "field": "adverse_accounts", "label": "Adverse accounts", "dtype": "int"},
    {"key": "internal_records.delinquency_count", "table": "internal_records", "field": "delinquency_count", "label": "Delinquency count", "dtype": "int"},
    {"key": "internal_records.default_history", "table": "internal_records", "field": "default_history", "label": "Default history", "dtype": "int"},
]

if RULE_STORE_PATH.exists() and RULE_STORE_PATH.stat().st_size == 0:
    RULE_STORE_PATH.write_text("[]", encoding="utf-8")


def load_rule_sets(store_path: Path | None = None) -> List[Dict[str, Any]]:
    path = store_path or RULE_STORE_PATH
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_rule_sets(rule_sets: List[Dict[str, Any]], store_path: Path | None = None) -> None:
    path = store_path or RULE_STORE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rule_sets, indent=2), encoding="utf-8")


def create_rule_set(name: str, category: str, rules: List[Dict[str, Any]], store_path: Path | None = None) -> Dict[str, Any]:
    rule_sets = load_rule_sets(store_path)
    rule_set = {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "category": category,
        "status": "draft",
        "created_at": str(Path().stat().st_mtime_ns) if False else "now",
        "rules": rules,
    }
    rule_sets.append(rule_set)
    save_rule_sets(rule_sets, store_path)
    return rule_set


def publish_rule_set(rule_id: str, store_path: Path | None = None) -> Dict[str, Any]:
    rule_sets = load_rule_sets(store_path)
    for rule_set in rule_sets:
        if rule_set["id"] == rule_id:
            rule_set["status"] = "published"
            save_rule_sets(rule_sets, store_path)
            return rule_set
    raise ValueError("rule set not found")


def update_rule_set(rule_id: str, updates: Dict[str, Any], store_path: Path | None = None) -> Dict[str, Any]:
    rule_sets = load_rule_sets(store_path)
    for rule_set in rule_sets:
        if rule_set["id"] == rule_id:
            rule_set.update(updates)
            save_rule_sets(rule_sets, store_path)
            return rule_set
    raise ValueError("rule set not found")


def delete_rule_set(rule_id: str, store_path: Path | None = None) -> List[Dict[str, Any]]:
    rule_sets = load_rule_sets(store_path)
    remaining = [rule_set for rule_set in rule_sets if rule_set["id"] != rule_id]
    save_rule_sets(remaining, store_path)
    return remaining


def load_client_configs(store_path: Path | None = None) -> List[Dict[str, Any]]:
    path = store_path or CLIENT_CONFIG_PATH
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_client_configs(configs: List[Dict[str, Any]], store_path: Path | None = None) -> None:
    path = store_path or CLIENT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(configs, indent=2), encoding="utf-8")


def derive_schema(
    rules: List[Dict[str, Any]],
    selected_rule_ids: List[str],
    field_mappings: Dict[str, str],
) -> Dict[str, Any]:
    """Build the data schema implied by the selected rules and their field links."""
    field_index = {entry["key"]: entry for entry in DATA_FIELDS}
    selected_ids = [r["rule_id"] for r in rules if r["rule_id"] in set(selected_rule_ids)]
    fields: Dict[str, Dict[str, Any]] = {}
    unmapped: List[str] = []
    for rule_id in selected_ids:
        source = field_mappings.get(rule_id)
        if not source:
            unmapped.append(rule_id)
            continue
        if source not in field_index:
            raise ValueError(f"unknown data field: {source!r}")
        entry = field_index[source]
        field = fields.setdefault(
            source,
            {
                "source": source,
                "table": entry["table"],
                "field": entry["field"],
                "label": entry["label"],
                "dtype": entry["dtype"],
                "rule_ids": [],
            },
        )
        field["rule_ids"].append(rule_id)
    return {"fields": list(fields.values()), "unmapped_rules": unmapped, "selected_rules": selected_ids}


def create_client_config(
    client_name: str,
    rule_set_id: str,
    selected_rule_ids: List[str],
    field_mappings: Dict[str, str],
    store_path: Path | None = None,
    rule_store_path: Path | None = None,
) -> Dict[str, Any]:
    rule_sets = load_rule_sets(rule_store_path)
    rule_set = next((rs for rs in rule_sets if rs["id"] == rule_set_id), None)
    if rule_set is None:
        raise ValueError("rule set not found")
    known = {rule["rule_id"] for rule in rule_set["rules"]}
    unknown = set(selected_rule_ids) - known
    if unknown:
        raise ValueError(f"rules not in rule set: {sorted(unknown)}")
    schema = derive_schema(rule_set["rules"], selected_rule_ids, field_mappings)
    config = {
        "id": str(uuid.uuid4())[:8],
        "client_name": client_name,
        "rule_set_id": rule_set_id,
        "rule_set_name": rule_set["name"],
        "selected_rule_ids": list(selected_rule_ids),
        "field_mappings": field_mappings,
        "schema": schema,
    }
    configs = load_client_configs(store_path)
    configs.append(config)
    save_client_configs(configs, store_path)
    return config


def delete_client_config(config_id: str, store_path: Path | None = None) -> List[Dict[str, Any]]:
    configs = load_client_configs(store_path)
    remaining = [config for config in configs if config["id"] != config_id]
    save_client_configs(remaining, store_path)
    return remaining


def score_applicant(payload: Dict[str, Any]) -> Dict[str, Any]:
    from projects.loan_scoring.decider_extensions.loan_scoring import CreditScorer

    scorer = CreditScorer(
        name="credit_scorer",
        dti_weight=200.0,
        utilization_weight=100.0,
        score_base=800.0,
    )
    input_df = pl.DataFrame(
        {
            "debt": [float(payload.get("debt", 0))],
            "income": [float(payload.get("income", 0))],
            "credit_used": [float(payload.get("credit_used", 0))],
            "credit_limit": [float(payload.get("credit_limit", 0))],
            "bureau_score": [float(payload.get("bureau_score", 0))],
            "adverse_accounts": [int(payload.get("adverse_accounts", 0))],
            "delinquency_count": [int(payload.get("delinquency_count", 0))],
            "default_history": [int(payload.get("default_history", 0))],
        }
    )
    result = scorer({"input": input_df})
    scored = result.to_dicts()[0]

    bureau_score = float(scored.get("bureau_score", 0))
    adverse_accounts = int(scored.get("adverse_accounts", 0))
    delinquency_count = int(scored.get("delinquency_count", 0))
    default_history = int(scored.get("default_history", 0))

    reason_codes = []
    if bureau_score < 700:
        reason_codes.append("bureau_score_low")
    if adverse_accounts > 0:
        reason_codes.append("adverse_accounts_present")
    if delinquency_count > 0:
        reason_codes.append("delinquency_present")
    if default_history > 0:
        reason_codes.append("default_history_present")

    if bureau_score < 600 or adverse_accounts > 0 or delinquency_count > 0 or default_history > 0:
        decision = "refer"
        risk_band = "medium"
    else:
        decision = "approve"
        risk_band = "low"

    scored["decision"] = decision
    scored["risk_band"] = risk_band
    scored["reason_codes"] = reason_codes
    return scored


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", page_ctx(request, "score_client"))


@app.get("/data", response_class=HTMLResponse)
async def data_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "data.html", page_ctx(request, "pull_data"))


@app.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request) -> HTMLResponse:
    rule_sets = load_rule_sets()
    return templates.TemplateResponse(request, "rules.html", rules_ctx(request, rule_sets=rule_sets, parsed_rules=[]))


@app.get("/rules/example")
async def download_example_rules():
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Commercial Rules"
    sheet.append(["Phase", "RuleID", "Description", "Action", "Disputable", "Rule Applied"])
    sheet.append(["Early_rules_1", "Pac_001", "Turnover < 100 000", "Decline", "No", "In_decision_engine"])
    sheet.append(["Early_rules_1", "Pac_002", "Approved legal entities", "Decline", "No", "In_decision_engine"])
    sheet.append(["Early_rules_2", "Pic_001", "Internal Arrears <= 12 months", "Decline", "No", "In_decision_engine"])
    sheet.append(["Middle_rules_1", "Fbc_001", "Payment Arrangements", "Decline", "No", "In_decision_engine"])
    sheet.append(["Late_rules_1", "Fac_001", "FICA", "Decline", "No", "In_decision_engine"])
    temp_path = Path(__file__).resolve().parent / "configs" / "example_rules.xlsx"
    workbook.save(temp_path)
    return FileResponse(temp_path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename="example_rules.xlsx")


@app.post("/rules/upload")
async def upload_rules(
    request: Request,
    name: str = Form(...),
    category: str = Form(...),
    file: UploadFile = File(...),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return templates.TemplateResponse(
            request,
            "rules.html",
            rules_ctx(request, error="Please upload an Excel workbook with rule definitions.", parsed_rules=[], rule_sets=load_rule_sets()),
        )

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as temp_file:
        content = await file.read()
        temp_file.write(content)
        temp_path = temp_file.name

    try:
        parsed = parse_workbook(temp_path)
        normalized_rules = []
        for rule in parsed["rules"]:
            normalized_rules.append(
                {
                    "phase": rule.get("phase", ""),
                    "rule_id": rule.get("rule_id", ""),
                    "description": rule.get("description", ""),
                    "action": rule.get("action", "Decline"),
                    "disputable": rule.get("disputable", False),
                    "rule_applied": rule.get("rule_applied", "In_decision_engine"),
                    "sheet_name": rule.get("sheet_name", ""),
                    "review_status": "pending",
                }
            )

        rule_set = create_rule_set(name=name, category=category, rules=normalized_rules)
        config_path = Path(__file__).resolve().parent / "configs" / "uploaded_rules.json"
        save_rules_config(temp_path, config_path)
        return templates.TemplateResponse(
            request,
            "rules.html",
            rules_ctx(
                request,
                parsed_rules=normalized_rules,
                summary=parsed,
                success="Rules imported successfully. Review and publish when ready.",
                rule_sets=load_rule_sets(),
                active_rule_set=rule_set,
            ),
        )
    finally:
        Path(temp_path).unlink(missing_ok=True)


@app.post("/rules/publish")
async def publish_rules(request: Request, rule_id: str = Form(...)):
    try:
        publish_rule_set(rule_id)
    except ValueError:
        return templates.TemplateResponse(request, "rules.html", rules_ctx(request, error="Unable to publish the selected rule set.", rule_sets=load_rule_sets(), parsed_rules=[]))
    return templates.TemplateResponse(request, "rules.html", rules_ctx(request, success="Rule set published.", rule_sets=load_rule_sets(), parsed_rules=[]))


@app.post("/rules/update")
async def update_rules(request: Request, rule_id: str = Form(...), status: str = Form(...)):
    updates = {"status": status}
    if status == "rejected":
        updates["review_notes"] = "Rejected by user"
    elif status == "draft":
        updates["review_notes"] = "Returned to draft"
    update_rule_set(rule_id, updates)
    return templates.TemplateResponse(request, "rules.html", rules_ctx(request, success="Rule set updated.", rule_sets=load_rule_sets(), parsed_rules=[]))


@app.post("/rules/delete")
async def delete_rules(request: Request, rule_id: str = Form(...)):
    delete_rule_set(rule_id)
    return templates.TemplateResponse(request, "rules.html", rules_ctx(request, success="Rule set removed.", rule_sets=load_rule_sets(), parsed_rules=[]))


@app.put("/rules/{rule_set_id}/rule/{rule_index}")
async def update_rule(request: Request, rule_set_id: str, rule_index: int):
    """Update a specific rule within a rule set"""
    try:
        body = await request.json()
        rule_sets = load_rule_sets()
        
        for rule_set in rule_sets:
            if rule_set["id"] == rule_set_id:
                if 0 <= rule_index < len(rule_set["rules"]):
                    # Update the rule at the specified index
                    rule_set["rules"][rule_index] = {
                        "phase": body.get("phase", ""),
                        "rule_id": body.get("rule_id", ""),
                        "description": body.get("description", ""),
                        "action": body.get("action", "Decline"),
                        "disputable": body.get("disputable", False),
                        "rule_applied": body.get("rule_applied", "In_decision_engine"),
                        "review_status": "pending"
                    }
                    save_rule_sets(rule_sets)
                    return JSONResponse({"success": True, "message": "Rule updated successfully"})
                else:
                    return JSONResponse({"success": False, "error": "Rule index out of range"}, status_code=400)
        
        return JSONResponse({"success": False, "error": "Rule set not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/rules/{rule_set_id}/rule")
async def add_rule(request: Request, rule_set_id: str):
    """Add a new rule to a rule set"""
    try:
        body = await request.json()
        rule_sets = load_rule_sets()
        
        for rule_set in rule_sets:
            if rule_set["id"] == rule_set_id:
                new_rule = {
                    "phase": body.get("phase", ""),
                    "rule_id": body.get("rule_id", ""),
                    "description": body.get("description", ""),
                    "action": body.get("action", "Decline"),
                    "disputable": body.get("disputable", False),
                    "rule_applied": body.get("rule_applied", "In_decision_engine"),
                    "review_status": "pending"
                }
                rule_set["rules"].append(new_rule)
                save_rule_sets(rule_sets)
                return JSONResponse({"success": True, "message": "Rule added successfully"})
        
        return JSONResponse({"success": False, "error": "Rule set not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.delete("/rules/{rule_set_id}/rule/{rule_index}")
async def delete_rule(request: Request, rule_set_id: str, rule_index: int):
    """Delete a specific rule from a rule set"""
    try:
        rule_sets = load_rule_sets()
        
        for rule_set in rule_sets:
            if rule_set["id"] == rule_set_id:
                if 0 <= rule_index < len(rule_set["rules"]):
                    rule_set["rules"].pop(rule_index)
                    save_rule_sets(rule_sets)
                    return JSONResponse({"success": True, "message": "Rule deleted successfully"})
                else:
                    return JSONResponse({"success": False, "error": "Rule index out of range"}, status_code=400)
        
        return JSONResponse({"success": False, "error": "Rule set not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/rules/edit")
async def edit_rules(request: Request, rule_id: str = Form(...), rule_name: str = Form(...), decision: str = Form(...)):
    rule_sets = load_rule_sets()
    for rule_set in rule_sets:
        if rule_set["id"] == rule_id:
            for rule in rule_set["rules"]:
                if rule.get("rule_name") == rule_name:
                    rule["decision"] = decision
            save_rule_sets(rule_sets)
            break
    return templates.TemplateResponse(request, "rules.html", rules_ctx(request, success="Rule updated.", rule_sets=load_rule_sets(), parsed_rules=[]))


@app.post("/clients/config")
async def save_client_configuration(request: Request):
    try:
        body = await request.json()
        client_name = str(body.get("client_name", "")).strip()
        if not client_name:
            raise ValueError("client name is required")
        config = create_client_config(
            client_name=client_name,
            rule_set_id=body.get("rule_set_id", ""),
            selected_rule_ids=body.get("selected_rule_ids", []),
            field_mappings=body.get("field_mappings", {}),
        )
        return JSONResponse({"success": True, "config": config})
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/clients/delete")
async def delete_client_configuration(request: Request, config_id: str = Form(...)):
    delete_client_config(config_id)
    return templates.TemplateResponse(request, "rules.html", rules_ctx(request, success="Client configuration removed."))


@app.get("/api/clients/{config_id}/schema")
def client_schema(config_id: str):
    for config in load_client_configs():
        if config["id"] == config_id:
            return JSONResponse(config["schema"])
    return JSONResponse({"error": "client configuration not found"}, status_code=404)


@app.post("/score")
async def score_endpoint(
    request: Request,
    debt: float = Form(...),
    income: float = Form(...),
    credit_used: float = Form(...),
    credit_limit: float = Form(...),
    bureau_score: float = Form(0),
    adverse_accounts: int = Form(0),
    delinquency_count: int = Form(0),
    default_history: int = Form(0),
):
    payload = {
        "debt": debt,
        "income": income,
        "credit_used": credit_used,
        "credit_limit": credit_limit,
        "bureau_score": bureau_score,
        "adverse_accounts": adverse_accounts,
        "delinquency_count": delinquency_count,
        "default_history": default_history,
    }
    result = score_applicant(payload)
    return templates.TemplateResponse(
        request,
        "result.html",
        page_ctx(request, "decision", payload=payload, result=result),
    )


@app.get("/api/score")
def api_score(
    debt: float,
    income: float,
    credit_used: float,
    credit_limit: float,
    bureau_score: float = 0,
    adverse_accounts: int = 0,
    delinquency_count: int = 0,
    default_history: int = 0,
):
    payload = {
        "debt": debt,
        "income": income,
        "credit_used": credit_used,
        "credit_limit": credit_limit,
        "bureau_score": bureau_score,
        "adverse_accounts": adverse_accounts,
        "delinquency_count": delinquency_count,
        "default_history": default_history,
    }
    return JSONResponse(score_applicant(payload))
