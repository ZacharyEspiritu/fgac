#!/usr/bin/env bash
# Table 1 background-load experiment driver (C-R3) — split (provision/install/run).
#
# This is the C-R3 counterpart of orchestration/run_existence_experiment.sh: it runs the
# EXPERIMENT against machines described by a "machine descriptor" and no longer
# provisions GCP VMs inline. Provisioning is a separate, provider-specific step
# (orchestration/provision/provision_vms.sh) that emits the descriptor; this driver consumes it
# and drives the DB + attacker + noise machines through a provider-agnostic
# transport (orchestration/util/_remote_transport.sh). Point it at an `ssh` descriptor for
# bring-your-own machines and it runs unchanged.
#
# The actual CPU-target sweep / noise lifecycle / calibration is carried by the
# existing orchestration/noise/run_oracle_accuracy_with_noise_controller.sh wrapper, which this driver invokes once
# per variant with MACHINES_FILE set (so the wrapper drives the VMs over the
# transport rather than direct gcloud).
#
# --variant selects which policy family to sweep:
#   join   -> join-based RLS policy  (point-query noise, controller off)
#   inline -> inline RLS policy       (range-query noise, DB-CPU controller on)
#   both   -> join then inline on the same stack (default)
#
# MODES (identical to run_existence_experiment.sh)
#   attached (descriptor file already exists at --machines): use those machines
#            as-is, never tear them down.
#   managed  (--machines path does not exist yet and TABLE1_AUTO_PROVISION!=0,
#            the default): provision a GCP stack (DB + attacker + noise) via
#            orchestration/provision/provision_vms.sh, install PostgreSQL + the repo/venv on all
#            three roles, run, and tear it down on exit unless TABLE1_SKIP_TEARDOWN=1.
#
# Usage (--config <file> and --machines <file> are REQUIRED):
#   CFG=orchestration/config/shared_config.yml
#   export RUN_ID="tab1-$(date +%s | tail -c8)"; M=results/machines/${RUN_ID}.yml
#   # One command, end-to-end (managed): provision -> install -> run both -> teardown.
#   bash orchestration/run_oracle_accuracy.sh --config "$CFG" --machines "$M"
#
#   # Separated (attached): provision the stack, install, then run.
#   bash orchestration/provision/provision_vms.sh            --config "$CFG" --output "$M"
#   bash orchestration/install/install_artifact_on_database.sh --config "$CFG" --machines "$M"
#   bash orchestration/install/install_artifact_on_attacker.sh --config "$CFG" --machines "$M"
#   bash orchestration/install/install_artifact_on_noise.sh    --config "$CFG" --machines "$M"
#   bash orchestration/run_oracle_accuracy.sh          --config "$CFG" --machines "$M" --variant both
#   bash orchestration/provision/cleanup_vms.sh    --machines "$M"
#
# Inputs:
#   --config <file>            - REQUIRED: config.yml to read (orchestration/config/shared_config.yml)
#   --machines <file>          - REQUIRED: machine descriptor (must include a noise role)
#   --variant <join|inline|both> - which policy family to sweep (default both)
#   --noise-sweep <spec>       - override the sweep for the selected variant(s)
#   TABLE1_AUTO_PROVISION      - 1 = provision when no descriptor (default); 0 = require one
#   TABLE1_SKIP_TEARDOWN       - 1 = leave managed VMs running after the run (default 0)
#   TABLE1_SKIP_WARMUP         - 1 = skip the per-variant cache-warmup scenario (default 0)
#   TABLE1_LOCAL_OUTPUT_PARENT - local artifact parent (default results/table1)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"   # relative paths (artifact download) resolve here

MACHINES_FILE_ARG=""
CONFIG_ARG=""
VARIANT_ARG="both"
NOISE_SWEEP_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machines)    MACHINES_FILE_ARG="$2"; shift 2 ;;
    --config)      CONFIG_ARG="$2"; shift 2 ;;
    --variant)     VARIANT_ARG="$2"; shift 2 ;;
    --noise-sweep) NOISE_SWEEP_OVERRIDE="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Load config from --config (REQUIRED); sets the env + CONFIG_FILE=/dev/null so
# the shared GCP scripts use it. shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_load_table1_config.sh" "${CONFIG_ARG}"
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_remote_transport.sh"

RLS_POLICY="${RLS_POLICY:-join}"   # base load policy; the wrapper swaps per variant
DB_ENGINE="postgres"
TABLE1_AUTO_PROVISION="${TABLE1_AUTO_PROVISION:-1}"
TABLE1_SKIP_TEARDOWN="${TABLE1_SKIP_TEARDOWN:-0}"
ATTACK_DB="${ATTACK_DB:-rls}"
ADMIN_DB="${ADMIN_DB:-rls}"
LOCAL_OUTPUT_PARENT="${TABLE1_LOCAL_OUTPUT_PARENT:-results/table1}"

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] $*"; }

if [[ -z "${RUN_ID:-}" ]]; then
  echo "RUN_ID must be set (e.g. export RUN_ID=\"tab1-\$(date +%s | tail -c8)\")." >&2
  exit 1
fi

case "${VARIANT_ARG}" in
  join)   VARIANTS=(join) ;;
  inline) VARIANTS=(inline) ;;
  both)   VARIANTS=(join inline) ;;
  *) echo "--variant must be one of: join, inline, both (got: ${VARIANT_ARG})." >&2; exit 1 ;;
esac

# ---- Resolve machine descriptor + run mode --------------------------------
if [[ -z "${MACHINES_FILE_ARG}" ]]; then
  echo "--machines <file> is required (e.g. --machines results/machines/${RUN_ID}.yml)." >&2
  exit 1
fi
MACHINES_FILE="${MACHINES_FILE_ARG}"
OWNED=0
if [[ -f "${MACHINES_FILE}" ]]; then
  MODE="attached"
elif [[ "${TABLE1_AUTO_PROVISION}" != "0" ]]; then
  MODE="managed"; OWNED=1
else
  echo "No machine descriptor at '${MACHINES_FILE}' and TABLE1_AUTO_PROVISION=0." >&2
  echo "Provision first, then re-run:" >&2
  echo "    bash orchestration/provision/provision_vms.sh --config ${TABLE1_CONFIG} --output ${MACHINES_FILE}" >&2
  echo "    bash orchestration/run_oracle_accuracy.sh --config ${TABLE1_CONFIG} --machines ${MACHINES_FILE}" >&2
  exit 1
fi

teardown() {
  local rc=$?
  if [[ "${OWNED}" -eq 1 && "${TABLE1_SKIP_TEARDOWN}" -eq 0 ]]; then
    log "Teardown: removing provisioned stack (${RUN_ID})"
    if [[ -f "${MACHINES_FILE}" ]]; then
      bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --machines "${MACHINES_FILE}" \
        || log "  WARNING: cleanup reported an error"
    else
      RUN_ID="${RUN_ID}" bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --config "${TABLE1_CONFIG}" \
        || log "  WARNING: cleanup reported an error"
    fi
    rm -f "${MACHINES_FILE}"
  elif [[ "${OWNED}" -eq 1 ]]; then
    log "Teardown: TABLE1_SKIP_TEARDOWN=1 — leaving provisioned VMs + descriptor running"
    log "  Descriptor: ${MACHINES_FILE}"
    log "  Clean up later: bash orchestration/provision/cleanup_vms.sh --machines ${MACHINES_FILE}"
  else
    log "Teardown: attached to externally-provisioned machines — leaving them untouched."
  fi
  exit "${rc}"
}
trap teardown EXIT

# ---- Provision + install (managed mode only) ------------------------------
# Table 1 needs all three roles: DB, attacker, and the noise generator. Managed
# mode provisions the noise VM by default (provision/provision_vms.sh, no --no-noise)
# and installs the repo/venv on it too.
if [[ "${MODE}" == "managed" ]]; then
  log "No descriptor at ${MACHINES_FILE}; provisioning a GCP stack (managed mode)."
  bash "${SCRIPT_DIR}/provision/provision_vms.sh" --config "${TABLE1_CONFIG}" --output "${MACHINES_FILE}"
  if [[ "${DB_INSTALL_BUNDLED:-0}" == "1" ]]; then
    log "DB install is bundled with provisioning; skipping the separate DB install."
  else
    log "Installing the database (${DB_INSTALL_SCRIPT:-install_artifact_on_database.sh})."
    bash "${SCRIPT_DIR}/install/${DB_INSTALL_SCRIPT:-install_artifact_on_database.sh}" --config "${TABLE1_CONFIG}" --machines "${MACHINES_FILE}"
  fi
  log "Installing repo + building venv on the attacker."
  bash "${SCRIPT_DIR}/install/install_artifact_on_attacker.sh" --config "${TABLE1_CONFIG}" --machines "${MACHINES_FILE}"
  log "Installing repo + building venv on the noise VM."
  bash "${SCRIPT_DIR}/install/install_artifact_on_noise.sh" --config "${TABLE1_CONFIG}" --machines "${MACHINES_FILE}"
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
if [[ -z "$(_tv MACHINES_NOISE_VM)$(_tv MACHINES_NOISE_HOST)" ]]; then
  echo "Descriptor ${MACHINES_FILE} has no 'noise:' role — Table 1 needs a noise generator." >&2
  echo "Re-provision WITHOUT --no-noise:  bash orchestration/provision/provision_vms.sh --config ${TABLE1_CONFIG} --output ${MACHINES_FILE}" >&2
  exit 1
fi

log "=== Table 1 background-load experiment (variants=${VARIANTS[*]}, mode=${MODE}) ==="
log "  RUN_ID:        ${RUN_ID}"
log "  Machines:      ${MACHINES_FILE}"
transport_summary
log "  DB address:    ${DB_ADDR}  (attacker/noise -> DB)"
log "  Remote dir:    ${REMOTE_DIR}"
log "  Probes/cell:   ${T1_PROBES}   calibration=${T1_CALIBRATION_MODE}"

# Sanity-check the attacker + noise actually have the repo + venv.
for role in attacker noise; do
  if ! transport_exec "${role}" "test -x '${REMOTE_DIR}/venv/bin/python' && echo OK" 2>/dev/null | grep -q OK; then
    echo "${role} venv not found at ${REMOTE_DIR}/venv/bin/python (machines not prepared?)." >&2
    echo "Install first:  bash orchestration/install/install_artifact_on_${role}.sh --config ${TABLE1_CONFIG} --machines ${MACHINES_FILE}" >&2
    exit 1
  fi
done

# ---- Ensure the dataset is loaded (and data/doctors.csv written on attacker) --
# The attack swaps the active RLS policy between join/inline on this dataset —
# it is loaded once with the base policy and reused across variants. The load
# also writes data/doctors.csv on the attacker (the noise user pool); we stage
# that on the noise VM once, just below.
ADMIN_DSN="postgresql://postgres:${POSTGRES_PASSWORD}@${DB_ADDR}/${ADMIN_DB}"
log "Stage: ensure dataset is loaded (${PATIENTS} patients, ${DOCTORS} doctors, ${SITES} sites)"
ALREADY="$(transport_exec attacker \
  "PGPASSWORD='${POSTGRES_PASSWORD}' psql -h '${DB_ADDR}' -U postgres -d ${ADMIN_DB} -tAc 'SELECT count(*) FROM patients;' 2>/dev/null || echo 0" \
  2>/dev/null | tr -d '\r ')" || ALREADY="0"
ALREADY="${ALREADY//[!0-9]/}"; [[ -z "${ALREADY}" ]] && ALREADY=0
log "  patients table has ${ALREADY} rows (target ${PATIENTS})"
if [[ "${ALREADY}" -lt "${PATIENTS}" ]]; then
  log "  loading dataset on the attacker..."
  transport_exec attacker "cd '${REMOTE_DIR}' && venv/bin/python -m patients.setup_db \
    --dsn '${ADMIN_DSN}' \
    --create-db \
    --patients ${PATIENTS} \
    --doctors ${DOCTORS} \
    --sites ${SITES} \
    --seed ${T1_SEED} \
    --rls-policy ${RLS_POLICY} \
    --reset \
    --analyze"
else
  log "  dataset already present; skipping load."
fi

# ---- Stage the noise user pool (data/doctors.csv) on the noise VM, ONCE ----
# The noise module reads its client pool from data/doctors.csv locally on the
# noise VM. The dataset load above writes that file on the ATTACKER; it is not
# git-tracked, so orchestration/install/install_artifact_on_noise.sh does not ship it.
# Copy it to the noise VM once here — the wrapper's per-scenario sync then sees
# it present and skips, instead of re-copying it for every CPU cell.
USERS_REL="data/doctors.csv"
USERS_REMOTE="${REMOTE_DIR}/${USERS_REL}"
log "Stage: copy ${USERS_REL} to the noise VM (attacker -> noise, once)"
if ! transport_exec attacker "test -f '${USERS_REMOTE}'" >/dev/null 2>&1; then
  echo "Expected ${USERS_REMOTE} on the attacker (written by the dataset load) but it is missing." >&2
  exit 1
fi
USERS_TMP="$(mktemp -t rls_doctors_XXXXXX.csv)"
transport_fetch attacker "${USERS_REMOTE}" "${USERS_TMP}"
transport_exec noise "mkdir -p '$(dirname "${USERS_REMOTE}")'" >/dev/null
transport_push noise "${USERS_TMP}" "${USERS_REMOTE}"
rm -f "${USERS_TMP}"

# ---- Run the sweep, once per variant, via the wrapper ---------------------
# The wrapper drives the VMs over the transport because MACHINES_FILE is exported.
export MACHINES_FILE REMOTE_BASE_DIR DB_ENGINE
export ATTACKER_USER ATTACKER_PASSWORD ATTACK_DB ADMIN_DB POSTGRES_PASSWORD
export TABLE1_USERS_FILE="data/doctors.csv"
export TABLE1_FAST="1" TABLE1_WARM_CACHE="1" TABLE1_ENABLE_DB_METRICS="1"
export TABLE1_SKIP_WARMUP="${TABLE1_SKIP_WARMUP:-0}"
export TABLE1_SEED="${T1_SEED}" TABLE1_NONEXISTENT_OFFSET="${T1_NONEXISTENT_OFFSET}"

for variant in "${VARIANTS[@]}"; do
  if [[ "${variant}" == "join" ]]; then
    EFF_POLICIES="${T1_JOIN_POLICIES}";   EFF_K_VALUES="${T1_JOIN_K_VALUES}"
    EFF_QUERY_MODE="${T1_JOIN_NOISE_QUERY_MODE}"; EFF_RANGE_WIDTH="${T1_JOIN_NOISE_RANGE_WIDTH}"
    EFF_SWEEP="${T1_JOIN_NOISE_SWEEP}"
  else
    EFF_POLICIES="${T1_INLINE_POLICIES}"; EFF_K_VALUES="${T1_INLINE_K_VALUES}"
    EFF_QUERY_MODE="${T1_INLINE_NOISE_QUERY_MODE}"; EFF_RANGE_WIDTH="${T1_INLINE_NOISE_RANGE_WIDTH}"
    EFF_SWEEP="${T1_INLINE_NOISE_SWEEP}"
  fi
  [[ -n "${NOISE_SWEEP_OVERRIDE}" ]] && EFF_SWEEP="${NOISE_SWEEP_OVERRIDE}"
  if [[ -z "${EFF_SWEEP}" ]]; then
    echo "No noise sweep configured for variant=${variant} (set table1.${variant}.noise_sweep or pass --noise-sweep)." >&2
    exit 1
  fi

  VARIANT_OUTPUT_DIR="${LOCAL_OUTPUT_PARENT}/${variant}-${RUN_ID}"
  export TABLE1_NOISE_QUERY_MODE="${EFF_QUERY_MODE}"
  export TABLE1_NOISE_RANGE_WIDTH="${EFF_RANGE_WIDTH}"

  log "=== variant=${variant}: policies=${EFF_POLICIES} k=${EFF_K_VALUES} -> ${VARIANT_OUTPUT_DIR} ==="
  bash "${SCRIPT_DIR}/noise/run_oracle_accuracy_with_noise_controller.sh" \
    --db-engine postgres \
    --policies "${EFF_POLICIES}" \
    --k-values "${EFF_K_VALUES}" \
    --probes "${T1_PROBES}" \
    --calibration-mode "${T1_CALIBRATION_MODE}" \
    --local-output-dir "${VARIANT_OUTPUT_DIR}" \
    --noise-sweep "${EFF_SWEEP}"
  log "  variant=${variant} complete -> ${VARIANT_OUTPUT_DIR}"
done

log "=== Table 1 experiment ${RUN_ID} complete (variants=${VARIANTS[*]}, mode=${MODE}) ==="
log "  Per-variant artifacts under: ${LOCAL_OUTPUT_PARENT}/<variant>-${RUN_ID}/"
log "  Render Table 1 with uv run python -m renderers.policy_heatmap (see README.md §C-R3)."
