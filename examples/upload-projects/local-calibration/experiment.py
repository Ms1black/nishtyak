import csv

from calibration import Calibration


def main() -> None:
    calibration = Calibration(scale=0.5, offset=-1.0)
    with open("measurements.csv", newline="") as source:
        measurements = list(csv.DictReader(source))

    rows = [
        {
            "measurement_id": row["measurement_id"],
            "calibrated_value": f"{calibration.apply(float(row['raw_value'])):.3f}",
        }
        for row in measurements
    ]
    for row in rows:
        print(row["measurement_id"], row["calibrated_value"], sep=",")


if __name__ == "__main__":
    main()
