#!/usr/bin/env bash
# Tuple-extension reconstruction experiment driver (C-R7, Table 4) — split
# (provision/install/run), so it runs on the SAME shared machine infra as the
# other shared-stack claims (DB + attacker; no noise VM needed).
#
# It runs the full-record tuple-extension attack (python -m reconstruction
# in tuple-extension recompute mode) against the shared DB over the provider-agnostic
# transport (orchestration/util/_remote_transport.sh).
#
# Grid: each attribute ORDERING x each worker count W x REPS reps. The 6 orderings
# permute {ssn, zip_code, age} (sza,saz,asz,azs,zsa,zas) — the "most-selective-first"
# vs other orders of Table 4. Config defaults preserve the C-R7 claim parameters:
# tuple-extension-mode=between, recompute threshold (cal-rounds 1),
# --skip-attr-probe, --sample-tuples=100000, per-probe baseline on,
# --log-oracle-calls, and rls-policy join. REPS defaults to 1; set it via
# config/--reps when repeated cells are needed.
#
# Per-cell output dirs are named so tuple_extension_table.py classifies them:
#   <WORKER_PREFIX>-c4[-r<rep>]-<ordering>
# where WORKER_PREFIX = tuplext-rc100k (W=1) | tuplext-rc100kw2 (W=2) |
# tuplext-rc100kw<W> (otherwise); rep 1 omits the -r token. Artifacts land under
# results/table4/<RUN_ID>/<cell>/reconstruction_summary.json.
#
# MODES (identical to run_singleattr_experiment.sh)
#   attached (descriptor exists at --machines): use those machines, never tear down.
#   managed  (--machines absent and TUPLEXT_AUTO_PROVISION!=0, the default):
#            provision a DB + attacker stack (--no-noise), install, run, tear down on
#            exit unless TUPLEXT_SKIP_TEARDOWN=1.
#
# Usage (--config <file> and --machines <file> are REQUIRED):
#   CFG=orchestration/config/shared_config.yml
#   export RUN_ID="tx-$(date +%s | tail -c8)"; M=results/machines/${RUN_ID}.yml
#   bash orchestration/run_tuplext_experiment.sh --config "$CFG" --machines "$M"
#   # then render Table 4:
#   uv run python -m renderers.tuple_extension_table --results-dir results/table4/${RUN_ID}
#
# Inputs:
#   --config <file>            - REQUIRED: config.yml (e.g. orchestration/config/shared_config.yml)
#   --machines <file>          - REQUIRED: machine descriptor
#   --orderings <sza,saz,...>  - override the configured orderings (subset of the 6)
#   --workers <1,2,4>          - override the configured worker counts
#   --reps <N>                 - reps per (ordering, W) cell (default from config, else 1);
#                                each cell runs N times, suffixed -r<rep> for rep>1
#   TUPLEXT_AUTO_PROVISION     - 1 = provision when no descriptor (default); 0 = require one
#   TUPLEXT_SKIP_TEARDOWN      - 1 = leave managed VMs running after the run (default 0)
#   TUPLEXT_LOCAL_OUTPUT_PARENT - local artifact parent (default results/table4)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

MACHINES_FILE_ARG=""
CONFIG_ARG=""
ORDERINGS_OVERRIDE=""
WORKERS_OVERRIDE=""
REPS_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machines)  MACHINES_FILE_ARG="$2"; shift 2 ;;
    --config)    CONFIG_ARG="$2"; shift 2 ;;
    --orderings) ORDERINGS_OVERRIDE="$2"; shift 2 ;;
    --workers)   WORKERS_OVERRIDE="$2"; shift 2 ;;
    --reps)      REPS_OVERRIDE="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_load_tuplext_config.sh" "${CONFIG_ARG}"
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_remote_transport.sh"

RLS_POLICY="${RLS_POLICY:-join}"   # the only policy exploitable per Table 1
ATTACK_DB="${ATTACK_DB:-rls}"
ADMIN_DB="${ADMIN_DB:-rls}"
TUPLEXT_AUTO_PROVISION="${TUPLEXT_AUTO_PROVISION:-1}"
TUPLEXT_SKIP_TEARDOWN="${TUPLEXT_SKIP_TEARDOWN:-0}"
LOCAL_OUTPUT_PARENT="${TUPLEXT_LOCAL_OUTPUT_PARENT:-results/table4}"

ORDERINGS="${ORDERINGS_OVERRIDE:-${TX_ORDERINGS}}"
WORKERS="${WORKERS_OVERRIDE:-${TX_WORKERS}}"
REPS="${REPS_OVERRIDE:-${TX_REPS}}"
if ! [[ "${REPS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "reps must be a positive integer (got: ${REPS})." >&2
  exit 1
fi

# Ordering token -> the comma-joined --attributes string (the tuple-extension order).
VALID_ORDERINGS="saz sza asz azs zsa zas"
ordering_attrs() {
  case "$1" in
    saz) echo "ssn,age,zip_code" ;;
    sza) echo "ssn,zip_code,age" ;;
    asz) echo "age,ssn,zip_code" ;;
    azs) echo "age,zip_code,ssn" ;;
    zsa) echo "zip_code,ssn,age" ;;
    zas) echo "zip_code,age,ssn" ;;
    *) return 1 ;;
  esac
}

# Show the per-cell progress bar only on a TTY (PTY allocated); quiet for nohup logs.
PROGRESS_TTY=0
[[ -t 1 ]] && PROGRESS_TTY=1

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] $*"; }

if [[ -z "${RUN_ID:-}" ]]; then
  echo "RUN_ID must be set (e.g. export RUN_ID=\"tx-\$(date +%s | tail -c8)\")." >&2
  exit 1
fi

IFS=',' read -r -a ORDERING_ARR <<< "${ORDERINGS}"
IFS=',' read -r -a WORKER_ARR <<< "${WORKERS}"
if [[ "${#ORDERING_ARR[@]}" -eq 0 ]]; then
  echo "tuplext.orderings is empty." >&2
  exit 1
fi
if [[ "${#WORKER_ARR[@]}" -eq 0 ]]; then
  echo "tuplext.workers is empty." >&2
  exit 1
fi
for o in "${ORDERING_ARR[@]}"; do
  if ! ordering_attrs "${o}" >/dev/null; then
    echo "Unknown ordering '${o}' (expected a subset of: ${VALID_ORDERINGS})." >&2
    exit 1
  fi
done

# ---- Resolve machine descriptor + run mode --------------------------------
if [[ -z "${MACHINES_FILE_ARG}" ]]; then
  echo "--machines <file> is required (e.g. --machines results/machines/${RUN_ID}.yml)." >&2
  exit 1
fi
MACHINES_FILE="${MACHINES_FILE_ARG}"
OWNED=0
if [[ -f "${MACHINES_FILE}" ]]; then
  MODE="attached"
elif [[ "${TUPLEXT_AUTO_PROVISION}" != "0" ]]; then
  MODE="managed"; OWNED=1
else
  echo "No machine descriptor at '${MACHINES_FILE}' and TUPLEXT_AUTO_PROVISION=0." >&2
  echo "Provision first, then re-run:" >&2
  echo "    bash orchestration/provision/provision_vms.sh --config ${TUPLEXT_CONFIG} --output ${MACHINES_FILE} --no-noise" >&2
  echo "    bash orchestration/run_tuplext_experiment.sh --config ${TUPLEXT_CONFIG} --machines ${MACHINES_FILE}" >&2
  exit 1
fi

teardown() {
  local rc=$?
  if [[ "${OWNED}" -eq 1 && "${TUPLEXT_SKIP_TEARDOWN}" -eq 0 ]]; then
    log "Teardown: removing provisioned stack (${RUN_ID})"
    if [[ -f "${MACHINES_FILE}" ]]; then
      bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --machines "${MACHINES_FILE}" \
        || log "  WARNING: cleanup reported an error"
    else
      RUN_ID="${RUN_ID}" bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --config "${TUPLEXT_CONFIG}" \
        || log "  WARNING: cleanup reported an error"
    fi
    rm -f "${MACHINES_FILE}"
  elif [[ "${OWNED}" -eq 1 ]]; then
    log "Teardown: TUPLEXT_SKIP_TEARDOWN=1 — leaving provisioned VMs + descriptor running"
    log "  Clean up later: bash orchestration/provision/cleanup_vms.sh --machines ${MACHINES_FILE}"
  else
    log "Teardown: attached to externally-provisioned machines — leaving them untouched."
  fi
  exit "${rc}"
}
trap teardown EXIT

# ---- Provision + install (managed mode only) — DB + attacker, NO noise ----
if [[ "${MODE}" == "managed" ]]; then
  log "No descriptor at ${MACHINES_FILE}; provisioning a GCP stack (managed mode, --no-noise)."
  bash "${SCRIPT_DIR}/provision/provision_vms.sh" --config "${TUPLEXT_CONFIG}" --output "${MACHINES_FILE}" --no-noise
  if [[ "${DB_INSTALL_BUNDLED:-0}" == "1" ]]; then
    log "DB install is bundled with provisioning; skipping the separate DB install."
  else
    log "Installing the database (${DB_INSTALL_SCRIPT:-install_artifact_on_database.sh})."
    bash "${SCRIPT_DIR}/install/${DB_INSTALL_SCRIPT:-install_artifact_on_database.sh}" --config "${TUPLEXT_CONFIG}" --machines "${MACHINES_FILE}"
  fi
  log "Installing repo + building venv on the attacker."
  bash "${SCRIPT_DIR}/install/install_artifact_on_attacker.sh" --config "${TUPLEXT_CONFIG}" --machines "${MACHINES_FILE}"
fi

# ---- Load the descriptor --------------------------------------------------
transport_load "${MACHINES_FILE}"
DB_ADDR="$(transport_db_addr)"
REMOTE_DIR="$(_tv MACHINES_REMOTE_DIR)"
REMOTE_DIR="${REMOTE_DIR:-${REMOTE_BASE_DIR:-rls-dir}/scratch}"
if [[ -z "${DB_ADDR}" ]]; then
  echo "Descriptor ${MACHINES_FILE} is missing db.internal_addr." >&2
  exit 1
fi

ATTACKER_DSN="postgresql://${ATTACKER_USER}:${ATTACKER_USER}@${DB_ADDR}/${ATTACK_DB}"
ADMIN_DSN="postgresql://postgres:${POSTGRES_PASSWORD}@${DB_ADDR}/${ADMIN_DB}"

log "=== Tuple-extension reconstruction (C-R7, Table 4; mode=${MODE}) ==="
log "  RUN_ID:        ${RUN_ID}"
log "  Machines:      ${MACHINES_FILE}"
transport_summary
log "  Orderings:     ${ORDERINGS}    workers: ${WORKERS}    reps: ${REPS}"
log "  Tuple mode:    ${TX_TUPLE_MODE}  sample_tuples: ${TX_SAMPLE_TUPLES}  recompute_cal_rounds: ${TX_RECOMPUTE_CAL_ROUNDS}"
log "  Config:        ${TX_RECONSTRUCTION_CONFIG}"
log "  Output:        ${LOCAL_OUTPUT_PARENT}/${RUN_ID}/"

# Sanity: attacker has the repo/venv and the tuple-extension reconstruction config.
if ! transport_exec attacker "test -x '${REMOTE_DIR}/venv/bin/python' && echo OK" 2>/dev/null | grep -q OK; then
  echo "attacker venv not found at ${REMOTE_DIR}/venv/bin/python (machines not prepared?)." >&2
  echo "Install first:  bash orchestration/install/install_artifact_on_attacker.sh --config ${TUPLEXT_CONFIG} --machines ${MACHINES_FILE}" >&2
  exit 1
fi
if ! transport_exec attacker "test -f '${REMOTE_DIR}/${TX_RECONSTRUCTION_CONFIG}' && echo OK" 2>/dev/null | grep -q OK; then
  echo "reconstruction config ${TX_RECONSTRUCTION_CONFIG} not found on the attacker (expected git-tracked + installed)." >&2
  exit 1
fi

# ---- Ensure the dataset is loaded (1M patients, join policy) --------------
log "Stage: ensure dataset is loaded (${PATIENTS} patients, ${DOCTORS} doctors, ${SITES} sites)"
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
    --dsn '${ADMIN_DSN}' --create-db \
    --patients ${PATIENTS} --doctors ${DOCTORS} --sites ${SITES} \
    --rls-policy ${RLS_POLICY} --reset --analyze"
else
  log "  dataset already present; skipping load."
fi

# ---- Run one tuple-extension cell over the transport ----------------------
# run_cell <ordering> <workers> <rep>
CELLS_OK=0; CELLS_FAIL=0; FAILED_CELLS=()
run_cell() {
  local ord="$1" w="$2" rep="${3:-1}"
  local attrs
  attrs="$(ordering_attrs "${ord}")"
  # Render-classifiable name: <prefix>-c4[-r<rep>]-<ordering>; rep 1 omits -r.
  local wp; [[ "${w}" == "1" ]] && wp="tuplext-rc100k" || wp="tuplext-rc100kw${w}"
  local mid="c4"; [[ "${rep}" -gt 1 ]] && mid="c4-r${rep}"
  local cell_id="${wp}-${mid}-${ord}"
  local remote_out="results/${cell_id}"
  local local_dir="${LOCAL_OUTPUT_PARENT}/${RUN_ID}/${cell_id}"

  # Interactive (TTY): run over a PTY with the progress bar on; non-TTY: quiet.
  local rt="transport_exec" prog_env="" prog_flag="--no-progress-output"
  if [[ "${PROGRESS_TTY}" == "1" ]]; then
    rt="transport_exec_tty"; prog_env="export TERM=xterm-256color RLS_PROGRESS=1; "; prog_flag=""
  fi

  log "--- cell: ordering=${ord} (attrs=${attrs}, W=${w}, rep=${rep}/${REPS}) -> ${cell_id} ---"
  if "${rt}" attacker "${prog_env}cd '${REMOTE_DIR}' && mkdir -p '${remote_out}' && \
    venv/bin/python -m reconstruction \
      --attacker-dsn '${ATTACKER_DSN}' \
      --admin-dsn '${ADMIN_DSN}' \
      --config '${TX_RECONSTRUCTION_CONFIG}' \
      --attributes ${attrs} \
      --workers ${w} \
      --num-queries-per-probe ${TX_PROBE_ROUNDS} \
      --output-dir '${remote_out}' \
      --sample-tuples ${TX_SAMPLE_TUPLES} \
      --tuple-extension-mode ${TX_TUPLE_MODE} \
      --tuple-recompute-cal-rounds ${TX_RECOMPUTE_CAL_ROUNDS} \
      ${prog_flag}"; then
    transport_fetch attacker "${REMOTE_DIR}/${remote_out}/reconstruction_summary.json" \
      "${local_dir}/reconstruction_summary.json"
    log "    OK -> ${local_dir}/reconstruction_summary.json"
    CELLS_OK=$((CELLS_OK + 1))
  else
    log "    WARNING: cell ${cell_id} failed; continuing the sweep."
    transport_fetch attacker "${REMOTE_DIR}/${remote_out}/reconstruction_summary.json" \
      "${local_dir}/reconstruction_summary.json" 2>/dev/null || true
    CELLS_FAIL=$((CELLS_FAIL + 1)); FAILED_CELLS+=("${cell_id}")
  fi
}

# orderings x workers x reps.
for ord in "${ORDERING_ARR[@]}"; do
  for w in "${WORKER_ARR[@]}"; do
    for ((rep=1; rep<=REPS; rep++)); do
      run_cell "${ord}" "${w}" "${rep}"
    done
  done
done

log "=== C-R7 complete (mode=${MODE}): ${CELLS_OK} cell(s) OK, ${CELLS_FAIL} failed ==="
[[ "${CELLS_FAIL}" -gt 0 ]] && log "  failed cells: ${FAILED_CELLS[*]}"
log "  Artifacts: ${LOCAL_OUTPUT_PARENT}/${RUN_ID}/<cell>/reconstruction_summary.json"
log "  Render Table 4:  uv run python -m renderers.tuple_extension_table --results-dir ${LOCAL_OUTPUT_PARENT}/${RUN_ID} --workers ${WORKERS}"
if [[ "${CELLS_OK}" -eq 0 ]]; then
  echo "All C-R7 cells failed — see the per-cell output above." >&2
  exit 1
fi
