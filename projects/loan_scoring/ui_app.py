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

RULE_STORE_PATH = Path(__file__).resolve().parent / "configs" / "rule_sets.json"

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
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request) -> HTMLResponse:
    rule_sets = load_rule_sets()
    return templates.TemplateResponse(request, "rules.html", {"request": request, "rule_sets": rule_sets, "parsed_rules": []})


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
            {"request": request, "error": "Please upload an Excel workbook with rule definitions.", "parsed_rules": [], "rule_sets": load_rule_sets()},
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
            {
                "request": request,
                "parsed_rules": normalized_rules,
                "summary": parsed,
                "success": "Rules imported successfully. Review and publish when ready.",
                "rule_sets": load_rule_sets(),
                "active_rule_set": rule_set,
            },
        )
    finally:
        Path(temp_path).unlink(missing_ok=True)


@app.post("/rules/publish")
async def publish_rules(request: Request, rule_id: str = Form(...)):
    try:
        publish_rule_set(rule_id)
    except ValueError:
        return templates.TemplateResponse(request, "rules.html", {"request": request, "error": "Unable to publish the selected rule set.", "rule_sets": load_rule_sets(), "parsed_rules": []})
    return templates.TemplateResponse(request, "rules.html", {"request": request, "success": "Rule set published.", "rule_sets": load_rule_sets(), "parsed_rules": []})


@app.post("/rules/update")
async def update_rules(request: Request, rule_id: str = Form(...), status: str = Form(...)):
    updates = {"status": status}
    if status == "rejected":
        updates["review_notes"] = "Rejected by user"
    elif status == "draft":
        updates["review_notes"] = "Returned to draft"
    update_rule_set(rule_id, updates)
    return templates.TemplateResponse(request, "rules.html", {"request": request, "success": "Rule set updated.", "rule_sets": load_rule_sets(), "parsed_rules": []})


@app.post("/rules/delete")
async def delete_rules(request: Request, rule_id: str = Form(...)):
    delete_rule_set(rule_id)
    return templates.TemplateResponse(request, "rules.html", {"request": request, "success": "Rule set removed.", "rule_sets": load_rule_sets(), "parsed_rules": []})


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
    return templates.TemplateResponse(request, "rules.html", {"request": request, "success": "Rule updated.", "rule_sets": load_rule_sets(), "parsed_rules": []})


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
        {"request": request, "payload": payload, "result": result},
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
