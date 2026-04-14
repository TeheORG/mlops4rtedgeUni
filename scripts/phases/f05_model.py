#!/usr/bin/env python3
"""
F05 — MODELING

Lee:
  - params.yaml de f05_modeling (incluye model_family, automl, search_space, evaluation, training, etc.)
  - outputs.yaml del parent f04_targets (dataset etiquetado, prediction_name, Tu/OW/LT/PW…)

Entrena un modelo binario con AutoML simple (random search sobre search_space),
selecciona el mejor por recall en validación, calcula métricas en test,
calcula umbral óptimo (por F1), guarda el modelo y genera outputs.yaml
conforme a traceability_schema.yaml.

Este código está diseñado para:
  - encajar con el patrón de F01–F04,
  - dejar todo listo para F06 (cuantización y edge unit),
  - mantener MLflow en Makefile (solo se deja bloque mlflow_registration).
"""

import argparse
import json
import time
from datetime import datetime, timezone
import shutil
import random

import numpy as np
import pandas as pd
import tensorflow as tf
from sklearn.metrics import (
    confusion_matrix,
    precision_score,
    recall_score,
    f1_score,
    precision_recall_curve,
)
from sklearn.model_selection import train_test_split
import yaml

from scripts.core.artifacts import (
    PROJECT_ROOT,
    get_variant_dir,
    save_outputs_yaml,
    sha256_of_file,
)
from scripts.core.phase_io import load_phase_outputs, load_variant_params
from scripts.core.sequence_utils import pad_sequences
from scripts.core.traceability import validate_outputs

# ============================================================
# CONSTANTES
# ============================================================

PHASE = "f05_modeling"
PARENT_PHASE = "f04_targets"
FAST_MAX_MAJORITY_SAMPLES = 20_000


def build_adam_optimizer(learning_rate: float):
    # Ruta única para todos los OS: evita divergencias por elegir optimizadores distintos.
    return tf.keras.optimizers.Adam(learning_rate=learning_rate)


def configure_reproducibility(seed: int, strict_cross_os: bool = False):
    """Configura semillas globales y, opcionalmente, modo determinista estricto.

    strict_cross_os=True intenta minimizar diferencias entre SOs:
      - activa operaciones deterministas de TensorFlow cuando están disponibles,
      - fija paralelismo a 1 hilo intra/inter-op para reducir no determinismo.
    """
    random.seed(seed)
    np.random.seed(seed)
    tf.keras.utils.set_random_seed(seed)

    if strict_cross_os:
        try:
            tf.config.experimental.enable_op_determinism()
        except Exception as exc:
            print(f"[WARN] No se pudo activar enable_op_determinism(): {exc}")

        try:
            tf.config.threading.set_intra_op_parallelism_threads(1)
            tf.config.threading.set_inter_op_parallelism_threads(1)
        except Exception as exc:
            print(f"[WARN] No se pudo fijar threading determinista: {exc}")


# ============================================================
# HELPERS DE MODELADO
# ============================================================

def compute_class_weights(y):
    pos = int(np.sum(y == 1))
    neg = int(np.sum(y == 0))
    if pos == 0:
        return None
    return {0: 1.0, 1: float(neg / pos)}


def convert_to_native_types(obj):
    if isinstance(obj, dict):
        return {k: convert_to_native_types(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_to_native_types(item) for item in obj]
    if isinstance(obj, (np.integer, np.int32, np.int64)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float32, np.float64)):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def split_vectorized_dataset(X, y, eval_cfg: dict):
    split = eval_cfg.get("split", {})
    train_ratio = float(split.get("train", 0.7))
    val_ratio = float(split.get("val", 0.15))
    test_ratio = float(split.get("test", 0.15))

    if not np.isclose(train_ratio + val_ratio + test_ratio, 1.0, atol=1e-6):
        raise ValueError(
            f"Las proporciones train/val/test no suman 1: "
            f"{train_ratio} + {val_ratio} + {test_ratio}"
        )

    X_temp, X_test, y_temp, y_test = train_test_split(
        X, y, test_size=test_ratio, stratify=y, random_state=42
    )

    tv_total = train_ratio + val_ratio
    val_rel = val_ratio / tv_total if tv_total > 0 else 0.0

    X_train, X_val, y_train, y_val = train_test_split(
        X_temp, y_temp, test_size=val_rel, stratify=y_temp, random_state=43
    )

    return X_train, y_train, X_val, y_val, X_test, y_test


def vectorize_dense_bow(df: pd.DataFrame, label_col: str):
    sequences = df["OW_events"].tolist()
    y = df[label_col].astype("int32").values

    vocab = sorted(set(event_id for seq in sequences for event_id in seq))
    index = {event_id: i for i, event_id in enumerate(vocab)}

    X = np.zeros((len(sequences), len(vocab)), dtype=np.float32)
    for i, seq in enumerate(sequences):
        for event_id in seq:
            X[i, index[event_id]] += 1.0

    return X, y, {
        "input_dim": int(X.shape[1]),
        "vocab_size": int(len(vocab)),
        "vectorization": "dense_bow",
    }


def vectorize_sequence(df: pd.DataFrame, label_col: str, event_type_count: int):
    sequences = df["OW_events"].tolist()
    y = df[label_col].astype("int32").values

    if event_type_count < 1:
        raise RuntimeError("event_type_count must be >= 1")

    normalized_seqs = []
    for seq in sequences:
        cur = []
        for event_id in seq:
            v = int(event_id)
            # Convención del catálogo: IDs reales en 1..n; 0 reservado para padding/no-evento.
            if v < 0 or v > event_type_count:
                raise RuntimeError(
                    f"event_id fuera de rango para secuencia: {v} "
                    f"(esperado 0 o 1..{event_type_count})"
                )
            cur.append(v)
        normalized_seqs.append(cur)

    lengths = [len(seq) for seq in normalized_seqs]
    max_len = max(1, int(np.percentile(lengths, 95))) if lengths else 1

    X = pad_sequences(normalized_seqs, max_len)

    return X, y, {
        "vocab_size": int(event_type_count),
        "max_len": int(max_len),
        "vectorization": "sequence",
    }


def vectorize_for_family(df: pd.DataFrame, label_col: str, model_family: str, event_type_count: int):
    if "OW_events" not in df.columns:
        raise RuntimeError("El dataset de F04 debe contener la columna 'OW_events'")

    if model_family == "dense_bow":
        return vectorize_dense_bow(df, label_col)

    if model_family in {"sequence_embedding", "cnn1d"}:
        return vectorize_sequence(df, label_col, event_type_count)

    raise ValueError(
        f"model_family no soportada: {model_family}. "
        "Use una de: dense_bow, sequence_embedding, cnn1d"
    )


def sample_hyperparams(search_space: dict, model_family: str, rng: np.random.Generator):
    """
    Genera una configuración de hiperparámetros a partir de search_space:

    search_space:
      common:
        batch_size: [128, 256]
        learning_rate: [0.001, 0.0005]
        n_layers: [1, 2]
        units: [64, 128]
        dropout: [0.0, 0.2]
      dense_bow: {}
      sequence_embedding: { ... }
      cnn1d: { ... }

    Aquí solo usamos 'common' y, opcionalmente, bloque específico de la familia.
    """
    common = search_space.get("common", {})
    family_space = search_space.get(model_family, {})

    hp = {}

    def pick(key, space):
        values = space.get(key)
        if isinstance(values, list) and values:
            return rng.choice(values)
        return None

    # Common
    hp["batch_size"] = int(pick("batch_size", common) or 128)
    hp["learning_rate"] = float(pick("learning_rate", common) or 1e-3)
    hp["n_layers"] = int(pick("n_layers", common) or 1)
    hp["units"] = int(pick("units", common) or 64)
    hp["dropout"] = float(pick("dropout", common) or 0.0)

    # Podrías extender con params específicos de la familia si quieres
    for key, values in family_space.items():
        if isinstance(values, list) and values:
            hp[key] = rng.choice(values)

    return hp


def build_dense_bow_model(aux: dict, hp: dict) -> tf.keras.Model:
    n_layers = int(hp.get("n_layers", 1))
    units = int(hp.get("units", 64))
    dropout = float(hp.get("dropout", 0.0))

    model = tf.keras.Sequential(name="dense_bow_binary_classifier")
    model.add(tf.keras.layers.Input(shape=(int(aux["input_dim"]),)))

    for _ in range(n_layers):
        model.add(tf.keras.layers.Dense(units, activation="relu"))
        if dropout > 0:
            model.add(tf.keras.layers.Dropout(dropout))

    model.add(tf.keras.layers.Dense(1, activation="sigmoid"))

    return model


def build_sequence_embedding_model(aux: dict, hp: dict) -> tf.keras.Model:
    n_layers = int(hp.get("n_layers", 1))
    units = int(hp.get("units", 64))
    dropout = float(hp.get("dropout", 0.0))
    embed_dim = int(hp.get("embed_dim", 32))

    model = tf.keras.Sequential(name="sequence_embedding_binary_classifier")
    model.add(tf.keras.layers.Input(shape=(int(aux["max_len"]),)))
    model.add(
        tf.keras.layers.Embedding(
            input_dim=int(aux["vocab_size"]) + 1,
            output_dim=embed_dim,
            mask_zero=True,
        )
    )
    model.add(tf.keras.layers.GlobalAveragePooling1D())

    for _ in range(n_layers):
        model.add(tf.keras.layers.Dense(units, activation="relu"))
        if dropout > 0:
            model.add(tf.keras.layers.Dropout(dropout))

    model.add(tf.keras.layers.Dense(1, activation="sigmoid"))

    return model


def build_cnn1d_model(aux: dict, hp: dict) -> tf.keras.Model:
    n_layers = int(hp.get("n_layers", 1))
    units = int(hp.get("units", 64))
    dropout = float(hp.get("dropout", 0.0))
    embed_dim = int(hp.get("embed_dim", 32))
    filters = int(hp.get("filters", 64))
    kernel_size = int(hp.get("kernel_size", 3))

    model = tf.keras.Sequential(name="cnn1d_binary_classifier")
    model.add(tf.keras.layers.Input(shape=(int(aux["max_len"]),)))
    model.add(
        tf.keras.layers.Embedding(
            input_dim=int(aux["vocab_size"]) + 1,
            output_dim=embed_dim,
        )
    )
    model.add(
        tf.keras.layers.Conv1D(
            filters=filters,
            kernel_size=kernel_size,
            activation="relu",
            padding="same",
        )
    )
    model.add(tf.keras.layers.GlobalMaxPooling1D())

    for _ in range(n_layers):
        model.add(tf.keras.layers.Dense(units, activation="relu"))
        if dropout > 0:
            model.add(tf.keras.layers.Dropout(dropout))

    model.add(tf.keras.layers.Dense(1, activation="sigmoid"))

    return model


def build_model(
    model_family: str,
    hp: dict,
    aux: dict,
) -> tf.keras.Model:
    if model_family == "dense_bow":
        model = build_dense_bow_model(aux, hp)
    elif model_family == "sequence_embedding":
        model = build_sequence_embedding_model(aux, hp)
    elif model_family == "cnn1d":
        model = build_cnn1d_model(aux, hp)
    else:
        raise ValueError(
            f"model_family no soportada: {model_family}. "
            "Use una de: dense_bow, sequence_embedding, cnn1d"
        )

    lr = float(hp.get("learning_rate", 1e-3))

    model.compile(
        optimizer=build_adam_optimizer(lr),
        loss="binary_crossentropy",
        metrics=[
            tf.keras.metrics.Precision(name="precision"),
            tf.keras.metrics.Recall(name="recall"),
        ],
    )

    return model


def train_with_automl(
    X_train,
    y_train,
    X_val,
    y_val,
    model_family: str,
    aux: dict,
    search_space: dict,
    automl_cfg: dict,
    training_cfg: dict,
    class_weights=None,
    experiments_dir=None,
):
    """
    Bucle AutoML simple:
      - num_trials = automl.max_trials
      - en cada trial se muestrean hiperparámetros
      - se entrena y se evalúa recall en validación
      - se queda con el modelo con mejor recall_val

    Devuelve:
      - best_model
      - best_hp (dict)
      - best_val_recall (float)
      - history (history.history del mejor modelo)
    """
    enabled = bool(automl_cfg.get("enabled", True))
    max_trials = int(automl_cfg.get("max_trials", 5))
    seed = int(automl_cfg.get("seed", 42))

    epochs = int(training_cfg.get("epochs", 20))
    max_samples = training_cfg.get("max_samples", None)

    rng = np.random.default_rng(seed)
    trials_summary = []

    if experiments_dir is not None:
        experiments_dir.mkdir(parents=True, exist_ok=True)

    num_trials = max_trials if enabled else 1

    # Si max_samples está definido, recortamos train
    if max_samples is not None:
        max_samples = int(max_samples)
        if max_samples < len(X_train):
            idx = rng.choice(len(X_train), size=max_samples, replace=False)
            X_train = X_train[idx]
            y_train = y_train[idx]

    if not enabled:
        print("[INFO] AutoML deshabilitado: se ejecutará 1 trial")

        # Trial único con hiperparámetros "por defecto"
        hp = sample_hyperparams(search_space, model_family, rng)
        model = build_model(model_family, hp, aux)

        print(
            f"[INFO] trial 1/1 | family={model_family} | "
            f"batch={int(hp.get('batch_size', 128))} | epochs={epochs}"
        )

        history = model.fit(
            X_train,
            y_train,
            validation_data=(X_val, y_val),
            epochs=epochs,
            batch_size=int(hp.get("batch_size", 128)),
            class_weight=class_weights,
            verbose=0,
        )
        y_val_prob = sanitize_probabilities(model.predict(X_val, verbose=0).ravel(), "val")
        y_val_pred = (y_val_prob >= 0.5).astype("int32")
        val_recall = recall_score(y_val, y_val_pred, zero_division=0)

        hp_native = convert_to_native_types(hp)
        trial_result = {
            "trial_id": 0,
            "hyperparameters": hp_native,
            "val_recall": float(val_recall),
            "epochs": epochs,
            "batch_size": int(hp.get("batch_size", 128)),
        }
        trials_summary.append(trial_result)

        if experiments_dir is not None:
            exp_dir = experiments_dir / "exp_000"
            exp_dir.mkdir(parents=True, exist_ok=True)
            model.save(exp_dir / "model.h5")
            (exp_dir / "metrics.json").write_text(
                json.dumps(trial_result, indent=2),
                encoding="utf-8",
            )

        print(f"[INFO] trial 1/1 | val_recall={float(val_recall):.4f}")

        return model, hp_native, float(val_recall), history.history, trials_summary, 0

    best_model = None
    best_hp = None
    best_val_recall = -1.0
    best_history = None

    print(f"[INFO] AutoML habilitado: trials={num_trials}")

    best_trial_id = 0

    for trial in range(num_trials):
        hp = sample_hyperparams(search_space, model_family, rng)
        model = build_model(model_family, hp, aux)

        print(
            f"[INFO] trial {trial + 1}/{num_trials} | family={model_family} | "
            f"batch={int(hp.get('batch_size', 128))} | epochs={epochs}"
        )

        history = model.fit(
            X_train,
            y_train,
            validation_data=(X_val, y_val),
            epochs=epochs,
            batch_size=int(hp.get("batch_size", 128)),
            class_weight=class_weights,
            verbose=0,
        )

        y_val_prob = sanitize_probabilities(model.predict(X_val, verbose=0).ravel(), "val")
        y_val_pred = (y_val_prob >= 0.5).astype("int32")
        val_recall = recall_score(y_val, y_val_pred, zero_division=0)

        hp_native = convert_to_native_types(hp)
        trial_result = {
            "trial_id": trial,
            "hyperparameters": hp_native,
            "val_recall": float(val_recall),
            "epochs": epochs,
            "batch_size": int(hp.get("batch_size", 128)),
        }
        trials_summary.append(trial_result)

        if experiments_dir is not None:
            exp_dir = experiments_dir / f"exp_{trial:03d}"
            exp_dir.mkdir(parents=True, exist_ok=True)
            model.save(exp_dir / "model.h5")
            (exp_dir / "metrics.json").write_text(
                json.dumps(trial_result, indent=2),
                encoding="utf-8",
            )

        print(f"[INFO] trial {trial + 1}/{num_trials} | val_recall={float(val_recall):.4f}")

        if val_recall > best_val_recall:
            best_val_recall = float(val_recall)
            best_model = model
            best_hp = hp_native
            best_history = history.history
            best_trial_id = trial
            print(
                f"[INFO] Nuevo mejor trial: {best_trial_id} "
                f"(val_recall={best_val_recall:.4f})"
            )

    return best_model, best_hp, best_val_recall, best_history, trials_summary, best_trial_id


def compute_optimal_thresholds(y_true, y_prob):
    """
    Calcula:
      - threshold por F1 (global)
      - threshold por recall>=target_recall con máxima precisión
    """
    y_prob = sanitize_probabilities(y_prob, "threshold")
    precisions, recalls, thresholds = precision_recall_curve(y_true, y_prob)

    # Para F1
    f1_scores = 2 * (precisions * recalls) / (precisions + recalls + 1e-8)
    best_f1_idx = int(np.argmax(f1_scores))
    best_f1_threshold = float(thresholds[best_f1_idx]) if best_f1_idx < len(thresholds) else 0.5

    # Para recall objetivo (ejemplo: 0.9)
    target_recall = 0.9
    idx = np.where(recalls >= target_recall)[0]
    if len(idx) > 0:
        best_idx = idx[np.argmax(precisions[idx])]
        best_recall_threshold = float(thresholds[best_idx]) if best_idx < len(thresholds) else 0.5
    else:
        best_recall_threshold = 0.5

    return best_f1_threshold, best_recall_threshold


def sanitize_probabilities(y_prob, context=""):
    """Normaliza scores a un rango válido [0,1] y elimina NaN/Inf."""
    arr = np.asarray(y_prob, dtype=np.float64)
    non_finite = ~np.isfinite(arr)
    if non_finite.any():
        count = int(non_finite.sum())
        print(f"[WARN] Se detectaron {count} scores no finitos en {context}; se normalizan a 0.0")

    arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=0.0)
    arr = np.clip(arr, 0.0, 1.0)
    return arr


def summarize_label_distribution(y) -> dict[int, int]:
    labels, counts = np.unique(y, return_counts=True)
    return {int(label): int(count) for label, count in zip(labels, counts)}


def explain_split_incompatibility(y) -> str | None:
    label_distribution = summarize_label_distribution(y)

    if len(y) < 3:
        return (
            f"Dataset con {len(y)} muestra(s): no alcanza para generar "
            "splits train/val/test no vacíos"
        )

    if len(label_distribution) < 2:
        only_label = next(iter(label_distribution.keys()), None)
        return (
            "Dataset monoclase: "
            f"solo contiene la clase {only_label} con {len(y)} muestra(s)"
        )

    min_class = min(label_distribution.values())
    if min_class < 2:
        return (
            f"La clase menos poblada tiene {min_class} muestra(s); "
            "train_test_split estratificado requiere al menos 2"
        )

    return None


def write_non_trainable_outputs(
    *,
    variant_dir,
    variant: str,
    parent_variant: str,
    training_dataset_path,
    prediction_name: str,
    model_family: str,
    Tu: int,
    OW: int,
    LT: int,
    PW: int,
    event_type_count: int,
    label_distribution: dict[int, int],
    reason: str,
    start_time: float,
):
    execution_time = float(time.perf_counter() - start_time)
    total_samples = int(sum(label_distribution.values()))
    positive_samples = int(label_distribution.get(1, 0))
    negative_samples = int(label_distribution.get(0, 0))

    report_path = variant_dir / "05_modeling_report.html"
    report_html = f"""
    <html>
    <body>
      <h1>F05 Modeling — {variant}</h1>
      <p><b>Parent F04:</b> {parent_variant}</p>
      <p><b>Prediction:</b> {prediction_name}</p>
      <p><b>Model family:</b> {model_family}</p>
      <h2>Status</h2>
      <ul>
        <li>trainable = False</li>
        <li>reason = {reason}</li>
      </ul>
      <h2>Dataset</h2>
      <ul>
        <li>n_samples_total = {total_samples}</li>
        <li>negative_samples = {negative_samples}</li>
        <li>positive_samples = {positive_samples}</li>
      </ul>
      <h2>Geometry</h2>
      <ul>
        <li>Tu = {Tu}</li>
        <li>OW = {OW}</li>
        <li>LT = {LT}</li>
        <li>PW = {PW}</li>
      </ul>
      <h2>Execution</h2>
      <p>execution_time = {execution_time:.1f} s</p>
    </body>
    </html>
    """
    report_path.write_text(report_html, encoding="utf-8")

    outputs_content = {
        "phase": PHASE,
        "variant": variant,
        "artifacts": {
            "labeled_dataset": {
                "path": training_dataset_path.name,
                "sha256": sha256_of_file(training_dataset_path),
            },
            "report": {
                "path": report_path.name,
                "sha256": sha256_of_file(report_path),
            },
        },
        "exports": {
            "Tu": int(Tu),
            "OW": int(OW),
            "LT": int(LT),
            "PW": int(PW),
            "event_type_count": int(event_type_count),
            "prediction_name": str(prediction_name),
            "model_family": str(model_family),
            "trainable": False,
            "incompatibility_reason": str(reason),
        },
        "metrics": {
            "execution_time": float(execution_time),
            "n_train": 0,
            "n_val": 0,
            "n_test": 0,
            "positive_ratio_train": 0.0,
            "tp": 0,
            "tn": 0,
            "fp": 0,
            "fn": 0,
            "n_samples_total": total_samples,
            "positive_samples": positive_samples,
            "negative_samples": negative_samples,
        },
        "provenance": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "parent_phase": PARENT_PHASE,
            "parent_variant": parent_variant,
        },
    }

    save_outputs_yaml(variant_dir, outputs_content)
    validate_outputs(PHASE, outputs_content)

    print(f"[WARN] Modelo no entrenable para {variant}: {reason}")
    print(f"===== FASE {PHASE} COMPLETADA SIN ENTRENAMIENTO — variante {variant} =====")


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, help="Variant id vY_XXXX for F05")
    args = parser.parse_args()

    variant = args.variant
    start_time = time.perf_counter()

    print(f"===== FASE {PHASE} — MODELING — variante {variant} =====")

    # ---------------------------------------------
    # 1. Cargar parámetros de F05 y parent F04
    # ---------------------------------------------
    params_data = load_variant_params(get_variant_dir, PHASE, variant, "F05")
    if not isinstance(params_data, dict):
        raise RuntimeError(f"params.yaml inválido para {PHASE}:{variant}")

    params = params_data.get("parameters", {})
    parent_variant = params_data.get("parent")

    if not parent_variant:
        raise RuntimeError("F05 requiere parent definido en params.yaml (f04_targets variant)")

    print(f"[INFO] Parent F04: {parent_variant}")

    parent_outputs, parent_dir = load_phase_outputs(PROJECT_ROOT, PARENT_PHASE, parent_variant, "F05")

    artifacts_parent = parent_outputs.get("artifacts", {})
    exports_parent = parent_outputs.get("exports", {})

    dataset_rel = artifacts_parent.get("dataset", {}).get("path")
    if not dataset_rel:
        raise RuntimeError("outputs.yaml de F04 no contiene artifacts.dataset.path")

    dataset_path = parent_dir / dataset_rel
    if not dataset_path.exists():
        raise FileNotFoundError(f"No se encuentra dataset etiquetado de F04 en {dataset_path}")

    # Label column: si F04 lo expone, lo usamos; si no, usamos 'target'
    label_col = exports_parent.get("target_column", "label")
    prediction_name = params.get("prediction_name") or exports_parent.get("prediction_name", "prediction")
    event_type_count = params.get("event_type_count")
    if event_type_count is None:
        event_type_count = exports_parent.get("event_type_count")
    if event_type_count is None:
        raise RuntimeError("event_type_count missing en exports del parent F04")

    Tu = int(params.get("Tu", exports_parent.get("Tu", 0)))
    OW = int(params.get("OW", exports_parent.get("OW", 0)))
    LT = int(params.get("LT", exports_parent.get("LT", 0)))
    PW = int(params.get("PW", exports_parent.get("PW", 0)))

    model_family = params["model_family"]

    automl_cfg = params.get("automl", {})
    search_space = params.get("search_space", {})
    evaluation_cfg = params.get("evaluation", {})
    training_cfg = params.get("training", {})
    imbalance_cfg = params.get("imbalance", {})
    imbalance_strategy = params.get("imbalance_strategy")
    imbalance_max_majority = params.get("imbalance_max_majority_samples")

    if imbalance_strategy is None and isinstance(imbalance_cfg, dict):
        imbalance_strategy = imbalance_cfg.get("strategy", "none")
    if imbalance_strategy is None:
        imbalance_strategy = "none"

    if imbalance_max_majority is None and isinstance(imbalance_cfg, dict):
        imbalance_max_majority = imbalance_cfg.get("max_majority_samples")

    automl_seed = int(automl_cfg.get("seed", 42))
    configure_reproducibility(automl_seed, strict_cross_os=True)
    print(f"[INFO] reproducibility seed={automl_seed}, strict_cross_os=True")

    # ---------------------------------------------
    # 2. Cargar dataset etiquetado
    # ---------------------------------------------
    print(f"[INFO] Leyendo dataset etiquetado de F04: {dataset_path}")
    df = pd.read_parquet(dataset_path)

    if label_col not in df.columns:
        raise RuntimeError(f"La columna de etiqueta '{label_col}' no está en el dataset")

    # (Opcional) manejar imbalance de forma simple
    # Aquí solo aplicamos rare_events max_majority_samples
    strategy = imbalance_strategy
    max_maj = imbalance_max_majority

    if strategy == "rare_events" and max_maj is not None:
        max_maj = min(int(max_maj), FAST_MAX_MAJORITY_SAMPLES)
        pos = df[df[label_col] == 1]
        neg = df[df[label_col] == 0]

        if len(neg) > max_maj:
            neg = neg.sample(n=max_maj, random_state=123)

        df = pd.concat([pos, neg]).sample(frac=1.0, random_state=123)
    elif strategy == "rare_events" and max_maj is None:
        max_maj = FAST_MAX_MAJORITY_SAMPLES
        pos = df[df[label_col] == 1]
        neg = df[df[label_col] == 0]

        if len(neg) > max_maj:
            neg = neg.sample(n=max_maj, random_state=123)

        df = pd.concat([pos, neg]).sample(frac=1.0, random_state=123)

    if strategy == "rare_events":
        print(
            f"[INFO] imbalance=rare_events, max_majority_samples={max_maj} "
            f"(cap={FAST_MAX_MAJORITY_SAMPLES})"
        )

    # ---------------------------------------------
    # 2b. Preparar carpeta de salida y snapshot dataset usado
    # ---------------------------------------------
    variant_dir = get_variant_dir(PHASE, variant)
    variant_dir.mkdir(parents=True, exist_ok=True)

    training_dataset_path = variant_dir / "05_modeling_training_dataset.parquet"
    df.to_parquet(training_dataset_path)

    parent_dataset_snapshot_path = variant_dir / "05_modeling_parent_dataset.parquet"
    if dataset_path.resolve() != parent_dataset_snapshot_path.resolve():
        shutil.copy2(dataset_path, parent_dataset_snapshot_path)

    print(f"[INFO] dataset_parent={dataset_path}")
    print(f"[INFO] dataset_parent_snapshot={parent_dataset_snapshot_path}")
    print(f"[INFO] dataset_training_used={training_dataset_path}")

    # ---------------------------------------------
    # 3. Vectorización por familia + splits train/val/test
    # ---------------------------------------------
    X, y, vectorization_info = vectorize_for_family(
        df,
        label_col,
        model_family,
        int(event_type_count),
    )
    label_distribution = summarize_label_distribution(y)
    split_incompatibility = explain_split_incompatibility(y)
    if split_incompatibility is not None:
        write_non_trainable_outputs(
            variant_dir=variant_dir,
            variant=variant,
            parent_variant=parent_variant,
            training_dataset_path=training_dataset_path,
            prediction_name=prediction_name,
            model_family=model_family,
            Tu=Tu,
            OW=OW,
            LT=LT,
            PW=PW,
            event_type_count=int(event_type_count),
            label_distribution=label_distribution,
            reason=split_incompatibility,
            start_time=start_time,
        )
        return

    try:
        X_train, y_train, X_val, y_val, X_test, y_test = split_vectorized_dataset(
            X, y, evaluation_cfg
        )
    except ValueError as exc:
        write_non_trainable_outputs(
            variant_dir=variant_dir,
            variant=variant,
            parent_variant=parent_variant,
            training_dataset_path=training_dataset_path,
            prediction_name=prediction_name,
            model_family=model_family,
            Tu=Tu,
            OW=OW,
            LT=LT,
            PW=PW,
            event_type_count=int(event_type_count),
            label_distribution=label_distribution,
            reason=f"No se pudo generar split train/val/test: {exc}",
            start_time=start_time,
        )
        return

    class_weights = compute_class_weights(y_train) if strategy == "auto" else None

    print(f"[INFO] n_train={len(y_train)}, n_val={len(y_val)}, n_test={len(y_test)}")
    print(f"[INFO] positive_ratio_train={y_train.mean():.4f}")
    print(f"[INFO] vectorization={vectorization_info.get('vectorization')}")

    # ---------------------------------------------
    # 4. AutoML — entrenamiento y selección
    # ---------------------------------------------
    experiments_dir = variant_dir / "experiments"

    best_model, best_hp, best_val_recall, history, trials_summary, best_trial_id = train_with_automl(
        X_train,
        y_train,
        X_val,
        y_val,
        model_family,
        vectorization_info,
        search_space,
        automl_cfg,
        training_cfg,
        class_weights=class_weights,
        experiments_dir=experiments_dir,
    )

    # ---------------------------------------------
    # 5. Evaluación final en test + thresholds
    # ---------------------------------------------
    y_test_prob = sanitize_probabilities(best_model.predict(X_test, verbose=0).ravel(), "test")

    # Umbral base 0.5
    y_test_pred05 = (y_test_prob >= 0.5).astype("int32")
    test_precision = precision_score(y_test, y_test_pred05, zero_division=0)
    test_recall = recall_score(y_test, y_test_pred05, zero_division=0)
    test_f1 = f1_score(y_test, y_test_pred05, zero_division=0)
    cm = confusion_matrix(y_test, y_test_pred05, labels=[0, 1])
    tn, fp, fn, tp = [int(v) for v in cm.ravel()]

    best_f1_thr, best_recall_thr = compute_optimal_thresholds(y_test, y_test_prob)

    execution_time = float(time.perf_counter() - start_time)

    print(f"[INFO] best_val_recall={best_val_recall:.4f}")
    print(f"[INFO] test_precision@0.5={test_precision:.4f}, test_recall@0.5={test_recall:.4f}, test_f1@0.5={test_f1:.4f}")
    print(f"[INFO] confusion@0.5: tp={tp}, tn={tn}, fp={fp}, fn={fn}")
    print(f"[INFO] best_f1_threshold={best_f1_thr:.4f}, best_recall_threshold={best_recall_thr:.4f}")
    print(f"[INFO] execution_time={execution_time:.1f}s")

    # ---------------------------------------------
    # 6. Guardar modelo + report + history (opcional)
    # ---------------------------------------------
    model_path = variant_dir / "05_modeling_model.h5"
    best_model.save(model_path)

    # history opcional
    history_path = variant_dir / "05_modeling_history.json"
    with history_path.open("w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    trials_summary_path = variant_dir / "05_modeling_trials_summary.json"
    trials_summary_path.write_text(
        json.dumps(convert_to_native_types(trials_summary), indent=2),
        encoding="utf-8",
    )

    best_model_in_experiments = experiments_dir / f"exp_{best_trial_id:03d}" / "model.h5"

    print(f"[INFO] best_trial_id={best_trial_id}")
    print(f"[INFO] experiments_dir={experiments_dir}")
    print(f"[INFO] best_model_in_experiments={best_model_in_experiments}")
    print(f"[INFO] best_model_final={model_path}")

    # Report muy simple (puedes refinar luego)
    report_path = variant_dir / "05_modeling_report.html"
    report_html = f"""
    <html>
    <body>
      <h1>F05 Modeling — {variant}</h1>
      <p><b>Parent F04:</b> {parent_variant}</p>
      <p><b>Prediction:</b> {prediction_name}</p>
      <p><b>Model family:</b> {model_family}</p>
      <h2>Geometry</h2>
      <ul>
        <li>Tu = {Tu}</li>
        <li>OW = {OW}</li>
        <li>LT = {LT}</li>
        <li>PW = {PW}</li>
      </ul>
      <h2>AutoML</h2>
      <pre>{json.dumps(best_hp, indent=2)}</pre>
      <h2>Validation</h2>
      <p>best_val_recall = {best_val_recall:.4f}</p>
      <h2>Test @0.5</h2>
      <ul>
        <li>precision = {test_precision:.4f}</li>
        <li>recall = {test_recall:.4f}</li>
        <li>f1 = {test_f1:.4f}</li>
                <li>tp = {tp}</li>
                <li>tn = {tn}</li>
                <li>fp = {fp}</li>
                <li>fn = {fn}</li>
      </ul>
      <h2>Thresholds</h2>
      <ul>
        <li>best_f1_threshold = {best_f1_thr:.4f}</li>
        <li>best_recall_threshold = {best_recall_thr:.4f}</li>
      </ul>
      <h2>Execution</h2>
      <p>execution_time = {execution_time:.1f} s</p>
    </body>
    </html>
    """
    report_path.write_text(report_html, encoding="utf-8")

    # ---------------------------------------------
    # 7. Construir outputs.yaml
    # ---------------------------------------------
    outputs_content = {
        "phase": PHASE,
        "variant": variant,
        "artifacts": {
            "model": {
                "path": model_path.name,
                "sha256": sha256_of_file(model_path),
            },
            "labeled_dataset": {
                "path": training_dataset_path.name,
                "sha256": sha256_of_file(training_dataset_path),
            },
            "history": {
                "path": history_path.name,
                "sha256": sha256_of_file(history_path),
            },
            "report": {
                "path": report_path.name,
                "sha256": sha256_of_file(report_path),
            },
        },
        "exports": {
            "Tu": int(Tu),
            "OW": int(OW),
            "LT": int(LT),
            "PW": int(PW),
            "event_type_count": int(event_type_count),
            "prediction_name": str(prediction_name),
            "model_family": str(model_family),
            "trainable": True,
            "decision_threshold": float(best_f1_thr),
            "best_val_recall": float(best_val_recall),
            "test_precision": float(test_precision),
            "test_recall": float(test_recall),
            "test_f1": float(test_f1),
        },
        "metrics": {
            "execution_time": float(execution_time),
            "n_train": int(len(y_train)),
            "n_val": int(len(y_val)),
            "n_test": int(len(y_test)),
            "positive_ratio_train": float(y_train.mean()),
            "tp": int(tp),
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
        },
        "provenance": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "parent_phase": PARENT_PHASE,
            "parent_variant": parent_variant,
        },
        # Bloque para MLflow — Makefile se encarga
        "mlflow_registration": {
            "experiment_name": f"F05_{prediction_name}",
            "run_name": f"{prediction_name}__{variant}",
            "metrics": {
                "val_recall": float(best_val_recall),
                "test_precision": float(test_precision),
                "test_recall": float(test_recall),
                "test_f1": float(test_f1),
                "test_tp": int(tp),
                "test_tn": int(tn),
                "test_fp": int(fp),
                "test_fn": int(fn),
            },
            "params": {
                **convert_to_native_types(best_hp),
                "model_family": model_family,
            },
            "artifacts": [
                str(model_path),
                str(history_path),
                str(trials_summary_path),
            ],
        },
    }

    save_outputs_yaml(variant_dir, outputs_content)
    validate_outputs(PHASE, outputs_content)

    print(f"===== FASE {PHASE} COMPLETADA — variante {variant} =====")


if __name__ == "__main__":
    main()