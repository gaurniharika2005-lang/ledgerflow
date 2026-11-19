from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data" / "PS_20174392719_1491204439457_log.csv"
df = pd.read_csv(DATA)
df = df.sort_values("step", kind="stable").reset_index(drop=True)
df["dest_prior_count"] = df.groupby("nameDest").cumcount()

risky = df[df["type"].isin(["TRANSFER", "CASH_OUT"])]
feasible = risky[(risky["amount"] > 0) & (risky["oldbalanceOrg"] >= risky["amount"])].copy()
feasible["share"] = feasible["amount"] / feasible["oldbalanceOrg"]
feasible["drained"] = feasible["amount"] == feasible["oldbalanceOrg"]


def heading(text):
    print()
    print("=== " + text + " ===")


fraud = feasible[feasible["isFraud"] == 1]
missed = fraud[~fraud["drained"]]
caught = fraud[fraud["drained"]]
legit = feasible[feasible["isFraud"] == 0]

heading("Fraud the one-line rule misses")
print(f"{len(missed)} of {len(fraud)} fraud rows are not 'amount equals whole balance'")
print(missed["type"].value_counts().to_string())

heading("How many of them have amount exactly 10,000,000?")
at_cap = lambda frame: int((frame["amount"] == 10_000_000).sum())
print(f"missed fraud at the cap: {at_cap(missed)} of {len(missed)}")
print(f"caught fraud at the cap: {at_cap(caught)} of {len(caught)}")
print(f"legitimate rows at the cap: {at_cap(legit)}")

heading("The missed frauds: sender balance and share taken")
print(missed[["oldbalanceOrg", "amount", "share"]].describe(percentiles=[0.1, 0.5, 0.9]).round(3).to_string())

heading("Missed fraud: how often is the receiving account new?")
print(f"new receiving account: {(missed['dest_prior_count'] == 0).mean():.1%}   legitimate rows: {(legit['dest_prior_count'] == 0).mean():.1%}")

heading("Legitimate rows with a huge sender balance (above 10,000,000)")
big = legit[legit["oldbalanceOrg"] > 10_000_000]
print(f"{len(big):,} legitimate rows; the largest amount among them is {big['amount'].max():,.0f}")