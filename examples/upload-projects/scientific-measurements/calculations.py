import numpy as np


def summarize(values: np.ndarray) -> dict[str, int | float]:
    return {
        "count": int(values.size),
        "mean_c": float(np.mean(values)),
        "sample_std_c": float(np.std(values, ddof=1)),
    }
