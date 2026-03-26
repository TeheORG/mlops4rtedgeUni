SHELL := /bin/bash

ifeq ($(OS),Windows_NT)
PYTHON_LOCAL ?= python
else
PYTHON_LOCAL ?= python3.11
endif


ifeq ($(shell command -v $(PYTHON_LOCAL) 2>/dev/null),)
$(error python3.11 not found. Please install it before running make setup)
endif

$(info [INFO] Using local Python interpreter: $(PYTHON_LOCAL))

ifneq ("$(wildcard .mlops4ofp/env.sh)","")
include .mlops4ofp/env.sh
export
endif

############################################
# SETUP — MLOps4OFP (one-time configuration)
############################################

SETUP_PY = setup/setup.py
SETUP_ENV = .mlops4ofp/env.sh
SETUP_CFG ?=

help-setup:
	@echo "=============================================="
	@echo " MLOps4OFP — PROJECT SETUP"
	@echo "=============================================="
	@echo ""
	@echo "This process is executed ONLY ONCE per project copy."
	@echo ""
	@echo "Available flows:"
	@echo ""
	@echo "  make setup SETUP_CFG=setup/local.yaml"
	@echo "      Non-interactive setup from YAML file"
	@echo ""
	@echo "  make setup SETUP_CFG=setup/remote.yaml"
	@echo "      Non-interactive setup from YAML file"
	@echo ""
	@echo "  make check-setup"
	@echo "      Verify that the setup is valid and the environment is correctly configured"
	@echo ""
	@echo "  make clean-setup"
	@echo "      Remove the setup configuration and all generated artifacts, allowing to start from scratch"
	@echo ""
	@echo "=============================================="

setup:
	@echo "==> Running project setup with configuration: $(SETUP_CFG)"
ifndef SETUP_CFG
	$(error You must specify SETUP_CFG=<file.yaml> (e.g., setup/local.yaml or setup/remote.yaml))
endif
	@$(PYTHON_LOCAL) -m pip install pyyaml==6.0.1
	@$(PYTHON_LOCAL) $(SETUP_PY) --config $(SETUP_CFG)
	@mkdir -p .mlops4ofp
	@cp $(SETUP_CFG) .mlops4ofp/setup.yaml


check-setup:
	@echo "==> Verifying base environment configuration"
	@$(PYTHON) setup/check_env.py
	@echo "==> Verifying project setup configuration"
	@$(PYTHON) setup/check_setup.py

clean-setup:
	@echo "==> Removing MLflow associated with the project (if exists)"
	@$(PYTHON_LOCAL) -c 'import yaml,pathlib,subprocess,os,shutil,json,sys;cfg_path=pathlib.Path(".mlops4ofp/setup.yaml");sys.exit(0) if not cfg_path.exists() else None;cfg=yaml.safe_load(cfg_path.read_text());ml=cfg.get("mlflow",{});sys.exit(0) if not ml.get("enabled",False) else None;uri=ml.get("tracking_uri","");(print(f"[INFO] Removing local MLflow at {path}") or shutil.rmtree(path)) if uri.startswith("file:") and os.path.exists(path:=uri.replace("file:","")) else (print("[INFO] Remote MLflow detected: removing project experiments (prefix F05_)") or [print(f"[INFO] Removing remote experiment {exp.get(\"name\",\"\")}") or subprocess.run(["mlflow","experiments","delete","--experiment-id",exp.get("experiment_id")],check=False) for exp in (experiments:=json.loads(subprocess.check_output(["mlflow","experiments","list","--format","json"]))) if exp.get("name","").startswith("F05_") and exp.get("experiment_id")] if True else None) if uri else None' 2>/dev/null || true
	@echo "==> Removing complete ML project environment"
	@rm -rf .mlops4ofp .dvc .dvc_storage local_dvc_store .venv executions
	@echo "[OK] ML project reinitialized. Run 'make setup' to rebuild base structure."


ifeq ($(OS),Windows_NT)
PYTHON := .venv/Scripts/python.exe
DVC := .venv/Scripts/dvc.exe
JUPYTER := .venv/Scripts/jupyter.exe
else
ifneq ("$(wildcard .venv/bin/python3)","")
PYTHON := .venv/bin/python3
DVC := .venv/bin/dvc
JUPYTER := .venv/bin/jupyter
else
PYTHON := python3
DVC := dvc
JUPYTER := jupyter
endif
endif

$(info [INFO] Using Python interpreter in venv: $(PYTHON))

############################################
# Generic targets by phase (reusable patterns for different phases)
############################################

check-variant-format:
	@test -n "$(VARIANT)" || (echo "[ERROR] You must specify VARIANT=vNNN"; exit 1)
	@if ! echo $(VARIANT) | grep -Eq '^v[0-9]{3}$$'; then \
	    echo "[ERROR] Incorrect format for VARIANT: $(VARIANT)"; \
	    echo "        It must be vNNN (e.g., v001, v023, v120)"; \
	    exit 1; \
	fi

variant-generic: check-variant-format
	@echo "==> Creating variant $(PHASE):$(VARIANT)"
	@$(PYTHON) -m scripts.core.params_manager create \
		--phase $(PHASE) \
		--variant $(VARIANT) \
		--set-args "$(strip $(EXTRA_FLAGS))"
	@echo "==> Variant created: $(PHASE):$(VARIANT)"
	@echo "==> Regenerando dashboard de linaje"
	@$(MAKE) generate_lineage || true

script-run-generic: check-variant-format
	@echo "==> Running script PHASE $(PHASE) for variant $(VARIANT)"
	@if [ -n "$(SCRIPT_MODULE)" ]; then \
		$(PYTHON) -m $(SCRIPT_MODULE) --variant $(VARIANT); \
	else \
		$(PYTHON) $(SCRIPT) --variant $(VARIANT); \
	fi

publish-generic: check-variant-format
	@echo "==> Validating variant $(PHASE):$(VARIANT)"
	$(PYTHON) -m scripts.core.traceability validate-variant --phase $(PHASE) --variant $(VARIANT)

	@echo "==> Registering DVC artifacts for $(PHASE):$(VARIANT)"
	@for ext in $(PUBLISH_EXTS); do \
	  $(DVC) add $(VARIANTS_DIR)/$(VARIANT)/*.$$ext 2>/dev/null || true; \
	done

	@echo "==> Adding to Git only the published variant files and DVC metadata"
	@git add $(VARIANTS_DIR)/$(VARIANT) 2>/dev/null || true
	@git add $(VARIANTS_DIR)/$(VARIANT)/*.dvc 2>/dev/null || true
	@git add $(VARIANTS_DIR)/variants.yaml 2>/dev/null || true
	@git add dvc.yaml dvc.lock 2>/dev/null || true

	@if [ -d ".git" ]; then \
		git commit -m "publish variant: $(PHASE) $(VARIANT)" || true; \
	else \
		echo "[INFO] No Git repo detected: skipping commit"; \
	fi
	# Pre-checks: DVC remote 'storage' must exist
	@if ! $(DVC) remote list 2>/dev/null | grep -q "^storage"; then \
		echo "[ERROR] Remote DVC 'storage' not configured. Run 'make setup' or contact the admin"; exit 1; \
	fi

	# Determine publish mode from setup.yaml and push to Git accordingly
	@MODE=$$($(PYTHON) -c "import yaml, pathlib; cfg = yaml.safe_load(pathlib.Path('.mlops4ofp/setup.yaml').read_text()); print(cfg.get('git', {}).get('mode', 'none'))"); \
	if [ "$$MODE" = "custom" ]; then \
		echo "[INFO] Remote 'publish' detected: pushing to publish"; \
		git push publish HEAD:main || echo "[WARN] git push publish failed"; \
	elif [ "$$MODE" = "none" ]; then \
		echo "[INFO] Setup in git.mode=none: local commit only"; \
	else \
		echo "[ERROR] Remote 'publish' not configured and setup not in 'none' mode."; exit 1; \
	fi

	@echo "==> Push DVC"
	@$(DVC) push -r storage || (echo "[ERROR] dvc push failed"; exit 1)

	@echo "[OK] Publication completed: variant $(PHASE):$(VARIANT)"

remove-generic: check-variant-format
	@echo "==> Checking if variant $(PHASE):$(VARIANT) has children…"
	$(PYTHON) -m scripts.core.traceability can-delete --phase $(PHASE) --variant $(VARIANT)

	@echo "==> Removing associated DVC artifacts (if any)"
	@for f in $(VARIANTS_DIR)/$(VARIANT)/*.dvc; do \
		if [ -f "$$f" ]; then \
			$(DVC) remove "$$f" || true; \
		fi; \
	done

	@echo "==> Removing complete variant folder"
	@rm -rf $(VARIANTS_DIR)/$(VARIANT)

	@echo "==> Updating variant registry"
	$(PYTHON) -m scripts.core.params_manager delete --phase $(PHASE) --variant $(VARIANT)

	@echo "==> Adding to Git only relevant changes for variant deletion"
	@git add $(VARIANTS_DIR) 2>/dev/null || true
	@git add dvc.yaml dvc.lock 2>/dev/null || true

	@git commit -m "remove variant: $(PHASE) $(VARIANT)" || true

	@MODE=$$($(PYTHON) -c "import yaml, pathlib; cfg = yaml.safe_load(pathlib.Path('.mlops4ofp/setup.yaml').read_text()); print(cfg.get('git', {}).get('mode', 'none'))"); \
	if [ "$$MODE" = "custom" ]; then \
		git push publish HEAD:main || echo "[WARN] git push publish failed"; \
	elif [ "$$MODE" = "none" ]; then \
		echo "[INFO] Setup in git.mode=none: local commit only"; \
	else \
		echo "[ERROR] Invalid or unconfigured setup."; exit 1; \
	fi

	@echo "==> Push DVC to propagate deletion"
	@$(DVC) push -r storage || echo "[WARN] dvc push failed"

	@echo "[OK] Variant $(PHASE):$(VARIANT) completely removed."

	@echo "==> Regenerando dashboard de linaje"
	@$(MAKE) generate_lineage || true

check-results-generic: check-variant-format
	@test -n "$(PHASE)" || (echo "[ERROR] PHASE not defined"; exit 1)
	@test -n "$(VARIANTS_DIR)" || (echo "[ERROR] VARIANTS_DIR not defined"; exit 1)
	@test -n "$(VARIANT)" || (echo "[ERROR] VARIANT not defined"; exit 1)
	@test -n "$(CHECK_FILES)" || (echo "[ERROR] CHECK_FILES not defined"; exit 1)

	@echo "===== CHECKING $(PHASE) results ($(VARIANT)) ====="

	@MISSING=0; \
	for f in $(CHECK_FILES); do \
		FILE="$(VARIANTS_DIR)/$(VARIANT)/$$f"; \
		if [ -f "$$FILE" ]; then \
			echo "[OK] $$f"; \
		else \
			echo "[FAIL] Missing $$f"; \
			MISSING=1; \
		fi; \
	done; \
	echo "================================"; \
	if [ "$$MISSING" -eq 1 ]; then \
		echo "[ERROR] Some files missing"; \
		exit 1; \
	fi


remove-phase-all:
	@echo "==> Removing ALL variants of phase $(PHASE) (SAFE mode: only if no children dependencies)"
	@test -d "$(VARIANTS_DIR)" || \
	  (echo "[INFO] $(VARIANTS_DIR) does not exist. Nothing to delete."; exit 0)

	@for v in $$(ls $(VARIANTS_DIR) | grep '^v[0-9]\{3\}$$'); do \
	  echo "----> Removing $(PHASE):$$v"; \
	  $(MAKE) remove-generic PHASE=$(PHASE) VARIANTS_DIR=$(VARIANTS_DIR) VARIANT=$$v || exit 1; \
	done

	@echo "[OK] Phase $(PHASE) completely removed (SAFE mode: only if no children dependencies)"



PHASE1 = f01_explore
SCRIPT1_MODULE = scripts.phases.f01_explore
VARIANTS_DIR1 = executions/$(PHASE1)

############################################
# Usage:
#   make variant1 VARIANT=vNNN RAW=/path/to/dataset \
#        CLEANING=basic \
#        [NAN_VALUES="[-999999, null]"] \
#        [ERROR_VALUES='{"col1":[-1],"col2":[999]}'] \
#        [FIRST_LINE=0] [MAX_LINES=1000]
############################################

variant1: check-variant-format
	@test -n "$(RAW)" || (echo "[ERROR] You must specify RAW=/path/to/dataset"; exit 1)
	@test -n "$(CLEANING)" || (echo "[ERROR] You must specify CLEANING=none|basic|strict"; exit 1)

	@$(MAKE) variant-generic \
		PHASE=$(PHASE1) \
		VARIANT=$(VARIANT) \
		EXTRA_FLAGS="\
			raw_path=$(RAW) \
			cleaning=$(CLEANING) \
			$(if $(strip $(NAN_VALUES)),nan_values=$(NAN_VALUES)) \
			$(if $(strip $(ERROR_VALUES)),error_values=$(ERROR_VALUES)) \
			$(if $(strip $(FIRST_LINE)),first_line=$(FIRST_LINE)) \
			$(if $(strip $(MAX_LINES)),max_lines=$(MAX_LINES))"

script1:
	$(MAKE) script-run-generic PHASE=$(PHASE1) SCRIPT_MODULE=$(SCRIPT1_MODULE) VARIANT=$(VARIANT)

check1: check-variant-format
	$(MAKE) check-results-generic PHASE=$(PHASE1) VARIANTS_DIR=$(VARIANTS_DIR1) \
		VARIANT=$(VARIANT) CHECK_FILES="01_explore_dataset.parquet \
		01_explore_report.html \
		outputs.yaml"

publish1: check-variant-format
	@test -n "$(VARIANT)" || (echo "[ERROR] Usage: make publish1 VARIANT=v00X"; exit 1)

	$(MAKE) publish-generic PHASE=$(PHASE1) VARIANTS_DIR=$(VARIANTS_DIR1) \
		PUBLISH_EXTS="parquet json html" VARIANT=$(VARIANT)

remove1: check-variant-format
	$(MAKE) remove-generic PHASE=$(PHASE1) VARIANTS_DIR=$(VARIANTS_DIR1) VARIANT=$(VARIANT)

remove1-all:
	$(MAKE) remove-phase-all PHASE=$(PHASE1) VARIANTS_DIR=$(VARIANTS_DIR1)



help1:
	@echo "==============================================="
	@echo " 01 — EXPLORE"
	@echo "==============================================="
	@echo ""
	@echo " Create:"
	@echo "   make variant1 VARIANT=v001 RAW=./data/raw.csv \\"
	@echo "       CLEANING=basic [NAN_VALUES='[-999999]'] [ERROR_VALUES='{}'] \\" 
	@echo "       [MAX_LINES=10000] [FIRST_LINE=1]"
	@echo ""
	@echo " Execution:"
	@echo "   make script1 VARIANT=v001"
	@echo ""
	@echo " Checking:"
	@echo "   make check1 VARIANT=v001"
	@echo ""
	@echo " Publish:"
	@echo "   make publish1 VARIANT=v001"
	@echo ""
	@echo " Remove: (only if no children variants)"
	@echo "   make remove1 VARIANT=v001"
	@echo ""
	@echo "==============================================="


PHASE2       = f02_events
SCRIPT2_MODULE = scripts.phases.f02_events
VARIANTS_DIR2 = executions/$(PHASE2)

############################################
# Usage:
#   make variant2 VARIANT=v201 PARENT=v001 \
#       STRATEGY=levels \
#       BANDS="[0.05, 0.1, 0.2]" \
#       NAN_MODE=discard
############################################

variant2: check-variant-format
	@test -n "$(PARENT)"   || (echo "[ERROR] You must specify PARENT=vNNN (parent F01 variant)"; exit 1)
	@test -n "$(STRATEGY)" || (echo "[ERROR] You must specify STRATEGY=levels|transitions|both"; exit 1)
	@test -n "$(BANDS)"    || (echo "[ERROR] You must specify BANDS=[...percentages...]"; exit 1)
	@test -n "$(NAN_MODE)" || (echo "[ERROR] You must specify NAN_MODE=keep|discard"; exit 1)

	@$(eval EXTRA_FLAGS := )
	@$(eval EXTRA_FLAGS += PARENT=$(PARENT))
	@$(eval EXTRA_FLAGS += strategy=$(STRATEGY))
	@$(eval EXTRA_FLAGS += bands=$(BANDS))
	@$(eval EXTRA_FLAGS += nan_mode=$(NAN_MODE))
	@$(if $(strip $(TU)),$(eval EXTRA_FLAGS += Tu=$(TU)))

	@$(MAKE) variant-generic \
		PHASE=$(PHASE2) \
		VARIANT=$(VARIANT) \
		EXTRA_FLAGS="$(EXTRA_FLAGS)"

script2:
	$(MAKE) script-run-generic PHASE=$(PHASE2) SCRIPT_MODULE=$(SCRIPT2_MODULE) VARIANT=$(VARIANT)

check2: check-variant-format
	$(MAKE) check-results-generic \
		PHASE=$(PHASE2) \
		VARIANTS_DIR=$(VARIANTS_DIR2) \
		VARIANT=$(VARIANT) \
		CHECK_FILES="02_events.parquet \
		02_events_catalog.json \
		02_events_report.html \
		outputs.yaml"
		
publish2: check-variant-format
	@test -n "$(VARIANT)" || (echo "[ERROR] Usage: make publish2 VARIANT=v2XX"; exit 1)

	$(MAKE) publish-generic PHASE=$(PHASE2) VARIANTS_DIR=$(VARIANTS_DIR2) \
		PUBLISH_EXTS="parquet json html yaml" VARIANT=$(VARIANT)

remove2: check-variant-format
	$(MAKE) remove-generic PHASE=$(PHASE2) VARIANTS_DIR=$(VARIANTS_DIR2) VARIANT=$(VARIANT)

remove2-all:
	$(MAKE) remove-phase-all PHASE=$(PHASE2) VARIANTS_DIR=$(VARIANTS_DIR2)



help2:
	@echo "==============================================="
	@echo " 02 — BUILD EVENTS DATASET"
	@echo "==============================================="
	@echo ""
	@echo " Create:"
	@echo "   make variant2 VARIANT=v201 PARENT=v001 \\"
	@echo "       STRATEGY=levels BANDS='[0.1, 0.2, 0.3]' NAN_MODE=discard"
	@echo ""
	@echo " Execution:"
	@echo "   make script2 VARIANT=v201"
	@echo ""
	@echo " Checking:"
	@echo "   make check2 VARIANT=v201"
	@echo ""
	@echo " Publish:"
	@echo "   make publish2 VARIANT=v201"
	@echo ""
	@echo " Remove: (only if no children variants)"
	@echo "   make remove2 VARIANT=v201"
	@echo ""
	@echo "==============================================="



PHASE3 = f03_windows
SCRIPT3_MODULE = scripts.phases.f03_windows
VARIANTS_DIR3 = executions/$(PHASE3)
############################################
# Usage:
#   make variant3 VARIANT=v301 PARENT=v201 \
#        OW=600 LT=100 PW=100 \
#        STRATEGY=synchro \
#        NAN_MODE=discard \
#        [TU=10]
############################################

variant3: check-variant-format
	@test -n "$(PARENT)"   || (echo "[ERROR] You must specify PARENT=v2XX (parent F02 variant)"; exit 1)
	@test -n "$(OW)"       || (echo "[ERROR] You must specify OW=<int>"; exit 1)
	@test -n "$(LT)"       || (echo "[ERROR] You must specify LT=<int>"; exit 1)
	@test -n "$(PW)"       || (echo "[ERROR] You must specify PW=<int>"; exit 1)
	@test -n "$(STRATEGY)" || (echo "[ERROR] You must specify STRATEGY=synchro|asynOW|withinPW|asynPW"; exit 1)
	@test -n "$(NAN_MODE)" || (echo "[ERROR] You must specify NAN_MODE=keep|discard"; exit 1)

	@$(eval EXTRA_FLAGS := )
	@$(eval EXTRA_FLAGS += PARENT=$(PARENT))
	@$(eval EXTRA_FLAGS += OW=$(OW))
	@$(eval EXTRA_FLAGS += LT=$(LT))
	@$(eval EXTRA_FLAGS += PW=$(PW))
	@$(eval EXTRA_FLAGS += window_strategy=$(STRATEGY))
	@$(eval EXTRA_FLAGS += nan_mode=$(NAN_MODE))
	@$(if $(strip $(TU)),$(eval EXTRA_FLAGS += Tu=$(TU)))

	@$(MAKE) variant-generic \
		PHASE=$(PHASE3) \
		VARIANT=$(VARIANT) \
		EXTRA_FLAGS="$(EXTRA_FLAGS)"


script3:
	$(MAKE) script-run-generic PHASE=$(PHASE3) SCRIPT_MODULE=$(SCRIPT3_MODULE) VARIANT=$(VARIANT)

check3: check-variant-format
	$(MAKE) check-results-generic \
		PHASE=$(PHASE3) \
		VARIANTS_DIR=$(VARIANTS_DIR3) \
		VARIANT=$(VARIANT) \
		CHECK_FILES="03_windows.parquet \
		03_events_catalog.json \
		03_windows_report.html \
		outputs.yaml"

publish3: check-variant-format
	@test -n "$(VARIANT)" || (echo "[ERROR] Usage: make publish3 VARIANT=v3XX"; exit 1)

	$(MAKE) publish-generic \
		PHASE=$(PHASE3) \
		VARIANTS_DIR=$(VARIANTS_DIR3) \
		PUBLISH_EXTS="parquet json html yaml" \
		VARIANT=$(VARIANT)

remove3: check-variant-format
	$(MAKE) remove-generic PHASE=$(PHASE3) VARIANTS_DIR=$(VARIANTS_DIR3) VARIANT=$(VARIANT)

remove3-all:
	$(MAKE) remove-phase-all PHASE=$(PHASE3) VARIANTS_DIR=$(VARIANTS_DIR3)


help3:
	@echo "==============================================="
	@echo " 03 — BUILD WINDOWS DATASET"
	@echo "==============================================="
	@echo ""
	@echo " Create:"
	@echo "   make variant3 VARIANT=v301 PARENT=v201 \\"
	@echo "       OW=600 LT=100 PW=100 \\"
	@echo "       STRATEGY=synchro \\"
	@echo "       NAN_MODE=discard"
	@echo ""
	@echo " Execution:"
	@echo "   make script3 VARIANT=v301"
	@echo ""
	@echo " Checking:"
	@echo "   make check3 VARIANT=v301"
	@echo ""
	@echo " Publish:"
	@echo "   make publish3 VARIANT=v301"
	@echo ""
	@echo " Remove: (only if no children variants)"
	@echo "   make remove3 VARIANT=v301"
	@echo ""
	@echo "==============================================="


PHASE4 = f04_targets
SCRIPT4_MODULE = scripts.phases.f04_targets
VARIANTS_DIR4 = executions/$(PHASE4)

############################################
# Usage:
#   make variant4 VARIANT=v401 PARENT=v301 \
#        NAME=battery_overheat \
#        OPERATOR=OR \
#        EVENTS='["Battery_Active_Power_80_100-to-100_120"]'
############################################

variant4: check-variant-format
	@test -n "$(PARENT)"   || (echo "[ERROR] You must specify PARENT=v3XX (parent F03 variant)"; exit 1)
	@test -n "$(NAME)"     || (echo "[ERROR] You must specify NAME=<prediction_name>"; exit 1)
	@test -n "$(OPERATOR)" || (echo "[ERROR] You must specify OPERATOR=OR"; exit 1)
	@test -n "$(EVENTS)"   || (echo "[ERROR] You must specify EVENTS=[\"event_type\", ...]"; exit 1)

	@$(eval EXTRA_FLAGS := )
	@$(eval EXTRA_FLAGS += PARENT=$(PARENT))
	@$(eval EXTRA_FLAGS += prediction_name=$(NAME))
	@$(eval EXTRA_FLAGS += target_operator=$(OPERATOR))
	@$(eval EXTRA_FLAGS += target_event_types=$(EVENTS))

	@$(MAKE) variant-generic \
		PHASE=$(PHASE4) \
		VARIANT=$(VARIANT) \
		EXTRA_FLAGS="$(EXTRA_FLAGS)"


script4:
	$(MAKE) script-run-generic PHASE=$(PHASE4) SCRIPT_MODULE=$(SCRIPT4_MODULE) VARIANT=$(VARIANT)


check4: check-variant-format
	$(MAKE) check-results-generic \
		PHASE=$(PHASE4) \
		VARIANTS_DIR=$(VARIANTS_DIR4) \
		VARIANT=$(VARIANT) \
		CHECK_FILES="04_targets.parquet \
		04_targets_report.html \
		outputs.yaml"


publish4: check-variant-format
	@test -n "$(VARIANT)" || (echo "[ERROR] Usage: make publish4 VARIANT=v4XX"; exit 1)

	$(MAKE) publish-generic \
		PHASE=$(PHASE4) \
		VARIANTS_DIR=$(VARIANTS_DIR4) \
		PUBLISH_EXTS="parquet html yaml" \
		VARIANT=$(VARIANT)


remove4: check-variant-format
	$(MAKE) remove-generic PHASE=$(PHASE4) VARIANTS_DIR=$(VARIANTS_DIR4) VARIANT=$(VARIANT)


remove4-all:
	$(MAKE) remove-phase-all PHASE=$(PHASE4) VARIANTS_DIR=$(VARIANTS_DIR4)


help4:
	@echo "==============================================="
	@echo " 04 — TARGET ENGINEERING"
	@echo "==============================================="
	@echo ""
	@echo " Create:"
	@echo "   make variant4 VARIANT=v401 PARENT=v301 \\"
	@echo "       NAME=battery_overheat \\"
	@echo "       OPERATOR=OR \\"
	@echo "       EVENTS='[\"Battery_Active_Power_80_100-to-100_120\"]'"
	@echo ""
	@echo " Execution:"
	@echo "   make script4 VARIANT=v401"
	@echo ""
	@echo " Checking:"
	@echo "   make check4 VARIANT=v401"
	@echo ""
	@echo " Publish:"
	@echo "   make publish4 VARIANT=v401"
	@echo ""
	@echo " Remove:"
	@echo "   make remove4 VARIANT=v401"
	@echo ""
	@echo "==============================================="
	
############################################
# FASE 05 — MODELING
############################################

PHASE5 = f05_modeling
SCRIPT5_MODULE = scripts.phases.f05_model
VARIANTS_DIR5 = executions/$(PHASE5)

############################################
# Usage:
#
#   make variant5 VARIANT=v501 PARENT=v401 \
#        MODEL_FAMILY=dense_bow \
#        IMBALANCE_STRATEGY=none \
#        [IMBALANCE_MAX_MAJ=200000] \
#        [BATCH_SIZE=128] \
#        [EPOCHS=50] \
#        [LEARNING_RATE=0.0005] \
#        [EARLY_STOPPING_PATIENCE=10] \
#        [EMBEDDING_DIM=256] \
#        [HIDDEN_UNITS=256] \
#        [DROPOUT=0.3]
############################################

variant5: check-variant-format
	@test -n "$(PARENT)" || (echo "[ERROR] You must specify PARENT=v4XX (parent F04 variant)"; exit 1)
	@test -n "$(MODEL_FAMILY)" || (echo "[ERROR] You must specify MODEL_FAMILY"; exit 1)

	@$(eval EXTRA_FLAGS := )
	@$(eval EXTRA_FLAGS += PARENT=$(PARENT))
	@$(eval EXTRA_FLAGS += model_family=$(MODEL_FAMILY))

	# Optional schema-native dict overrides
	@$(if $(strip $(AUTOML)), \
		$(eval EXTRA_FLAGS += automl=$(AUTOML)))
	@$(if $(strip $(SEARCH_SPACE)), \
		$(eval EXTRA_FLAGS += search_space=$(SEARCH_SPACE)))
	@$(if $(strip $(TRAINING)), \
		$(eval EXTRA_FLAGS += training=$(TRAINING)))
	@$(if $(strip $(EVALUATION)), \
		$(eval EXTRA_FLAGS += evaluation=$(EVALUATION)))

	# Imbalance explícito por variables separadas (evita dict JSON en CLI)
	@$(eval EXTRA_FLAGS += imbalance_strategy=$(if $(strip $(IMBALANCE_STRATEGY)),$(IMBALANCE_STRATEGY),none))
	@$(if $(strip $(IMBALANCE_MAX_MAJ)), \
		$(eval EXTRA_FLAGS += imbalance_max_majority_samples=$(IMBALANCE_MAX_MAJ)))

	# Optional training hyperparameters (schema defaults si no se pasan)
	@$(if $(strip $(BATCH_SIZE)), \
		$(eval EXTRA_FLAGS += batch_size=$(BATCH_SIZE)))
	@$(if $(strip $(EPOCHS)), \
		$(eval EXTRA_FLAGS += epochs=$(EPOCHS)))
	@$(if $(strip $(LEARNING_RATE)), \
		$(eval EXTRA_FLAGS += learning_rate=$(LEARNING_RATE)))
	@$(if $(strip $(EARLY_STOPPING_PATIENCE)), \
		$(eval EXTRA_FLAGS += early_stopping_patience=$(EARLY_STOPPING_PATIENCE)))
	@$(if $(strip $(EMBEDDING_DIM)), \
		$(eval EXTRA_FLAGS += embedding_dim=$(EMBEDDING_DIM)))
	@$(if $(strip $(HIDDEN_UNITS)), \
		$(eval EXTRA_FLAGS += hidden_units=$(HIDDEN_UNITS)))
	@$(if $(strip $(DROPOUT)), \
		$(eval EXTRA_FLAGS += dropout=$(DROPOUT)))

	# Legacy flags: se mantienen para compatibilidad de CLI, pero el schema actual
	# puede rechazarlos si no están definidos como parámetros de fase.

	@$(MAKE) variant-generic \
		PHASE=$(PHASE5) \
		VARIANT=$(VARIANT) \
		EXTRA_FLAGS="$(EXTRA_FLAGS)"

script5:
	$(MAKE) script-run-generic PHASE=$(PHASE5) SCRIPT_MODULE=$(SCRIPT5_MODULE) VARIANT=$(VARIANT)

check5: check-variant-format
	$(MAKE) check-results-generic \
		PHASE=$(PHASE5) \
		VARIANTS_DIR=$(VARIANTS_DIR5) \
		VARIANT=$(VARIANT) \
		CHECK_FILES="05_model.h5 \
		05_model_report.html \
		05_labeled_dataset.parquet \
		outputs.yaml"

############################################
# PUBLICAR + REGISTRO MLFLOW
############################################

publish5: check-variant-format
	@test -n "$(VARIANT)" || (echo "[ERROR] Usage: make publish5 VARIANT=v5XX"; exit 1)

	@echo "==> Checking MLflow setup (from .mlops4ofp/setup.yaml)"
	@MLFLOW_ENABLED=$$($(PYTHON) -c 'import pathlib,yaml; p=pathlib.Path(".mlops4ofp/setup.yaml"); cfg=(yaml.safe_load(p.read_text()) if p.exists() else {}); print("1" if isinstance(cfg,dict) and cfg.get("mlflow",{}).get("enabled",False) else "0")'); \
	if [ "$$MLFLOW_ENABLED" = "1" ]; then \
		echo "==> MLflow enabled: registering run for $(PHASE5):$(VARIANT)"; \
		VAR_DIR="$(VARIANTS_DIR5)/$(VARIANT)"; \
		OUTS="$$VAR_DIR/outputs.yaml"; \
		if [ ! -f "$$OUTS" ]; then \
			echo "[ERROR] outputs.yaml not found in $$VAR_DIR"; exit 1; \
		fi; \
		VARIANT="$(VARIANT)" PHASE5="$(PHASE5)" $(PYTHON) -c 'import os,subprocess,yaml,json,pathlib,sys; variant=os.environ.get("VARIANT"); phase=os.environ.get("PHASE5","f05_modeling"); outs_path=pathlib.Path(f"executions/{phase}/{variant}/outputs.yaml"); data=(yaml.safe_load(outs_path.read_text()) if outs_path.exists() else None); \nif data is None: print(f"[ERROR] outputs.yaml not found at {outs_path}") or sys.exit(1); reg=(data.get("mlflow_registration") if isinstance(data,dict) else None); \nif not reg: print("[WARN] No '"'"'mlflow_registration'"'"' block in outputs.yaml — skipping MLflow registration") or sys.exit(0); experiment_name=(reg.get("experiment_name") or f"F05_{variant}"); metrics=reg.get("metrics",{}); params=reg.get("params",{}); artifacts=reg.get("artifacts",[]); subprocess.run(["mlflow","experiments","create","--experiment-name",experiment_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); exps=json.loads(subprocess.check_output(["mlflow","experiments","list","--format","json"])); exp_id=next((e.get("experiment_id") for e in exps if e.get("name")==experiment_name), None); \nif not exp_id: print("[ERROR] Could not obtain experiment_id for", experiment_name) or sys.exit(1); run=json.loads(subprocess.check_output(["mlflow","runs","create","--experiment-id",exp_id,"--format","json"])); run_id=run["info"]["run_id"]; [subprocess.run(["mlflow","runs","log-param","--run-id",run_id,"--key",str(k),"--value",str(v)]) for k,v in params.items()]; [subprocess.run(["mlflow","runs","log-metric","--run-id",run_id,"--key",str(k),"--value",str(v)]) for k,v in metrics.items()]; [subprocess.run(["mlflow","runs","log-artifact","--run-id",run_id,"--local-path",a]) for a in artifacts if os.path.exists(a)]; data["mlflow"]={"run_id":run_id,"experiment_id":exp_id,"experiment_name":experiment_name}; outs_path.write_text(yaml.safe_dump(data, sort_keys=False)); print(f"[OK] MLflow run created: {run_id} (experiment: {experiment_name})")'; \
	else \
		echo "[INFO] MLflow disabled in setup — skipping MLflow registration"; \
	fi

	$(MAKE) publish-generic \
		PHASE=$(PHASE5) \
		VARIANTS_DIR=$(VARIANTS_DIR5) \
		PUBLISH_EXTS="h5 html json yaml parquet" \
		VARIANT=$(VARIANT)

############################################
# ELIMINAR VARIANTES
############################################

remove5: check-variant-format
	$(MAKE) remove-generic PHASE=$(PHASE5) VARIANTS_DIR=$(VARIANTS_DIR5) VARIANT=$(VARIANT)

remove5-all:
	$(MAKE) remove-phase-all PHASE=$(PHASE5) VARIANTS_DIR=$(VARIANTS_DIR5)

############################################
# HELP
############################################

help5:
	@echo "==============================================="
	@echo " 05 — MODELING"
	@echo "==============================================="
	@echo ""
	@echo " Create:"
	@echo "   make variant5 VARIANT=v501 PARENT=v401 \\"
	@echo "       MODEL_FAMILY=dense_bow \\"
	@echo "       IMBALANCE_STRATEGY=none"
	@echo ""
	@echo " Optional overrides:"
	@echo "   BATCH_SIZE=128"
	@echo "   EPOCHS=50"
	@echo "   LEARNING_RATE=0.0005"
	@echo "   EARLY_STOPPING_PATIENCE=10"
	@echo "   EMBEDDING_DIM=256"
	@echo "   HIDDEN_UNITS=256"
	@echo "   DROPOUT=0.3"
	@echo "   AUTOML=true|false"
	@echo "   MAX_TRIALS=10"
	@echo "   SEARCH_SPACE='{\"lr\":[0.001,0.0005]}'"
	@echo "   EVAL_SPLIT='{train:0.7,val:0.15,test:0.15}'"
	@echo ""
	@echo " Execution:"
	@echo "   make script5 VARIANT=v501"
	@echo ""
	@echo " Checking:"
	@echo "   make check5 VARIANT=v501"
	@echo ""
	@echo " Publish:"
	@echo "   make publish5 VARIANT=v501"
	@echo ""
	@echo " Remove:"
	@echo "   make remove5 VARIANT=v501"
	@echo ""
	@echo "==============================================="


############################################
# FASE 06 — QUANTIZATION & EEDU
############################################

PHASE6         = f06_quant
SCRIPT6_MODULE = scripts.phases.f06_quant
VARIANTS_DIR6  = executions/$(PHASE6)

############################################
# Usage:
#
#   make variant6 VARIANT=v601 PARENT=v5XX \
#        [DEPLOY_TARGET=esp32] \
#        [DEPLOY_RUNTIME=esp-tflite-micro] \
#        [DEPLOY_VERSION=1.3.3] \
#        [REQUIRE_INT8=true] \
#        [MEMORY_LIMIT=327680] \
#        [QUANTIZATION='{"tflite_optimization":"INT8_FULL"}'] \
#        [THRESHOLDING='{"maximize_metric":"recall"}'] \
#        [EEDU='{"version":"1.0"}']
############################################

variant6: check-variant-format
	@test -n "$(PARENT)" || (echo "[ERROR] You must specify PARENT=v5XX (parent F05 variant)"; exit 1)

	@$(eval EXTRA_FLAGS := )
	@$(eval EXTRA_FLAGS += PARENT=$(PARENT))

	# -----------------------------
	# Deployment (simple CLI args)
	# -----------------------------

	@$(if $(strip $(DEPLOY_TARGET)), \
		$(eval EXTRA_FLAGS += deployment.target=$(DEPLOY_TARGET)))

	@test -n "$(DEPLOY_TARGET)" || echo "[INFO] Using default deployment target (esp32)"

	@$(if $(strip $(DEPLOY_RUNTIME)), \
		$(eval EXTRA_FLAGS += deployment.runtime=$(DEPLOY_RUNTIME)))

	@$(if $(strip $(DEPLOY_VERSION)), \
		$(eval EXTRA_FLAGS += deployment.runtime_version=$(DEPLOY_VERSION)))

	@$(if $(strip $(REQUIRE_INT8)), \
		$(eval EXTRA_FLAGS += deployment.require_int8=$(REQUIRE_INT8)))

	@$(if $(strip $(MEMORY_LIMIT)), \
		$(eval EXTRA_FLAGS += deployment.memory_limit_bytes=$(MEMORY_LIMIT)))

	# -----------------------------
	# Quantization / thresholding
	# -----------------------------

	@$(if $(strip $(QUANTIZATION)), \
		$(eval EXTRA_FLAGS += quantization=$(QUANTIZATION)))

	@$(if $(strip $(THRESHOLDING)), \
		$(eval EXTRA_FLAGS += thresholding=$(THRESHOLDING)))

	@$(if $(strip $(EEDU)), \
		$(eval EXTRA_FLAGS += eedu=$(EEDU)))

	@$(MAKE) variant-generic \
		PHASE=$(PHASE6) \
		VARIANT=$(VARIANT) \
		EXTRA_FLAGS="$(EXTRA_FLAGS)"
		

script6:
	$(MAKE) script-run-generic PHASE=$(PHASE6) SCRIPT_MODULE=$(SCRIPT6_MODULE) VARIANT=$(VARIANT)

############################################
# FASE 06 — CHECK
############################################

check6: check-variant-format
	@echo "==> Checking F06 results for variant $(VARIANT)"

	@test -n "$(VARIANT)" || (echo "[ERROR] Usage: make check6 VARIANT=v6XX"; exit 1)

	@VAR_DIR="$(VARIANTS_DIR6)/$(VARIANT)"; \
	if [ ! -d "$$VAR_DIR" ]; then \
		echo "[ERROR] Variant directory not found: $$VAR_DIR"; exit 1; \
	fi; \
	if [ ! -f "$$VAR_DIR/outputs.yaml" ]; then \
		echo "[ERROR] outputs.yaml not found in $$VAR_DIR"; exit 1; \
	fi; \
	echo "[OK] outputs.yaml found"; \
	if [ ! -f "$$VAR_DIR/06_calibration_dataset.parquet" ]; then \
		echo "[ERROR] calibration dataset not found"; exit 1; \
	fi; \
	if [ ! -f "$$VAR_DIR/06_model_float.h5" ]; then \
		echo "[ERROR] model_float not found"; exit 1; \
	fi; \
	echo "[OK] Base artifacts found"; \
	EDGE_CAPABLE=$$($(PYTHON) -c "import os,yaml; d=yaml.safe_load(open(os.path.join('$$VAR_DIR','outputs.yaml'))); print('1' if d.get('exports',{}).get('edge_capable') else '0')"); \
	if [ "$$EDGE_CAPABLE" = "1" ]; then \
		echo "[INFO] edge_capable = true — checking EEDU artifacts"; \
		[ -f "$$VAR_DIR/06_model_tflite.tflite" ] || { echo "[ERROR] model_tflite missing"; exit 1; }; \
		[ -f "$$VAR_DIR/eedu/eedu_manifest.yaml" ] || { echo "[ERROR] eedu_manifest missing"; exit 1; }; \
		[ -f "$$VAR_DIR/eedu/operators_resolver.cc" ] || { echo "[ERROR] operators_resolver missing"; exit 1; }; \
		echo "[OK] EEDU artifacts present"; \
	else \
		echo "[INFO] edge_capable = false — skipping EEDU checks"; \
	fi; \
	echo "[SUCCESS] F06 check passed for $(VARIANT)"

############################################
# PUBLICAR (sin MLflow)
############################################

############################################
# FASE 06 — PUBLISH
############################################

publish6: check-variant-format
	@test -n "$(VARIANT)" || (echo "[ERROR] Usage: make publish6 VARIANT=v6XX"; exit 1)

	@echo "==> Publishing F06 variant $(VARIANT)"

	@VAR_DIR="$(VARIANTS_DIR6)/$(VARIANT)"; \
	if [ ! -d "$$VAR_DIR" ]; then \
		echo "[ERROR] Variant directory not found: $$VAR_DIR"; exit 1; \
	fi; \
	if [ ! -f "$$VAR_DIR/outputs.yaml" ]; then \
		echo "[ERROR] outputs.yaml not found"; exit 1; \
	fi; \
	EDGE_CAPABLE=$$($(PYTHON) -c "import os,yaml; d=yaml.safe_load(open(os.path.join('$$VAR_DIR','outputs.yaml'))); print('1' if d.get('exports',{}).get('edge_capable') else '0')"); \
	if [ "$$EDGE_CAPABLE" = "1" ]; then \
		echo "[INFO] edge_capable = true — publishing full EEDU"; \
		$(MAKE) publish-generic \
			PHASE=$(PHASE6) \
			VARIANTS_DIR=$(VARIANTS_DIR6) \
			PUBLISH_EXTS="h5 html yaml parquet tflite cc" \
			VARIANT=$(VARIANT); \
	else \
		echo "[INFO] edge_capable = false — publishing non-edge artifacts only"; \
		$(MAKE) publish-generic \
			PHASE=$(PHASE6) \
			VARIANTS_DIR=$(VARIANTS_DIR6) \
			PUBLISH_EXTS="h5 html yaml parquet" \
			VARIANT=$(VARIANT); \
	fi

	@echo "[SUCCESS] Publish6 completed for $(VARIANT)"
	
############################################
# ELIMINAR VARIANTES
############################################

remove6: check-variant-format
	$(MAKE) remove-generic PHASE=$(PHASE6) VARIANTS_DIR=$(VARIANTS_DIR6) VARIANT=$(VARIANT)

remove6-all:
	$(MAKE) remove-phase-all PHASE=$(PHASE6) VARIANTS_DIR=$(VARIANTS_DIR6)

############################################
# HELP
############################################

help6:
	@echo "==============================================="
	@echo " 06 — QUANTIZATION & EEDU"
	@echo "==============================================="
	@echo ""
	@echo " Create:"
	@echo "   make variant6 VARIANT=v601 PARENT=v5XX \\"
	@echo "       [QUANTIZATION='{\"tflite_optimization\":\"INT8_FULL\",\"target_hw\":\"esp32\"}'] \\"
	@echo "       [THRESHOLDING='{\"strategy\":\"recalibrate_on_quantized\",\"maximize_metric\":\"recall\"}'] \\"
	@echo "       [EEDU='{\"version\":\"1.0\",\"layout\":\"default\"}']"
	@echo " Deployment:"
	@echo "   DEPLOY_TARGET=esp32"
	@echo "   DEPLOY_RUNTIME=esp-tflite-micro"
	@echo "   DEPLOY_VERSION=1.3.3"
	@echo "   REQUIRE_INT8=true"
	@echo "   MEMORY_LIMIT=327680"
	@echo ""
	@echo " Execution:"
	@echo "   make script6 VARIANT=v601"
	@echo ""
	@echo " Checking:"
	@echo "   make check6 VARIANT=v601"
	@echo ""
	@echo " Publish:"
	@echo "   make publish6 VARIANT=v601"
	@echo ""
	@echo " Remove:"
	@echo "   make remove6 VARIANT=v601"
	@echo ""
	@echo "==============================================="


############################################
# FASE 07 — MODEL VALIDATION (EDGE)
############################################

PHASE7        = f07_modval
VARIANTS_DIR7 = executions/$(PHASE7)

############################################
# Usage:
#
#   make variant7 VARIANT=v701 PARENT=v6XX \
#        PLATFORM=esp32 \
#        MTI_MS=... \
#        [ITMAX=...]
#		 [TIME_SCALE=0.01] \
#        [MAX_ROWS=200]
#
#   make script7-prepare-build VARIANT=v701
#   make script7-build-only   VARIANT=v701
#   make script7-flash-run    VARIANT=v701 PORT=/dev/ttyUSB0 [MODE=serial|memory] [BAUD=115200] [DRAIN_SECONDS=..]
#   make script7-post         VARIANT=v701
#
#   make script7 VARIANT=v701 PORT=/dev/ttyUSB0
############################################

variant7: check-variant-format
	@test -n "$(PARENT)" || (echo "[ERROR] You must specify PARENT=v6XX (parent F06 variant)"; exit 1)
	@test -n "$(PLATFORM)" || (echo "[ERROR] You must specify PLATFORM (e.g. PLATFORM=esp32)"; exit 1)
	@test -n "$(MTI_MS)" || (echo "[ERROR] You must specify MTI_MS in milliseconds (e.g. MTI_MS=100000)"; exit 1)
	@echo "[INFO] MTI_MS=$(MTI_MS) ms"
	@echo "[INFO] PLATFORM=$(PLATFORM)"

ifeq ($(TIME_SCALE),)
	@echo "[INFO] TIME_SCALE not provided -> using default time_scale_factor=1.0"
else
	@echo "[INFO] TIME_SCALE=$(TIME_SCALE)"
endif

ifeq ($(ITMAX),)
	@echo "[INFO] ITMAX not provided -> preparebuild will use ITmax=MTI_MS"
else
	@echo "[INFO] ITMAX=$(ITMAX)"
endif

ifeq ($(MAX_ROWS),)
	@echo "[INFO] MAX_ROWS not provided -> preparebuild will use full dataset"
else
	@echo "[INFO] MAX_ROWS=$(MAX_ROWS)"
endif

	@$(eval EXTRA_FLAGS := )
	@$(eval EXTRA_FLAGS += PARENT=$(PARENT))
	@$(eval EXTRA_FLAGS += platform=$(PLATFORM))
	@$(eval EXTRA_FLAGS += MTI_MS=$(MTI_MS))

ifneq ($(ITMAX),)
	@$(eval EXTRA_FLAGS += ITmax=$(ITMAX))
endif

ifneq ($(TIME_SCALE),)
	@$(eval EXTRA_FLAGS += time_scale_factor=$(TIME_SCALE))
endif

ifneq ($(MAX_ROWS),)
	@$(eval EXTRA_FLAGS += max_rows=$(MAX_ROWS))
endif

	@$(MAKE) variant-generic \
		PHASE=$(PHASE7) \
		VARIANT=$(VARIANT) \
		EXTRA_FLAGS="$(EXTRA_FLAGS)"

############################################
# SUBFASES
############################################

script7-prepare-build:
	$(PYTHON) -m scripts.phases.f071_preparebuild --variant $(VARIANT)

# Solo compila firmware (sin flash/run). Útil para depurar compilación/enlazado.
# Limpia build por defecto en cada ejecución para evitar estados sucios de CMake/Ninja.
script7-build-only:
	$(PYTHON) -m scripts.phases.f072_flashrun \
		--variant $(VARIANT) \
		--build-only

# PORT es obligatorio; el resto tiene defaults en f072_flashrun.py
script7-flash-run:
	$(PYTHON) -m scripts.phases.f072_flashrun \
		--variant $(VARIANT) \
		$(if $(PORT),--port $(PORT),) \
		$(if $(MODE),--mode $(MODE),) \
		$(if $(BAUD),--baud $(BAUD),) \
		$(if $(DRAIN_SECONDS),--drain-seconds $(DRAIN_SECONDS),)

script7-post:
	$(PYTHON) -m scripts.phases.f073_post --variant $(VARIANT)

############################################
# FASE COMPLETA
############################################

script7:
	@EDGE_CAPABLE="$$($(PYTHON) -c 'import yaml; from pathlib import Path; v="$(VARIANT)"; p=Path("executions")/"f07_modval"/v/"params.yaml"; d=(yaml.safe_load(p.read_text()) or {}) if p.exists() else {}; parent=d.get("parent"); o=(Path("executions")/"f06_quant"/str(parent)/"outputs.yaml") if parent else None; e=((yaml.safe_load(o.read_text()) or {}).get("exports", {})) if (o and o.exists()) else {}; print("true" if bool(e.get("edge_capable", False)) else "false")')"; \
	if [ "$$EDGE_CAPABLE" = "false" ]; then \
		echo "[INFO] Parent F06 no edge_capable para $(VARIANT). Saltando prepare-build/flash-run y ejecutando solo script7-post."; \
		$(PYTHON) -m scripts.phases.f073_post --variant $(VARIANT); \
	else \
		$(PYTHON) -m scripts.phases.f071_preparebuild --variant $(VARIANT); \
		set +e; \
		$(PYTHON) -m scripts.phases.f072_flashrun --variant $(VARIANT) $(if $(PORT),--port $(PORT),) $(if $(MODE),--mode $(MODE),) $(if $(BAUD),--baud $(BAUD),) $(if $(DRAIN_SECONDS),--drain-seconds $(DRAIN_SECONDS),); \
		rc=$$?; \
		if [ $$rc -eq 130 ] || [ $$rc -eq 2 ]; then \
			echo "[INFO] script7-flash-run interrumpido por usuario (Ctrl+C). Continuando con script7-post para resultados parciales."; \
		elif [ $$rc -ne 0 ]; then \
			echo "[INFO] script7-flash-run terminó con código $$rc. Ejecutando script7-post de todas formas."; \
		fi; \
		$(PYTHON) -m scripts.phases.f073_post --variant $(VARIANT); \
	fi

check7: check-variant-format
	@test -n "$(VARIANT)" || (echo "[ERROR] Usage: make check7 VARIANT=v7XX"; exit 1)

	@VAR_DIR="$(VARIANTS_DIR7)/$(VARIANT)"; \
	[ -d "$$VAR_DIR" ] || { echo "[ERROR] Variant dir not found"; exit 1; }; \
	[ -f "$$VAR_DIR/outputs.yaml" ] || { echo "[ERROR] outputs.yaml missing"; exit 1; }; \
	EDGE_CAPABLE="$$(VAR_DIR="$$VAR_DIR" $(PYTHON) -c 'import os, yaml; from pathlib import Path; p = Path(os.environ["VAR_DIR"]) / "outputs.yaml"; d = yaml.safe_load(p.read_text()) or {}; ex = d.get("exports", {}) or {}; v = ex.get("edge_capable"); print("false" if v is False else "true")')"; \
	if [ "$$EDGE_CAPABLE" = "false" ]; then \
		[ -f "$$VAR_DIR/params.yaml" ] || { echo "[ERROR] params.yaml missing"; exit 1; }; \
		echo "[SUCCESS] F07 check passed for $(VARIANT) (edge_capable=false, ejecución edge omitida)"; \
	else \
		[ -f "$$VAR_DIR/07_edge_run_config.yaml" ] || { echo "[ERROR] 07_edge_run_config.yaml missing"; exit 1; }; \
		[ -f "$$VAR_DIR/07_model_profile.yaml" ] || { echo "[ERROR] 07_model_profile.yaml missing"; exit 1; }; \
		[ -f "$$VAR_DIR/07_input_dataset.csv" ] || { echo "[ERROR] 07_input_dataset.csv missing"; exit 1; }; \
		[ -f "$$VAR_DIR/07_evaluation_dataset.csv" ] || { echo "[ERROR] 07_evaluation_dataset.csv missing"; exit 1; }; \
		[ -f "$$VAR_DIR/metrics_models.csv" ] || { echo "[ERROR] metrics_models.csv missing"; exit 1; }; \
		[ -f "$$VAR_DIR/metrics_memory.csv" ] || { echo "[ERROR] metrics_memory.csv missing"; exit 1; }; \
		[ -f "$$VAR_DIR/metrics_system_timing.csv" ] || { echo "[ERROR] metrics_system_timing.csv missing"; exit 1; }; \
		[ -f "$$VAR_DIR/07_esp_build_log.txt" ] || { echo "[ERROR] 07_esp_build_log.txt missing"; exit 1; }; \
		[ -f "$$VAR_DIR/07_esp_flash_log.txt" ] || { echo "[ERROR] 07_esp_flash_log.txt missing"; exit 1; }; \
		[ -f "$$VAR_DIR/07_esp_monitor_log.txt" ] || { echo "[ERROR] 07_esp_monitor_log.txt missing"; exit 1; }; \
		if [ ! -f "$$VAR_DIR/sdkconfig" ] && ! ls "$$VAR_DIR"/*_project/sdkconfig >/dev/null 2>&1; then \
			echo "[ERROR] sdkconfig missing (root o *_project/)"; exit 1; \
		fi; \
		echo "[SUCCESS] F07 check passed for $(VARIANT)"; \
	fi

############################################
# FASE 07 — PUBLISH
############################################

publish7: check-variant-format
	@test -n "$(VARIANT)" || (echo "[ERROR] Usage: make publish7 VARIANT=v7XX"; exit 1)

	$(MAKE) publish-generic \
		PHASE=$(PHASE7) \
		VARIANTS_DIR=$(VARIANTS_DIR7) \
		PUBLISH_EXTS="yaml csv json txt html" \
		VARIANT=$(VARIANT)

remove7: check-variant-format
	$(MAKE) remove-generic PHASE=$(PHASE7) VARIANTS_DIR=$(VARIANTS_DIR7) VARIANT=$(VARIANT)

remove7-all:
	$(MAKE) remove-phase-all PHASE=$(PHASE7) VARIANTS_DIR=$(VARIANTS_DIR7)


help7:
	@echo "==============================================="
	@echo " 07 — MODEL VALIDATION ON EDGE"
	@echo "==============================================="
	@echo ""
	@echo " Create:"
	@echo "   make variant7 VARIANT=v701 PARENT=v6XX PLATFORM=esp32 MTI_MS=100000"
	@echo "     (MTI_MS is mandatory and must be provided in milliseconds)"
	@echo ""
	@echo " Parameters for variant7:"
	@echo "   Required:"
	@echo "     PARENT=v6XX"
	@echo "     PLATFORM=<edge folder name>   (e.g. esp32, stm32, arduino)"
	@echo "     MTI_MS=<milliseconds>"
	@echo "   Optional:"
	@echo "     TIME_SCALE=<float>   (default: 1.0)"
	@echo "     ITMAX=<integer>      (default: MTI_MS)"
	@echo "     MAX_ROWS=<integer>   (default: full dataset in 07_input_dataset.csv)"
	@echo ""
	@echo " Execution (step-by-step):"
	@echo "   make script7-prepare-build VARIANT=v701"
	@echo "   make script7-flash-run   VARIANT=v701"
	@echo "   make script7-post    VARIANT=v701"
	@echo ""
	@echo " Full execution:"
	@echo "   make script7 VARIANT=v701"
	@echo ""
	@echo " Checking:"
	@echo "   make check7 VARIANT=v701"
	@echo ""
	@echo " Publish:"
	@echo "   make publish7 VARIANT=v701"
	@echo ""
	@echo "==============================================="

############################################
# FASE 08 — SYSTEM VALIDATION (MULTI-MODEL EDGE)
############################################

PHASE8        = f08_sysval
VARIANTS_DIR8 = executions/$(PHASE8)

############################################
# Usage:
#
#   make variant8 VARIANT=v801 PARENTS='[v701, v702, ...]' \
#        (también admite PARENTS=v701,v702,...) \
#        PLATFORM=esp32 \
#        MTI_MS=... \
#        [TIME_SCALE=0.01] \
#        [MAX_ROWS=200] \
#        [MEMORY_BUDGET_BYTES=...] \
#        [MAX_MODELS=...] \
#        [MIN_QUALITY_SCORE=...]
#
#   make script8-select-config VARIANT=v801
#   make script8-prepare-build VARIANT=v801
#   make script8-build-only    VARIANT=v801
#   make script8-flash-run     VARIANT=v801 PORT=/dev/ttyUSB0 [MODE=serial|memory] [BAUD=115200] [DRAIN_SECONDS=..]
#   make script8-post          VARIANT=v801
#
#   make script8 VARIANT=v801 PORT=/dev/ttyUSB0
############################################

variant8: check-variant-format
	@test -n "$(PARENTS)" || (echo "[ERROR] You must specify PARENTS as list (e.g. '[v700, v701]' or 'v700,v701')"; exit 1)
	@test -n "$(PLATFORM)" || (echo "[ERROR] You must specify PLATFORM (e.g. PLATFORM=esp32)"; exit 1)
	@test -n "$(MTI_MS)" || (echo "[ERROR] You must specify MTI_MS in milliseconds (e.g. MTI_MS=100)"; exit 1)

	@echo "[INFO] PARENTS=$(PARENTS)"
	@echo "[INFO] MTI_MS=$(MTI_MS) ms"
	@echo "[INFO] PLATFORM=$(PLATFORM)"

# --- selection mode ---
ifeq ($(SELECTION_MODE),)
	@echo "[INFO] SELECTION_MODE not provided -> using default 'manual'"
	@$(eval SELECTION_MODE := manual)
else
	@echo "[INFO] SELECTION_MODE=$(SELECTION_MODE)"
endif

# --- objective ---
ifeq ($(OBJECTIVE),)
	@echo "[INFO] OBJECTIVE not provided"
else
	@echo "[INFO] OBJECTIVE=$(OBJECTIVE)"
endif

# --- thresholds ---
ifeq ($(MIN_PRECISION),)
	@echo "[INFO] MIN_PRECISION not provided"
else
	@echo "[INFO] MIN_PRECISION=$(MIN_PRECISION)"
endif

ifeq ($(MIN_RECALL),)
	@echo "[INFO] MIN_RECALL not provided"
else
	@echo "[INFO] MIN_RECALL=$(MIN_RECALL)"
endif

ifeq ($(TIME_SCALE),)
	@echo "[INFO] TIME_SCALE not provided -> using default time_scale_factor=1.0"
else
	@echo "[INFO] TIME_SCALE=$(TIME_SCALE)"
endif

ifeq ($(MAX_ROWS),)
	@echo "[INFO] MAX_ROWS not provided -> preparebuild will use full dataset"
else
	@echo "[INFO] MAX_ROWS=$(MAX_ROWS)"
endif

ifeq ($(MEMORY_BUDGET_BYTES),)
	@echo "[INFO] MEMORY_BUDGET_BYTES not provided"
else
	@echo "[INFO] MEMORY_BUDGET_BYTES=$(MEMORY_BUDGET_BYTES)"
endif

ifeq ($(MAX_MODELS),)
	@echo "[INFO] MAX_MODELS not provided"
else
	@echo "[INFO] MAX_MODELS=$(MAX_MODELS)"
endif

ifeq ($(MIN_QUALITY_SCORE),)
	@echo "[INFO] MIN_QUALITY_SCORE not provided"
else
	@echo "[INFO] MIN_QUALITY_SCORE=$(MIN_QUALITY_SCORE)"
endif

# --- EXTRA FLAGS ---
	@$(eval EXTRA_FLAGS := )
	@$(eval EXTRA_FLAGS += parents=$(PARENTS))
	@$(eval EXTRA_FLAGS += selection_mode=$(SELECTION_MODE))
	@$(eval EXTRA_FLAGS += platform=$(PLATFORM))
	@$(eval EXTRA_FLAGS += MTI_MS=$(MTI_MS))

ifneq ($(OBJECTIVE),)
	@$(eval EXTRA_FLAGS += objective=$(OBJECTIVE))
endif

ifneq ($(TIME_SCALE),)
	@$(eval EXTRA_FLAGS += time_scale_factor=$(TIME_SCALE))
endif

ifneq ($(MAX_ROWS),)
	@$(eval EXTRA_FLAGS += max_rows=$(MAX_ROWS))
endif

ifneq ($(MEMORY_BUDGET_BYTES),)
	@$(eval EXTRA_FLAGS += memory_budget_bytes=$(MEMORY_BUDGET_BYTES))
endif

ifneq ($(MAX_MODELS),)
	@$(eval EXTRA_FLAGS += max_models=$(MAX_MODELS))
endif

ifneq ($(MIN_QUALITY_SCORE),)
	@$(eval EXTRA_FLAGS += min_quality_score=$(MIN_QUALITY_SCORE))
endif

ifneq ($(MIN_PRECISION),)
	@$(eval EXTRA_FLAGS += min_precision=$(MIN_PRECISION))
endif

ifneq ($(MIN_RECALL),)
	@$(eval EXTRA_FLAGS += min_recall=$(MIN_RECALL))
endif

	@$(MAKE) variant-generic \
		PHASE=$(PHASE8) \
		VARIANT=$(VARIANT) \
		EXTRA_FLAGS="$(EXTRA_FLAGS)"

############################################
# SUBFASES
############################################

script8-select-config:
	$(PYTHON) -m scripts.phases.f081_selectconfig --variant $(VARIANT)

script8-prepare-build:
	$(PYTHON) -m scripts.phases.f082_preparebuild --variant $(VARIANT)

# Solo compila firmware (sin flash/run). Útil para depurar compilación/enlazado.
script8-build-only:
	@test -n "$(VARIANT)" || (echo "[ERROR] You must specify VARIANT=v8XX"; exit 1)
	@test -d "executions/$(PHASE8)/$(VARIANT)/esp32_project" || (echo "[ERROR] esp32_project not found for executions/$(PHASE8)/$(VARIANT)"; exit 1)
	@test -d "executions/$(PHASE8)/$(VARIANT)/esp32_project/build_generated" || (echo "[ERROR] build_generated missing. Run script8-prepare-build first"; exit 1)
	@if [ -f "executions/$(PHASE8)/$(VARIANT)/esp32_project/sdkconfig.defaults" ]; then \
		cp "executions/$(PHASE8)/$(VARIANT)/esp32_project/sdkconfig.defaults" "executions/$(PHASE8)/$(VARIANT)/esp32_project/sdkconfig"; \
		echo "[F08] sdkconfig regenerado desde sdkconfig.defaults para build Docker"; \
	fi
	@rm -rf "executions/$(PHASE8)/$(VARIANT)/esp32_project/build/build_generated"
	@mkdir -p "executions/$(PHASE8)/$(VARIANT)/esp32_project/build"
	@cp -R "executions/$(PHASE8)/$(VARIANT)/esp32_project/build_generated" "executions/$(PHASE8)/$(VARIANT)/esp32_project/build/build_generated"
	@echo "[F08] sync build_generated -> build/build_generated"
	@echo "[F08] Build-only en Docker para $(VARIANT)"
	@docker run --rm -i \
		-v "$(PWD)/executions/$(PHASE8)/$(VARIANT)/esp32_project:/project" \
		-w /project \
		--entrypoint /bin/bash \
		mlops4ofp-idf:6.0 \
		-lc "source /opt/esp/idf/export.sh >/dev/null 2>&1 && idf.py build"

# PORT es obligatorio; el resto tiene defaults en f083_flashrun.py
script8-flash-run:
	$(PYTHON) -m scripts.phases.f083_flashrun \
		--variant $(VARIANT) \
		$(if $(PORT),--port $(PORT),) \
		$(if $(MODE),--mode $(MODE),) \
		$(if $(BAUD),--baud $(BAUD),) \
		$(if $(DRAIN_SECONDS),--drain-seconds $(DRAIN_SECONDS),)

script8-post:
	$(PYTHON) -m scripts.phases.f084_post --variant $(VARIANT)

############################################
# FASE COMPLETA
############################################

script8:
	@$(PYTHON) -m scripts.phases.f081_selectconfig --variant $(VARIANT); \
	CONFIG_EDGE_CAPABLE="$$($(PYTHON) -c 'import yaml; from pathlib import Path; v="$(VARIANT)"; p=Path("executions")/"f08_sysval"/v/"08_selected_configuration.yaml"; d=(yaml.safe_load(p.read_text()) or {}) if p.exists() else {}; print("true" if bool(d.get("configuration_edge_capable", False)) else "false")')"; \
	if [ "$$CONFIG_EDGE_CAPABLE" = "false" ]; then \
		echo "[INFO] Configuración F08 no edge_capable para $(VARIANT). Ejecutando post sin flash-run."; \
		$(PYTHON) -m scripts.phases.f084_post --variant $(VARIANT); \
	else \
		$(PYTHON) -m scripts.phases.f082_preparebuild --variant $(VARIANT); \
		set +e; \
		$(PYTHON) -m scripts.phases.f083_flashrun --variant $(VARIANT) $(if $(PORT),--port $(PORT),) $(if $(MODE),--mode $(MODE),) $(if $(BAUD),--baud $(BAUD),) $(if $(DRAIN_SECONDS),--drain-seconds $(DRAIN_SECONDS),); \
		rc=$$?; \
		if [ $$rc -eq 130 ] || [ $$rc -eq 2 ]; then \
			echo "[INFO] script8-flash-run interrumpido por usuario (Ctrl+C). Continuando con script8-post para resultados parciales."; \
		elif [ $$rc -ne 0 ]; then \
			echo "[INFO] script8-flash-run terminó con código $$rc. Ejecutando script8-post de todas formas."; \
		fi; \
		$(PYTHON) -m scripts.phases.f084_post --variant $(VARIANT); \
	fi

############################################
# FASE 08 — CHECK
############################################

check8: check-variant-format
	@test -n "$(VARIANT)" || (echo "[ERROR] Usage: make check8 VARIANT=v8XX"; exit 1)

	@VAR_DIR="$(VARIANTS_DIR8)/$(VARIANT)"; \
	[ -d "$$VAR_DIR" ] || { echo "[ERROR] Variant dir not found"; exit 1; }; \
	[ -f "$$VAR_DIR/outputs.yaml" ] || { echo "[ERROR] outputs.yaml missing"; exit 1; }; \
	[ -f "$$VAR_DIR/08_selected_configuration.yaml" ] || { echo "[ERROR] 08_selected_configuration.yaml missing"; exit 1; }; \
	CONFIG_EDGE_CAPABLE="$$(VAR_DIR="$$VAR_DIR" $(PYTHON) -c 'import os, yaml; from pathlib import Path; p = Path(os.environ["VAR_DIR"]) / "outputs.yaml"; d = yaml.safe_load(p.read_text()) or {}; ex = d.get("exports", {}) or {}; v = ex.get("configuration_edge_capable"); print("false" if v is False else "true")')"; \
	if [ "$$CONFIG_EDGE_CAPABLE" = "false" ]; then \
		[ -f "$$VAR_DIR/params.yaml" ] || { echo "[ERROR] params.yaml missing"; exit 1; }; \
		echo "[SUCCESS] F08 check passed for $(VARIANT) (configuration_edge_capable=false, ejecución edge omitida)"; \
	else \
		[ -f "$$VAR_DIR/08_system_profile.yaml" ] || { echo "[ERROR] 08_system_profile.yaml missing"; exit 1; }; \
		[ -f "$$VAR_DIR/08_edge_run_config.yaml" ] || { echo "[ERROR] 08_edge_run_config.yaml missing"; exit 1; }; \
		[ -f "$$VAR_DIR/08_input_dataset.csv" ] || { echo "[ERROR] 08_input_dataset.csv missing"; exit 1; }; \
		[ -f "$$VAR_DIR/metrics_models.csv" ] || { echo "[ERROR] metrics_models.csv missing"; exit 1; }; \
		[ -f "$$VAR_DIR/metrics_memory.csv" ] || { echo "[ERROR] metrics_memory.csv missing"; exit 1; }; \
		[ -f "$$VAR_DIR/metrics_system_timing.csv" ] || { echo "[ERROR] metrics_system_timing.csv missing"; exit 1; }; \
		[ -f "$$VAR_DIR/08_esp_build_log.txt" ] || { echo "[ERROR] 08_esp_build_log.txt missing"; exit 1; }; \
		[ -f "$$VAR_DIR/08_esp_flash_log.txt" ] || { echo "[ERROR] 08_esp_flash_log.txt missing"; exit 1; }; \
		[ -f "$$VAR_DIR/08_esp_monitor_log.txt" ] || { echo "[ERROR] 08_esp_monitor_log.txt missing"; exit 1; }; \
		if [ ! -f "$$VAR_DIR/sdkconfig" ] && ! ls "$$VAR_DIR"/*_project/sdkconfig >/dev/null 2>&1; then \
			echo "[ERROR] sdkconfig missing (root o *_project/)"; exit 1; \
		fi; \
		echo "[SUCCESS] F08 check passed for $(VARIANT)"; \
	fi

############################################
# FASE 08 — PUBLISH
############################################

publish8: check-variant-format
	@test -n "$(VARIANT)" || (echo "[ERROR] Usage: make publish8 VARIANT=v8XX"; exit 1)

	$(MAKE) publish-generic \
		PHASE=$(PHASE8) \
		VARIANTS_DIR=$(VARIANTS_DIR8) \
		PUBLISH_EXTS="yaml csv json txt html" \
		VARIANT=$(VARIANT)

remove8: check-variant-format
	$(MAKE) remove-generic PHASE=$(PHASE8) VARIANTS_DIR=$(VARIANTS_DIR8) VARIANT=$(VARIANT)

remove8-all:
	$(MAKE) remove-phase-all PHASE=$(PHASE8) VARIANTS_DIR=$(VARIANTS_DIR8)

help8:
	@echo ""
	@echo "====================== F08 — SYSTEM VALIDATION ======================"
	@echo ""
	@echo "Genera una variante F08 (multi-model edge configuration)"
	@echo ""
	@echo "USO BÁSICO:"
	@echo "  make variant8 VARIANT=v800 PARENTS=v700,v703 PLATFORM=esp32 MTI_MS=100"
	@echo ""
	@echo "PARÁMETROS OBLIGATORIOS:"
	@echo "  VARIANT=v8XX              Nombre de variante"
	@echo "  PARENTS=v7XX,...          Modelos candidatos (F07)"
	@echo "  PLATFORM=esp32            Plataforma objetivo"
	@echo "  MTI_MS=100                Tiempo máximo total (ms)"
	@echo ""
	@echo "SELECCIÓN:"
	@echo "  SELECTION_MODE=manual | auto_ilp     (default: manual)"
	@echo "  OBJECTIVE=max_global_recall | max_tp"
	@echo ""
	@echo "FILTROS (opcionales):"
	@echo "  MIN_PRECISION=0.01"
	@echo "  MIN_RECALL=0.8"
	@echo "  MIN_QUALITY_SCORE=0.01"
	@echo ""
	@echo "RESTRICCIONES:"
	@echo "  MEMORY_BUDGET_BYTES=300000"
	@echo "  MAX_MODELS=3"
	@echo ""
	@echo "OTROS:"
	@echo "  TIME_SCALE=1.0"
	@echo "  MAX_ROWS=1000"
	@echo ""
	@echo "====================== EJEMPLOS ======================"
	@echo ""
	@echo "1. Selección manual:"
	@echo "  make variant8 VARIANT=v800 PARENTS=v700,v703 PLATFORM=esp32 MTI_MS=100"
	@echo ""
	@echo "2. ILP optimizando recall:"
	@echo "  make variant8 VARIANT=v801 PARENTS=v700,v703 PLATFORM=esp32 MTI_MS=100 \\"
	@echo "      SELECTION_MODE=auto_ilp OBJECTIVE=max_global_recall"
	@echo ""
	@echo "3. ILP optimizando TP:"
	@echo "  make variant8 VARIANT=v802 PARENTS=v700,v703 PLATFORM=esp32 MTI_MS=100 \\"
	@echo "      SELECTION_MODE=auto_ilp OBJECTIVE=max_tp"
	@echo ""
	@echo "4. ILP con filtros:"
	@echo "  make variant8 VARIANT=v803 PARENTS=v700,v703 PLATFORM=esp32 MTI_MS=100 \\"
	@echo "      SELECTION_MODE=auto_ilp OBJECTIVE=max_tp MIN_PRECISION=0.01"
	@echo ""
	@echo "====================================================================="


help: help-setup help1 help2 help3 help4 help5 help6 help7 help8	
	@echo "==============================================="

.PHONY: \
	setup check-setup clean-setup \
	nb-run-generic script-run-generic \
	variant-generic check-variant-format \
	publish-generic remove-generic check-results-generic export-generic \
	script1 script2 script3 script4 script5 script6 script7 script8 \
	variant1 variant2 variant3 variant4 variant5 variant6 variant7 variant8 \
	check1 check2 check3 check4 check5 check6 check7 check8 \
	publish1 publish2 publish3 publish4 publish5 publish6 publish7 publish8 \
	remove1 remove2 remove3 remove4 remove5 remove6 remove7 remove8 \
	help1 help2 help3 help4 help5 help6 help7 help8

############################################
# Utils
############################################
generate_lineage: 
	${PYTHON} scripts/core/variants_lineage/generate_lineage.py 
