"""
Evaluation metrics for binary deepfake detection.
All functions accept numpy arrays or plain Python lists.
"""
from __future__ import annotations
import numpy as np


def eer(labels: list[int], scores: list[float]) -> float:
    """
    Equal Error Rate — threshold where FAR == FRR.
    labels: 1 = manipulated, 0 = authentic
    scores: manipulation probability in [0, 1]
    """
    labels = np.array(labels)
    scores = np.array(scores)
    thresholds = np.linspace(0, 1, 200)
    best = 1.0
    for t in thresholds:
        preds = (scores >= t).astype(int)
        fp = ((preds == 1) & (labels == 0)).sum()
        fn = ((preds == 0) & (labels == 1)).sum()
        n_neg = (labels == 0).sum() or 1
        n_pos = (labels == 1).sum() or 1
        far = fp / n_neg
        frr = fn / n_pos
        best = min(best, abs(far - frr))
    return float(best)


def compute_metrics(labels: list[int], scores: list[float], abstained: list[bool] = None) -> dict:
    """
    Returns accuracy, AUC, EER, precision, recall, F1, confusion matrix,
    and abstention_rate at threshold 0.5.
    """
    from sklearn.metrics import (
        roc_auc_score, accuracy_score, precision_score,
        recall_score, f1_score, confusion_matrix,
    )
    labels    = np.array(labels)
    scores    = np.array(scores)
    abstained = np.array(abstained) if abstained is not None else np.zeros(len(labels), dtype=bool)
    preds     = (scores >= 0.5).astype(int)

    cm = confusion_matrix(labels, preds).tolist()
    abstention_rate = float(abstained.mean()) if len(abstained) else 0.0

    return {
        "accuracy":         float(accuracy_score(labels, preds)),
        "auc":              float(roc_auc_score(labels, scores)),
        "eer":              eer(labels.tolist(), scores.tolist()),
        "precision":        float(precision_score(labels, preds, zero_division=0)),
        "recall":           float(recall_score(labels, preds, zero_division=0)),
        "f1":               float(f1_score(labels, preds, zero_division=0)),
        "confusion_matrix": cm,
        "abstention_rate":  abstention_rate,
        "n_samples":        int(len(labels)),
    }
