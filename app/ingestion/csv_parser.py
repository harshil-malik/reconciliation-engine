from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

from app.ingestion.normalize import normalize_dataframe
from app.schema import Transaction


def parse_csv(
    path: str | Path, *, source: Literal["bank", "ledger"], file_name: str | None = None
) -> list[Transaction]:
    path = Path(path)
    # dtype=str keeps every cell as originally printed (e.g. "1,234.50") so raw_row
    # stays a faithful copy for traceability; amount parsing handles the formatting.
    df = pd.read_csv(path, dtype=str)
    return normalize_dataframe(df, source=source, file_name=file_name or path.name)
