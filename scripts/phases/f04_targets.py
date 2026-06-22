#!/usr/bin/env python3

import argparse
import hashlib
import time
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from scripts.core.artifacts import (
    sha256_of_file,
    save_outputs_yaml,
    load_params,
    get_variant_dir,
    load_json,
)
from scripts.core.phase_io import load_phase_outputs, resolve_artifact_path
from scripts.core.traceability import validate_outputs


# ============================================================
PHASE = "f04_targets"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIN_POSITIVE_RATIO_FOR_TARGET_COMPATIBILITY = 0.001
# ============================================================


# ============================================================
# Helper functions
# ============================================================
def extract_measure_name(prediction_name: str) -> str:
    suffix = "_any-to-"
    if suffix in prediction_name:
        return prediction_name.split(suffix, 1)[0]
    return prediction_name


def parse_transition_event_name(event_name: str) -> dict | None:
    match = re.match(
        r"^(?P<measure>.+)_(?P<src>-?\d+(?:\.\d+)?_-?\d+(?:\.\d+)?)-to-(?P<dst>-?\d+(?:\.\d+)?_-?\d+(?:\.\d+)?)$",
        str(event_name),
    )
    if not match:
        return None
    return match.groupdict()


def band_sort_key(label: str):
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(label))
    if len(nums) >= 2:
        return (float(nums[0]), float(nums[1]))
    return (float("inf"), float("inf"))


def load_f02_target_candidates(parent_f02: str) -> tuple[dict, dict]:
    f02_dir = PROJECT_ROOT / "executions" / "f02_events" / str(parent_f02)
    outputs_path = f02_dir / "outputs.yaml"
    if not outputs_path.exists():
        raise RuntimeError(
            f"No se pudo validar target_candidates: no existe {outputs_path}"
        )

    import yaml

    outputs = yaml.safe_load(outputs_path.read_text()) or {}
    exports = outputs.get("exports", {}) or {}
    candidates = exports.get("target_candidates")
    if not isinstance(candidates, dict) or not candidates:
        raise RuntimeError(
            "No se pudo validar target_candidates: "
            f"exports.target_candidates missing en {outputs_path}. "
            "Regenera F02 con el pipeline actualizado."
        )

    return candidates, {}

def infer_target_candidate_checks(target_event_types: list[str], per_measure: dict) -> list[tuple[str, str]]:
    checks = set()
    parsed_events = []

    for event_name in target_event_types:
        parsed = parse_transition_event_name(event_name)
        if parsed is None:
            continue
        parsed_events.append(parsed)

    for parsed in parsed_events:
        measure = parsed["measure"]
        dst = parsed["dst"]
        measure_stats = per_measure.get(measure, {}) or {}
        band_occupancy = measure_stats.get("band_occupancy", {}) or {}
        labels = list(band_occupancy.keys())
        if not labels:
            labels = sorted(
                {
                    item["src"]
                    for item in parsed_events
                    if item["measure"] == measure
                }
                | {
                    item["dst"]
                    for item in parsed_events
                    if item["measure"] == measure
                },
                key=band_sort_key,
            )
        if not labels:
            continue

        ordered = sorted(labels, key=band_sort_key)
        if dst == ordered[0]:
            checks.add((measure, "low"))
        elif dst == ordered[-1]:
            checks.add((measure, "high"))

    return sorted(checks)


def evaluate_f02_target_candidates(parent_f02: str, target_event_types: list[str]) -> dict:
    target_candidates, per_measure = load_f02_target_candidates(parent_f02)
    checks = infer_target_candidate_checks(target_event_types, per_measure)

    if not checks:
        print("[WARN] No se pudo inferir high/low desde target_event_types; se omite validacion target_candidates F02.")
        return {
            "compatible": True,
            "checks": [],
            "failures": [],
            "reason": None,
        }

    failures = []
    for measure, direction in checks:
        measure_info = target_candidates.get(measure)
        direction_info = (measure_info or {}).get(direction, {}) if isinstance(measure_info, dict) else {}
        candidate = bool(direction_info.get("candidate", False))
        reason = str(direction_info.get("reason", "reason no disponible"))
        if not candidate:
            failures.append((measure, direction, reason))

    if failures:
        details = "; ".join(
            f"{measure}.{direction}: candidate=false ({reason})"
            for measure, direction, reason in failures
        )
        return {
            "compatible": False,
            "checks": checks,
            "failures": failures,
            "reason": (
                "F04 target incompatible segun target_candidates de F02. "
                f"parent_f02={parent_f02}. {details}"
            ),
        }

    return {
        "compatible": True,
        "checks": checks,
        "failures": [],
        "reason": None,
    }


def validate_f02_target_candidates(parent_f02: str, target_event_types: list[str]):
    evaluation = evaluate_f02_target_candidates(parent_f02, target_event_types)
    if not evaluation["compatible"]:
        raise RuntimeError(evaluation["reason"])
    return evaluation["checks"]
# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True)
    args = parser.parse_args()

    variant = args.variant
    variant_dir = get_variant_dir(PHASE, variant)

    params_data = load_params(PHASE, variant)
    params = params_data["parameters"]
    parent_variant = params_data["parent"]

    print(f"\n===== INICIO {PHASE} / {variant} =====")

    start_time = time.perf_counter()

    # --------------------------------------------------------
    # Resolver parent F03
    # --------------------------------------------------------

    parent_phase = "f03_windows"

    parent_outputs, parent_dir = load_phase_outputs(
        PROJECT_ROOT,
        parent_phase,
        parent_variant,
        "F04",
    )

    parent_dataset_path = resolve_artifact_path(
        parent_dir,
        parent_outputs,
        ["dataset"],
        "F04",
    )

    parent_catalog_path = resolve_artifact_path(
        parent_dir,
        parent_outputs,
        ["catalog"],
        "F04",
    )

    if not parent_dataset_path.exists():
        raise RuntimeError("No existe dataset de ventanas F03")

    if not parent_catalog_path.exists():
        raise RuntimeError("No existe catálogo de eventos F03")

    # --------------------------------------------------------
    # Parámetros de objetivo
    # --------------------------------------------------------

    prediction_name = params["prediction_name"]
    target_operator = params["target_operator"]
    target_event_types_raw = params["target_event_types"]

    def normalize_target_event_types(raw_value):
        if isinstance(raw_value, str):
            raw_items = [raw_value]
        elif isinstance(raw_value, list):
            raw_items = raw_value
        else:
            raise ValueError("target_event_types debe ser string o list")

        normalized = []

        for item in raw_items:
            if not isinstance(item, str):
                raise ValueError("Cada item de target_event_types debe ser string")

            value = item.strip()
            if not value:
                continue

            if any(sep in value for sep in [",", ";"]) or re.search(r"\s+", value):
                parts = re.split(r"[\s,;]+", value)
                normalized.extend(part for part in parts if part)
            else:
                normalized.append(value)

        return normalized

    target_event_types = normalize_target_event_types(target_event_types_raw)
    parent_measure_name = parent_outputs.get("exports", {}).get("measure_name")
    measure_name = parent_measure_name or extract_measure_name(prediction_name)
    target_event_count = len(target_event_types)

    if target_operator != "OR":
        raise NotImplementedError(
            f"Operador no soportado: {target_operator}"
        )

    print("[INFO] Objetivo de predicción:")
    print(f"  prediction_name = {prediction_name}")
    print(f"  operator        = {target_operator}")
    print(f"  event_types     = {target_event_types}")
    parent_exports = parent_outputs.get("exports", {})
    parent_f02 = parent_exports.get("parent_f02")
    if not parent_f02:
        raise RuntimeError(
            "No se pudo validar target_candidates: parent_f02 missing en exports de F03"
        )
    target_candidate_eval = evaluate_f02_target_candidates(
        parent_f02,
        target_event_types,
    )
    target_candidate_checks = target_candidate_eval["checks"]
    if not target_candidate_eval["compatible"]:
        elapsed = time.perf_counter() - start_time
        reason = str(target_candidate_eval["reason"])
        report_path = variant_dir / "04_targets_report.html"
        report_path.write_text(
            f"""
            <html>
            <body>
            <h1>F04 Targets - {variant}</h1>
            <p>Parent: {parent_variant}</p>
            <p>Prediction name: {prediction_name}</p>
            <p>Operator: {target_operator}</p>
            <p>Event types: {target_event_types}</p>
            <p>Target compatible: False</p>
            <p>Incompatibility reason: {reason}</p>
            </body>
            </html>
            """,
            encoding="utf-8",
        )
        outputs_content = {
            "phase": PHASE,
            "variant": variant,
            "artifacts": {
                "report": {
                    "path": report_path.name,
                    "sha256": sha256_of_file(report_path),
                },
            },
            "exports": {
                "Tu": int(parent_exports.get("Tu", params.get("Tu"))),
                "OW": int(parent_exports.get("OW", params.get("OW"))),
                "LT": int(parent_exports.get("LT", params.get("LT"))),
                "PW": int(parent_exports.get("PW", params.get("PW"))),
                "event_type_count": int(parent_exports.get("event_type_count", params.get("event_type_count", 0))),
                "prediction_name": prediction_name,
                "measure_name": measure_name,
                "target_operator": target_operator,
                "target_event_types": target_event_types,
                "target_event_count": int(target_event_count),
                "n_windows": 0,
                "n_windows_pos": 0,
                "n_windows_neg": 0,
                "n_positive": 0,
                "n_negative": 0,
                "target_compatible": False,
                "incompatibility_reason": reason,
                "target_candidate_checks": [
                    {"measure": measure, "direction": direction}
                    for measure, direction in target_candidate_checks
                ],
                "target_candidate_failures": [
                    {
                        "measure": measure,
                        "direction": direction,
                        "reason": failure_reason,
                    }
                    for measure, direction, failure_reason in target_candidate_eval["failures"]
                ],
                "parent_f03": parent_variant,
                "parent_f02": parent_f02,
                "parent_f01": parent_exports.get("parent_f01"),
            },
            "metrics": {
                "execution_time": float(elapsed),
                "n_windows": 0,
                "n_positive": 0,
                "n_negative": 0,
                "positive_ratio": 0.0,
                "target_compatible": False,
                "incompatibility_reason": reason,
            },
            "provenance": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        save_outputs_yaml(variant_dir, outputs_content)
        validate_outputs(PHASE, outputs_content)
        print(f"[WARN] F04 target incompatible: {reason}")
        print(f"\n===== FASE {PHASE} COMPLETADA SIN DATASET =====")
        return

    print("[INFO] target_candidates F02 validados:", target_candidate_checks)

    # --------------------------------------------------------
    # Cargar catálogo (name -> code)
    # --------------------------------------------------------

    catalog = load_json(parent_catalog_path)
    event_type_count = int(len(catalog))

    name_to_code = {
        name: int(code)
        for name, code in catalog.items()
    }

    target_event_codes = []

    for name in target_event_types:
        if name not in name_to_code:
            raise ValueError(
                f"Evento '{name}' no existe en catálogo"
            )
        target_event_codes.append(name_to_code[name])

    target_event_codes = set(target_event_codes)
    df = pq.read_table(parent_dataset_path, memory_map=True).to_pandas()

    if "OW_events" not in df.columns or "PW_events" not in df.columns:
        raise RuntimeError(
            "El dataset F03 debe contener columnas OW_events y PW_events"
        )

    print("[INFO] Códigos objetivo:", sorted(target_event_codes))

    # --------------------------------------------------------
    # Etiquetado
    # --------------------------------------------------------

    def label_window(pw_events):
        return int(any(ev in target_event_codes for ev in pw_events))

    df["label"] = df["PW_events"].apply(label_window)

    df_out = df[["OW_events", "label"]].copy()

    # --------------------------------------------------------
    # Estadísticas
    # --------------------------------------------------------

    total = len(df_out)
    positives = int(df_out["label"].sum())
    negatives = total - positives
    ratio = positives / total if total else 0.0

    elapsed = time.perf_counter() - start_time

    print(f"[INFO] Total ventanas: {total}")
    print(f"[INFO] Positivas: {positives}")
    print(f"[INFO] Negativas: {negatives}")
    print(f"[INFO] Positive ratio: {ratio:.6f}")

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    report_path = variant_dir / "04_targets_report.html"
    report_path.write_text(
        f"""
        <html>
        <body>
        <h1>F04 Targets — {variant}</h1>
        <p>Parent: {parent_variant}</p>
        <p>Prediction name: {prediction_name}</p>
        <p>Operator: {target_operator}</p>
        <p>Event types: {target_event_types}</p>
        <p>Total windows: {total}</p>
        <p>Positives: {positives}</p>
        <p>Negatives: {negatives}</p>
        <p>Positive ratio: {ratio:.6f}</p>
        </body>
        </html>
        """
    )

    target_compatible = ratio >= MIN_POSITIVE_RATIO_FOR_TARGET_COMPATIBILITY
    incompatibility_reason = None
    if not target_compatible:
        incompatibility_reason = (
            f"positive_ratio={ratio:.6f} below minimum "
            f"{MIN_POSITIVE_RATIO_FOR_TARGET_COMPATIBILITY:.6f}"
        )

    if not target_compatible:
        parent_exports = parent_outputs.get("exports", {})
        parent_f02 = parent_exports.get("parent_f02")
        window_strategy = parent_exports.get("window_strategy")

        outputs_content = {
            "phase": PHASE,
            "variant": variant,
            "artifacts": {
                "report": {
                    "path": report_path.name,
                    "sha256": sha256_of_file(report_path),
                },
            },
            "exports": {
                "Tu": int(parent_exports.get("Tu", params.get("Tu"))),
                "OW": int(parent_exports.get("OW", params.get("OW"))),
                "LT": int(parent_exports.get("LT", params.get("LT"))),
                "PW": int(parent_exports.get("PW", params.get("PW"))),
                "prediction_name": prediction_name,
                "measure_name": measure_name,
                "target_operator": target_operator,
                "target_event_types": target_event_types,
                "target_event_count": int(target_event_count),
                "target_compatible": False,
                "incompatibility_reason": incompatibility_reason,
                "target_candidate_checks": [
                    {"measure": measure, "direction": direction}
                    for measure, direction in target_candidate_checks
                ],
                "event_type_count": event_type_count,
                "window_strategy": window_strategy,
                "parent_f03": parent_variant,
                "parent_f02": parent_f02,
                "parent_f01": parent_exports.get("parent_f01"),
                "n_windows": int(total),
                "n_windows_pos": int(positives),
                "n_windows_neg": int(negatives),
                "n_positive": int(positives),
                "n_negative": int(negatives),
                "class_balance_ratio": float(ratio),
            },
            "metrics": {
                "execution_time": float(elapsed),
                "n_windows": int(total),
                "n_positive": int(positives),
                "n_negative": int(negatives),
                "positive_ratio": float(ratio),
                "target_compatible": False,
                "incompatibility_reason": incompatibility_reason,
            },
            "provenance": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        }

        save_outputs_yaml(variant_dir, outputs_content)
        validate_outputs(PHASE, outputs_content)
        print(f"[WARN] F04 target incompatible: {incompatibility_reason}")
        print(f"\n===== FASE {PHASE} COMPLETADA SIN DATASET =====")
        return

    # --------------------------------------------------------
    # Guardar dataset
    # --------------------------------------------------------

    output_path = variant_dir / "04_targets.parquet"

    schema = pa.schema([
        ("OW_events", pa.list_(pa.int32())),
        ("label", pa.int8()),
    ])

    table_out = pa.Table.from_pandas(
        df_out,
        schema=schema,
        preserve_index=False,
    )

    pq.write_table(table_out, output_path, compression="snappy")

    # --------------------------------------------------------
    # outputs.yaml
    # --------------------------------------------------------


    def analyze_ow_duplicates(ow_events_list, labels):
        """
        Analiza duplicados basados SOLO en OW (ignorando label)
        """

        total = len(ow_events_list)

        # --------------------------------------------------------
        # Agrupar por OW
        # --------------------------------------------------------

        groups = defaultdict(list)

        for ow, label in zip(ow_events_list, labels):
            key = tuple(ow)
            groups[key].append(label)

        unique_ow = len(groups)

        # --------------------------------------------------------
        # Duplicados estructurales
        # --------------------------------------------------------

        num_duplicate_sequences = sum(len(v) - 1 for v in groups.values() if len(v) > 1)
        duplicate_ratio = num_duplicate_sequences / total if total else 0.0

        # --------------------------------------------------------
        # Ambigüedad de labels (CRÍTICO)
        # --------------------------------------------------------

        ambiguous_sequences = 0
        ambiguous_samples = 0

        for labels_list in groups.values():
            if len(set(labels_list)) > 1:
                ambiguous_sequences += 1
                ambiguous_samples += len(labels_list)

        ambiguous_ratio = ambiguous_samples / total if total else 0.0

        # --------------------------------------------------------
        # Dominancia de clase por OW
        # --------------------------------------------------------

        majority_consistency = []

        for labels_list in groups.values():
            count = Counter(labels_list)
            majority = max(count.values())
            consistency = majority / len(labels_list)
            majority_consistency.append(consistency)

        avg_consistency = sum(majority_consistency) / len(majority_consistency)

        return {
            "total_sequences": total,
            "unique_ow_sequences": unique_ow,
            "num_duplicate_sequences": num_duplicate_sequences,
            "duplicate_ratio": duplicate_ratio,

 
            "ambiguous_sequences": ambiguous_sequences,
            "ambiguous_samples": ambiguous_samples,
            "ambiguous_ratio": ambiguous_ratio,

            # calidad del dataset
            "avg_label_consistency_per_ow": avg_consistency,
        }
    
    dedup_stats = analyze_ow_duplicates(df_out["OW_events"], df_out["label"])


    parent_exports = parent_outputs.get("exports", {})

    parent_f02 = parent_exports.get("parent_f02")
    window_strategy = parent_exports.get("window_strategy")
    dup_ratio_ow = parent_exports.get("dup_ratio_ow")
    dup_ratio_pw = parent_exports.get("dup_ratio_pw")
    seq_len_mean_ow = parent_exports.get("seq_len_mean_ow")

    outputs_content = {
        "phase": PHASE,
        "variant": variant,
        "artifacts": {
            "dataset": {
                "path": output_path.name,
                "sha256": sha256_of_file(output_path),
            },
            "report": {
                "path": report_path.name,
                "sha256": sha256_of_file(report_path),
            },
        },
        "exports": {
            "Tu": int(parent_outputs.get("exports", {}).get("Tu", params.get("Tu"))),
            "OW": int(parent_outputs.get("exports", {}).get("OW", params.get("OW"))),
            "LT": int(parent_outputs.get("exports", {}).get("LT", params.get("LT"))),
            "PW": int(parent_outputs.get("exports", {}).get("PW", params.get("PW"))),
            "prediction_name": prediction_name,
            "measure_name": measure_name,
            "target_operator": target_operator,
            "target_event_types": target_event_types,
            "target_event_count": int(target_event_count),
            "target_compatible": True,
            "incompatibility_reason": None,
            "target_candidate_checks": [
                {"measure": measure, "direction": direction}
                for measure, direction in target_candidate_checks
            ],
            "event_type_count": event_type_count,
            "window_strategy": window_strategy,
            "parent_f03": parent_variant,
            "parent_f02": parent_f02,
            "n_windows": int(total),
            "n_windows_pos": int(positives),
            "n_windows_neg": int(negatives),
            "n_positive": int(positives),
            "n_negative": int(negatives),
            "class_balance_ratio": float(ratio),
            "deduplication_stats": dedup_stats,
            "unique_ratio": dedup_stats["unique_ow_sequences"] / total if total else 0.0,
            "dup_ratio_ow_parent": dup_ratio_ow,
            "dup_ratio_pw_parent": dup_ratio_pw,
            "seq_len_mean_ow_parent": seq_len_mean_ow,
        },
        "metrics": {
            "execution_time": float(elapsed),
            "n_windows": int(total),
            "n_positive": int(positives),
            "n_negative": int(negatives),
            "positive_ratio": float(ratio),
            "target_compatible": True,
            "incompatibility_reason": None,
        },
        "provenance": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    save_outputs_yaml(variant_dir, outputs_content)
    validate_outputs(PHASE, outputs_content)

    print(f"\n===== FASE {PHASE} COMPLETADA =====")


if __name__ == "__main__":
    main()
