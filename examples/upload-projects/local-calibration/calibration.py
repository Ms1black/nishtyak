from dataclasses import dataclass


@dataclass
class Calibration:
    scale: float
    offset: float

    def apply(self, value: float) -> float:
        return value * self.scale + self.offset
