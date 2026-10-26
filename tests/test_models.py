"""Model, Flask API and PDF tests. API tests use a temporary JSON file."""
import json
import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from models import (Account, Budget, ExpenseTransaction, FinancialAnalyzer,
                    IncomeTransaction, StatementGenerator, Transaction, transaction_from_dict)
from storage import JSONStorage, StorageError
import api.index as web


def income(amount=1000, when="2026-10-01", category="Salary", name="Pay"):
    return IncomeTransaction(name, amount, category, when)


def expense(amount=250, when="2026-10-02", category="Food", name="Lunch"):
    return ExpenseTransaction(name, amount, category, when)


class ModelTests(unittest.TestCase):
    def test_income_and_expense_balance_effects(self):
        self.assertEqual(income().get_balance_effect(), 1000)
        self.assertEqual(expense().get_balance_effect(), -250)
        with self.assertRaises(TypeError):
            Transaction("Abstract", 1, "income", "Salary", "2026-10-01")

    def test_stored_transaction_type_is_strict(self):
        record = {"description":"Bad row", "amount":10, "type":"transfer",
                  "category":"Other", "date":"2026-10-01"}
        with self.assertRaisesRegex(ValueError, "exactly 'income' or 'expense'"):
            transaction_from_dict(record)

    def test_account_balance_and_totals(self):
        account = Account([income(), expense()])
        self.assertEqual(account.get_balance(), 750)
        self.assertEqual(account.get_total_income(), 1000)
        self.assertEqual(account.get_total_expenses(), 250)

    def test_add_update_delete(self):
        account = Account(); first = income(); account.add_transaction(first)
        updated = ExpenseTransaction("Books", 300, "Education", "2026-10-03")
        account.update_transaction(first.id, updated)
        self.assertEqual(account.get_balance(), -300)
        self.assertEqual(account.remove_transaction(first.id).description, "Books")
        self.assertEqual(account.get_transactions(), [])
        with self.assertRaises(KeyError): account.remove_transaction("missing")

    def test_savings_and_zero_income(self):
        analyzer = FinancialAnalyzer(Account([income(800), expense(300)]))
        self.assertEqual(analyzer.calculate_savings(), 500)
        self.assertEqual(analyzer.calculate_savings_rate(), 62.5)
        no_income = FinancialAnalyzer(Account([expense()]))
        self.assertEqual(no_income.calculate_savings_rate(), Decimal("0.0"))
        self.assertEqual(no_income.calculate_average_monthly_savings_rate(), Decimal("0.0"))
        self.assertEqual(FinancialAnalyzer(Account()).calculate_average_monthly_expenses(), Decimal("0.00"))

    def test_decimal_paise_calculations(self):
        account = Account([income(Decimal("0.10")), income(Decimal("0.20")), expense(Decimal("0.10"))])
        self.assertEqual(account.get_total_income(), Decimal("0.30"))
        self.assertEqual(account.get_balance(), Decimal("0.20"))
        self.assertEqual(account.get_transactions()[0].to_dict()["amount"], 0.1)
        with self.assertRaisesRegex(ValueError, "two decimal places"):
            ExpenseTransaction("Fractional paise", Decimal("1.001"), "Food", "2026-10-02")

    def test_budget_calculations(self):
        rows = [expense(300, category="Food"), expense(150, category="Food", when="2026-09-01"), income()]
        budget = Budget("Food", 500, "2026-10")
        self.assertEqual(budget.calculate_spending(rows), 300)
        self.assertEqual(budget.remaining_budget(rows), 200)
        self.assertFalse(budget.get_budget_status(rows)["over_budget"])
        self.assertTrue(Budget("Food", 200, "2026-10").get_budget_status(rows)["over_budget"])

    def test_analytics(self):
        rows = [income(1000), expense(250, category="Food"), expense(350, category="Rent"), income(1000, when="2026-09-01")]
        analyzer = FinancialAnalyzer(Account(rows))
        self.assertEqual(analyzer.get_category_breakdown(), {"Rent": 350, "Food": 250})
        self.assertEqual(analyzer.get_highest_expense_category(), "Rent")
        self.assertEqual(analyzer.get_monthly_summary()["2026-10"]["savings"], 400)
        self.assertEqual(analyzer.compare_months("2026-10", "2026-09")["expense_change"], 600)

    def test_inactive_months_and_savings_rate_definitions(self):
        rows = [income(1000, "2026-01-10"), expense(500, "2026-01-11"),
                income(1000, "2026-03-10"), expense(500, "2026-03-11")]
        analyzer = FinancialAnalyzer(Account(rows))
        months = analyzer.get_monthly_summary()
        self.assertEqual(list(months), ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(months["2026-02"]["expenses"], Decimal("0.00"))
        self.assertEqual(analyzer.calculate_average_monthly_expenses(), Decimal("333.33"))
        self.assertEqual(analyzer.calculate_savings_rate(), Decimal("50.0"))
        self.assertEqual(analyzer.calculate_average_monthly_savings_rate(), Decimal("33.3"))

    def test_statement_opening_closing_and_period_filter(self):
        account = Account([income(1000, "2026-09-20"), expense(100, "2026-10-02"), income(500, "2026-10-04"), expense(75, "2026-11-01")])
        statement = StatementGenerator(account)
        self.assertEqual(statement.calculate_opening_balance("2026-10-01"), 1000)
        self.assertEqual(statement.calculate_closing_balance("2026-10-01", "2026-10-31"), 1400)
        data = statement.generate_statement_data("2026-10-01", "2026-10-31")
        self.assertEqual(len(data["transactions"]), 2)
        self.assertEqual(data["transactions"][-1]["running_balance"], 1400)
        with self.assertRaises(ValueError): statement.generate_statement_data("2026-11-01", "2026-10-01")

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError): ExpenseTransaction("", 5, "Food", "2026-10-01").validate()
        with self.assertRaises(ValueError): ExpenseTransaction("A", 0, "Food", "2026-10-01").validate()
        with self.assertRaises(ValueError): ExpenseTransaction("A", float("nan"), "Food", "2026-10-01").validate()
        with self.assertRaises(ValueError): ExpenseTransaction("A", 1, "Food", "not-a-date").validate()
        with self.assertRaises(ValueError): ExpenseTransaction("A", 1, "Food", "20261001").validate()
        with self.assertRaises(ValueError): Budget("Food", 0, "2026-10")

    def test_pdf_is_nonempty(self):
        pdf = StatementGenerator(Account([income(), expense()])).generate_pdf("2026-10-01", "2026-10-31")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 500)

    def test_pdf_embeds_unicode_font_and_wraps_long_text_across_pages(self):
        from reportlab.pdfbase.ttfonts import TTFont
        font_path = Path(__file__).resolve().parents[1] / "fonts" / "DejaVuSans.ttf"
        unicode_font = TTFont("TestUnicodeFont", str(font_path))
        self.assertIn(0x20B9, unicode_font.face.charWidths)

        rows = [income(Decimal("1234.56"), "2026-09-30", name="Opening credit")]
        for index in range(150):
            long_description = f"Course installment {index:03d} " + "international tuition and learning materials " * 2
            rows.append(expense(Decimal("0.10"), f"2026-10-{1 + index % 28:02d}",
                                category="Continuing education", name=long_description[:120]))
        statement = StatementGenerator(Account(rows))
        self.assertEqual(statement._format_currency(Decimal("1234.56")), "₹1,234.56")
        data = statement.generate_statement_data("2026-10-01", "2026-10-31")
        self.assertEqual(data["opening_balance"], Decimal("1234.56"))
        self.assertEqual(data["total_debits"], Decimal("15.00"))
        self.assertEqual(data["closing_balance"], Decimal("1219.56"))

        pdf = statement.generate_pdf("2026-10-01", "2026-10-31")
        self.assertIn(b"/FontFile2", pdf)
        self.assertGreaterEqual(len(re.findall(rb"/Type\s*/Page\b", pdf)), 2)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.path = Path(self.temp.name) / "ledger.json"
        self.storage = JSONStorage(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_malformed_json_is_reported_and_preserved(self):
        original = b'{"transactions":[{"type":"income"}'
        self.path.write_bytes(original)
        with self.assertRaisesRegex(StorageError, "malformed.*original file was left untouched"):
            self.storage.load()
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_file_shape_is_reported_and_preserved(self):
        original = b'{"transactions":{},"budgets":[]}'
        self.path.write_bytes(original)
        with self.assertRaisesRegex(StorageError, "transaction and budget arrays"):
            self.storage.load()
        self.assertEqual(self.path.read_bytes(), original)

    def test_unreadable_and_unwritable_storage_return_useful_errors(self):
        self.path.write_text('{"transactions":[],"budgets":[]}', encoding="utf-8")
        original = self.path.read_bytes()
        with patch.object(Path, "open", side_effect=PermissionError("read denied")):
            with self.assertRaisesRegex(StorageError, "could not be read"):
                self.storage.load()
        with patch("storage.os.replace", side_effect=PermissionError("replace denied")):
            with self.assertRaisesRegex(StorageError, "previous file was preserved"):
                self.storage.save({"transactions":[],"budgets":[]})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(Path(self.temp.name).glob("*.tmp")), [])


class ApiTests(unittest.TestCase):
    def setUp(self):
        # Keep this disposable file under the workspace, where the test runner can
        # atomically replace it even in restricted Windows sandbox environments.
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.original_storage = web.storage
        self.original_testing = web.app.testing
        self.original_default_path = web.DEFAULT_PATH
        web.storage = JSONStorage(Path(self.temp.name) / "isolated.json")
        sample = json.loads((Path(__file__).resolve().parents[1] / "data" / "demo_data.json").read_text(encoding="utf-8"))
        web.storage.save(sample)
        web.DEFAULT_PATH = Path(self.temp.name) / "reset-sample.json"
        web.DEFAULT_PATH.write_text(json.dumps(sample), encoding="utf-8")
        web.app.config["TESTING"] = True
        self.client = web.app.test_client()

    def tearDown(self):
        web.storage = self.original_storage
        web.app.config["TESTING"] = self.original_testing
        web.DEFAULT_PATH = self.original_default_path
        self.temp.cleanup()

    def test_api_responses_and_status_codes(self):
        for path in ("/", "/style.css", "/app.js"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            response.close()
        self.assertEqual(self.client.get("/api/dashboard").status_code, 200)
        self.assertEqual(self.client.get("/api/transactions").status_code, 200)
        invalid = self.client.post("/api/transactions", json={"description":"Bad", "amount":-3, "type":"expense", "category":"Food", "date":"2026-10-01"})
        self.assertEqual(invalid.status_code, 400)
        created = self.client.post("/api/transactions", json={"description":"Test", "amount":10, "type":"income", "category":"Other", "date":"2026-10-27"})
        self.assertEqual(created.status_code, 201)
        txid = created.json["transaction"]["id"]
        self.assertEqual(self.client.put(f"/api/transactions/{txid}", json={"description":"Edited", "amount":11, "type":"income", "category":"Other", "date":"2026-10-27"}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/transactions/{txid}").status_code, 200)
        self.assertEqual(self.client.delete("/api/transactions/missing").status_code, 404)
        self.assertEqual(self.client.get("/api/transactions?type=unknown").status_code, 400)
        self.assertEqual(self.client.get("/api/transactions?start_date=20261001").status_code, 400)
        decimal_response = self.client.post("/api/transactions", json={"description":"Paise check", "amount":0.30, "type":"income", "category":"Other", "date":"2026-10-27"})
        self.assertEqual(decimal_response.status_code, 201)
        self.assertEqual(decimal_response.json["transaction"]["amount"], 0.3)

    def test_budget_analytics_classes_routes(self):
        self.assertEqual(self.client.get("/api/budgets").status_code, 200)
        self.assertEqual(self.client.post("/api/budgets", json={"category":"Books", "limit":0, "month":"2026-10"}).status_code, 400)
        self.assertEqual(self.client.post("/api/budgets", json={"category":"Books", "limit":300, "month":"2026-10"}).status_code, 201)
        self.assertEqual(self.client.get("/api/analytics").status_code, 200)
        classes = self.client.get("/api/classes")
        self.assertEqual(classes.status_code, 200)
        by_name = {item["name"]: item for item in classes.json["classes"]}
        self.assertIn("FinancialAnalyzer", by_name)
        self.assertIn("generate_pdf", by_name["StatementGenerator"]["method_highlights"]["statement"])
        self.assertEqual(self.client.post("/api/reset-demo").status_code, 200)

    def test_analytics_route_zero_income_and_inactive_months(self):
        rows = [expense(Decimal("12.34"), "2026-01-10"),
                income(Decimal("100.00"), "2026-03-10"),
                expense(Decimal("25.00"), "2026-03-11")]
        data = {"transactions":[row.to_dict() for row in rows], "budgets":[]}
        web.storage.save(data)
        result = self.client.get("/api/analytics").json
        self.assertEqual(result["monthly"]["2026-02"]["expenses"], 0)
        self.assertEqual(result["overall_savings_rate"], 62.7)
        self.assertEqual(result["average_monthly_savings_rate"], 25.0)
        self.assertEqual(result["average_monthly_expenses"], 12.45)

        web.storage.save({"transactions":[expense().to_dict()], "budgets":[]})
        no_income = self.client.get("/api/analytics").json
        self.assertEqual(no_income["overall_savings_rate"], 0)
        self.assertEqual(no_income["average_monthly_savings_rate"], 0)

    def test_stored_invalid_type_and_malformed_json_are_503_and_preserved(self):
        path = web.storage.path
        invalid_type = b'{"transactions":[{"description":"bad","amount":1,"type":"transfer","category":"Other","date":"2026-10-01"}],"budgets":[]}'
        path.write_bytes(invalid_type)
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 503)
        self.assertIn("type must be exactly", response.json["error"].lower())
        self.assertEqual(path.read_bytes(), invalid_type)

        malformed = b'{"transactions":['
        path.write_bytes(malformed)
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 503)
        self.assertIn("malformed", response.json["error"].lower())
        self.assertEqual(path.read_bytes(), malformed)

    def test_storage_failures_return_service_error(self):
        with patch.object(web.storage, "load", side_effect=StorageError("read denied")):
            response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 503)
        self.assertIn("read denied", response.json["error"])

        with patch.object(web.storage, "save", side_effect=StorageError("disk full")):
            response = self.client.post("/api/transactions", json={"description":"Save failure", "amount":10,
                "type":"expense", "category":"Food", "date":"2026-10-01"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("disk full", response.json["error"])

    def test_statement_route_validation_and_pdf(self):
        self.assertEqual(self.client.post("/api/statements/pdf", json={"start_date":"2026-10-31", "end_date":"2026-10-01"}).status_code, 400)
        invalid_format = self.client.post("/api/statements/pdf", json={"start_date":"20261001", "end_date":"2026-10-31"})
        self.assertEqual(invalid_format.status_code, 400)
        response = self.client.post("/api/statements/pdf", json={"start_date":"2026-10-01", "end_date":"2026-10-31"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF"))
        with patch.object(web.storage, "load", side_effect=StorageError("statement storage unavailable")):
            failed = self.client.post("/api/statements/pdf", json={"start_date":"2026-10-01", "end_date":"2026-10-31"})
        self.assertEqual(failed.status_code, 503)
        self.assertIn("statement storage unavailable", failed.json["error"])


if __name__ == "__main__": unittest.main()
