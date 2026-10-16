import csv
from pathlib import Path

from calibration.model import Calibration


def main() -> None:
    project = Path(__file__).resolve().parent
    calibration = Calibration(scale=0.5, offset=-1.0)
    with (project / "data" / "measurements.csv").open(newline="") as source:
        measurements = list(csv.DictReader(source))

    rows = [
        {
            "measurement_id": row["measurement_id"],
            "calibrated_value": f"{calibration.apply(float(row['raw_value'])):.3f}",
        }
        for row in measurements
    ]
    output = project / "outputs" / "calibrated.csv"
    output.parent.mkdir(exist_ok=True)
    with output.open("w", newline="") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=["measurement_id", "calibrated_value"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(output.read_text(), end="")


if __name__ == "__main__":
    main()
