from __future__ import annotations

from pydantic import BaseModel

from app.schema import Transaction


class AIMatchedPair(BaseModel):
    bank_transaction: Transaction
    ledger_transaction: Transaction
    confidence: float
    reasoning: str


class AIMatchResult(BaseModel):
    ai_matched: list[AIMatchedPair]
    # final leftovers after the AI net -> CA review queue, bucket "unmatched"
    # (an efficiency problem, not a risk flag — see app/anomaly for the risk bucket)
    unmatched_bank: list[Transaction]
    unmatched_ledger: list[Transaction]
