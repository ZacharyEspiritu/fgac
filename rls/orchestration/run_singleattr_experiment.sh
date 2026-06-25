#!/usr/bin/env bash
# Single-attribute reconstruction experiment driver (C-R6, Table 3) — split
# (provision/install/run), so it runs on the SAME shared machine infra as the
# other shared-stack claims (DB + attacker; no noise VM needed).
#
# It runs the single-attribute binary-search reconstruction attack
# (python -m reconstruction) against the shared DB over the
# provider-agnostic transport (orchestration/util/_remote_transport.sh). Configurable reps
# per column (default 1);
# reps>1 append a -r<rep> suffix that single_attribute_table.py aggregates (mean±CI).
#
# Two strategies, both driven by configs in src/reconstruction/config/:
#   binary  -> singleattr_binary.yml — run for each column x each
#              worker count W.
#   linear  -> singleattr_linear.yml — run once (W=1) per column. ssn
#              is reduced to a 100k sub-domain in that config and EXTRAPOLATED x10^4 to the full
#              ~10^9 domain by src/renderers/single_attribute_table.py; age (120) and
#              zip (100k) run their full domains.
# Config defaults preserve the claim parameters: --num-queries-per-probe <k>,
# policy/verify/oracle logging defaults, per-probe baseline ON, and no
# skip-attr/tuple/recompute.
#
# Per-cell output dirs are named so single_attribute_table.py classifies them:
#   binary: table3-binary-<col>-w<W>       linear: table3-linear-<col>-w<W>
# (<col> token = ssn|age|zip; --attributes uses the full column, zip -> zip_code).
# Artifacts land under results/table3/<RUN_ID>/<cell>/reconstruction_summary.json.
#
# MODES (identical to run_existence_experiment.sh / run_oracle_accuracy.sh)
#   attached (descriptor exists at --machines): use those machines, never tear down.
#   managed  (--machines absent and SINGLEATTR_AUTO_PROVISION!=0, the default):
#            provision a DB + attacker stack (--no-noise; C-R6 needs no noise),
#            install PostgreSQL + the repo/venv, run, tear down on exit unless
#            SINGLEATTR_SKIP_TEARDOWN=1.
#
# Usage (--config <file> and --machines <file> are REQUIRED):
#   CFG=orchestration/config/shared_config.yml
#   export RUN_ID="sa-$(date +%s | tail -c8)"; M=results/machines/${RUN_ID}.yml
#   bash orchestration/run_singleattr_experiment.sh --config "$CFG" --machines "$M"
#   # then render Table 3 from the per-cell dirs:
#   uv run python -m renderers.single_attribute_table --results-dir results/table3/${RUN_ID}
#
# Inputs:
#   --config <file>            - REQUIRED: config.yml (e.g. orchestration/config/shared_config.yml)
#   --machines <file>          - REQUIRED: machine descriptor
#   --columns <ssn,age,zip>    - override the configured columns
#   --workers <1,2,4,...>      - override the configured binary worker counts
#   --reps <N>                 - reps per column (default from config, else 1); each cell
#                                runs N times, suffixed -r<rep> when N>1 (aggregated by render)
#   --no-linear                - skip the linear baseline (run binary cells only)
#   SINGLEATTR_AUTO_PROVISION  - 1 = provision when no descriptor (default); 0 = require one
#   SINGLEATTR_SKIP_TEARDOWN   - 1 = leave managed VMs running after the run (default 0)
#   SINGLEATTR_LOCAL_OUTPUT_PARENT - local artifact parent (default results/table3)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

MACHINES_FILE_ARG=""
CONFIG_ARG=""
COLUMNS_OVERRIDE=""
WORKERS_OVERRIDE=""
REPS_OVERRIDE=""
NO_LINEAR=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machines) MACHINES_FILE_ARG="$2"; shift 2 ;;
    --config)   CONFIG_ARG="$2"; shift 2 ;;
    --columns)  COLUMNS_OVERRIDE="$2"; shift 2 ;;
    --workers)  WORKERS_OVERRIDE="$2"; shift 2 ;;
    --reps)     REPS_OVERRIDE="$2"; shift 2 ;;
    --no-linear) NO_LINEAR=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_load_singleattr_config.sh" "${CONFIG_ARG}"
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_remote_transport.sh"

RLS_POLICY="${RLS_POLICY:-join}"   # the only policy exploitable per Table 1
ATTACK_DB="${ATTACK_DB:-rls}"
ADMIN_DB="${ADMIN_DB:-rls}"
SINGLEATTR_AUTO_PROVISION="${SINGLEATTR_AUTO_PROVISION:-1}"
SINGLEATTR_SKIP_TEARDOWN="${SINGLEATTR_SKIP_TEARDOWN:-0}"
LOCAL_OUTPUT_PARENT="${SINGLEATTR_LOCAL_OUTPUT_PARENT:-results/table3}"

COLUMNS="${COLUMNS_OVERRIDE:-${SA_COLUMNS}}"
WORKERS="${WORKERS_OVERRIDE:-${SA_WORKERS}}"
PROBE_ROUNDS="${SA_PROBE_ROUNDS}"
# Reps per column: each cell (binary <col,W> and linear <col>) runs REPS times. >1
# appends a -r<rep> suffix so single_attribute_table.py aggregates them (mean±CI);
# REPS=1 (default) keeps the unsuffixed name (point value).
REPS="${REPS_OVERRIDE:-${SA_REPS}}"
if ! [[ "${REPS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "reps must be a positive integer (got: ${REPS})." >&2
  exit 1
fi
# run_linear: enabled unless the config/flag says otherwise.
RUN_LINEAR=1
SA_RUN_LINEAR_NORM="$(printf '%s' "${SA_RUN_LINEAR}" | tr '[:upper:]' '[:lower:]')"
case "${SA_RUN_LINEAR_NORM}" in 0|false|no|off) RUN_LINEAR=0 ;; esac
[[ "${NO_LINEAR}" == "1" ]] && RUN_LINEAR=0

# Column token -> the actual DB column passed to --attributes (zip -> zip_code).
attr_full() {
  case "$1" in
    ssn) echo "ssn" ;;
    age) echo "age" ;;
    zip) echo "zip_code" ;;
    *) echo "$1" ;;
  esac
}

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] $*"; }

if [[ -z "${RUN_ID:-}" ]]; then
  echo "RUN_ID must be set (e.g. export RUN_ID=\"sa-\$(date +%s | tail -c8)\")." >&2
  exit 1
fi

IFS=',' read -r -a COLUMN_ARR <<< "${COLUMNS}"
IFS=',' read -r -a WORKER_ARR <<< "${WORKERS}"
if [[ "${#COLUMN_ARR[@]}" -eq 0 ]]; then
  echo "singleattr.columns is empty." >&2
  exit 1
fi
if [[ "${#WORKER_ARR[@]}" -eq 0 ]]; then
  echo "singleattr.workers is empty." >&2
  exit 1
fi

# Show reconstruction's per-cell progress bar (util.progress.ProgressBar
# -> stderr, in-place) only when the caller's stdout is a terminal: a PTY is then
# allocated for the run so it renders live. On a non-TTY (e.g. nohup -> logfile) keep
# --no-progress-output so the log stays readable.
PROGRESS_TTY=0
[[ -t 1 ]] && PROGRESS_TTY=1

# ---- Resolve machine descriptor + run mode --------------------------------
if [[ -z "${MACHINES_FILE_ARG}" ]]; then
  echo "--machines <file> is required (e.g. --machines results/machines/${RUN_ID}.yml)." >&2
  exit 1
fi
MACHINES_FILE="${MACHINES_FILE_ARG}"
OWNED=0
if [[ -f "${MACHINES_FILE}" ]]; then
  MODE="attached"
elif [[ "${SINGLEATTR_AUTO_PROVISION}" != "0" ]]; then
  MODE="managed"; OWNED=1
else
  echo "No machine descriptor at '${MACHINES_FILE}' and SINGLEATTR_AUTO_PROVISION=0." >&2
  echo "Provision first, then re-run:" >&2
  echo "    bash orchestration/provision/provision_vms.sh --config ${SINGLEATTR_CONFIG} --output ${MACHINES_FILE} --no-noise" >&2
  echo "    bash orchestration/run_singleattr_experiment.sh --config ${SINGLEATTR_CONFIG} --machines ${MACHINES_FILE}" >&2
  exit 1
fi

teardown() {
  local rc=$?
  if [[ "${OWNED}" -eq 1 && "${SINGLEATTR_SKIP_TEARDOWN}" -eq 0 ]]; then
    log "Teardown: removing provisioned stack (${RUN_ID})"
    if [[ -f "${MACHINES_FILE}" ]]; then
      bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --machines "${MACHINES_FILE}" \
        || log "  WARNING: cleanup reported an error"
    else
      RUN_ID="${RUN_ID}" bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --config "${SINGLEATTR_CONFIG}" \
        || log "  WARNING: cleanup reported an error"
    fi
    rm -f "${MACHINES_FILE}"
  elif [[ "${OWNED}" -eq 1 ]]; then
    log "Teardown: SINGLEATTR_SKIP_TEARDOWN=1 — leaving provisioned VMs + descriptor running"
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
  bash "${SCRIPT_DIR}/provision/provision_vms.sh" --config "${SINGLEATTR_CONFIG}" --output "${MACHINES_FILE}" --no-noise
  if [[ "${DB_INSTALL_BUNDLED:-0}" == "1" ]]; then
    log "DB install is bundled with provisioning; skipping the separate DB install."
  else
    log "Installing the database (${DB_INSTALL_SCRIPT:-install_artifact_on_database.sh})."
    bash "${SCRIPT_DIR}/install/${DB_INSTALL_SCRIPT:-install_artifact_on_database.sh}" --config "${SINGLEATTR_CONFIG}" --machines "${MACHINES_FILE}"
  fi
  log "Installing repo + building venv on the attacker."
  bash "${SCRIPT_DIR}/install/install_artifact_on_attacker.sh" --config "${SINGLEATTR_CONFIG}" --machines "${MACHINES_FILE}"
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

log "=== Single-attribute reconstruction (C-R6, Table 3; mode=${MODE}) ==="
log "  RUN_ID:        ${RUN_ID}"
log "  Machines:      ${MACHINES_FILE}"
transport_summary
log "  Columns:       ${COLUMNS}    binary workers: ${WORKERS}    probe-rounds(k): ${PROBE_ROUNDS}"
log "  Linear:        $([[ "${RUN_LINEAR}" == "1" ]] && echo "yes (per column; ssn reduced + extrapolated)" || echo "no")"
log "  Output:        ${LOCAL_OUTPUT_PARENT}/${RUN_ID}/"

# Sanity: attacker has the repo/venv and reconstruction configs (git-tracked, shipped by install).
if ! transport_exec attacker "test -x '${REMOTE_DIR}/venv/bin/python' && echo OK" 2>/dev/null | grep -q OK; then
  echo "attacker venv not found at ${REMOTE_DIR}/venv/bin/python (machines not prepared?)." >&2
  echo "Install first:  bash orchestration/install/install_artifact_on_attacker.sh --config ${SINGLEATTR_CONFIG} --machines ${MACHINES_FILE}" >&2
  exit 1
fi
REQUIRED_RECONSTRUCTION_CONFIGS=("${SA_BINARY_RECONSTRUCTION_CONFIG}")
if [[ "${RUN_LINEAR}" == "1" ]]; then
  REQUIRED_RECONSTRUCTION_CONFIGS+=("${SA_LINEAR_RECONSTRUCTION_CONFIG}")
fi
for recon_config in "${REQUIRED_RECONSTRUCTION_CONFIGS[@]}"; do
  if ! transport_exec attacker "test -f '${REMOTE_DIR}/${recon_config}' && echo OK" 2>/dev/null | grep -q OK; then
    echo "reconstruction config ${recon_config} not found on the attacker (expected git-tracked + installed)." >&2
    exit 1
  fi
done

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

# ---- Run one reconstruction cell over the transport -----------------------
# run_cell <strategy:binary|linear> <col-token> <workers> <rep>
CELLS_OK=0; CELLS_FAIL=0; FAILED_CELLS=()
run_cell() {
  local strategy="$1" col="$2" w="$3" rep="${4:-1}"
  local attr
  attr="$(attr_full "${col}")"
  local recon_config cell_id rsuf=""
  [[ "${REPS}" -gt 1 ]] && rsuf="-r${rep}"   # multi-rep: -r<rep> so reps aggregate
  if [[ "${strategy}" == "linear" ]]; then
    recon_config="${SA_LINEAR_RECONSTRUCTION_CONFIG}"
    cell_id="table3-linear-${col}-w${w}${rsuf}"
  else
    recon_config="${SA_BINARY_RECONSTRUCTION_CONFIG}"
    cell_id="table3-binary-${col}-w${w}${rsuf}"
  fi
  local remote_out="results/${cell_id}"
  local local_dir="${LOCAL_OUTPUT_PARENT}/${RUN_ID}/${cell_id}"

  # Interactive (TTY): run over a PTY (transport_exec_tty) with the progress bar on
  # (drop --no-progress-output, set RLS_PROGRESS=1). Non-TTY: plain exec + --no-progress-output so
  # the logfile stays clean.
  local rt="transport_exec" prog_env="" prog_flag="--no-progress-output"
  if [[ "${PROGRESS_TTY}" == "1" ]]; then
    rt="transport_exec_tty"; prog_env="export TERM=xterm-256color RLS_PROGRESS=1; "; prog_flag=""
  fi

  log "--- cell: ${strategy} ${col} (attr=${attr}, W=${w}, k=${PROBE_ROUNDS}, config=${recon_config}, rep=${rep}/${REPS}) -> ${cell_id} ---"
  if "${rt}" attacker "${prog_env}cd '${REMOTE_DIR}' && mkdir -p '${remote_out}' && \
    venv/bin/python -m reconstruction \
      --attacker-dsn '${ATTACKER_DSN}' \
      --admin-dsn '${ADMIN_DSN}' \
      --config '${recon_config}' \
      --attributes ${attr} \
      --workers ${w} \
      --num-queries-per-probe ${PROBE_ROUNDS} \
      --output-dir '${remote_out}' \
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

# Binary search: columns x workers x reps.
for col in "${COLUMN_ARR[@]}"; do
  for w in "${WORKER_ARR[@]}"; do
    for ((rep=1; rep<=REPS; rep++)); do
      run_cell binary "${col}" "${w}" "${rep}"
    done
  done
done

# Linear baseline: one run (W=1) per column x reps. ssn is reduced in the linear
# config and extrapolated by the renderer; age/zip run full domains.
if [[ "${RUN_LINEAR}" == "1" ]]; then
  for col in "${COLUMN_ARR[@]}"; do
    for ((rep=1; rep<=REPS; rep++)); do
      run_cell linear "${col}" 1 "${rep}"
    done
  done
fi

log "=== C-R6 complete (mode=${MODE}): ${CELLS_OK} cell(s) OK, ${CELLS_FAIL} failed ==="
[[ "${CELLS_FAIL}" -gt 0 ]] && log "  failed cells: ${FAILED_CELLS[*]}"
log "  Artifacts: ${LOCAL_OUTPUT_PARENT}/${RUN_ID}/<cell>/reconstruction_summary.json"
log "  Render Table 3:  uv run python -m renderers.single_attribute_table --results-dir ${LOCAL_OUTPUT_PARENT}/${RUN_ID}"
# A wholesale failure (no cell produced a summary) is a hard error.
if [[ "${CELLS_OK}" -eq 0 ]]; then
  echo "All C-R6 cells failed — see the per-cell output above." >&2
  exit 1
fi
