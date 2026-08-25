from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def bank_df() -> pd.DataFrame:
    """Synthetic HDFC-style bank statement export: split Withdrawal/Deposit columns."""
    return pd.DataFrame(
        [
            {
                "Date": "01/04/24",
                "Narration": "NEFT-AXISBANK-VENDOR PAYMENT",
                "Chq/Ref No": "N123456789",
                "Withdrawal Amt": "15000.00",
                "Deposit Amt": "",
            },
            {
                "Date": "03/04/24",
                "Narration": "UPI-CUSTOMER-INV1042",
                "Chq/Ref No": "U987654321",
                "Withdrawal Amt": "",
                "Deposit Amt": "25000.00",
            },
        ]
    )


@pytest.fixture
def ledger_df() -> pd.DataFrame:
    """Synthetic internal ledger export: single signed Amount column."""
    return pd.DataFrame(
        [
            {
                "Txn Date": "01-04-2024",
                "Particulars": "Vendor Payment - ABC Supplies",
                "Voucher No": "N123456789",
                "Amount": "-15000",
            },
            {
                "Txn Date": "03-04-2024",
                "Particulars": "Invoice 1042 - Customer XYZ",
                "Voucher No": "U987654321",
                "Amount": "25000",
            },
        ]
    )
