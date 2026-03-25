#!/usr/bin/env python3

import argparse
import time
import re
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
# ============================================================


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

    df = pd.read_parquet(parent_dataset_path)

    if "OW_events" not in df.columns or "PW_events" not in df.columns:
        raise RuntimeError(
            "El dataset F03 debe contener columnas OW_events y PW_events"
        )

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

    if target_operator != "OR":
        raise NotImplementedError(
            f"Operador no soportado: {target_operator}"
        )

    print("[INFO] Objetivo de predicción:")
    print(f"  prediction_name = {prediction_name}")
    print(f"  operator        = {target_operator}")
    print(f"  event_types     = {target_event_types}")

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

    # --------------------------------------------------------
    # outputs.yaml
    # --------------------------------------------------------

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
            "target_operator": target_operator,
            "target_event_types": target_event_types,
            "event_type_count": event_type_count,
            "n_windows": total,
            "n_windows_pos": positives,
            "n_windows_neg": negatives,
        },
        "metrics": {
            "execution_time": float(elapsed),
            "positive_ratio": float(ratio),
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