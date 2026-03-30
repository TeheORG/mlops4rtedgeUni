#!/bin/bash
set -e
set -o pipefail

LOGFILE="$(dirname "$0")/run-test-audit.log"
exec > >(tee "$LOGFILE") 2>&1

TMPERR=$(mktemp)
EXITCODE=0

log_ok()   { echo "[OK]   $1"; }
log_fail() { echo "[FAIL] $1"; cat "$TMPERR"; EXITCODE=1; }

run_expect_ok() {
  "$@" >"$TMPERR" 2>&1 && log_ok "$*" || log_fail "$*"
}

run_expect_fail() {
  "$@" >"$TMPERR" 2>&1 && log_ok "$* (expected FAIL)" || log_ok "$* (correctly failed)"
}

echo "========================================"
echo " EXPERIMENT: AUDITABILITY DEMONSTRATION "
echo "========================================"

############################################
# CLEAN
############################################

echo "[STEP 0] Cleaning all variants"
for p in {1..8}; do make remove${p}-all >/dev/null 2>&1 || true; done

############################################
# STEP 1 — CREATION OK CHAIN
############################################

echo "[STEP 1] Creating valid chain v105 -> v205 -> v305"

run_expect_ok make variant1 VARIANT=v105 RAW=data/raw.csv CLEANING=basic NAN_VALUES='[-999999]' MAX_LINES=2000
run_expect_ok make script1 VARIANT=v105
run_expect_ok make register1 VARIANT=v105

run_expect_ok make variant2 VARIANT=v205 PARENT=v105 BANDS="[40,60]" STRATEGY=transitions NAN_MODE=keep
run_expect_ok make script2 VARIANT=v205
run_expect_ok make register2 VARIANT=v205

run_expect_ok make variant3 VARIANT=v305 PARENT=v205 OW=6 LT=1 PW=1 STRATEGY=synchro NAN_MODE=discard
run_expect_ok make script3 VARIANT=v305
run_expect_ok make register3 VARIANT=v305

############################################
# STEP 2 — MISSING PARENT
############################################

echo "[STEP 2] Creating v505 WITHOUT v405 (must fail)"

run_expect_fail make variant5 VARIANT=v505 PARENT=v405 MODEL_FAMILY=dense_bow IMBALANCE_STRATEGY=none
rm -rf executions/f05_modeling/v505 2>/dev/null || true

echo "[STEP 2b] Creating missing parent v405"

run_expect_ok make variant4 VARIANT=v405 PARENT=v305 NAME=test OPERATOR=OR EVENTS='["event"]'
run_expect_ok make script4 VARIANT=v405
run_expect_ok make register4 VARIANT=v405

echo "[STEP 2c] Now v505 should work"

run_expect_ok make variant5 VARIANT=v505 PARENT=v405 MODEL_FAMILY=dense_bow IMBALANCE_STRATEGY=none
run_expect_ok make script5 VARIANT=v505
run_expect_ok make register5 VARIANT=v505

############################################
# STEP 3 — TAMPERING outputs.yaml
############################################

echo "[STEP 3] Modify parent outputs.yaml -> must break child register"

run_expect_ok make variant6 VARIANT=v606 PARENT=v505
run_expect_ok make script6 VARIANT=v606

echo "[INFO] Tampering outputs.yaml of parent v505"
echo "# tamper $(date)" >> executions/f05_modeling/v505/outputs.yaml

run_expect_fail make register6 VARIANT=v606

echo "[INFO] Reverting outputs.yaml"
git checkout -- executions/f05_modeling/v505/outputs.yaml

echo "[STEP 3b] Register should now work"

run_expect_ok make register6 VARIANT=v606

############################################
# STEP 4 — TAMPERING SOURCE CODE
############################################

echo "[STEP 4] Modify phase code -> must break register"

run_expect_ok make variant6 VARIANT=v607 PARENT=v505
run_expect_ok make script6 VARIANT=v607

echo "[INFO] Tampering source code (f06)"
echo "# tamper $(date)" >> scripts/phases/f06_packaging.py

run_expect_fail make register6 VARIANT=v607

echo "[INFO] Reverting source code"
git checkout -- scripts/phases/f06_packaging.py

echo "[STEP 4b] Register should now work"

run_expect_ok make register6 VARIANT=v607

############################################

echo "========================================"
echo " RESULT: AUDITABILITY TEST COMPLETED "
echo "========================================"

rm "$TMPERR"
exit $EXITCODE
