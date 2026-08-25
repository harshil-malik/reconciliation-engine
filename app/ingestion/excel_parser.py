from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

from app.ingestion.normalize import normalize_dataframe
from app.schema import Transaction


def parse_excel(
    path: str | Path, *, source: Literal["bank", "ledger"], file_name: str | None = None
) -> list[Transaction]:
    path = Path(path)
    df = pd.read_excel(path, dtype=str)
    return normalize_dataframe(df, source=source, file_name=file_name or path.name)
