from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data" / "PS_20174392719_1491204439457_log.csv"

df = pd.read_csv(DATA)


def heading(text):
    print()
    print("=== " + text + " ===")


heading("Size")
print(f"{len(df):,} rows, {df.shape[1]} columns")
print(df.dtypes.to_string())

heading("How rare is fraud?")
print(f"{int(df['isFraud'].sum()):,} fraud rows out of {len(df):,} ({df['isFraud'].mean():.4%})")

heading("Fraud by transaction type")
by_type = df.groupby("type")["isFraud"].agg(rows="size", fraud="sum")
by_type["fraud_rate_%"] = (by_type["fraud"] / by_type["rows"] * 100).round(4)
print(by_type.to_string())

# Fraud only happens in these two types, so compare like with like from here on.
risky = df[df["type"].isin(["TRANSFER", "CASH_OUT"])]
fraud = risky[risky["isFraud"] == 1]
legit = risky[risky["isFraud"] == 0]
print(f"\nTRANSFER + CASH_OUT rows: {len(risky):,} (fraud: {len(fraud):,})")

heading("Amounts in TRANSFER + CASH_OUT")
print(pd.DataFrame({"fraud": fraud["amount"].describe(), "not fraud": legit["amount"].describe()}).round(0).to_string())

heading("Does the amount equal the sender's whole balance? (account drained)")
print(f"fraud: {(fraud['amount'] == fraud['oldbalanceOrg']).mean():.1%}   not fraud: {(legit['amount'] == legit['oldbalanceOrg']).mean():.1%}")

heading("LEAKAGE CHECK: sender balance AFTER the transaction is exactly 0")
print(f"fraud: {(fraud['newbalanceOrig'] == 0).mean():.1%}   not fraud: {(legit['newbalanceOrig'] == 0).mean():.1%}")

heading("The dataset's own rule (isFlaggedFraud) vs the truth")
print(pd.crosstab(df["isFlaggedFraud"], df["isFraud"]).to_string())

heading("Fraud rate by hour of day (%), TRANSFER + CASH_OUT")
hourly = risky.assign(hour=risky["step"] % 24).groupby("hour")["isFraud"].mean() * 100
print(hourly.round(3).to_string())

heading("Do senders repeat? (matters for 'recent activity' features)")
senders = df["nameOrig"].value_counts()
print(f"distinct senders: {senders.size:,}; most transactions by one sender: {senders.iloc[0]}; senders seen more than once: {(senders > 1).sum():,}")