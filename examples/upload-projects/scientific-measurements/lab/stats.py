import numpy as np


def summarize(values: np.ndarray) -> dict[str, int | float]:
    if values.size < 2 or not np.isfinite(values).all():
        raise ValueError("Each sample needs at least two finite measurements")
    return {
        "count": int(values.size),
        "mean_c": float(np.mean(values)),
        "sample_std_c": float(np.std(values, ddof=1)),
    }
