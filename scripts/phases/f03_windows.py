#!/usr/bin/env python3

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from bisect import bisect_left

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import hashlib

from scripts.core.artifacts import (
    sha256_of_file,
    save_outputs_yaml,
    load_params,
    get_variant_dir,
    save_json,
    load_json,
)
from scripts.core.phase_io import load_phase_outputs, resolve_artifact_path
from scripts.core.traceability import validate_outputs


# ============================================================
PHASE = "f03_windows"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# ============================================================


# ============================================================
# HELPERS
# ============================================================

def has_nan_in_range(nan_prefix, i0, i1):
    if i0 >= i1:
        return False
    return nan_prefix[i1 - 1] - (nan_prefix[i0 - 1] if i0 else 0) > 0


def flush_rows(writer, rows, schema):
    if rows:
        writer.write_table(pa.Table.from_pylist(rows, schema))
        rows.clear()


def range_event_count(offsets, i0, i1):
    return int(offsets[i1] - offsets[i0])

def stable_array_hash(arr):
    if len(arr) == 0:
        return "EMPTY"
    a = np.asarray(arr, dtype=np.int32)
    return hashlib.md5(a.tobytes()).hexdigest()


def register_window(rows, ow, pw, ow_lengths, pw_lengths, ow_hashes, pw_hashes):
    rows.append({"OW_events": ow, "PW_events": pw})
    ow_lengths.append(len(ow))
    pw_lengths.append(len(pw))
    ow_hashes.add(stable_array_hash(ow))
    pw_hashes.add(stable_array_hash(pw))


def evaluate_measure_target_candidates(parent_exports: dict, measure_name: str | None) -> dict:
    target_candidates = parent_exports.get("target_candidates") or {}
    if not measure_name or not isinstance(target_candidates, dict):
        return {"compatible": True, "reason": None, "checks": []}

    measure_candidates = target_candidates.get(measure_name)
    if not isinstance(measure_candidates, dict):
        return {"compatible": True, "reason": None, "checks": []}

    checks = []
    for direction in ("high", "low"):
        direction_info = measure_candidates.get(direction) or {}
        checks.append({
            "measure": measure_name,
            "direction": direction,
            "candidate": bool(direction_info.get("candidate", False)),
            "reason": str(direction_info.get("reason", "reason no disponible")),
        })

    if checks and not any(check["candidate"] for check in checks):
        details = "; ".join(
            f"{check['direction']}: candidate=false ({check['reason']})"
            for check in checks
        )
        return {
            "compatible": False,
            "reason": (
                f"F03 skipped because measure_name={measure_name} has no viable "
                f"F02 target candidates for high or low. {details}"
            ),
            "checks": checks,
        }

    return {"compatible": True, "reason": None, "checks": checks}

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
    # Resolver parent F02
    # --------------------------------------------------------

    parent_phase = "f02_events"

    parent_outputs, parent_dir = load_phase_outputs(
        PROJECT_ROOT,
        parent_phase,
        parent_variant,
        "F03",
    )

    # --------------------------------------------------------
    # Parámetros
    # --------------------------------------------------------

    Tu = params["Tu"]
    OW = params["OW"]
    LT = params["LT"]
    PW = params["PW"]
    window_strategy = params["window_strategy"]
    nan_mode = params["nan_mode"]
    parent_exports = parent_outputs.get("exports", {}) or {}
    measure_name = params.get("measure_name") or parent_exports.get("measure_name")
    BATCH = 10_000

    if parent_exports.get("compatible") is False:
        elapsed = time.perf_counter() - start_time
        parent_reason = parent_exports.get("incompatibility_reason") or "compatible=false"
        reason = f"Parent F02 incompatible: {parent_reason}"
        report_path = variant_dir / "03_windows_report.html"
        report_path.write_text(
            f"""
            <html>
            <body>
            <h1>F03 Windows - {variant}</h1>
            <p>Parent F02: {parent_variant}</p>
            <p>Measure: {measure_name}</p>
            <p>Compatible: False</p>
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
                "Tu": int(Tu),
                "OW": int(OW),
                "LT": int(LT),
                "PW": int(PW),
                "Ratio_PW_OW": PW / OW if OW > 0 else None,
                "event_type_count": int(parent_exports.get("n_event_types", 0)),
                "window_strategy": window_strategy,
                "nan_mode": nan_mode,
                "measure_name": measure_name,
                "parent_f02": parent_variant,
                "n_windows": 0,
                "compatible": False,
                "incompatibility_reason": reason,
                "target_candidate_checks": [],
            },
            "metrics": {
                "execution_time": float(elapsed),
                "n_events_in": 0,
                "n_windows_out": 0,
                "compatible": False,
                "incompatibility_reason": reason,
            },
            "provenance": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        save_outputs_yaml(variant_dir, outputs_content)
        validate_outputs(PHASE, outputs_content)
        print(f"[WARN] F03 incompatible: {reason}")
        print(f"\n===== FASE {PHASE} COMPLETADA SIN DATASET =====")
        return

    target_candidate_eval = evaluate_measure_target_candidates(parent_exports, measure_name)
    if not target_candidate_eval["compatible"]:
        elapsed = time.perf_counter() - start_time
        reason = str(target_candidate_eval["reason"])
        report_path = variant_dir / "03_windows_report.html"
        report_path.write_text(
            f"""
            <html>
            <body>
            <h1>F03 Windows - {variant}</h1>
            <p>Parent F02: {parent_variant}</p>
            <p>Measure: {measure_name}</p>
            <p>Compatible: False</p>
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
                "Tu": int(Tu),
                "OW": int(OW),
                "LT": int(LT),
                "PW": int(PW),
                "Ratio_PW_OW": PW / OW if OW > 0 else None,
                "event_type_count": int(parent_exports.get("n_event_types", parent_exports.get("event_type_count", 0))),
                "window_strategy": window_strategy,
                "nan_mode": nan_mode,
                "measure_name": measure_name,
                "parent_f02": parent_variant,
                "n_windows": 0,
                "compatible": False,
                "incompatibility_reason": reason,
                "target_candidate_checks": target_candidate_eval["checks"],
            },
            "metrics": {
                "execution_time": float(elapsed),
                "n_events_in": 0,
                "n_windows_out": 0,
                "compatible": False,
                "incompatibility_reason": reason,
            },
            "provenance": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        save_outputs_yaml(variant_dir, outputs_content)
        validate_outputs(PHASE, outputs_content)
        print(f"[WARN] F03 incompatible: {reason}")
        print(f"\n===== FASE {PHASE} COMPLETADA SIN DATASET =====")
        return

    parent_dataset_path = resolve_artifact_path(
        parent_dir,
        parent_outputs,
        ["events"],
        "F03",
    )
    parent_catalog_path = resolve_artifact_path(
        parent_dir,
        parent_outputs,
        ["catalog"],
        "F03",
    )

    df = pq.read_table(parent_dataset_path, memory_map=True).to_pandas()

    # --------------------------------------------------------
    # Validaciones básicas
    # --------------------------------------------------------

    if "segs" not in df.columns:
        raise RuntimeError("El dataset padre no contiene columna 'segs'")

    if "events" not in df.columns:
        raise RuntimeError("El dataset padre no contiene columna 'events'")

    df = df.sort_values("segs", kind="mergesort").reset_index(drop=True)

    # --------------------------------------------------------
    # Snapshot catálogo
    # --------------------------------------------------------

    catalog = load_json(parent_catalog_path)

    event_type_count = int(len(catalog))

    catalog_path = variant_dir / "03_events_catalog.json"
    save_json(catalog_path, catalog)

    # --------------------------------------------------------
    # Preparar arrays
    # --------------------------------------------------------

    times = df["segs"].to_numpy(dtype=np.int64)
    events = df["events"].to_numpy()

    lengths = np.fromiter((len(e) for e in events), dtype=np.int64)
    offsets = np.empty(len(events) + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(lengths, out=offsets[1:])

    total_events = int(offsets[-1])
    events_flat = np.empty(total_events, dtype=np.int32)

    has_nan = None
    nan_prefix = None

    if nan_mode == "discard":
        nan_codes = {v for k, v in catalog.items() if k.endswith("_NaN_NaN")}
        has_nan = np.zeros(len(events), dtype=bool)

    pos = 0
    for i, evs in enumerate(events):
        l = len(evs)
        if l:
            events_flat[pos:pos + l] = evs
            if nan_mode == "discard":
                for ev in evs:
                    if ev in nan_codes:
                        has_nan[i] = True
                        break
            pos += l

    if nan_mode == "discard":
        nan_prefix = np.cumsum(has_nan, dtype=np.int64)

    # --------------------------------------------------------
    # Geometría temporal
    # --------------------------------------------------------

    OW_span = OW * Tu
    PW_start = (OW + LT) * Tu
    PW_span = PW * Tu
    total_span = PW_start + PW_span

    # --------------------------------------------------------
    # Output parquet
    # --------------------------------------------------------

    output_path = variant_dir / "03_windows.parquet"

    schema = pa.schema([
        ("OW_events", pa.list_(pa.int32())),
        ("PW_events", pa.list_(pa.int32())),
    ])

    writer = pq.ParquetWriter(output_path, schema, compression="snappy")

    rows = []
    windows_total = 0
    windows_written = 0


    ow_lengths = []
    pw_lengths = []

    ow_hashes = set()
    pw_hashes = set()

    # =================================================================
    # FAST PATH: SYNCHRO
    # =================================================================
    if window_strategy == "synchro":
        n = len(times)
        t0 = times[0]

        i_ow_0 = bisect_left(times, t0)
        i_ow_1 = bisect_left(times, t0 + OW_span)
        i_pw_0 = bisect_left(times, t0 + PW_start)
        i_pw_1 = bisect_left(times, t0 + PW_start + PW_span)

        while t0 + total_span <= times[-1]:
            windows_total += 1

            if i_ow_0 != i_ow_1 or i_pw_0 != i_pw_1:
                if nan_mode == "discard":
                    if (
                        has_nan_in_range(nan_prefix, i_ow_0, i_ow_1)
                        or has_nan_in_range(nan_prefix, i_pw_0, i_pw_1)
                    ):
                        pass
                    else:
                        ow = events_flat[offsets[i_ow_0]:offsets[i_ow_1]]
                        pw = events_flat[offsets[i_pw_0]:offsets[i_pw_1]]
                        if len(ow) or len(pw):
                            register_window(rows, ow, pw, ow_lengths, pw_lengths, ow_hashes, pw_hashes)
                            windows_written += 1
                else:
                    ow = events_flat[offsets[i_ow_0]:offsets[i_ow_1]]
                    pw = events_flat[offsets[i_pw_0]:offsets[i_pw_1]]
                    if len(ow) or len(pw):
                        register_window(rows, ow, pw, ow_lengths, pw_lengths, ow_hashes, pw_hashes)
                        windows_written += 1


            if len(rows) >= BATCH:
                flush_rows(writer, rows, schema)

            t0 += Tu
            ow_start = t0
            ow_end = t0 + OW_span
            pw_start = t0 + PW_start
            pw_end = pw_start + PW_span

            while i_ow_0 < n and times[i_ow_0] < ow_start:
                i_ow_0 += 1
            while i_ow_1 < n and times[i_ow_1] < ow_end:
                i_ow_1 += 1
            while i_pw_0 < n and times[i_pw_0] < pw_start:
                i_pw_0 += 1
            while i_pw_1 < n and times[i_pw_1] < pw_end:
                i_pw_1 += 1

    # =================================================================
    # ASYNOW
    # =================================================================
    elif window_strategy == "asynOW":
        active_bins = np.unique(((times[lengths > 0] - times[0]) // Tu).astype(np.int64))

        for b in active_bins:
            t0 = times[0] + b * Tu
            if t0 + total_span > times[-1]:
                continue

            windows_total += 1

            i_ow_0 = bisect_left(times, t0)
            i_ow_1 = bisect_left(times, t0 + OW_span)
            if i_ow_0 == i_ow_1:
                continue

            i_pw_0 = bisect_left(times, t0 + PW_start)
            i_pw_1 = bisect_left(times, t0 + PW_start + PW_span)

            if nan_mode == "discard":
                if (
                    has_nan_in_range(nan_prefix, i_ow_0, i_ow_1)
                    or has_nan_in_range(nan_prefix, i_pw_0, i_pw_1)
                ):
                    continue

            ow = events_flat[offsets[i_ow_0]:offsets[i_ow_1]]
            pw = events_flat[offsets[i_pw_0]:offsets[i_pw_1]]
            if len(ow) or len(pw):
                register_window(rows, ow, pw, ow_lengths, pw_lengths, ow_hashes, pw_hashes)
                windows_written += 1

            if len(rows) >= BATCH:
                flush_rows(writer, rows, schema)

    else:
        raise ValueError(f"Estrategia desconocida: {window_strategy}")

    flush_rows(writer, rows, schema)
    writer.close()

    elapsed = time.perf_counter() - start_time

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    report_path = variant_dir / "03_windows_report.html"
    report_path.write_text(
        f"""
        <html>
        <body>
        <h1>F03 Windows — {variant}</h1>
        <p>Parent: {parent_variant}</p>
        <p>Strategy: {window_strategy}</p>
        <p>OW={OW}, LT={LT}, PW={PW}, Tu={Tu}</p>
        <p>Windows total: {windows_total}</p>
        <p>Windows written: {windows_written}</p>
        </body>
        </html>
        """
    )

    # --------------------------------------------------------
    # outputs.yaml
    # --------------------------------------------------------

    n_unique_ow_hash = len(ow_hashes)
    n_unique_pw_hash = len(pw_hashes)

    dup_ratio_ow = (
        1.0 - (n_unique_ow_hash / windows_written)
        if windows_written > 0 else 0.0
    )
    dup_ratio_pw = (
        1.0 - (n_unique_pw_hash / windows_written)
        if windows_written > 0 else 0.0
    )

    seq_len_mean_ow = float(np.mean(ow_lengths)) if ow_lengths else 0.0
    seq_len_mean_pw = float(np.mean(pw_lengths)) if pw_lengths else 0.0
    seq_len_std_ow = float(np.std(ow_lengths)) if ow_lengths else 0.0
    seq_len_std_pw = float(np.std(pw_lengths)) if pw_lengths else 0.0

    outputs_content = {
        "phase": PHASE,
        "variant": variant,
        "artifacts": {
            "dataset": {
                "path": output_path.name,
                "sha256": sha256_of_file(output_path),
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
            "Tu": Tu,
            "OW": OW,
            "LT": LT,
            "PW": PW,
            "Ratio_PW_OW": PW / OW if OW > 0 else None,
            "event_type_count": event_type_count,
            "window_strategy": window_strategy,
            "nan_mode": nan_mode,
            "measure_name": measure_name,
            "parent_f02": parent_variant,
            "n_windows": windows_written,
            "compatible": True,
            "incompatibility_reason": None,
            "n_unique_ow_hash": n_unique_ow_hash,
            "n_unique_pw_hash": n_unique_pw_hash,
            "dup_ratio_ow": float(dup_ratio_ow),
            "dup_ratio_pw": float(dup_ratio_pw),
            "seq_len_mean_ow": seq_len_mean_ow,
            "seq_len_mean_pw": seq_len_mean_pw,
            "seq_len_std_ow": seq_len_std_ow,
            "seq_len_std_pw": seq_len_std_pw,
        },
        "metrics": {
            "execution_time": float(elapsed),
            "n_events_in": int(len(df)),
            "n_windows_out": int(windows_written),
            "compatible": True,
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
