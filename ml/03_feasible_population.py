from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data" / "PS_20174392719_1491204439457_log.csv"
df = pd.read_csv(DATA)


def heading(text):
    print()
    print("=== " + text + " ===")


df = df.sort_values("step", kind="stable").reset_index(drop=True)
df["dest_prior_count"] = df.groupby("nameDest").cumcount()

risky = df[df["type"].isin(["TRANSFER", "CASH_OUT"])].copy()

# What LedgerFlow could actually let through: it rejects a transfer whose amount
# is zero or larger than the sender's balance before any fraud check runs.
feasible_mask = (risky["amount"] > 0) & (risky["oldbalanceOrg"] >= risky["amount"])
feasible = risky[feasible_mask].copy()
dropped = risky[~feasible_mask]

heading("Which rows could LedgerFlow even execute?")
print(f"TRANSFER + CASH_OUT rows: {len(risky):,} (fraud {int(risky['isFraud'].sum()):,})")
print(f"feasible rows: {len(feasible):,} (fraud {int(feasible['isFraud'].sum()):,}, {feasible['isFraud'].mean():.3%})")
print(f"dropped rows:  {len(dropped):,} (fraud {int(dropped['isFraud'].sum()):,})")

heading("Feasible rows by type")
print(pd.crosstab(feasible["type"], feasible["isFraud"]).to_string())

heading("One rule: amount equals the sender's whole balance")
drained = feasible["amount"] == feasible["oldbalanceOrg"]
is_fraud = feasible["isFraud"] == 1
tp = int((drained & is_fraud).sum())
fp = int((drained & ~is_fraud).sum())
fn = int((~drained & is_fraud).sum())
print(f"flagged: {tp + fp:,}   fraud caught: {tp:,}   false alarms: {fp:,}   fraud missed: {fn:,}")
print(f"precision: {tp / max(tp + fp, 1):.2%}   recall: {tp / max(tp + fn, 1):.2%}")

heading("Amount as a share of the sender's balance, feasible rows")
feasible["share"] = feasible["amount"] / feasible["oldbalanceOrg"]
fraud = feasible[feasible["isFraud"] == 1]
legit = feasible[feasible["isFraud"] == 0]
shares = pd.DataFrame(
    {
        "fraud": fraud["share"].describe(percentiles=[0.5, 0.9]),
        "not fraud": legit["share"].describe(percentiles=[0.5, 0.9]),
    }
)
print(shares.round(3).to_string())

heading("Receiving account history, feasible rows")
bins = [-1, 0, 1, 3, 10, 10_000_000]
labels = ["0 (new)", "1", "2-3", "4-10", "11+"]
table = pd.crosstab(pd.cut(feasible["dest_prior_count"], bins=bins, labels=labels), feasible["isFraud"])
table["fraud_rate_%"] = (table[1] / (table[0] + table[1]) * 100).round(3)
print(table.to_string())

heading("Fraud rate by hour of day, feasible rows")
hourly = feasible.assign(hour=feasible["step"] % 24).groupby("hour")["isFraud"].agg(rows="size", fraud="sum")
hourly["rate_%"] = (hourly["fraud"] / hourly["rows"] * 100).round(2)
print(hourly.to_string())

heading("Rows and fraud by period, feasible rows")
feasible["period"] = pd.cut(feasible["step"], bins=[0, 150, 300, 450, 600, 744])
print(feasible.groupby("period", observed=True)["isFraud"].agg(rows="size", fraud="sum").to_string())