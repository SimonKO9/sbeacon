from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .instruments import Instrument


class EventType(StrEnum):
    TRADE = "trade"
    DIVIDEND = "dividend"
    FEE = "fee"
    INTEREST = "interest"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    FX_CONVERSION = "fx"
    TAX = "tax"
    CORPORATE_ACTION = "corp"
    TRANSFER = "transfer"


@dataclass(frozen=True)
class SourceRef:
    file: Path
    sheet: str
    row: int
    raw: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": str(self.file),
            "sheet": self.sheet,
            "row": self.row,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SourceRef:
        return cls(
            file=Path(d["file"]),
            sheet=d["sheet"],
            row=d["row"],
            raw=d.get("raw", {}),
        )


@dataclass(frozen=True)
class Event:
    id: str
    account_id: str
    timestamp: datetime
    type: EventType
    amount: Decimal
    currency: str
    source: SourceRef
    instrument: Instrument | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    fees: Decimal = Decimal(0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "timestamp": self.timestamp.isoformat(),
            "type": self.type.value,
            "amount": str(self.amount),
            "currency": self.currency,
            "source": self.source.to_dict(),
            "instrument": self.instrument.to_dict() if self.instrument else None,
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "price": str(self.price) if self.price is not None else None,
            "fees": str(self.fees),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        from .instruments import Instrument

        return cls(
            id=d["id"],
            account_id=d["account_id"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            type=EventType(d["type"]),
            amount=Decimal(d["amount"]),
            currency=d["currency"],
            source=SourceRef.from_dict(d["source"]),
            instrument=Instrument.from_dict(d["instrument"]) if d.get("instrument") else None,
            quantity=Decimal(d["quantity"]) if d.get("quantity") is not None else None,
            price=Decimal(d["price"]) if d.get("price") is not None else None,
            fees=Decimal(d.get("fees", "0")),
        )
