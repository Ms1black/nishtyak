import csv

from helpers import average

with open("data.csv", newline="") as stream:
    values = [float(row["value"]) for row in csv.DictReader(stream)]
print(f"mean={average(values):.2f}")
