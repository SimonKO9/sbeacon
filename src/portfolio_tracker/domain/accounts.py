from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Wrapper(StrEnum):
    REGULAR = "regular"
    IKE = "ike"
    IKZE = "ikze"


@dataclass(frozen=True)
class Account:
    account_id: str
    broker: str
    wrapper: Wrapper
    base_currency: str
