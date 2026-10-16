from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class Calibration:
    scale: float
    offset: float

    def apply(self, value: float) -> float:
        if not all(isfinite(number) for number in (value, self.scale, self.offset)):
            raise ValueError("Calibration parameters and measurements must be finite")
        return value * self.scale + self.offset
