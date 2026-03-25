import json
import subprocess
import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any
import yaml
from scripts.core.schema_utils import load_traceability_schema as _load_traceability_schema


# ============================================================
# RUTAS DEL PROYECTO
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXECUTIONS_DIR = PROJECT_ROOT / "executions"
PIPELINE_REF_PATH = PROJECT_ROOT / ".mlops4ofp" / "pipeline_ref.yaml"


# ============================================================
# UTILIDADES GIT
# ============================================================

def current_git_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def git_info() -> Dict[str, Any]:
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            text=True
        ).strip()
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True
        ).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            text=True
        ).strip()

        return {
            "commit": commit,
            "branch": branch,
            "status_clean": (status == "")
        }
    except Exception:
        return {
            "commit": "unknown",
            "branch": "unknown",
            "status_clean": False
        }


# ============================================================
# PIPELINE REF
# ============================================================

def load_pipeline_ref() -> Dict[str, Any]:
    if not PIPELINE_REF_PATH.exists():
        return {
            "pipeline_repo": "unknown",
            "pipeline_commit": "unknown"
        }
    try:
        return yaml.safe_load(PIPELINE_REF_PATH.read_text())
    except Exception:
        return {
            "pipeline_repo": "unknown",
            "pipeline_commit": "unknown"
        }


def load_traceability_schema():
    try:
        return _load_traceability_schema()
    except FileNotFoundError:
        raise RuntimeError("No existe traceability_schema.yaml")


# ============================================================
# VALIDACIÓN OUTPUTS
# ============================================================

def validate_outputs(phase: str, outputs: dict):

    schema = load_traceability_schema()

    if "phases" not in schema or phase not in schema["phases"]:
        raise RuntimeError(f"No hay definición de fase {phase} en schema")

    phase_schema = schema["phases"][phase]
    outputs_schema = phase_schema.get("outputs", {})

    # --------------------------------------------------------
    # ARTIFACTS
    # --------------------------------------------------------

    artifacts_schema = outputs_schema.get("artifacts", {})
    artifacts = outputs.get("artifacts", {})

    for key, rule in artifacts_schema.items():

        if rule.get("required", False) and key not in artifacts:
            raise RuntimeError(f"Falta artifact requerido: {key}")

        if key in artifacts:
            path = artifacts[key].get("path")
            if not path:
                raise RuntimeError(f"Artifact {key} sin path")

            ext = rule.get("extension")
            if ext and not path.endswith(f".{ext}"):
                raise RuntimeError(
                    f"Artifact {key} debe tener extensión .{ext}"
                )

    # --------------------------------------------------------
    # EXPORTS
    # --------------------------------------------------------

    exports_schema = outputs_schema.get("exports", {})
    exports = outputs.get("exports", {})

    for key, rule in exports_schema.items():

        if rule.get("required", False) and key not in exports:
            raise RuntimeError(f"Falta export requerido: {key}")

        if key in exports:
            value = exports[key]
            _validate_basic_type(value, rule, f"export {key}")

    # --------------------------------------------------------
    # METRICS
    # --------------------------------------------------------

    metrics_schema = outputs_schema.get("metrics", {})
    metrics = outputs.get("metrics", {})

    for key, rule in metrics_schema.items():

        if rule.get("required", False) and key not in metrics:
            raise RuntimeError(f"Falta metric requerido: {key}")

        if key in metrics:
            value = metrics[key]
            _validate_basic_type(value, rule, f"metric {key}")

    print("[OK] Outputs validados contra schema")


# ============================================================
# VALIDACIÓN TIPOS BÁSICOS
# ============================================================

def _validate_basic_type(value, rule, context):

    if value is None:
        if rule.get("nullable", False):
            return
        raise RuntimeError(f"{context} no puede ser null")

    expected = rule.get("type")

    if expected == "integer" and not isinstance(value, int):
        raise RuntimeError(f"{context} debe ser integer")

    if expected == "float" and not isinstance(value, float):
        raise RuntimeError(f"{context} debe ser float")

    if expected == "string" and not isinstance(value, str):
        raise RuntimeError(f"{context} debe ser string")

# ============================================================
# CARGA DE VARIANTES
# ============================================================

def load_variants_for_phase(phase: str) -> Dict[str, Any]:
    reg = EXECUTIONS_DIR / phase / "variants.yaml"
    if not reg.exists():
        return {"variants": {}}
    with reg.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"variants": {}}


def load_all_variants() -> Dict[str, Dict[str, Any]]:
    result = {}

    if not EXECUTIONS_DIR.exists():
        return result

    for ph in EXECUTIONS_DIR.iterdir():
        if not ph.is_dir():
            continue
        reg = ph / "variants.yaml"
        if reg.exists():
            data = yaml.safe_load(reg.read_text()) or {"variants": {}}
            result[ph.name] = data.get("variants", {})

    return result


# ============================================================
# VALIDACIÓN DE EXISTENCIA
# ============================================================

def validate_variant_exists(phase: str, variant: str):
    reg = load_variants_for_phase(phase)
    if variant not in reg.get("variants", {}):
        raise ValueError(
            f"La variante {variant} no existe en la fase {phase}.\n"
            f"Archivo: executions/{phase}/variants.yaml"
        )
    return True


# ============================================================
# RELACIONES PADRE-HIJO
# ============================================================

def find_children(phase: str, variant: str) -> List[str]:
    allv = load_all_variants()
    children = []

    for ph, variants in allv.items():
        for vname, meta in variants.items():
            if (
                meta.get("parent_phase") == phase
                and meta.get("parent_variant") == variant
            ):
                children.append(f"{ph}:{vname}")

    return children


def can_delete_variant(phase: str, variant: str):
    validate_variant_exists(phase, variant)
    children = find_children(phase, variant)

    if children:
        msg = (
            f"[FAIL] La variante {phase}:{variant} tiene variantes hijas y NO puede borrarse.\n"
            f"Hijos:\n" + "\n".join(f"  - {c}" for c in children)
        )
        raise RuntimeError(msg)

    print(f"[OK] La variante {phase}:{variant} no tiene hijos y puede borrarse.")


# ============================================================
# ESCRITURA DE METADATA (AUTOCONTENIDO)
# ============================================================

def write_metadata(
    stage: str,
    variant: str,
    parent_variant: str | None,
    inputs: List[str],
    outputs: List[str],
    params: Dict[str, Any],
    metadata_path: str,
) -> None:

    metadata_path = Path(metadata_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    pipeline_ref = load_pipeline_ref()

    data = {
        "stage": stage,
        "variant": variant,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "inputs": inputs,
        "outputs": outputs,
        "params": params,
        "git": git_info(),
        "pipeline": pipeline_ref   # ← NUEVO BLOQUE AUTOCONTENIDO
    }

    if parent_variant is not None:
        data["parent_variant"] = parent_variant

    metadata_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ============================================================
# VALIDACIÓN DE METADATA
# ============================================================

def load_schema() -> Dict[str, Any]:
    return load_traceability_schema()


def validate_metadata(metadata: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    errors = []
    fields = schema.get("fields", {})

    for field_name, rules in fields.items():
        if rules.get("required") and field_name not in metadata:
            errors.append(f"Falta el campo obligatorio '{field_name}'.")

    for field_name, rules in fields.items():
        if field_name not in metadata:
            continue

        expected = rules.get("type")
        val = metadata[field_name]

        if expected == "string" and not isinstance(val, str):
            errors.append(f"'{field_name}' debe ser string.")
        if expected == "list" and not isinstance(val, list):
            errors.append(f"'{field_name}' debe ser lista.")
        if expected == "dict" and not isinstance(val, dict):
            errors.append(f"'{field_name}' debe ser dict.")

    if "parent_variant" in metadata and "parent_variants" in metadata:
        errors.append(
            "No se permite definir simultáneamente "
            "'parent_variant' y 'parent_variants'."
        )
    return errors


def validate_metadata_file(metadata_path: str) -> List[str]:
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    schema = load_schema()
    return validate_metadata(metadata, schema)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Herramientas de trazabilidad — MLOps4OFP-FGCS"
    )
    subparsers = parser.add_subparsers(dest="command")

    p1 = subparsers.add_parser("can-delete")
    p1.add_argument("--phase", required=True)
    p1.add_argument("--variant", required=True)

    p2 = subparsers.add_parser("validate-variant")
    p2.add_argument("--phase", required=True)
    p2.add_argument("--variant", required=True)

    args = parser.parse_args()

    try:
        if args.command == "can-delete":
            can_delete_variant(args.phase, args.variant)
        elif args.command == "validate-variant":
            validate_variant_exists(args.phase, args.variant)
            print(f"[OK] La variante {args.phase}:{args.variant} existe.")
        else:
            parser.print_help()
            sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()