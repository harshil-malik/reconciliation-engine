from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Optional

import pandas as pd

from app.ingestion.amounts import to_decimal
from app.ingestion.bank_templates.base import BankPDFTemplate
from app.ingestion.vision_client import VisionExtractor
from app.schema import Transaction

logger = logging.getLogger(__name__)


class PDFExtractionError(ValueError):
    """Raised when extracted rows cannot be trusted.

    Preferred over returning best-effort numbers: a reconciliation report built on
    silently wrong amounts is worse than no report, because nothing downstream can
    tell that the figures are fiction.
    """


def _is_phantom_row(raw_row: dict) -> bool:
    """True for rows that are extraction artifacts rather than transactions.

    The common artifact is a wrapped narration line, or a stray table cell, being
    emitted as its own row — which surfaces as a "transaction" whose description is
    a bare number, or which carries no date at all.

    A blank description alone is NOT disqualifying: real statements do contain rows
    with an amount but no narration, and dropping those loses money from the
    reconciliation. Such a row is only an artifact if it carries no figures either.
    """
    if not str(raw_row.get("date") or "").strip():
        return True

    description = str(raw_row.get("description") or "").strip()
    # A description that is only digits/separators/currency is a misread cell.
    if description and re.fullmatch(r"[\d.,\s₹Rs]+", description, flags=re.IGNORECASE):
        return True

    if description:
        return False
    has_figures = _parse_balance(raw_row.get("balance")) is not None or any(
        to_decimal(raw_row.get(field) or 0) != 0 for field in ("debit", "credit")
    )
    return not has_figures


def _parse_balance(value: object) -> Optional[Decimal]:
    if value is None or str(value).strip() in ("", "-", "null", "None"):
        return None
    try:
        return to_decimal(value)
    except (ValueError, InvalidOperation):
        return None


def _reconcile_against_balances(
    rows: list[dict], amounts: list[Decimal], *, file_name: str
) -> list[Decimal]:
    """Audit each amount against the statement's own running balance.

    Consecutive closing balances differ by exactly the transaction amount, so the
    balance column is an independent, arithmetic check on what the model read out of
    the debit/credit columns — and it is immune to the failure that motivated it,
    where a flattened table led to a running balance being reported as the amount.

    It is also sign-convention agnostic: a rising balance means money in, whether the
    source labels that a credit (bank statement) or a debit (bank-account ledger).

    When the two readings disagree, the balance delta wins only if it is corroborated
    — its magnitude must match a number the model actually saw on that row, meaning
    only the column or sign was misread. If the two disagree outright, neither can be
    trusted and extraction fails loudly.
    """
    balances = [_parse_balance(row.get("balance")) for row in rows]
    corrected = list(amounts)
    corrections: list[str] = []

    # An opening balance, when the statement prints one, gives the first row the
    # predecessor it otherwise lacks — so row 1 gets audited like every other row
    # instead of being taken on trust from its column position alone.
    opening = _parse_balance(rows[0].get("opening_balance")) if rows else None
    start_index = 1
    if opening is not None:
        balances = [opening, *balances]
        rows = [{"date": "opening", "description": "opening balance"}, *rows]
        corrected = [Decimal("0"), *corrected]
        amounts = [Decimal("0"), *amounts]
    elif rows and balances[0] is not None:
        logger.warning(
            "%s prints no opening balance, so the first row (%s %r, %s) cannot be "
            "checked against a balance movement and rests on its column position "
            "alone — verify it against the statement",
            file_name,
            rows[0].get("date"),
            str(rows[0].get("description"))[:40],
            amounts[0],
        )

    for i in range(start_index, len(rows)):
        previous, current = balances[i - 1], balances[i]
        if previous is None or current is None:
            continue

        delta = current - previous
        if delta == amounts[i]:
            continue

        # No amount was printed in either money column, but the balance moved: the
        # figure is missing from the source text (some statements omit it, and some
        # PDFs simply do not render it), and the delta is the only record of what
        # happened. Nothing contradicts it, so take it.
        if amounts[i] == 0:
            corrections.append(
                f"row {i + 1} ({rows[i].get('date')} {str(rows[i].get('description'))[:30]!r}): "
                f"no amount printed, recovered {delta} from the balance movement"
            )
            corrected[i] = delta
            continue

        seen = {
            abs(to_decimal(rows[i].get("debit") or 0)),
            abs(to_decimal(rows[i].get("credit") or 0)),
        }
        if abs(delta) in seen and abs(delta) != 0:
            corrections.append(
                f"row {i + 1} ({rows[i].get('date')} {str(rows[i].get('description'))[:30]!r}): "
                f"read {amounts[i]}, balance delta says {delta}"
            )
            corrected[i] = delta
            continue

        raise PDFExtractionError(
            f"Extraction from {file_name} is inconsistent and was rejected rather than "
            f"reported. Row {i + 1} ({rows[i].get('date')} "
            f"{str(rows[i].get('description'))[:40]!r}) was read as {amounts[i]}, but the "
            f"closing balance moved by {delta} ({previous} -> {current}). The transaction "
            "amount and the running balance disagree, so neither can be trusted — the "
            "table was probably misread. Check the statement against the extracted rows, "
            "or use a hosted vision extractor for this file."
        )

    if corrections:
        logger.warning(
            "Corrected %d amount(s) in %s against the running balance: %s",
            len(corrections),
            file_name,
            "; ".join(corrections),
        )
    # Drop the synthetic opening-balance row added above, if any.
    return corrected[1:] if opening is not None else corrected


def parse_pdf(
    path: str | Path,
    *,
    source: Literal["bank", "ledger"],
    template: BankPDFTemplate,
    extractor: VisionExtractor,
    file_name: str | None = None,
) -> list[Transaction]:
    path = Path(path)
    pdf_bytes = path.read_bytes()
    resolved_name = file_name or path.name

    response_text = extractor.extract(pdf_bytes, template.build_prompt())
    raw_rows = template.parse_response(response_text)

    rows = [row for row in raw_rows if not _is_phantom_row(row)]
    if skipped := len(raw_rows) - len(rows):
        logger.info(
            "Skipped %d non-transaction row(s) from %s (wrapped narration or misread cell)",
            skipped,
            resolved_name,
        )

    # Which column means "money in" depends on whose books these are. A bank
    # statement is written from the bank's side, so a Deposit (credit) is money in.
    # A bank-account ledger in the client's own books is the mirror image: a Debit
    # to the bank asset account is money in. Templates declare their own convention.
    debit_is_inflow = getattr(template, "debit_is_inflow", False)

    amounts: list[Decimal] = []
    for raw_row in rows:
        debit = to_decimal(raw_row.get("debit", 0))
        credit = to_decimal(raw_row.get("credit", 0))
        amounts.append(debit - credit if debit_is_inflow else credit - debit)

    amounts = _reconcile_against_balances(rows, amounts, file_name=resolved_name)

    transactions: list[Transaction] = []
    for raw_row, amount in zip(rows, amounts):
        parsed_date = pd.to_datetime(
            raw_row["date"], dayfirst=True, errors="raise"
        ).date()
        reference = raw_row.get("reference")

        # Some statements print an amount with no narration at all. Marked
        # explicitly rather than left blank so a CA reading the report can tell
        # "the statement said nothing here" apart from "extraction lost the text".
        description = str(raw_row.get("description") or "").strip() or "(no narration)"

        extractor_refs = getattr(template, "extra_references", None)
        alt_references = list(extractor_refs(description)) if extractor_refs else []

        transactions.append(
            Transaction(
                date=parsed_date,
                amount=amount,
                description=description,
                alt_references=alt_references,
                reference=str(reference).strip() if reference else None,
                source=source,
                file_name=resolved_name,
                raw_row=raw_row,
            )
        )

    return transactions
