#!/usr/bin/env bash
# Join-policy RLS mitigation experiment driver (C-R8, Table 5 / §3.4).
#
# This driver runs the EXPERIMENT against machines described by a "machine
# descriptor"; provisioning is the separate provider-specific step
# (orchestration/provision/provision_vms.sh) and the driver talks to the machines through
# the provider-agnostic transport (orchestration/util/_remote_transport.sh). It is therefore not
# tied to Google Cloud — point it at an ssh descriptor and it runs unchanged.
#
# The sweep compares, all under live RLS enforcement and with EXPLAIN evidence:
#   baseline             plpgsql join policy + single-column index  (leaks)
#   plpgsql_composite    plpgsql join policy + (site_id,attr) index (leaks)
#   subq_inline_single   inline-subquery policy + single-column     (leaks)
#   subq_inline          inline-subquery policy + (site_id,attr)     (sealed)
#
# MODES (same as orchestration/run_existence_experiment.sh)
#   attached (descriptor exists at --machines): use those machines, never tear down.
#   managed  (descriptor absent, MIT_AUTO_PROVISION!=0, default): provision a bare
#            DB+attacker stack, install PostgreSQL + the repo/venv, run, and tear it
#            down on exit unless MIT_SKIP_TEARDOWN=1 (the leak-safe one-command flow).
#
# Usage (--config <file> and --machines <file> are REQUIRED):
#   CFG=orchestration/config/shared_config.yml
#   export RUN_ID="mit-c4-$(date +%s | tail -c6)"; M=results/machines/${RUN_ID}.yml
#   # One command, end-to-end (managed):
#   bash orchestration/run_join_mitigations_experiment.sh --config "$CFG" --machines "$M"
#
#   # Separated (attached):
#   bash orchestration/provision/provision_vms.sh      --config "$CFG" --output "$M"
#   bash orchestration/install/install_artifact_on_database.sh --config "$CFG" --machines "$M"
#   bash orchestration/install/install_artifact_on_attacker.sh --config "$CFG" --machines "$M"
#   bash orchestration/run_join_mitigations_experiment.sh --config "$CFG" --machines "$M"
#   bash orchestration/provision/cleanup_vms.sh    --machines "$M"
#
# Inputs:
#   --config <file>      - REQUIRED: config.yml to read (e.g. orchestration/config/shared_config.yml)
#   --machines <file>    - REQUIRED: machine descriptor (e.g. results/machines/<RUN_ID>.yml)
#   MIT_AUTO_PROVISION   - 1 = provision when no descriptor (default); 0 = require one
#   MIT_SKIP_TEARDOWN    - 1 = leave managed VMs running after the run (default 0)
#   MIT_LOCAL_OUTPUT_DIR - local artifact dir (default results/table5/<RUN_ID>)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"   # relative paths (artifact download, local renders) resolve here

MACHINES_FILE_ARG=""
CONFIG_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machines) MACHINES_FILE_ARG="$2"; shift 2 ;;
    --config)   CONFIG_ARG="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Load config from the --config file (REQUIRED); sets the env + CONFIG_FILE=/dev/null.
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_load_mitigations_config.sh" "${CONFIG_ARG}"
# Machine addressing comes from the descriptor (not _run_id_overlay.sh).
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_remote_transport.sh"

RLS_POLICY="${RLS_POLICY:-join}"
ATTACK_DB="${ATTACK_DB:-rls}"
ADMIN_DB="${ADMIN_DB:-rls}"
MIT_AUTO_PROVISION="${MIT_AUTO_PROVISION:-1}"
MIT_SKIP_TEARDOWN="${MIT_SKIP_TEARDOWN:-0}"
LOCAL_OUTPUT_DIR="${MIT_LOCAL_OUTPUT_DIR:-results/table5/${RUN_ID:-no-run-id}}"
REMOTE_OUT="results/table5_${RUN_ID:-no-run-id}"

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] $*"; }

if [[ -z "${RUN_ID:-}" ]]; then
  echo "RUN_ID must be set (e.g. export RUN_ID=\"mit-c4-\$(date +%s | tail -c6)\")." >&2
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
elif [[ "${MIT_AUTO_PROVISION}" != "0" ]]; then
  MODE="managed"; OWNED=1
else
  echo "No machine descriptor at '${MACHINES_FILE}' and MIT_AUTO_PROVISION=0." >&2
  echo "Provision first, then re-run:" >&2
  echo "    bash orchestration/provision/provision_vms.sh --config ${MITIGATIONS_CONFIG} --output ${MACHINES_FILE}" >&2
  echo "    bash orchestration/run_join_mitigations_experiment.sh --config ${MITIGATIONS_CONFIG} --machines ${MACHINES_FILE}" >&2
  exit 1
fi

teardown() {
  local rc=$?
  if [[ "${OWNED}" -eq 1 && "${MIT_SKIP_TEARDOWN}" -eq 0 ]]; then
    log "Teardown: removing provisioned stack (${RUN_ID})"
    if [[ -f "${MACHINES_FILE}" ]]; then
      bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --machines "${MACHINES_FILE}" \
        || log "  WARNING: cleanup reported an error"
    else
      RUN_ID="${RUN_ID}" bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --config "${MITIGATIONS_CONFIG}" \
        || log "  WARNING: cleanup reported an error"
    fi
    rm -f "${MACHINES_FILE}"
  elif [[ "${OWNED}" -eq 1 ]]; then
    log "Teardown: MIT_SKIP_TEARDOWN=1 — leaving provisioned VMs + descriptor running"
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
  bash "${SCRIPT_DIR}/provision/provision_vms.sh" --config "${MITIGATIONS_CONFIG}" --output "${MACHINES_FILE}"
  log "Installing PostgreSQL on the DB VM."
  bash "${SCRIPT_DIR}/install/install_artifact_on_database.sh" --config "${MITIGATIONS_CONFIG}" --machines "${MACHINES_FILE}"
  log "Installing repo + building venv on the attacker."
  bash "${SCRIPT_DIR}/install/install_artifact_on_attacker.sh" --config "${MITIGATIONS_CONFIG}" --machines "${MACHINES_FILE}"
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

log "=== Join-policy RLS mitigation experiment (mode=${MODE}) ==="
log "  RUN_ID:       ${RUN_ID}"
log "  Machines:     ${MACHINES_FILE}"
transport_summary
log "  DB address:   ${DB_ADDR}  (attacker -> DB)"
log "  Remote dir:   ${REMOTE_DIR}"
log "  Configs:      ${MIT_CONFIGS}"
log "  Probes:       ${MIT_PROBES}   k=${MIT_KVALUES}   attribute=${MIT_ATTRIBUTE}"
log "  Output dir:   ${LOCAL_OUTPUT_DIR}"

# Sanity-check the attacker actually has the repo + venv.
if ! transport_exec attacker "test -x '${REMOTE_DIR}/venv/bin/python' && echo OK" 2>/dev/null | grep -q OK; then
  echo "Attacker venv not found at ${REMOTE_DIR}/venv/bin/python (machines not prepared?)." >&2
  echo "Sync the repo + venv first:  bash orchestration/install/install_artifact_on_attacker.sh --config ${MITIGATIONS_CONFIG} --machines ${MACHINES_FILE}" >&2
  exit 1
fi

ADMIN_DSN="postgresql://postgres:${POSTGRES_PASSWORD}@${DB_ADDR}/${ADMIN_DB}"
ATTACKER_DSN="postgresql://${ATTACKER_USER}:${ATTACKER_USER}@${DB_ADDR}/${ATTACK_DB}"

# ---- Stage 5: load dataset ------------------------------------------------
log "Stage 5: ensure dataset is loaded (${PATIENTS} patients, ${DOCTORS} doctors, ${SITES} sites)"
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
    --rls-policy ${RLS_POLICY} \
    --reset \
    --analyze"
else
  log "  dataset already present; skipping load."
fi

# ---- Stage 6: run the mitigation sweep (nohup + poll) ---------------------
# Run under nohup on the attacker and poll for completion, so the sweep (which
# can run 60-90 min) survives a dropped SSH session.
log "Stage 6: launch mitigation sweep on the attacker (nohup; configs=${MIT_CONFIGS})"
transport_exec attacker "
  cd '${REMOTE_DIR}' && mkdir -p '${REMOTE_OUT}' && \
  RLS_PROGRESS=0 nohup venv/bin/python -m mitigation \
    --admin-dsn '${ADMIN_DSN}' \
    --attacker-dsn '${ATTACKER_DSN}' \
    --attacker-user '${ATTACKER_USER}' \
    --attribute '${MIT_ATTRIBUTE}' \
    --configs '${MIT_CONFIGS}' \
    --probes '${MIT_PROBES}' \
    --k-values '${MIT_KVALUES}' \
    --warm-cache \
    --output-dir '${REMOTE_OUT}' \
    > '${REMOTE_OUT}.log' 2>&1 &
  echo \"sweep pid \$!\"
"

log "Stage 6b: poll for completion (timeout ${MIT_RUN_TIMEOUT_S}s)"
run_deadline=$(( $(date +%s) + MIT_RUN_TIMEOUT_S ))
while true; do
  # bracket trick so pgrep never self-matches the poll's own shell.
  STATUS="$(transport_exec attacker "
    cd '${REMOTE_DIR}' || exit
    if [[ -f '${REMOTE_OUT}/summary.csv' ]]; then echo DONE;
    elif pgrep -f '[p]ython.*-m mitigation' >/dev/null 2>&1; then echo RUNNING;
    else echo DEAD; fi
  " 2>/dev/null | tr -d '\r ')" || STATUS="UNKNOWN"
  case "${STATUS}" in
    DONE)    log "  sweep finished (summary.csv present)."; break ;;
    RUNNING) : ;;
    DEAD)    log "ERROR: sweep process exited without writing summary.csv. Tail of remote log:"
             transport_exec attacker "tail -n 30 '${REMOTE_DIR}/${REMOTE_OUT}.log' 2>/dev/null" 2>/dev/null || true
             exit 1 ;;
    *)       log "  poll status: ${STATUS} (transient ssh issue?)" ;;
  esac
  if [[ $(date +%s) -gt ${run_deadline} ]]; then
    log "ERROR: sweep did not finish within ${MIT_RUN_TIMEOUT_S}s." >&2
    exit 1
  fi
  DONE_N="$(transport_exec attacker \
    "ls '${REMOTE_DIR}/${REMOTE_OUT}'/*/accuracy.csv 2>/dev/null | wc -l" 2>/dev/null | tr -d '\r ')" || DONE_N="?"
  log "  ...running (configs completed so far: ${DONE_N})"
  sleep 60
done

# ---- Stage 7: download this run's artifacts -------------------------------
# transport_fetch is single-file, so tar the remote output dir's CONTENTS and
# fetch one tarball (works for both the gcloud and ssh backends).
log "Stage 7: download artifacts into ${LOCAL_OUTPUT_DIR}/"
mkdir -p "$(dirname "${LOCAL_OUTPUT_DIR}")"
rm -rf "${LOCAL_OUTPUT_DIR}"; mkdir -p "${LOCAL_OUTPUT_DIR}"
LOCAL_TGZ="$(mktemp -t mit_out_XXXXXX.tgz)"
trap 'rm -f "${LOCAL_TGZ}"' RETURN 2>/dev/null || true
transport_exec attacker "cd '${REMOTE_DIR}/${REMOTE_OUT}' && tar --no-xattrs -czf /tmp/mit_out.tgz ."
transport_fetch attacker "/tmp/mit_out.tgz" "${LOCAL_TGZ}"
tar -xzf "${LOCAL_TGZ}" -C "${LOCAL_OUTPUT_DIR}"
rm -f "${LOCAL_TGZ}"
transport_exec attacker "rm -f /tmp/mit_out.tgz" 2>/dev/null || true
# also grab the run log for the record
transport_fetch attacker "${REMOTE_DIR}/${REMOTE_OUT}.log" "${LOCAL_OUTPUT_DIR}/run.log" 2>/dev/null || true

# NOTE: the supplementary accuracy bar chart + per-mitigation timing histograms are
# no longer auto-rendered here. They are NOT part of the C-R8 claim — Table 5 is the
# heatmap (src/renderers/join_mitigation_heatmap.py). Their renderers are
# not maintained in this artifact; this run still saves summary.csv and per-config
# timings.csv for ad hoc analysis.

log "=== Mitigation experiment ${RUN_ID} complete (mode=${MODE}) ==="
log "  Summary: ${LOCAL_OUTPUT_DIR}/summary.md"
log "  Output:  ${LOCAL_OUTPUT_DIR}/"
