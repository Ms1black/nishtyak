import pandas as pd
from calculations import summarize


def main() -> None:
    measurements = pd.read_csv("measurements.csv")
    rows = []
    for sample, group in measurements.groupby("sample", sort=True):
        summary = summarize(group["temperature_c"].to_numpy())
        rows.append({"sample": sample, **summary})

    result = pd.DataFrame(rows)
    print(result.to_csv(index=False, float_format="%.3f"), end="")


if __name__ == "__main__":
    main()
