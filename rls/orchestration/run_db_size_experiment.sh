#!/usr/bin/env bash
# Database physical-size measurement driver (C-R9, §1.1).
#
# Runs the measurement against machines described by a "machine descriptor";
# provisioning is the separate provider-specific step (orchestration/provision/provision_vms.sh)
# and the driver talks to the machines through the provider-agnostic transport
# (orchestration/util/_remote_transport.sh) — so it is not tied to Google Cloud.
#
# C-R9 has no experiment parameters beyond the dataset: it loads the 1M-row
# patients/doctors dataset and runs python -m microbenchmarks.measure_db_sizes (PostgreSQL catalog
# size queries) from the attacker VM against the DB, then fetches the JSON and
# validates it against the C-R9 paper claim (§1.1) with
# src/renderers/validate_cr9_db_sizes.py (done at the end of this script; no
# committed reference JSON is needed — the expected values are baked into the validator).
#
# MODES (same as orchestration/run_existence_experiment.sh)
#   attached (descriptor exists at --machines): use those machines, never tear down.
#   managed  (descriptor absent, DBSIZE_AUTO_PROVISION!=0, default): provision a bare
#            DB+attacker stack, install PostgreSQL + repo/venv, measure, and tear it
#            down on exit unless DBSIZE_SKIP_TEARDOWN=1.
#
# Usage (--config <file> and --machines <file> are REQUIRED):
#   CFG=orchestration/config/shared_config.yml
#   export RUN_ID="dbsize-$(date +%s | tail -c6)"; M=results/machines/${RUN_ID}.yml
#   bash orchestration/run_db_size_experiment.sh --config "$CFG" --machines "$M"
#
# Inputs:
#   --config <file>         - REQUIRED: config.yml to read (e.g. orchestration/config/shared_config.yml)
#   --machines <file>       - REQUIRED: machine descriptor (e.g. results/machines/<RUN_ID>.yml)
#   DBSIZE_AUTO_PROVISION   - 1 = provision when no descriptor (default); 0 = require one
#   DBSIZE_SKIP_TEARDOWN    - 1 = leave managed VMs running after the run (default 0)
#   DBSIZE_LOCAL_OUTPUT_DIR - local artifact dir (default results/dbsize/<RUN_ID>)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

MACHINES_FILE_ARG=""
CONFIG_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machines) MACHINES_FILE_ARG="$2"; shift 2 ;;
    --config)   CONFIG_ARG="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# C-R9 has no experiment params, so the shared loader (infra + plumbing) is enough.
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_load_gcloud_config.sh" "${CONFIG_ARG}"
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_remote_transport.sh"
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_local_preflight.sh"

RLS_POLICY="${RLS_POLICY:-join}"
ATTACK_DB="${ATTACK_DB:-rls}"
ADMIN_DB="${ADMIN_DB:-rls}"
DBSIZE_AUTO_PROVISION="${DBSIZE_AUTO_PROVISION:-1}"
DBSIZE_SKIP_TEARDOWN="${DBSIZE_SKIP_TEARDOWN:-0}"
LOCAL_OUTPUT_DIR="${DBSIZE_LOCAL_OUTPUT_DIR:-results/dbsize/${RUN_ID:-no-run-id}}"

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] $*"; }

PROGRESS_TTY=0
[[ -t 1 ]] && PROGRESS_TTY=1

if [[ -z "${RUN_ID:-}" ]]; then
  echo "RUN_ID must be set (e.g. export RUN_ID=\"dbsize-\$(date +%s | tail -c6)\")." >&2
  exit 1
fi

# ---- Resolve machine descriptor + run mode --------------------------------
if [[ -z "${MACHINES_FILE_ARG}" ]]; then
  echo "--machines <file> is required (e.g. --machines results/machines/${RUN_ID}.yml)." >&2
  exit 1
fi
MACHINES_FILE="${MACHINES_FILE_ARG}"
OWNED=0
if [[ -f "${MACHINES_FILE}" ]]; then
  MODE="attached"
elif [[ "${DBSIZE_AUTO_PROVISION}" != "0" ]]; then
  MODE="managed"; OWNED=1
else
  echo "No machine descriptor at '${MACHINES_FILE}' and DBSIZE_AUTO_PROVISION=0." >&2
  echo "Provision first, then re-run:" >&2
  echo "    bash orchestration/provision/provision_vms.sh --config ${GCLOUD_CONFIG} --output ${MACHINES_FILE}" >&2
  echo "    bash orchestration/run_db_size_experiment.sh --config ${GCLOUD_CONFIG} --machines ${MACHINES_FILE}" >&2
  exit 1
fi

teardown() {
  local rc=$?
  if [[ "${OWNED}" -eq 1 && "${DBSIZE_SKIP_TEARDOWN}" -eq 0 ]]; then
    log "Teardown: removing provisioned stack (${RUN_ID})"
    if [[ -f "${MACHINES_FILE}" ]]; then
      bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --machines "${MACHINES_FILE}" \
        || log "  WARNING: cleanup reported an error"
    else
      RUN_ID="${RUN_ID}" bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --config "${GCLOUD_CONFIG}" \
        || log "  WARNING: cleanup reported an error"
    fi
    rm -f "${MACHINES_FILE}"
  elif [[ "${OWNED}" -eq 1 ]]; then
    log "Teardown: DBSIZE_SKIP_TEARDOWN=1 — leaving provisioned VMs + descriptor running"
    log "  Clean up later: bash orchestration/provision/cleanup_vms.sh --machines ${MACHINES_FILE}"
  else
    log "Teardown: attached to externally-provisioned machines — leaving them untouched."
  fi
  exit "${rc}"
}
trap teardown EXIT

# ---- Provision + install (managed mode only) ------------------------------
if [[ "${MODE}" == "managed" ]]; then
  log "No descriptor at ${MACHINES_FILE}; provisioning a GCP stack (managed mode)."
  bash "${SCRIPT_DIR}/provision/provision_vms.sh" --config "${GCLOUD_CONFIG}" --output "${MACHINES_FILE}"
  log "Installing PostgreSQL on the DB VM."
  bash "${SCRIPT_DIR}/install/install_artifact_on_database.sh" --config "${GCLOUD_CONFIG}" --machines "${MACHINES_FILE}"
  log "Installing repo + building venv on the attacker (measurement client)."
  bash "${SCRIPT_DIR}/install/install_artifact_on_attacker.sh" --config "${GCLOUD_CONFIG}" --machines "${MACHINES_FILE}"
fi

# ---- Load the descriptor and describe the run -----------------------------
transport_load "${MACHINES_FILE}"
DB_ADDR="$(transport_db_addr)"
REMOTE_DIR="$(_tv MACHINES_REMOTE_DIR)"
REMOTE_DIR="${REMOTE_DIR:-${REMOTE_BASE_DIR:-rls-dir}/scratch}"

if [[ -z "${DB_ADDR}" ]]; then
  echo "Descriptor ${MACHINES_FILE} is missing db.internal_addr." >&2
  exit 1
fi

log "=== Database physical-size measurement (mode=${MODE}) ==="
log "  RUN_ID:       ${RUN_ID}"
log "  Machines:     ${MACHINES_FILE}"
transport_summary
log "  DB address:   ${DB_ADDR}  (attacker -> DB)"
log "  Output dir:   ${LOCAL_OUTPUT_DIR}"

# Sanity-check the attacker actually has the repo + venv.
if ! transport_exec attacker "test -x '${REMOTE_DIR}/venv/bin/python' && echo OK" 2>/dev/null | grep -q OK; then
  echo "Attacker venv not found at ${REMOTE_DIR}/venv/bin/python (machines not prepared?)." >&2
  echo "Sync the repo + venv first:  bash orchestration/install/install_artifact_on_attacker.sh --config ${GCLOUD_CONFIG} --machines ${MACHINES_FILE}" >&2
  exit 1
fi

ADMIN_DSN="postgresql://postgres:${POSTGRES_PASSWORD}@${DB_ADDR}/${ADMIN_DB}"

# ---- Stage 5: load dataset ------------------------------------------------
log "Stage 5: ensure dataset is loaded (${PATIENTS} patients, ${DOCTORS} doctors, ${SITES} sites)"
ALREADY="$(transport_exec attacker \
  "PGPASSWORD='${POSTGRES_PASSWORD}' psql -h '${DB_ADDR}' -U postgres -d ${ADMIN_DB} -tAc 'SELECT count(*) FROM patients;' 2>/dev/null || echo 0" \
  2>/dev/null | tr -d '\r ')" || ALREADY="0"
ALREADY="${ALREADY//[!0-9]/}"; [[ -z "${ALREADY}" ]] && ALREADY=0
log "  patients table has ${ALREADY} rows (target ${PATIENTS})"
if [[ "${ALREADY}" -lt "${PATIENTS}" ]]; then
  log "  loading dataset on the attacker..."
  dataset_rt="transport_exec"
  dataset_prog_env="export RLS_PROGRESS=0; "
  if [[ "${PROGRESS_TTY}" == "1" ]]; then
    dataset_rt="transport_exec_tty"
    dataset_prog_env="export TERM=xterm-256color RLS_PROGRESS=1; "
  fi
  "${dataset_rt}" attacker "${dataset_prog_env}cd '${REMOTE_DIR}' && venv/bin/python -m patients.setup_db \
    --dsn '${ADMIN_DSN}' \
    --create-db \
    --patients ${PATIENTS} \
    --doctors ${DOCTORS} \
    --sites ${SITES} \
    --rls-policy ${RLS_POLICY} \
    --reset \
    --analyze"
else
  log "  dataset already present; skipping load."
fi

# ---- Stage 6: measure physical sizes --------------------------------------
log "Stage 6: measure physical sizes on the DB (via the attacker client)"
REMOTE_JSON="results/db_sizes_${RUN_ID}.json"
transport_exec attacker "cd '${REMOTE_DIR}' && mkdir -p results && venv/bin/python -m microbenchmarks.measure_db_sizes \
  --dsn '${ADMIN_DSN}' \
  --output '${REMOTE_JSON}'"

mkdir -p "${LOCAL_OUTPUT_DIR}"
transport_fetch attacker "${REMOTE_DIR}/${REMOTE_JSON}" "${LOCAL_OUTPUT_DIR}/db_sizes.json"
log "  wrote ${LOCAL_OUTPUT_DIR}/db_sizes.json"

# ---- Stage 7: validate the measurement against the C-R9 paper claim (§1.1) -
# No committed reference JSON is needed: validate_cr9_db_sizes.py checks the measured
# sizes against the paper's expected values (±1 MB) and prints the per-component breakdown.
# Its output is saved to summary.txt (and echoed) so the verdict is captured with the run.
SUMMARY="${LOCAL_OUTPUT_DIR}/summary.txt"
log "Stage 7: validate measured sizes against the C-R9 paper claim -> ${SUMMARY}"
if rls_uv_run_module renderers.validate_cr9_db_sizes "${LOCAL_OUTPUT_DIR}/db_sizes.json" > "${SUMMARY}" 2>&1; then
  log "  C-R9 validation PASSED"
else
  log "  C-R9 validation reported a divergence from the paper claim (see ${SUMMARY})"
fi
cat "${SUMMARY}"

log "=== DB size measurement ${RUN_ID} complete (mode=${MODE}) ==="
log "  Output: ${LOCAL_OUTPUT_DIR}/ (db_sizes.json + summary.txt)"
