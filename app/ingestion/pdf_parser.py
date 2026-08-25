from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

from app.ingestion.amounts import to_decimal
from app.ingestion.bank_templates.base import BankPDFTemplate
from app.ingestion.vision_client import VisionExtractor
from app.schema import Transaction


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

    response_text = extractor.extract(pdf_bytes, template.build_prompt())
    raw_rows = template.parse_response(response_text)

    transactions: list[Transaction] = []
    for raw_row in raw_rows:
        parsed_date = pd.to_datetime(
            raw_row["date"], dayfirst=True, errors="raise"
        ).date()
        amount = to_decimal(raw_row.get("credit", 0)) - to_decimal(raw_row.get("debit", 0))
        reference = raw_row.get("reference")

        transactions.append(
            Transaction(
                date=parsed_date,
                amount=amount,
                description=str(raw_row.get("description", "")).strip(),
                reference=str(reference).strip() if reference else None,
                source=source,
                file_name=file_name or path.name,
                raw_row=raw_row,
            )
        )

    return transactions
