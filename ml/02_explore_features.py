from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data" / "PS_20174392719_1491204439457_log.csv"
df = pd.read_csv(DATA)


def heading(text):
    print()
    print("=== " + text + " ===")


df = df.sort_values("step", kind="stable").reset_index(drop=True)

# Number of EARLIER rows (any type) that went to the same destination.
# LedgerFlow would know this too: it's the receiving wallet's past activity.
df["dest_prior_count"] = df.groupby("nameDest").cumcount()

risky = df[df["type"].isin(["TRANSFER", "CASH_OUT"])].copy()
fraud = risky[risky["isFraud"] == 1]
legit = risky[risky["isFraud"] == 0]


def share(mask_fraud, mask_legit):
    return (
        f"fraud: {mask_fraud.mean():.2%} ({int(mask_fraud.sum()):,})   "
        f"not fraud: {mask_legit.mean():.2%} ({int(mask_legit.sum()):,})"
    )


heading("Sender had a zero balance BEFORE the transaction")
print(share(fraud["oldbalanceOrg"] == 0, legit["oldbalanceOrg"] == 0))

heading("Among senders with a positive balance: amount == whole balance (drained)")
fraud_pos = fraud[fraud["oldbalanceOrg"] > 0]
legit_pos = legit[legit["oldbalanceOrg"] > 0]
print(share(fraud_pos["amount"] == fraud_pos["oldbalanceOrg"], legit_pos["amount"] == legit_pos["oldbalanceOrg"]))
print(f"rows with a positive balance: fraud {len(fraud_pos):,}, not fraud {len(legit_pos):,}")

heading("Amount as a share of the sender's balance (positive balances only)")


def ratio(frame):
    return (frame["amount"] / frame["oldbalanceOrg"]).describe(percentiles=[0.5, 0.9, 0.99]).round(3)


print(pd.DataFrame({"fraud": ratio(fraud_pos), "not fraud": ratio(legit_pos)}).to_string())

heading("Receiving account: how many earlier transactions went to it?")
bins = [-1, 0, 1, 3, 10, 10_000_000]
labels = ["0 (new)", "1", "2-3", "4-10", "11+"]
table = pd.crosstab(pd.cut(risky["dest_prior_count"], bins=bins, labels=labels), risky["isFraud"])
table["fraud_rate_%"] = (table[1] / (table[0] + table[1]) * 100).round(3)
print(table.to_string())

heading("Are the balance columns internally consistent? (sender debit and receiver credit)")
tolerance = 0.01
sender_ok = (risky["newbalanceOrig"] + risky["amount"] - risky["oldbalanceOrg"]).abs() < tolerance
dest_ok = (risky["oldbalanceDest"] + risky["amount"] - risky["newbalanceDest"]).abs() < tolerance
for name, ok in [("sender balance adds up", sender_ok), ("receiver balance adds up", dest_ok)]:
    print(f"{name}: fraud {ok[risky['isFraud'] == 1].mean():.1%}   not fraud {ok[risky['isFraud'] == 0].mean():.1%}")

heading("Largest amounts")
top = fraud["amount"].max()
print(f"largest fraud amount: {top:,.0f}   fraud rows at that exact amount: {(fraud['amount'] == top).sum()}")
print(f"non-fraud rows above that amount: {(legit['amount'] > top).sum():,}")

heading("Fraud across time (for choosing a train/test split)")
risky["period"] = pd.cut(risky["step"], bins=[0, 150, 300, 450, 600, 744])
print(risky.groupby("period", observed=True)["isFraud"].agg(rows="size", fraud="sum").to_string())
cutoff = 520
for name, part in [("step <= 520", risky[risky["step"] <= cutoff]), ("step > 520", risky[risky["step"] > cutoff])]:
    print(f"{name}: {len(part):,} rows, {int(part['isFraud'].sum()):,} fraud ({part['isFraud'].mean():.3%})")