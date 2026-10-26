"""Flask application for local use and Vercel's Python function runtime."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import json
import sys

from flask import Flask, jsonify, request, send_file
from flask.json.provider import DefaultJSONProvider
from io import BytesIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import (Account, Budget, ExpenseTransaction, FinancialAnalyzer, Transaction,
                    IncomeTransaction, StatementGenerator, transaction_from_dict)
from storage import JSONStorage, DEFAULT_PATH, StorageError


class DecimalJSONProvider(DefaultJSONProvider):
    """Expose exact model amounts as ordinary JSON numbers for the browser."""
    def default(self, value):
        if isinstance(value, Decimal):
            return float(value)
        return super().default(value)

app = Flask(__name__, static_folder=str(ROOT / "public"), static_url_path="")
app.json_provider_class = DecimalJSONProvider
app.json = app.json_provider_class(app)
app.json.sort_keys = False
storage = JSONStorage()


def _load():
    data = storage.load()
    rows = []
    for index, item in enumerate(data.get("transactions", []), start=1):
        try:
            row = transaction_from_dict(item)
            row.validate()
            rows.append(row)
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError(
                f"Stored transaction {index} is invalid: {exc} The original ledger was left untouched."
            ) from exc
    try:
        account = Account(rows)
        for index, item in enumerate(data.get("budgets", []), start=1):
            Budget(item["category"], item["limit"], item["month"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError(f"Stored ledger data is invalid: {exc} The original ledger was left untouched.") from exc
    return data, account


def _save(data, account):
    data["transactions"] = [tx.to_dict() for tx in account.get_transactions()]
    storage.save(data)


def _make_transaction(payload):
    if not isinstance(payload, dict): raise ValueError("Request body must be a JSON object.")
    kind = str(payload.get("type", "")).lower()
    cls = IncomeTransaction if kind == "income" else ExpenseTransaction if kind == "expense" else None
    if cls is None: raise ValueError("Type must be income or expense.")
    tx = cls(payload.get("description", ""), payload.get("amount"), payload.get("category", ""),
             payload.get("date", ""), payload.get("payment_method", "UPI"), payload.get("notes", ""), payload.get("id"))
    tx.validate()
    return tx


def _parse_iso_date(value):
    if not isinstance(value, str):
        raise ValueError("Date must use YYYY-MM-DD format.")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Date must use YYYY-MM-DD format.")
    return parsed


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"): return jsonify(error="Not found."), 404
    return "Not found", 404


@app.errorhandler(StorageError)
def storage_error(error):
    return jsonify(error=str(error)), 503


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.get("/api/dashboard")
def dashboard():
    data, account = _load(); analyzer = FinancialAnalyzer(account)
    today = date.today(); month = today.strftime("%Y-%m")
    month_rows = [tx for tx in account.get_transactions() if tx.date.startswith(month)]
    income = sum((t.amount for t in month_rows if t.is_income()), Decimal("0.00"))
    expenses = sum((t.amount for t in month_rows if t.is_expense()), Decimal("0.00"))
    monthly = analyzer.get_monthly_summary()
    labels = list(monthly)[-6:]
    budgets = [Budget(b["category"], b["limit"], b["month"]).get_budget_status(account.get_transactions()) for b in data.get("budgets", []) if b.get("month") == month]
    return jsonify(balance=account.get_balance(), month=month, income=income, expenses=expenses,
                   net_savings=income-expenses, savings_rate=analyzer.calculate_savings_rate(month_rows),
                   transaction_count=len(account.get_transactions()), months=labels,
                   monthly_income=[monthly[m]["income"] for m in labels], monthly_expenses=[monthly[m]["expenses"] for m in labels],
                   category_breakdown=analyzer.get_category_breakdown(month_rows), budgets=budgets)


@app.route("/api/transactions", methods=["GET", "POST"])
def transactions():
    data, account = _load()
    if request.method == "GET":
        rows = account.get_transactions()
        start, end, category, kind = (request.args.get(k) for k in ("start_date", "end_date", "category", "type"))
        try:
            parsed_start = _parse_iso_date(start) if start else None
            parsed_end = _parse_iso_date(end) if end else None
            if parsed_start and parsed_end and parsed_start > parsed_end:
                raise ValueError("Start date must be on or before end date.")
            if parsed_start: rows = [t for t in rows if t.date >= parsed_start.isoformat()]
            if parsed_end: rows = [t for t in rows if t.date <= parsed_end.isoformat()]
        except (TypeError, ValueError) as exc:
            return jsonify(error=str(exc) or "Filters must use valid YYYY-MM-DD dates."), 400
        if category: rows = [t for t in rows if t.category.casefold() == category.casefold()]
        if kind:
            if kind not in ("income", "expense"): return jsonify(error="Type filter must be income or expense."), 400
            rows = [t for t in rows if t.type == kind]
        return jsonify(transactions=[t.to_dict() for t in sorted(rows, key=lambda t: (t.date, t.id), reverse=True)])
    try:
        tx = _make_transaction(request.get_json(silent=True)); account.add_transaction(tx); _save(data, account)
        return jsonify(transaction=tx.to_dict()), 201
    except ValueError as exc: return jsonify(error=str(exc)), 400


@app.route("/api/transactions/<transaction_id>", methods=["PUT", "DELETE"])
def transaction_detail(transaction_id):
    data, account = _load()
    try:
        if request.method == "DELETE":
            account.remove_transaction(transaction_id); _save(data, account); return jsonify(message="Transaction deleted.")
        tx = _make_transaction(request.get_json(silent=True)); updated = account.update_transaction(transaction_id, tx); _save(data, account)
        return jsonify(transaction=updated.to_dict())
    except KeyError as exc: return jsonify(error=str(exc).strip("'")), 404
    except ValueError as exc: return jsonify(error=str(exc)), 400


@app.route("/api/budgets", methods=["GET", "POST"])
def budgets():
    data, account = _load()
    if request.method == "GET":
        statuses = [Budget(b["category"], b["limit"], b["month"]).get_budget_status(account.get_transactions())
                    for b in data.get("budgets", [])]
        return jsonify(budgets=statuses)
    payload = request.get_json(silent=True) or {}
    try:
        budget = Budget(payload.get("category", ""), payload.get("limit"), payload.get("month", ""))
        data["budgets"] = [b for b in data.get("budgets", []) if not (b["category"].casefold() == budget.category.casefold() and b["month"] == budget.month)]
        data["budgets"].append({"category": budget.category, "limit": float(budget.limit), "month": budget.month})
        storage.save(data)
        return jsonify(budget=budget.get_budget_status(account.get_transactions())), 201
    except (ValueError, TypeError) as exc: return jsonify(error=str(exc)), 400


@app.get("/api/analytics")
def analytics():
    _, account = _load(); analyzer = FinancialAnalyzer(account); rows = account.get_transactions()
    monthly = analyzer.get_monthly_summary()
    current = date.today().replace(day=1); previous = (current - timedelta(days=1)).replace(day=1)
    return jsonify(monthly=monthly, category_breakdown=analyzer.get_category_breakdown(),
                   savings_trend=[{"month": m, "savings": v["savings"], "savings_rate": v["savings_rate"]} for m, v in monthly.items()],
                   highest_expense_category=analyzer.get_highest_expense_category(), largest_expense=max((t.to_dict() for t in rows if t.is_expense()), key=lambda t: t["amount"], default=None),
                   average_monthly_expenses=analyzer.calculate_average_monthly_expenses(),
                   overall_savings_rate=analyzer.calculate_savings_rate(),
                   average_monthly_savings_rate=analyzer.calculate_average_monthly_savings_rate(),
                   month_comparison=analyzer.compare_months(current.strftime("%Y-%m"), previous.strftime("%Y-%m")))


@app.post("/api/statements/pdf")
def statement_pdf():
    try:
        payload = request.get_json(silent=True) or {}
        start, end = payload.get("start_date", ""), payload.get("end_date", "")
        if not start or not end: raise ValueError("Choose both a start date and an end date.")
        _, account = _load(); pdf = StatementGenerator(account).generate_pdf(start, end)
        return send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                         download_name=f"paisaledger-statement-{start}-to-{end}.pdf")
    except StorageError: raise
    except ValueError as exc: return jsonify(error=str(exc)), 400
    except Exception: return jsonify(error="The statement could not be generated. Please check the selected period and try again."), 500


@app.post("/api/reset-demo")
def reset_demo():
    try:
        sample = JSONStorage(DEFAULT_PATH).load()
        storage.save(sample)
        return jsonify(message="Demo data restored.")
    except StorageError: raise


@app.get("/api/classes")
def classes():
    class_rows = [
        {"name":"Transaction","parent":None,"purpose":"Base data model and shared validation for a financial event.","attributes":["id","description","amount","type","category","date","payment_method","notes"],"methods":["validate","is_income","is_expense","get_balance_effect","get_display_label","to_dict"],"overrides":[],"relationships":["Account stores transactions; FinancialAnalyzer and StatementGenerator read them."],"highlights":["adding"]},
        {"name":"IncomeTransaction","parent":"Transaction","purpose":"Income transaction whose polymorphic balance effect is positive.","attributes":["Inherited Transaction attributes"],"methods":["get_balance_effect"],"overrides":["get_balance_effect"],"relationships":["Created for income records and stored in Account."],"highlights":["adding","statement"]},
        {"name":"ExpenseTransaction","parent":"Transaction","purpose":"Expense transaction whose polymorphic balance effect is negative.","attributes":["Inherited Transaction attributes"],"methods":["get_balance_effect"],"overrides":["get_balance_effect"],"relationships":["Created for expense records and stored in Account."],"highlights":["adding","budget","analysis","statement"]},
        {"name":"Account","parent":None,"purpose":"Owns and manages the transaction collection and account totals.","attributes":["_transactions"],"methods":["add_transaction","update_transaction","remove_transaction","get_balance","get_total_income","get_total_expenses","get_transactions"],"overrides":[],"relationships":["FinancialAnalyzer and StatementGenerator use Account."],"highlights":["adding","analysis","statement"]},
        {"name":"Budget","parent":None,"purpose":"Calculates spending, remaining allowance and over-budget status for a category and month.","attributes":["category","limit","month"],"methods":["calculate_spending","remaining_budget","get_budget_status"],"overrides":[],"relationships":["Reads Account transactions for one category/month."],"highlights":["budget"]},
        {"name":"FinancialAnalyzer","parent":None,"purpose":"Calculates savings, savings rate, breakdowns, trends and month comparisons.","attributes":["account"],"methods":["calculate_savings","calculate_savings_rate","get_category_breakdown","get_monthly_summary","get_highest_expense_category","compare_months"],"overrides":[],"relationships":["Analyzes Account transaction data."],"highlights":["analysis"]},
        {"name":"StatementGenerator","parent":None,"purpose":"Calculates period balances and generates a paginated PDF statement.","attributes":["account"],"methods":["calculate_opening_balance","calculate_closing_balance","generate_statement_data","generate_pdf"],"overrides":[],"relationships":["Reads Account transactions and uses ReportLab for output."],"highlights":["statement"]}
    ]
    # Filter the educational method labels against the methods that really exist.
    # The flow map describes code responsibilities, not methods called in this request.
    class_types = {"Transaction": Transaction, "IncomeTransaction": IncomeTransaction,
                   "ExpenseTransaction": ExpenseTransaction, "Account": Account,
                   "Budget": Budget, "FinancialAnalyzer": FinancialAnalyzer,
                   "StatementGenerator": StatementGenerator}
    flow_methods = {
        "adding": {"validate", "add_transaction", "update_transaction", "get_balance_effect"},
        "analysis": {"get_balance", "get_total_income", "get_total_expenses", "calculate_savings",
                     "calculate_savings_rate", "get_category_breakdown", "get_monthly_summary",
                     "get_highest_expense_category", "compare_months"},
        "budget": {"calculate_spending", "remaining_budget", "get_budget_status", "get_transactions"},
        "statement": {"calculate_opening_balance", "calculate_closing_balance",
                      "generate_statement_data", "generate_pdf", "get_balance_effect"}
    }
    for row in class_rows:
        model_class = class_types[row["name"]]
        row["methods"] = [name for name in row["methods"] if callable(getattr(model_class, name, None))]
        row["method_highlights"] = {
            flow: [name for name in row["methods"] if name in flow_methods[flow]]
            for flow in row["highlights"]
        }
    return jsonify(classes=class_rows)


if __name__ == "__main__":
    app.run(debug=True)

