import pandas as pd
# was removed as this was a test to get the power hourly column names verified.
df = pd.read_csv("data/raw/power/power_test_valencia_1990-01.csv", skiprows=11)
print(df.head())
print(df.columns)
