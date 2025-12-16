import pandas as pd, zipfile, os

faostat_path = "data/raw/faostat"
for f in os.listdir(faostat_path):
    if f.lower().endswith(".zip"):
        print("Found ZIP:", f)

