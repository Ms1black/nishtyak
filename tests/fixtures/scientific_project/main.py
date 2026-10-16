from pathlib import Path

import numpy as np
import pandas as pd

data = pd.read_csv(Path(__file__).with_name("data.csv"))
print(f"mean={np.mean(data['value'].to_numpy()):.2f}")
