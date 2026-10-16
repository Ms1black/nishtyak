from pathlib import Path

import pandas as pd
from lab.stats import summarize


def main() -> None:
    project = Path(__file__).resolve().parent
    measurements = pd.read_csv(project / "data" / "measurements.csv")
    rows = []
    for sample, group in measurements.groupby("sample", sort=True):
        summary = summarize(group["temperature_c"].to_numpy())
        rows.append({"sample": sample, **summary})

    result = pd.DataFrame(rows)
    output = project / "outputs" / "summary.csv"
    output.parent.mkdir(exist_ok=True)
    result.to_csv(output, index=False, float_format="%.3f")
    print(result.to_csv(index=False, float_format="%.3f"), end="")


if __name__ == "__main__":
    main()
