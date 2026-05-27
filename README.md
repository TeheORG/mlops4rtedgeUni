# MLOps4RT-Edge

![Python 3.11](https://img.shields.io/badge/python-3.11-blue)
![Platforms macOS Linux Windows](https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-success)
![Repository scope code only](https://img.shields.io/badge/repository-code--only-informational)
![Artifacts DVC backend](https://img.shields.io/badge/artifacts-DVC%20backend-orange)

MLOps4RT-Edge is a reproducible, phase-based MLOps pipeline for edge machine learning workflows, from raw time-series data to quantized models and on-device validation.

It is intended for teams that need a structured and traceable path from data preparation to embedded deployment experiments, while keeping binary artifacts and execution state outside the public code repository.

This README is written for users who want to run the pipeline with their own data, generate their own execution artifacts, and store those artifacts in their own project storage backends.

If you want to work on the pipeline code itself, see [DEVELOPERS.md](DEVELOPERS.md).

## At A Glance

1. Eight-phase pipeline from exploration to edge system validation.
2. Setup-driven project configuration for Git, DVC, and MLflow backends.
3. Cross-platform workflow for macOS, Linux, and Windows.
4. Public repository kept code-only by design.
5. Intended for project workspaces that keep executions and artifacts outside this repo.

## Project Status

This project is intended to be published as a reusable public pipeline codebase.

Current status:

1. The repository is maintained as a code-only public repository.
2. Generated executions, local DVC state, MLflow state, and project artifacts are intentionally excluded from Git.
3. Users are expected to run the pipeline in their own project workspace and connect it to their own DVC and MLflow backends.

## What This Project Does

The pipeline is organized into eight phases:

1. Explore raw data.
2. Build an events dataset.
3. Build a windows dataset.
4. Engineer prediction targets.
5. Train models.
6. Quantize and package models for edge deployment.
7. Validate a model on edge hardware.
8. Validate a multi-model system on edge hardware.

Each phase creates a variant under a local executions workspace and can be validated, registered, and removed independently.

## Repository Scope

This public repository is the pipeline codebase only.

- It does contain: source code, setup templates, tests, documentation, and automation.
- It does not contain: your local DVC cache, your MLflow state, your execution outputs, or your project-specific DVC references.

When you run the pipeline for your own project:

- Your generated outputs are written to your local workspace under executions/.
- Large binary artifacts must go to the DVC backend configured by your project setup.
- Your project Git repository, if used, is the repository defined in your setup, not this public code repository.

## Prerequisites

Minimum prerequisites:

1. Python 3.11.
2. GNU Make.
3. Git.
4. A working virtual environment can be created locally by the setup flow.

Optional but commonly needed:

1. Docker for containerized phases such as F06 and embedded build flows.
2. A supported edge platform toolchain and hardware for F07 and F08.
3. DVC and MLflow backends, configured through the project setup.

## Platform Compatibility

The pipeline is designed to be cross-platform at the project level and should be usable from macOS, Linux, and Windows.

What is platform-independent:

1. The phase model.
2. The Makefile-based workflow.
3. Setup-driven DVC and MLflow configuration.
4. Variant creation, validation, traceability, and registration logic.

What may still vary by operating system in practice:

1. Serial port names, such as `/dev/ttyUSB0` on Linux-like systems and `COMx` on Windows.
2. Serial port permissions on systems that protect device access.
3. Docker path handling, especially on Windows shells.
4. Vendor-specific toolchain installation details for embedded targets.

These are operational differences, not pipeline design differences.

## Quick Start

### 1. Clone the pipeline code

```bash
git clone https://github.com/STRAST-UPM/mlops4rtedge.git
cd mlops4rtedge
```

### 2. Choose a setup mode

For a fully local project workspace:

```bash
make setup SETUP_CFG=setup/local.yaml
make check-setup
```

For a project that should publish to its own remote services, start from the remote template:

```bash
cp setup/remote.yaml .mlops4ofp.remote.yaml
# edit the file with your own Git, DVC, and MLflow endpoints
make setup SETUP_CFG=.mlops4ofp.remote.yaml
make check-setup
```

The local setup template uses:

- local DVC storage at ./.dvc_storage
- local MLflow tracking at file:./mlruns
- no Git publishing from pipeline register commands

## Recommended Usage Model

For project work, use this repository as the pipeline engine and keep your data and executions in your own working copy or project repository.

Recommended pattern:

1. Clone this repository.
2. Run setup with either local or remote configuration.
3. Place your raw dataset in data/.
4. Create and execute variants phase by phase.
5. Store binary artifacts in the DVC backend configured by your project.
6. Keep any project-specific execution history in your own project environment.

## Common Commands

Show the built-in help:

```bash
make help
```

Set up the environment:

```bash
make setup SETUP_CFG=setup/local.yaml
make check-setup
```

Reset the local project environment:

```bash
make clean-setup
```

Remove all variants from a phase:

```bash
make remove-phase-all PHASE=f05_modeling VARIANTS_DIR=executions/f05_modeling
```

## Phase-by-Phase Example

### F01: Explore raw data

```bash
make variant1 VARIANT=v001 RAW=./data/raw.csv CLEANING=basic NAN_VALUES='[-999999]'
make script1 VARIANT=v001
make check1 VARIANT=v001
make register1 VARIANT=v001
```

#### Limpieza profunda F01:
```bash
make variant1 VARIANT=v000 RAW=data/raw.csv CLEANING=basic NAN_VALUES='[-999999]' ERROR_VALUES='{"MG-LV-MSB_AC_Voltage":[0.0],"Receiving_Point_AC_Voltage":[0.0],"Island_mode_MCCB_AC_Voltage":[0.0],"Island_mode_MCCB_Frequency":[-327.679993,0.0],"MG-LV-MSB_Frequency":[-327.679993,0.0],"Outlet_Temperature":[-52.5],"Inlet_Temperature_of_Chilled_Water":[-52.5,-52.400002,-52.299999]}'
make script1 VARIANT=v000
make check1 VARIANT=v000
make register1 VARIANT=v000
```




### F02: Build events dataset

```bash
make variant2 VARIANT=v2_0001 PARENT=v1_0000 MEASURE=Battery_Active_Power STRATEGY=transitions BANDS='[10,20,30,40,50,60,70,80,90]' NAN_MODE=discard
make script2 VARIANT=v2_0001
make check2 VARIANT=v2_0001
make register2 VARIANT=v2_0001
```

F02 is univariate: each F02 variant selects exactly one measure through `MEASURE`.
The selected measure is validated against `exports.measure_cols` from the F01 parent
`outputs.yaml`. If it is invalid, variant creation stops with:

```bash
[ERROR] Invalid MEASURE. Must be one of: ...
```

F02 stores the selected measure and event definition in `outputs.yaml` under
`exports.measure_name`, `exports.event_strategy`, `exports.bands`,
`exports.event_types`, `exports.n_event_types`, and the legacy-compatible
`exports.n_types`.

### F03: Build windows dataset

```bash
make variant3 VARIANT=v3_0001 PARENT=v2_0001 OW=600 LT=100 PW=100 STRATEGY=synchro NAN_MODE=discard
make script3 VARIANT=v3_0001
make check3 VARIANT=v3_0001
make register3 VARIANT=v3_0001
```

### F04: Create prediction targets

```bash
make variant4 VARIANT=v4_0001 PARENT=v3_0001 NAME=battery_active_power_high_90 OPERATOR=OR EVENTS='["Battery_Active_Power_80_90-to-90_100"]'
make script4 VARIANT=v4_0001
make check4 VARIANT=v4_0001
make register4 VARIANT=v4_0001
```

### F05: Train models

```bash
make variant5 VARIANT=v5_0001 PARENT=v4_0001 MODEL_FAMILY=cnn1d IMBALANCE_STRATEGY=rare_events IMBALANCE_MAX_MAJ=20000 SEED=42
make script5 VARIANT=v5_0001
make check5 VARIANT=v5_0001
make register5 VARIANT=v5_0001
```

Common F05 overrides include batch size, epochs, learning rate, embedding size, hidden units, dropout, AutoML, and evaluation split.

### F06: Quantize and package for edge

```bash
make variant6 VARIANT=v6_0001 PARENT=v5_0001
make script6 VARIANT=v6_0001
make check6 VARIANT=v6_0001
make register6 VARIANT=v6_0001
```

F06 uses Docker for reproducible packaging in the default flow.

## Univariate F02 Change Summary

Modified files:

1. `Makefile`
2. `scripts/phases/f02_events.py`
3. `scripts/phases/f03_windows.py`
4. `scripts/phases/f04_targets.py`
5. `scripts/traceability_schema.yaml`
6. `makefile_check_phases.yml`
7. `README.md`

What changed:

1. `make variant2` now requires `MEASURE=<measure>`.
2. `MEASURE` is validated against `executions/f01_explore/<PARENT>/outputs.yaml`
   at `exports.measure_cols`.
3. F02 passes `measure_name=<MEASURE>` to the parameter manager.
4. F02 loads the clean F01 dataset but generates levels/transitions/both only
   for the selected measure.
5. F02 exports `measure_name`, `event_strategy`, `bands`, `event_types`,
   `n_event_types`, `Tu`, and the existing compatible count fields.
6. F03 carries `measure_name` forward in its exports when available.
7. F04 prefers the inherited univariate `measure_name` when available.
8. F05 and F06 keep their command shape and consume the univariate parent
   artifacts through the existing catalog/window/target flow.

Recommended full-flow test:

```bash
make variant2 VARIANT=v2_0001 PARENT=v1_0000 MEASURE=Battery_Active_Power STRATEGY=transitions BANDS='[10,20,30,40,50,60,70,80,90]' NAN_MODE=discard
make script2 VARIANT=v2_0001
make check2 VARIANT=v2_0001

make variant3 VARIANT=v3_0001 PARENT=v2_0001 OW=600 LT=100 PW=100 STRATEGY=synchro NAN_MODE=discard
make script3 VARIANT=v3_0001
make check3 VARIANT=v3_0001

make variant4 VARIANT=v4_0001 PARENT=v3_0001 NAME=battery_active_power_high_90 OPERATOR=OR EVENTS='["Battery_Active_Power_80_90-to-90_100"]'
make script4 VARIANT=v4_0001
make check4 VARIANT=v4_0001

make variant5 VARIANT=v5_0001 PARENT=v4_0001 MODEL_FAMILY=cnn1d IMBALANCE_STRATEGY=rare_events IMBALANCE_MAX_MAJ=20000 SEED=42
make script5 VARIANT=v5_0001
make check5 VARIANT=v5_0001

make variant6 VARIANT=v6_0001 PARENT=v5_0001
make script6 VARIANT=v6_0001
make check6 VARIANT=v6_0001
```

Known limitation:

F02 is now intentionally one-measure-per-variant. To compare multiple measures,
create one F02 variant per measure and run the downstream phases from each
univariate F02 parent.

### F07: Validate a model on edge hardware

```bash
# make variant7 VARIANT=v701 PARENT=v601 PLATFORM=esp32 MTI_MS=100000
make variant7 VARIANT=v701 PARENT=v601 PLATFORM=esp32 MTI_MS=100 TIME_SCALE=0.01
make script7 VARIANT=v701
make check7 VARIANT=v701
make register7 VARIANT=v701
```

You can also run F07 step by step:

```bash
make script7-prepare-build VARIANT=v701
make script7-flash-run VARIANT=v701
make script7-post VARIANT=v701
```

### F08: Validate a multi-model edge system

```bash
make variant8 VARIANT=v801 PARENTS=v702,v703 PLATFORM=esp32 MTI_MS=100
make script8 VARIANT=v801
make check8 VARIANT=v801
make register8 VARIANT=v801
```

F08 also supports manual and ILP-based selection modes.

## Where Outputs Go

During execution, the pipeline writes generated files to your local workspace under executions/.

Examples of generated content:

1. params.yaml and outputs.yaml for each variant.
2. Reports, catalogs, metrics, and calibration datasets.
3. Model binaries and quantized artifacts.
4. Edge build outputs and runtime logs for hardware phases.

These files are local project outputs and are intentionally not versioned in this public repository.

## DVC, MLflow, and Git Responsibilities

The intended split is:

1. Git in this public repository stores the reusable pipeline code only.
2. DVC stores large binary artifacts generated by your project.
3. MLflow stores experiment tracking data for your project.
4. If your project uses its own Git repository, that repository is configured through your setup file.

## Troubleshooting

### Setup validation fails

Run:

```bash
make check-setup
```

Then inspect your setup file, Python version, DVC backend, and MLflow endpoint.

### A phase fails after a parent changes

The pipeline tracks parent-child relationships across variants. Re-run the affected phase after fixing the parent or create a new variant that references the updated parent.

### Edge execution fails on serial or flash steps

Check:

1. That the target board is connected.
2. That the serial port is correct.
3. That your user has permission to access the port.
4. That Docker and board toolchains are installed if the selected phase requires them.

## Additional References

1. [DEVELOPERS.md](DEVELOPERS.md) for contributors and maintainers.
2. [setup/local.yaml](setup/local.yaml) and [setup/remote.yaml](setup/remote.yaml) as setup templates.
