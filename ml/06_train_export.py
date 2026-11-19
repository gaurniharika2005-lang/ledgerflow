import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_curve
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).parent
DATA = HERE / "data" / "PS_20174392719_1491204439457_log.csv"
MODEL_OUT = HERE / "models" / "fraud_model.json"

SPLIT_STEP = 450      # train on steps <= 450, test on later steps
FPR_BUDGET = 0.01     # share of legitimate transfers we accept flagging
FEATURES = ["share", "drained", "dest_new", "log_dest_prior"]
HARD_MODE = ["dest_new", "log_dest_prior"]
rng = np.random.default_rng(0)


def heading(text):
    print()
    print("=== " + text + " ===")


def load_transfers():
    df = pd.read_csv(DATA)
    df = df.sort_values("step", kind="stable").reset_index(drop=True)
    # How many earlier transactions (any type) went to this destination.
    df["dest_prior_count"] = df.groupby("nameDest").cumcount()
    transfers = df[df["type"] == "TRANSFER"]
    # Only transfers LedgerFlow would let through: 0 < amount <= sender's balance.
    ok = (transfers["amount"] > 0) & (transfers["oldbalanceOrg"] >= transfers["amount"])
    t = transfers[ok].copy()
    t["share"] = t["amount"] / t["oldbalanceOrg"]
    t["drained"] = (t["amount"] == t["oldbalanceOrg"]).astype(int)
    t["dest_new"] = (t["dest_prior_count"] == 0).astype(int)
    t["log_dest_prior"] = np.log1p(t["dest_prior_count"])
    return t


def recall_at_fpr(y, scores, budget):
    fpr, tpr, _ = roc_curve(y, scores)
    return float(tpr[fpr <= budget].max())


def bootstrap_ci(y, scores, n=300):
    y = np.asarray(y)
    scores = np.asarray(scores)
    prs, recalls = [], []
    for _ in range(n):
        idx = rng.integers(0, len(y), len(y))
        if y[idx].min() == y[idx].max():
            continue
        prs.append(average_precision_score(y[idx], scores[idx]))
        recalls.append(recall_at_fpr(y[idx], scores[idx], FPR_BUDGET))
    ci = lambda values: (np.percentile(values, 2.5), np.percentile(values, 97.5))
    return ci(prs), ci(recalls)


def fit(train, columns):
    scaler = StandardScaler().fit(train[columns])
    model = LogisticRegression(max_iter=1000).fit(scaler.transform(train[columns]), train["isFraud"])
    return scaler, model


def score(scaler, model, frame, columns):
    return model.predict_proba(scaler.transform(frame[columns]))[:, 1]


def threshold_for_budget(y, scores, budget):
    fpr, tpr, thresholds = roc_curve(y, scores)
    ok = np.where(fpr <= budget)[0]
    return float(thresholds[ok[np.argmax(tpr[ok])]])


def operating_point(y, flagged):
    y = np.asarray(y).astype(bool)
    flagged = np.asarray(flagged).astype(bool)
    tp = int((flagged & y).sum())
    fp = int((flagged & ~y).sum())
    return {
        "flagged": int(flagged.sum()),
        "recall": tp / max(int(y.sum()), 1),
        "false_alarm_rate": fp / max(int((~y).sum()), 1),
        "precision": tp / max(tp + fp, 1),
        "false_alarms": fp,
    }


transfers = load_transfers()
train = transfers[transfers["step"] <= SPLIT_STEP]
test = transfers[transfers["step"] > SPLIT_STEP]

heading("Data: feasible TRANSFER rows (what LedgerFlow's transfer endpoint scores)")
for name, part in [("train", train), ("test", test)]:
    print(f"{name}: {len(part):,} rows, {int(part['isFraud'].sum()):,} fraud ({part['isFraud'].mean():.1%})")

scaler, model = fit(train, FEATURES)
hard_scaler, hard_model = fit(train, HARD_MODE)
core_test = score(scaler, model, test, FEATURES)
hard_test = score(hard_scaler, hard_model, test, HARD_MODE)
rule_test = test["drained"].to_numpy()

heading("Ranking quality on the test period (95% bootstrap intervals)")
print(f"no-skill PR-AUC equals the test fraud share: {test['isFraud'].mean():.3f}")
for name, s in [
    ("logistic regression, 4 features", core_test),
    ("logistic regression, receiver history only", hard_test),
    ("rule: amount == whole balance", rule_test),
]:
    pr = average_precision_score(test["isFraud"], s)
    rc = recall_at_fpr(test["isFraud"], s, FPR_BUDGET)
    (pl, ph), (rl, rh) = bootstrap_ci(test["isFraud"], s)
    print(f"{name}: PR-AUC {pr:.4f} [{pl:.4f}, {ph:.4f}]   recall at {FPR_BUDGET:.0%} false alarms {rc:.3f} [{rl:.3f}, {rh:.3f}]")

core_train = score(scaler, model, train, FEATURES)
threshold = threshold_for_budget(train["isFraud"], core_train, FPR_BUDGET)

heading(f"Operating point: threshold chosen on TRAINING data for a {FPR_BUDGET:.0%} false-alarm budget")
print(f"probability threshold: {threshold:.4f}")
points = {
    "model (4 features)": core_test >= threshold,
    "rule: amount == whole balance": rule_test == 1,
    "rule: takes >= 90% of balance": (test["share"] >= 0.9).to_numpy(),
    "rule: takes >= 90% AND new receiver": ((test["share"] >= 0.9) & (test["dest_new"] == 1)).to_numpy(),
}
table = pd.DataFrame([operating_point(test["isFraud"], f) for f in points.values()], index=list(points))
print(table.round(4).to_string())

heading("What the model learned (standardized coefficients; positive = more suspicious)")
for name, coef in zip(FEATURES, model.coef_[0]):
    print(f"{name:>16}: {coef:+.3f}")
print(f"{'intercept':>16}: {model.intercept_[0]:+.3f}")

artifact = {
    "model": "logistic regression",
    "trained_on": date.today().isoformat(),
    "data": "PaySim synthetic mobile-money dataset (Kaggle: ealaxi/paysim1), feasible TRANSFER rows only",
    "citation": "Lopez-Rojas, Elmir, Axelsson (2016). PaySim: A financial mobile money simulator for fraud detection. EMSS.",
    "train_steps": f"<= {SPLIT_STEP}",
    "features": FEATURES,
    "feature_definitions": {
        "share": "amount / sender balance before the transfer",
        "drained": "1 if the amount equals the sender's whole balance, else 0",
        "dest_new": "1 if the receiving wallet has received no earlier transaction, else 0",
        "log_dest_prior": "natural log of (1 + number of earlier transactions received by the receiving wallet)",
    },
    "means": [float(v) for v in scaler.mean_],
    "scales": [float(v) for v in scaler.scale_],
    "coefficients": [float(v) for v in model.coef_[0]],
    "intercept": float(model.intercept_[0]),
    "threshold": threshold,
    "false_alarm_budget": FPR_BUDGET,
    "warning": "Trained on synthetic data. Scores are illustrative, not calibrated for real customers.",
}
MODEL_OUT.parent.mkdir(exist_ok=True)
MODEL_OUT.write_text(json.dumps(artifact, indent=2))
heading("Saved")
print(MODEL_OUT)