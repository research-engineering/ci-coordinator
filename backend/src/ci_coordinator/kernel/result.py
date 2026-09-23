from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Ok[T]:
    value: T

    @property
    def is_ok(self) -> Literal[True]:
        return True

    @property
    def is_err(self) -> Literal[False]:
        return False


@dataclass(frozen=True)
class Err:
    code: str
    message: str

    @property
    def is_ok(self) -> Literal[False]:
        return False

    @property
    def is_err(self) -> Literal[True]:
        return True


type ResultValue[T] = Ok[T] | Err
