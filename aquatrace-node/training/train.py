"""Trains one lightweight anomaly model per contaminant (§5.3) on the dataset
generate_dataset.py produces, saving each as node/ml/artifacts/{contaminant_id}_{version}.pkl
plus a matching .sha256 checksum file (used by the OTA checksum-verify flow, ml/inference.py).

Production-shape note: the intended real design is a single shared-trunk multi-head model
(one shared feature encoder, one small head per contaminant) — training N independent small
models here is a disclosed hackathon simplification, not an architectural recommendation.

Usage: python3 -m training.train [--dataset training/datasets/dataset.parquet] [--version v1]
"""
import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import joblib
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from config import get_settings

# Must match ml/inference.py's LoadedModel.predict() feature ordering and
# core/virtual_node.py's VirtualNode._features() output keys exactly.
FEATURE_ORDER = ["value", "level_m", "speed_mps", "Q_m3s", "rolling_mean_1h", "rolling_std_1h"]

DATASET_COLUMN_MAP = {
    "raw_value": "value",
    "level": "level_m",
    "speed": "speed_mps",
    "Q": "Q_m3s",
}


def train_one(df: pd.DataFrame, contaminant_id: str, version: str, artifact_dir: Path) -> bool:
    df_c = df[df["contaminant_id"] == contaminant_id]
    if df_c["anomaly_zscore_label"].nunique() < 2:
        print(f"  {contaminant_id}: skipped -- only one label class present ({len(df_c)} rows)")
        return False

    X = df_c.rename(columns=DATASET_COLUMN_MAP)[FEATURE_ORDER].values
    y = df_c["anomaly_zscore_label"].values

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

    pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", MLPClassifier(hidden_layer_sizes=(16, 8), max_iter=300, random_state=42)),
        ]
    )
    pipeline.fit(X_train, y_train)
    accuracy = pipeline.score(X_test, y_test)
    print(f"  {contaminant_id}: n={len(df_c)} positive_rate={y.mean():.3%} test_accuracy={accuracy:.3f}")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    model_path = artifact_dir / f"{contaminant_id}_{version}.pkl"
    joblib.dump({"estimator": pipeline, "feature_order": FEATURE_ORDER}, model_path)
    checksum = hashlib.sha256(model_path.read_bytes()).hexdigest()
    (artifact_dir / f"{contaminant_id}_{version}.sha256").write_text(checksum)
    print(f"    saved {model_path.name} (sha256={checksum[:16]}...)")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=str, default=str(REPO_ROOT / "training" / "datasets" / "dataset.parquet"))
    parser.add_argument("--version", type=str, default="v1")
    parser.add_argument("--artifact-dir", type=str, default=None)
    args = parser.parse_args()

    df = pd.read_parquet(args.dataset)
    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else Path(get_settings().model_artifact_dir)

    print(f"training on {len(df)} rows from {args.dataset}, writing to {artifact_dir}\n")
    trained = 0
    for contaminant_id in sorted(df["contaminant_id"].unique()):
        if train_one(df, contaminant_id, args.version, artifact_dir):
            trained += 1
    print(f"\ntrained {trained}/{df['contaminant_id'].nunique()} contaminant models")


if __name__ == "__main__":
    main()
