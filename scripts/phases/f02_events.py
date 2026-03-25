#!/usr/bin/env python3

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


from scripts.core.artifacts import (
    sha256_of_file,
    save_outputs_yaml,
    load_params,
    get_variant_dir,
    save_json
)
from scripts.core.phase_io import load_phase_outputs, resolve_artifact_path
from scripts.core.traceability import validate_outputs


# ============================================================
# CONSTANTES
# ============================================================

PHASE = "f02_events"
PROJECT_ROOT = REPO_ROOT


# ============================================================
# FUNCIONES DE NEGOCIO (adaptadas de 02_prepareeventsds)
# ============================================================

def compute_minmax(df: pd.DataFrame, measure_cols):
    return {
        col: {
            "min": float(df[col].min()),
            "max": float(df[col].max()),
        }
        for col in measure_cols
    }


def compute_cuts_and_labels(minmax_stats, pct_thresholds):
    pct_list = [0.0] + pct_thresholds + [100.0]
    out = {}

    for col, mm in minmax_stats.items():
        mn, mx = mm["min"], mm["max"]
        r = mx - mn

        if r == 0:
            cuts = np.array([mn, mx])
            labels = ["0_100"]
        else:
            cuts = np.array([mn + p / 100 * r for p in pct_list])
            labels = [
                f"{int(pct_list[i])}_{int(pct_list[i + 1])}"
                for i in range(len(pct_list) - 1)
            ]

        out[col] = {"cuts": cuts, "labels": labels}

    return out


def build_event_catalog(bands, strategy, nan_mode):
    event_to_id = {}
    next_id = 1

    strat = strategy.lower()
    nan_keep = (nan_mode.lower() == "keep")

    for col, info in bands.items():
        labels = info["labels"]

        if strat in ("transitions", "both"):
            for a in labels:
                for b in labels:
                    if a != b:
                        event_to_id[f"{col}_{a}-to-{b}"] = next_id
                        next_id += 1

        if strat in ("levels", "both"):
            for a in labels:
                event_to_id[f"{col}_{a}"] = next_id
                next_id += 1

        if nan_keep:
            event_to_id[f"{col}_NaN_NaN"] = next_id
            next_id += 1

    return event_to_id


def assign_bands_to_column(values, cuts, labels):
    is_nan = np.isnan(values)
    idx = np.searchsorted(cuts, values, side="right") - 1
    idx = np.clip(idx, 0, len(labels) - 1)

    labels_arr = np.array(labels, dtype=object)
    assigned = labels_arr[idx]
    assigned[is_nan] = None

    kind = np.where(is_nan, "NaN", "band")

    return kind, assigned


def generate_events(df, epoch_col, measure_cols, bands, event_to_id, strategy, nan_mode, Tu):

    N = len(df)
    epochs = df[epoch_col].values.astype(np.int64)

    is_consecutive = np.zeros(N, dtype=bool)
    is_consecutive[1:] = (np.diff(epochs) == Tu)

    strat = strategy.lower()
    nan_keep = nan_mode.lower() == "keep"

    events_column = [[] for _ in range(N)]

    prev_kind = {col: None for col in measure_cols}
    prev_label = {col: None for col in measure_cols}

    col_kind = {}
    col_label = {}

    for col in measure_cols:
        vals = df[col].values
        cuts = bands[col]["cuts"]
        labels = bands[col]["labels"]

        k_arr, lbl_arr = assign_bands_to_column(vals, cuts, labels)
        col_kind[col] = k_arr
        col_label[col] = lbl_arr

    for i in range(N):
        row_events = []

        for col in measure_cols:
            curr_k = col_kind[col][i]
            curr_lbl = col_label[col][i]

            if i > 0 and is_consecutive[i] and strat in ("transitions", "both"):
                pk = prev_kind[col]
                pl = prev_label[col]
                if pk == "band" and curr_k == "band" and pl != curr_lbl:
                    ev = event_to_id.get(f"{col}_{pl}-to-{curr_lbl}")
                    if ev:
                        row_events.append(ev)

            if curr_k == "band" and strat in ("levels", "both"):
                ev = event_to_id.get(f"{col}_{curr_lbl}")
                if ev:
                    row_events.append(ev)

            elif curr_k == "NaN" and nan_keep:
                ev = event_to_id.get(f"{col}_NaN_NaN")
                if ev:
                    row_events.append(ev)

            prev_kind[col] = curr_k
            prev_label[col] = curr_lbl

        events_column[i] = row_events

    return pd.DataFrame({
        epoch_col: df[epoch_col].values,
        "events": events_column
    })


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
    # Resolver parent F01
    # --------------------------------------------------------

    parent_phase = "f01_explore"
    parent_outputs, parent_dir = load_phase_outputs(
        PROJECT_ROOT,
        parent_phase,
        parent_variant,
        "F02",
    )

    parent_dataset_path = resolve_artifact_path(
        parent_dir,
        parent_outputs,
        ["dataset"],
        "F02",
    )

    df = pd.read_parquet(parent_dataset_path)

    Tu = params["Tu"]
    strategy = params["strategy"]
    bands_pct = params["bands"]
    nan_mode = params["nan_mode"]

    # --------------------------------------------------------
    # Determinar columna temporal 'segs'
    # --------------------------------------------------------

    if "segs" in df.columns:
        epoch_col = "segs"
    elif df.index.name == "segs":
        df = df.reset_index()
        epoch_col = "segs"
    else:
        raise RuntimeError(
            "No se encontró 'segs' ni como columna ni como índice en el dataset padre"
        )

    # --------------------------------------------------------
    # Columnas de medida: vienen de F01 (exports.measure_cols)
    # --------------------------------------------------------

    exports_parent = parent_outputs.get("exports", {})
    measure_cols = exports_parent.get("measure_cols")

    if not measure_cols:
        raise RuntimeError(
            "El parent no exporta 'measure_cols' en outputs.yaml (F01 incompleto)"
        )

    # Verificación de coherencia
    missing = [c for c in measure_cols if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"Columnas de medida declaradas en F01 no están en el dataset padre: {missing}"
        )
    
    # --------------------------------------------------------
    # Generar eventos
    # --------------------------------------------------------

    minmax_stats = compute_minmax(df, measure_cols)
    bands = compute_cuts_and_labels(minmax_stats, bands_pct)
    event_to_id = build_event_catalog(bands, strategy, nan_mode)

    df_events = generate_events(
        df=df,
        epoch_col=epoch_col,
        measure_cols=measure_cols,
        bands=bands,
        event_to_id=event_to_id,
        strategy=strategy,
        nan_mode=nan_mode,
        Tu=Tu,
    )

    # --------------------------------------------------------
    # Guardar artefactos
    # --------------------------------------------------------

    events_path = variant_dir / "02_events.parquet"
    catalog_path = variant_dir / "02_events_catalog.json"
    report_path = variant_dir / "02_events_report.html"

    df_events.to_parquet(events_path, index=False)

    save_json(catalog_path, event_to_id)

    # Report muy simple (puedes refinar luego)
    report_html = f"""
    <html>
    <body>
    <h1>F02 Events — {variant}</h1>
    <p>Parent: {parent_variant}</p>
    <p>Strategy: {strategy}</p>
    <p>Band thresholds: {bands_pct}</p>
    <p>N events: {len(df_events)}</p>
    <p>N event types: {len(event_to_id)}</p>
    </body>
    </html>
    """

    report_path.write_text(report_html)

    execution_time = float(time.perf_counter() - start_time)

    # --------------------------------------------------------
    # Construir outputs.yaml
    # --------------------------------------------------------

    outputs_content = {
        "phase": PHASE,
        "variant": variant,
        "artifacts": {
            "events": {
                "path": events_path.name,
                "sha256": sha256_of_file(events_path),
            },
            "catalog": {
                "path": catalog_path.name,
                "sha256": sha256_of_file(catalog_path),
            },
            "report": {
                "path": report_path.name,
                "sha256": sha256_of_file(report_path),
            },
        },
        "exports": {
            "Tu": int(Tu),
            "n_events": int(len(df_events)),
            "n_types": int(len(event_to_id)),
        },
        "metrics": {
            "execution_time": execution_time,
            "n_rows_in": int(len(df)),
            "n_rows_out": int(len(df_events)),
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