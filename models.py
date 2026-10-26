"""Core domain objects for PaisaLedger. Keep financial rules here, not in routes."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable
import uuid

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_AMOUNT = Decimal("1000000000.00")


def _as_money(value, label: str = "Amount") -> Decimal:
    """Convert JSON/Python numbers to exact INR decimal values (paise precision)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{label} must be a number greater than zero.")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        if not amount.is_finite() or amount <= ZERO:
            raise ValueError(f"{label} must be a finite number greater than zero.")
        if amount > MAX_AMOUNT:
            raise ValueError(f"{label} cannot exceed ₹1,00,00,00,000.")
        paise_amount = amount.quantize(CENT)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a valid amount with at most two decimal places.") from exc
    if paise_amount != amount:
        raise ValueError(f"{label} must have no more than two decimal places.")
    return paise_amount


def _as_date(value: str | date) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        parsed = date.fromisoformat(str(value))
        if parsed.isoformat() != str(value):
            raise ValueError
        return parsed
    except (TypeError, ValueError) as exc:
        raise ValueError("Date must be a valid YYYY-MM-DD date.") from exc


class Transaction(ABC):
    """One dated credit or debit; subclasses supply the signed balance effect."""
    VALID_TYPES = {"income", "expense"}
    VALID_METHODS = {"Cash", "UPI", "Card", "Bank transfer", "Other"}

    def __init__(self, description: str, amount: float | Decimal, type: str, category: str,
                 date: str, payment_method: str = "UPI", notes: str = "", id: str | None = None):
        self.id = str(id or uuid.uuid4())
        self.description = str(description).strip()
        self.amount = _as_money(amount)
        self.type = str(type).lower().strip()
        self.category = str(category).strip()
        self.date = _as_date(date).isoformat()
        self.payment_method = str(payment_method).strip()
        self.notes = str(notes).strip()

    def validate(self) -> bool:
        if not self.description or len(self.description) > 120:
            raise ValueError("Description is required and must be at most 120 characters.")
        if self.type not in self.VALID_TYPES:
            raise ValueError("Type must be income or expense.")
        if not self.category or len(self.category) > 60:
            raise ValueError("Category is required and must be at most 60 characters.")
        _as_date(self.date)
        if self.payment_method not in self.VALID_METHODS:
            raise ValueError("Choose a valid payment method.")
        if len(self.notes) > 500:
            raise ValueError("Notes must be at most 500 characters.")
        return True

    def is_income(self) -> bool:
        return self.type == "income"

    def is_expense(self) -> bool:
        return self.type == "expense"

    @abstractmethod
    def get_balance_effect(self) -> Decimal:
        """Return a positive credit or negative debit. Concrete types supply this rule."""
        raise NotImplementedError

    def get_display_label(self) -> str:
        return f"{self.description} · {self.category}"

    def to_dict(self) -> dict:
        # JSON consumers receive a number; two-decimal Decimal values round-trip as paise.
        return {"id": self.id, "description": self.description, "amount": float(self.amount),
                "type": self.type, "category": self.category, "date": self.date,
                "payment_method": self.payment_method, "notes": self.notes}


class IncomeTransaction(Transaction):
    def __init__(self, description, amount, category, date, payment_method="UPI", notes="", id=None):
        super().__init__(description, amount, "income", category, date, payment_method, notes, id)

    def get_balance_effect(self) -> Decimal:
        return self.amount


class ExpenseTransaction(Transaction):
    def __init__(self, description, amount, category, date, payment_method="UPI", notes="", id=None):
        super().__init__(description, amount, "expense", category, date, payment_method, notes, id)

    def get_balance_effect(self) -> Decimal:
        return -self.amount


def transaction_from_dict(data: dict) -> Transaction:
    if not isinstance(data, dict):
        raise ValueError("Stored transaction must be a JSON object.")
    kind = data.get("type")
    if kind not in Transaction.VALID_TYPES:
        raise ValueError("Stored transaction type must be exactly 'income' or 'expense'.")
    cls = IncomeTransaction if kind == "income" else ExpenseTransaction
    return cls(data.get("description", ""), data.get("amount"), data.get("category", ""),
               data.get("date", ""), data.get("payment_method", "UPI"), data.get("notes", ""), data.get("id"))


class Account:
    """Owns transactions and provides the account totals."""
    def __init__(self, transactions: Iterable[Transaction] = ()):
        self._transactions = []
        for tx in transactions:
            self.add_transaction(tx)

    def add_transaction(self, transaction: Transaction) -> Transaction:
        transaction.validate()
        if any(tx.id == transaction.id for tx in self._transactions):
            raise ValueError("A transaction with this ID already exists.")
        self._transactions.append(transaction)
        return transaction

    def update_transaction(self, transaction_id: str, replacement: Transaction) -> Transaction:
        replacement.validate()
        for index, existing in enumerate(self._transactions):
            if existing.id == transaction_id:
                replacement.id = transaction_id
                self._transactions[index] = replacement
                return replacement
        raise KeyError("Transaction not found.")

    def remove_transaction(self, transaction_id: str) -> Transaction:
        for index, transaction in enumerate(self._transactions):
            if transaction.id == transaction_id:
                return self._transactions.pop(index)
        raise KeyError("Transaction not found.")

    def get_balance(self) -> Decimal:
        return sum((tx.get_balance_effect() for tx in self._transactions), ZERO)

    def get_total_income(self) -> Decimal:
        return sum((tx.amount for tx in self._transactions if tx.is_income()), ZERO)

    def get_total_expenses(self) -> Decimal:
        return sum((tx.amount for tx in self._transactions if tx.is_expense()), ZERO)

    def get_transactions(self) -> list[Transaction]:
        return list(self._transactions)


class Budget:
    def __init__(self, category: str, limit: float | Decimal, month: str):
        self.category, self.limit, self.month = str(category).strip(), _as_money(limit, "Budget limit"), str(month)
        if not self.category or len(self.category) > 60:
            raise ValueError("Budget category is required.")
        try:
            if datetime.strptime(self.month, "%Y-%m").strftime("%Y-%m") != self.month:
                raise ValueError
        except ValueError as exc:
            raise ValueError("Budget month must use YYYY-MM format.") from exc

    def calculate_spending(self, transactions: Iterable[Transaction]) -> Decimal:
        return sum((tx.amount for tx in transactions if tx.is_expense() and
                    tx.category.casefold() == self.category.casefold() and tx.date[:7] == self.month), ZERO)

    def remaining_budget(self, transactions: Iterable[Transaction]) -> Decimal:
        return self.limit - self.calculate_spending(transactions)

    def get_budget_status(self, transactions: Iterable[Transaction]) -> dict:
        spent = self.calculate_spending(transactions)
        return {"category": self.category, "month": self.month, "limit": self.limit,
                "spent": spent, "remaining": self.limit - spent,
                "percentage_used": (spent / self.limit * 100).quantize(Decimal("0.1")),
                "over_budget": spent > self.limit}


class FinancialAnalyzer:
    def __init__(self, account: Account):
        self.account = account

    def calculate_savings(self, transactions: Iterable[Transaction] | None = None) -> Decimal:
        rows = list(transactions if transactions is not None else self.account.get_transactions())
        income = sum((t.amount for t in rows if t.is_income()), ZERO)
        expenses = sum((t.amount for t in rows if t.is_expense()), ZERO)
        return income - expenses

    def calculate_savings_rate(self, transactions: Iterable[Transaction] | None = None) -> Decimal:
        """Overall rate: aggregate net savings divided by aggregate income."""
        rows = list(transactions if transactions is not None else self.account.get_transactions())
        income = sum((t.amount for t in rows if t.is_income()), ZERO)
        return (self.calculate_savings(rows) / income * 100).quantize(Decimal("0.1")) if income else Decimal("0.0")

    def calculate_average_monthly_savings_rate(self, transactions: Iterable[Transaction] | None = None) -> Decimal:
        """Arithmetic mean of monthly rates, including zero-income and inactive months as 0%."""
        summaries = self.get_monthly_summary(transactions)
        if not summaries:
            return Decimal("0.0")
        return (sum((month["savings_rate"] for month in summaries.values()), Decimal("0.0")) /
                len(summaries)).quantize(Decimal("0.1"))

    def calculate_average_monthly_expenses(self, transactions: Iterable[Transaction] | None = None) -> Decimal:
        """Average expenses from the first transaction month through the last, including gaps."""
        summaries = self.get_monthly_summary(transactions)
        if not summaries:
            return ZERO
        total = sum((month["expenses"] for month in summaries.values()), ZERO)
        return (total / len(summaries)).quantize(CENT)

    def get_category_breakdown(self, transactions: Iterable[Transaction] | None = None) -> dict[str, Decimal]:
        result = defaultdict(lambda: ZERO)
        for tx in transactions if transactions is not None else self.account.get_transactions():
            if tx.is_expense(): result[tx.category] += tx.amount
        return dict(sorted(result.items(), key=lambda item: item[1], reverse=True))

    def get_monthly_summary(self, transactions: Iterable[Transaction] | None = None) -> dict[str, dict]:
        rows = list(transactions if transactions is not None else self.account.get_transactions())
        if not rows:
            return {}
        months = defaultdict(lambda: {"income": ZERO, "expenses": ZERO})
        for tx in rows:
            key = tx.date[:7]
            months[key]["income" if tx.is_income() else "expenses"] += tx.amount

        # Include every calendar month between the first and last transaction.
        first = datetime.strptime(min(months), "%Y-%m").date().replace(day=1)
        last = datetime.strptime(max(months), "%Y-%m").date().replace(day=1)
        summaries = {}
        cursor = first
        while cursor <= last:
            month = cursor.strftime("%Y-%m")
            values = months[month]
            savings = values["income"] - values["expenses"]
            rate = (savings / values["income"] * 100).quantize(Decimal("0.1")) if values["income"] else Decimal("0.0")
            summaries[month] = {**values, "savings": savings, "savings_rate": rate}
            cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
        return summaries

    def get_highest_expense_category(self, transactions: Iterable[Transaction] | None = None) -> str | None:
        breakdown = self.get_category_breakdown(transactions)
        return next(iter(breakdown), None)

    def compare_months(self, current_month: str, previous_month: str, transactions: Iterable[Transaction] | None = None) -> dict:
        summaries = self.get_monthly_summary(transactions)
        empty = {"income": ZERO, "expenses": ZERO, "savings": ZERO, "savings_rate": Decimal("0.0")}
        current = summaries.get(current_month, empty)
        previous = summaries.get(previous_month, empty)
        return {"current_month": current_month, "previous_month": previous_month, "current": current, "previous": previous,
                "expense_change": current["expenses"] - previous["expenses"], "income_change": current["income"] - previous["income"]}


class StatementGenerator:
    def __init__(self, account: Account): self.account = account

    @staticmethod
    def _format_currency(value: Decimal) -> str:
        return f"₹{value:,.2f}"

    def _period(self, start_date: str, end_date: str) -> tuple[date, date]:
        start, end = _as_date(start_date), _as_date(end_date)
        if start > end: raise ValueError("Start date must be on or before end date.")
        return start, end

    def calculate_opening_balance(self, start_date: str) -> Decimal:
        start = _as_date(start_date)
        return sum((tx.get_balance_effect() for tx in self.account.get_transactions() if _as_date(tx.date) < start), ZERO)

    def calculate_closing_balance(self, start_date: str, end_date: str) -> Decimal:
        start, end = self._period(start_date, end_date)
        period_effect = sum((tx.get_balance_effect() for tx in self.account.get_transactions()
                             if start <= _as_date(tx.date) <= end), ZERO)
        return self.calculate_opening_balance(start.isoformat()) + period_effect

    def generate_statement_data(self, start_date: str, end_date: str) -> dict:
        start, end = self._period(start_date, end_date)
        rows = sorted((tx for tx in self.account.get_transactions() if start <= _as_date(tx.date) <= end), key=lambda tx: (tx.date, tx.id))
        opening = self.calculate_opening_balance(start.isoformat())
        balance = opening; entries = []; credits = debits = ZERO
        for tx in rows:
            effect = tx.get_balance_effect(); balance += effect
            if tx.is_income(): credits += tx.amount
            else: debits += tx.amount
            entries.append({**tx.to_dict(), "debit": tx.amount if tx.is_expense() else ZERO,
                            "credit": tx.amount if tx.is_income() else ZERO, "running_balance": balance})
        return {"start_date": start.isoformat(), "end_date": end.isoformat(), "generated_on": date.today().isoformat(),
                "opening_balance": opening, "total_credits": credits, "total_debits": debits,
                "closing_balance": balance, "transactions": entries}

    def generate_pdf(self, start_date: str, end_date: str) -> bytes:
        from io import BytesIO
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from xml.sax.saxutils import escape

        data = self.generate_statement_data(start_date, end_date)
        font_path = Path(__file__).resolve().parent / "fonts" / "DejaVuSans.ttf"
        if not font_path.exists():
            raise RuntimeError("The bundled DejaVu Sans Unicode font is missing.")
        font_name = "PaisaLedgerUnicode"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))

        buffer = BytesIO()
        page = landscape(A4)
        doc = SimpleDocTemplate(buffer, pagesize=page, rightMargin=12*mm, leftMargin=12*mm,
                                topMargin=14*mm, bottomMargin=16*mm)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("LedgerTitle", parent=styles["Title"], fontName=font_name,
                                     textColor=colors.HexColor("#1F5A45"), alignment=0)
        body_style = ParagraphStyle("LedgerCell", parent=styles["Normal"], fontName=font_name,
                                    fontSize=7.5, leading=9, wordWrap="CJK")
        right_style = ParagraphStyle("LedgerNumber", parent=body_style, alignment=2)
        header_style = ParagraphStyle("LedgerHeader", parent=body_style, fontName="Helvetica-Bold",
                                      textColor=colors.white, alignment=0)

        def paragraph(value, style=body_style):
            return Paragraph(escape(str(value)), style)

        def money(value):
            return self._format_currency(value)

        story = [Paragraph("PaisaLedger", title_style),
                 paragraph(f"Account statement · {data['start_date']} to {data['end_date']} · Generated {data['generated_on']}"),
                 Spacer(1, 7*mm)]
        summary_labels = ["Opening balance", "Total credits", "Total debits", "Closing balance"]
        summary_values = [data["opening_balance"], data["total_credits"], data["total_debits"], data["closing_balance"]]
        summary = [[paragraph(label, header_style) for label in summary_labels],
                   [paragraph(money(value), right_style) for value in summary_values]]
        summary_table = Table(summary, colWidths=[page[0]/4-7*mm]*4)
        summary_table.setStyle(TableStyle([("BACKGROUND", (0,0),(-1,0), colors.HexColor("#1F5A45")),
                                           ("GRID", (0,0),(-1,-1),0.5,colors.HexColor("#1C1A17")),
                                           ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                           ("PADDING",(0,0),(-1,-1),7)]))
        story += [summary_table, Spacer(1, 7*mm)]

        headings = ["Date", "Description", "Category", "Payment method", "Debit", "Credit", "Balance"]
        rows = [[paragraph(value, header_style) for value in headings]]
        for tx in data["transactions"]:
            rows.append([paragraph(tx["date"]), paragraph(tx["description"]),
                         paragraph(tx["category"]), paragraph(tx["payment_method"]),
                         paragraph(money(tx["debit"]) if tx["debit"] else "—", right_style),
                         paragraph(money(tx["credit"]) if tx["credit"] else "—", right_style),
                         paragraph(money(tx["running_balance"]), right_style)])
        table = Table(rows, repeatRows=1, colWidths=[24*mm, 56*mm, 34*mm, 32*mm, 29*mm, 29*mm, 31*mm])
        table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1F5A45")),
                                   ("GRID",(0,0),(-1,-1),0.35,colors.HexColor("#777066")),
                                   ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#F3ECDC")]),
                                   ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("PADDING",(0,0),(-1,-1),5)]))
        story.append(table)
        def footer(canvas, document):
            canvas.saveState()
            canvas.setFont(font_name, 8)
            canvas.setFillColor(colors.HexColor("#55504A"))
            canvas.drawRightString(page[0]-12*mm, 8*mm, f"Page {document.page}")
            canvas.restoreState()
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        return buffer.getvalue()

