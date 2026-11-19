from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

DATA = Path(__file__).parent / "data" / "PS_20174392719_1491204439457_log.csv"
SPLIT_STEP = 450  # train on earlier steps, test on later ones

CORE = ["share", "drained", "is_transfer", "dest_new", "log_dest_prior"]
FEATURE_SETS = {
    "core": CORE,
    "hard mode (no share or drained)": ["is_transfer", "dest_new", "log_dest_prior"],
    "core + raw amount": CORE + ["log_amount"],
    "core + hour of day (artifact)": CORE + ["hour"],
    "core + balances AFTER (leaky)": CORE + ["newbalanceOrig", "newbalanceDest"],
}


def heading(text):
    print()
    print("=== " + text + " ===")


def load_feasible():
    df = pd.read_csv(DATA)
    df = df.sort_values("step", kind="stable").reset_index(drop=True)
    # Earlier transactions to the same destination, counted before this row.
    df["dest_prior_count"] = df.groupby("nameDest").cumcount()
    risky = df[df["type"].isin(["TRANSFER", "CASH_OUT"])]
    # Only rows LedgerFlow would let through: 0 < amount <= sender's balance.
    feasible = risky[(risky["amount"] > 0) & (risky["oldbalanceOrg"] >= risky["amount"])].copy()
    feasible["share"] = feasible["amount"] / feasible["oldbalanceOrg"]
    feasible["drained"] = (feasible["amount"] == feasible["oldbalanceOrg"]).astype(int)
    feasible["is_transfer"] = (feasible["type"] == "TRANSFER").astype(int)
    feasible["dest_new"] = (feasible["dest_prior_count"] == 0).astype(int)
    feasible["log_dest_prior"] = np.log1p(feasible["dest_prior_count"])
    feasible["log_amount"] = np.log1p(feasible["amount"])
    feasible["hour"] = feasible["step"] % 24
    return feasible


def recall_at_fpr(y_true, scores, target_fpr):
    fpr, tpr, _ = roc_curve(y_true, scores)
    return tpr[fpr <= target_fpr].max()


def flagged_at_fpr(y_true, scores, target_fpr):
    fpr, tpr, thresholds = roc_curve(y_true, scores)
    ok = np.where(fpr <= target_fpr)[0]
    best = ok[np.argmax(tpr[ok])]
    return scores >= thresholds[best]


def evaluate(y_true, scores):
    return {
        "roc_auc": roc_auc_score(y_true, scores),
        "pr_auc": average_precision_score(y_true, scores),
        "recall@0.1%FPR": recall_at_fpr(y_true, scores, 0.001),
        "recall@1%FPR": recall_at_fpr(y_true, scores, 0.01),
    }


def make_models():
    return {
        "logistic regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)),
        "gradient boosting": HistGradientBoostingClassifier(random_state=0),
    }


feasible = load_feasible()
train = feasible[feasible["step"] <= SPLIT_STEP]
test = feasible[feasible["step"] > SPLIT_STEP]

heading("Split")
for name, part in [("train (step <= %d)" % SPLIT_STEP, train), ("test  (step >  %d)" % SPLIT_STEP, test)]:
    print(f"{name}: {len(part):,} rows, {int(part['isFraud'].sum()):,} fraud ({part['isFraud'].mean():.2%})")
print("No-skill PR-AUC on the test set equals its fraud share, shown above.")

rows = []
core_scores = None
for set_name, columns in FEATURE_SETS.items():
    for model_name, model in make_models().items():
        model.fit(train[columns], train["isFraud"])
        scores = model.predict_proba(test[columns])[:, 1]
        rows.append({"features": set_name, "model": model_name, **evaluate(test["isFraud"], scores)})
        if set_name == "core" and model_name == "gradient boosting":
            core_scores = scores

for name, column in [("RULE: amount == whole balance", "drained"), ("RULE: dataset isFlaggedFraud", "isFlaggedFraud")]:
    rows.append({"features": "-", "model": name, **evaluate(test["isFraud"], test[column])})

heading("Results on the test period")
print(pd.DataFrame(rows).round(4).to_string(index=False))

heading("Can the model find fraud the one-line rule misses?")
residual = ((test["isFraud"] == 1) & (test["drained"] == 0)).to_numpy()
print(f"fraud the rule misses in the test period: {int(residual.sum())} of {int(test['isFraud'].sum())}")
for target in (0.001, 0.01):
    flagged = flagged_at_fpr(test["isFraud"], core_scores, target)
    caught = flagged[residual].mean() if residual.sum() else float("nan")
    false_alarms = int((flagged & (test["isFraud"] == 0).to_numpy()).sum())
    print(f"at {target:.1%} false-alarm rate: the model catches {caught:.1%} of the rule's misses ({false_alarms:,} false alarms)")