#!/usr/bin/env bash
# Existence-family microbenchmark experiment driver (Figures 2 & 4).
#
# This script runs the EXPERIMENT against machines described by a "machine
# descriptor" — it no longer provisions GCP VMs itself. Provisioning is a
# separate, provider-specific step (orchestration/provision/provision_vms.sh) that emits
# the descriptor; this driver consumes it and talks to the machines through a
# provider-agnostic transport (orchestration/util/_remote_transport.sh). The experiment is
# therefore not tied to Google Cloud: point it at a descriptor for any two
# machines (MACHINES_TRANSPORT=ssh) and it runs unchanged.
#
# --job (REQUIRED) selects which job (both use the same 3 query classes —
# nonexistent / authorized / unauthorized — and the same machines):
#   existence (default) -> python -m microbenchmarks.run_existence_distribution --query equality -> existence_kde.pgf       (Figure 2)
#   range               -> python -m microbenchmarks.run_existence_distribution --query range    -> existence_range_kde.pgf (Figure 4)
#
# MODES
#   attached (a descriptor file already exists at the --machines path): use those
#            machines as-is and never tear them down — their lifecycle belongs to
#            whoever provisioned them.
#   managed  (the --machines path does not exist yet and EXISTENCE_AUTO_PROVISION!=0,
#            the default; the descriptor is created there): provision
#            a DB + attacker GCP stack via orchestration/provision/provision_vms.sh --no-noise,
#            install PostgreSQL via orchestration/install/install_artifact_on_database.sh, install
#            the repo + venv via orchestration/install/install_artifact_on_attacker.sh, run, and
#            tear it down on exit unless EXISTENCE_SKIP_TEARDOWN=1.
#
# Usage (--config <file>, --machines <file>, and --job existence|range are REQUIRED):
#   CFG=orchestration/config/shared_config.yml
#   export RUN_ID="exist-$(date +%s | tail -c8)"; M=results/machines/${RUN_ID}.yml
#   # One command, end-to-end (managed): provision -> install DB + attacker -> run -> teardown.
#   bash orchestration/run_existence_experiment.sh --config "$CFG" --machines "$M" --job existence   # Figure 2
#   bash orchestration/run_existence_experiment.sh --config "$CFG" --machines "$M" --job range       # Figure 3
#
#   # Separated (attached): provision bare VMs, install the DB + the code, then run.
#   bash orchestration/provision/provision_vms.sh      --config "$CFG" --output "$M" --no-noise
#   bash orchestration/install/install_artifact_on_database.sh --config "$CFG" --machines "$M"
#   bash orchestration/install/install_artifact_on_attacker.sh --config "$CFG" --machines "$M"
#   bash orchestration/run_existence_experiment.sh     --config "$CFG" --machines "$M" --job existence
#   bash orchestration/provision/cleanup_vms.sh    --machines "$M"
#
# Config (machine specs, dataset, sample counts, etc.) is read from the REQUIRED
# --config <file> (e.g. orchestration/config/shared_config.yml). Inputs:
#   --config <file>            - REQUIRED: config.yml to read
#   --job <existence|range>    - REQUIRED: which job to run (Figure 2 / Figure 3)
#   --machines <file>          - REQUIRED: machine descriptor (e.g. results/machines/<RUN_ID>.yml)
#   EXISTENCE_AUTO_PROVISION   - 1 = provision when no descriptor (default); 0 = require one
#   EXISTENCE_SKIP_TEARDOWN    - 1 = leave managed VMs running after the run (default 0)
#   EXISTENCE_LOCAL_OUTPUT_DIR - local artifact dir (default results/<job>/<RUN_ID>)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"   # relative paths (artifact download, local renders) resolve here

MACHINES_FILE_ARG=""
JOB_ARG=""
CONFIG_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --job)      JOB_ARG="$2"; shift 2 ;;
    --machines) MACHINES_FILE_ARG="$2"; shift 2 ;;
    --config)   CONFIG_ARG="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Load config from the --config file (REQUIRED); sets the
# env + CONFIG_FILE=/dev/null so the shared GCP scripts use it.
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_load_existence_config.sh" "${CONFIG_ARG}"
# Machine addressing comes from the descriptor (not _run_id_overlay.sh).
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_remote_transport.sh"

RLS_POLICY="${RLS_POLICY:-join}"
DB_ENGINE="postgres"
# --job is required (no default): the caller must explicitly pick existence or range.
EXISTENCE_JOB="${JOB_ARG}"
EXISTENCE_SKIP_TEARDOWN="${EXISTENCE_SKIP_TEARDOWN:-0}"
EXISTENCE_AUTO_PROVISION="${EXISTENCE_AUTO_PROVISION:-1}"
ATTACK_DB="${ATTACK_DB:-rls}"
ADMIN_DB="${ADMIN_DB:-rls}"

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] $*"; }

if [[ -z "${RUN_ID:-}" ]]; then
  echo "RUN_ID must be set (e.g. export RUN_ID=\"exist-\$(date +%s | tail -c8)\")." >&2
  exit 1
fi
if [[ -z "${EXISTENCE_JOB}" ]]; then
  echo "--job is required: pass --job existence (Figure 2) or --job range (Figure 3)." >&2
  exit 1
fi

case "${EXISTENCE_JOB}" in
  existence) JOB_SAMPLES="${EXISTENCE_SAMPLES:-500}";     JOB_FIGURE="existence_kde.pgf";       JOB_CSV="existence_latency.csv" ;;
  range)     JOB_SAMPLES="${RANGE_ATTACK_SAMPLES:-500}";  JOB_FIGURE="existence_range_kde.pgf"; JOB_CSV="existence_range_latency.csv" ;;
  *) echo "Unsupported --job: '${EXISTENCE_JOB}' (expected 'existence' or 'range')." >&2; exit 1 ;;
esac
LOCAL_OUTPUT_DIR="${EXISTENCE_LOCAL_OUTPUT_DIR:-results/${EXISTENCE_JOB}/${RUN_ID}}"
if [[ "${JOB_SAMPLES}" =~ ^[0-9]+$ ]]; then PROBES="$((3 * JOB_SAMPLES))"; else PROBES="?"; fi

# ---- Resolve machine descriptor + run mode --------------------------------
if [[ -z "${MACHINES_FILE_ARG}" ]]; then
  echo "--machines <file> is required (e.g. --machines results/machines/${RUN_ID}.yml)." >&2
  exit 1
fi
MACHINES_FILE="${MACHINES_FILE_ARG}"
OWNED=0
if [[ -f "${MACHINES_FILE}" ]]; then
  MODE="attached"
elif [[ "${EXISTENCE_AUTO_PROVISION}" != "0" ]]; then
  MODE="managed"; OWNED=1
else
  echo "No machine descriptor at '${MACHINES_FILE}' and EXISTENCE_AUTO_PROVISION=0." >&2
  echo "Provision first, then re-run:" >&2
  echo "    bash orchestration/provision/provision_vms.sh --config ${EXISTENCE_CONFIG} --output ${MACHINES_FILE} --no-noise" >&2
  echo "    bash orchestration/run_existence_experiment.sh --config ${EXISTENCE_CONFIG} --machines ${MACHINES_FILE} --job ${EXISTENCE_JOB}" >&2
  exit 1
fi

teardown() {
  local rc=$?
  if [[ "${OWNED}" -eq 1 && "${EXISTENCE_SKIP_TEARDOWN}" -eq 0 ]]; then
    log "Teardown: removing provisioned stack (${RUN_ID})"
    if [[ -f "${MACHINES_FILE}" ]]; then
      bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --machines "${MACHINES_FILE}" \
        || log "  WARNING: cleanup reported an error"
    else
      # Provisioning failed before writing the descriptor; clean by RUN_ID + config.
      RUN_ID="${RUN_ID}" bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --config "${EXISTENCE_CONFIG}" \
        || log "  WARNING: cleanup reported an error"
    fi
    rm -f "${MACHINES_FILE}"
  elif [[ "${OWNED}" -eq 1 ]]; then
    log "Teardown: EXISTENCE_SKIP_TEARDOWN=1 — leaving provisioned VMs + descriptor running"
    log "  Descriptor: ${MACHINES_FILE}"
    log "  Clean up later: bash orchestration/provision/cleanup_vms.sh --machines ${MACHINES_FILE}"
  else
    log "Teardown: attached to externally-provisioned machines — leaving them untouched."
  fi
  exit "${rc}"
}
trap teardown EXIT

# ---- Provision + install (managed mode only) ------------------------------
# Managed mode chains the otherwise-separate steps: provision bare VMs, install
# PostgreSQL on the DB, install the repo + venv on the attacker, then fall
# through to the experiment — the one-command flow.
if [[ "${MODE}" == "managed" ]]; then
  log "No descriptor at ${MACHINES_FILE}; provisioning a GCP stack (managed mode, --no-noise)."
  bash "${SCRIPT_DIR}/provision/provision_vms.sh" --config "${EXISTENCE_CONFIG}" --output "${MACHINES_FILE}" --no-noise
  if [[ "${DB_INSTALL_BUNDLED:-0}" == "1" ]]; then
    log "DB install is bundled with provisioning; skipping the separate DB install."
  else
    log "Installing the database (${DB_INSTALL_SCRIPT:-install_artifact_on_database.sh})."
    bash "${SCRIPT_DIR}/install/${DB_INSTALL_SCRIPT:-install_artifact_on_database.sh}" --config "${EXISTENCE_CONFIG}" --machines "${MACHINES_FILE}"
  fi
  log "Installing repo + building venv on the attacker."
  bash "${SCRIPT_DIR}/install/install_artifact_on_attacker.sh" --config "${EXISTENCE_CONFIG}" --machines "${MACHINES_FILE}"
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

log "=== Existence-family Microbenchmark (job=${EXISTENCE_JOB}, mode=${MODE}) ==="
log "  RUN_ID:        ${RUN_ID}"
log "  Job:           ${EXISTENCE_JOB} -> ${JOB_FIGURE}"
log "  Machines:      ${MACHINES_FILE}"
transport_summary
log "  RLS policy:    ${RLS_POLICY}"
log "  DB address:    ${DB_ADDR}  (attacker -> DB)"
log "  Remote dir:    ${REMOTE_DIR}"
log "  Samples/type:  ${JOB_SAMPLES}  (3 classes => ${PROBES} timed probes)"
log "  Output dir:    ${LOCAL_OUTPUT_DIR}"

# Sanity-check the attacker actually has the repo + venv (catches an attached
# run against machines that were provisioned but never synced).
if ! transport_exec attacker "test -x '${REMOTE_DIR}/venv/bin/python' && echo OK" 2>/dev/null | grep -q OK; then
  echo "Attacker venv not found at ${REMOTE_DIR}/venv/bin/python (machines not prepared?)." >&2
  echo "Sync the repo + venv first:  bash orchestration/install/install_artifact_on_attacker.sh --machines ${MACHINES_FILE}" >&2
  exit 1
fi

# ---- Stage 5: load dataset ------------------------------------------------
ADMIN_DSN="postgresql://postgres:${POSTGRES_PASSWORD}@${DB_ADDR}/${ADMIN_DB}"
ATTACKER_DSN="postgresql://${ATTACKER_USER}:${ATTACKER_USER}@${DB_ADDR}/${ATTACK_DB}"

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

# ---- Stage 6: run the selected existence-family timing attack -------------
log "Stage 6: run ${EXISTENCE_JOB} timing attack (rls-policy=${RLS_POLICY}, samples/type=${JOB_SAMPLES})"
artifacts=()
case "${EXISTENCE_JOB}" in
  existence)
    EXISTENCE_FLAGS=""
    if [[ "${EXISTENCE_FAST}" == "1" ]]; then
      EXISTENCE_FLAGS+=" --fast"
    fi
    if [[ "${EXISTENCE_WARM_CACHE}" == "1" ]]; then
      EXISTENCE_FLAGS+=" --warm-cache"
    fi
    if [[ -n "${EXISTENCE_AUTH_CARD}" ]]; then
      EXISTENCE_FLAGS+=" --authorized-cardinality ${EXISTENCE_AUTH_CARD}"
    fi
    if [[ -n "${EXISTENCE_UNAUTH_CARD}" ]]; then
      EXISTENCE_FLAGS+=" --unauthorized-cardinality ${EXISTENCE_UNAUTH_CARD}"
    fi
    if [[ -n "${EXISTENCE_PLOT_FORMAT}" ]]; then
      EXISTENCE_FLAGS+=" --plot-format ${EXISTENCE_PLOT_FORMAT}"
    fi
    command_value="venv/bin/python -m microbenchmarks.run_existence_distribution \
      --query equality \
      --admin-dsn '${ADMIN_DSN}' \
      --attacker-dsn '${ATTACKER_DSN}' \
      --attacker-user '${ATTACKER_USER}' \
      --rls-policy ${RLS_POLICY} \
      --samples ${EXISTENCE_SAMPLES} \
      --output ${EXISTENCE_OUTPUT} \
      --plot-output ${EXISTENCE_PLOT_OUTPUT} \
      --seed ${EXISTENCE_SEED}${EXISTENCE_FLAGS} \
      --explain"
    artifacts=("${EXISTENCE_PLOT_OUTPUT}" "${EXISTENCE_OUTPUT}")
    ;;
  range)
    RANGE_ATTACK_FLAGS=""
    if [[ "${RANGE_ATTACK_FAST}" == "1" ]]; then
      RANGE_ATTACK_FLAGS+=" --fast"
    fi
    if [[ "${RANGE_ATTACK_WARM_CACHE}" == "1" ]]; then
      RANGE_ATTACK_FLAGS+=" --warm-cache"
    fi
    if [[ "${RANGE_ATTACK_EXPLAIN}" == "1" ]]; then
      RANGE_ATTACK_FLAGS+=" --explain"
    fi
    if [[ -n "${RANGE_ATTACK_PLOT_FORMAT}" ]]; then
      RANGE_ATTACK_FLAGS+=" --plot-format ${RANGE_ATTACK_PLOT_FORMAT}"
    fi
    command_value="venv/bin/python -m microbenchmarks.run_existence_distribution \
      --query range \
      --admin-dsn '${ADMIN_DSN}' \
      --attacker-dsn '${ATTACKER_DSN}' \
      --attacker-user '${ATTACKER_USER}' \
      --rls-policy ${RLS_POLICY} \
      --samples ${RANGE_ATTACK_SAMPLES} \
      --range-width ${RANGE_ATTACK_WIDTH} \
      --max-tries ${RANGE_ATTACK_MAX_TRIES} \
      --nonexistent-offset ${RANGE_ATTACK_NONEXISTENT_OFFSET} \
      --seed ${RANGE_ATTACK_SEED} \
      --output ${RANGE_ATTACK_OUTPUT} \
      --plot-output ${RANGE_ATTACK_PLOT_OUTPUT}${RANGE_ATTACK_FLAGS}"
    artifacts=("${RANGE_ATTACK_PLOT_OUTPUT}" "${RANGE_ATTACK_OUTPUT}")
    ;;
esac
command_value="${command_value//\"/\\\"}"   # escape double quotes for the remote shell
log "  Command: ${command_value}"
transport_exec_tty attacker "cd '${REMOTE_DIR}' && ${command_value}"

# Fetch this job's artifacts to results/<engine>/<policy>/<job>/...
remote_path() { local p="$1"; if [[ "${p}" = /* ]]; then printf '%s' "${p}"; else printf '%s' "${REMOTE_DIR}/${p}"; fi; }
ARTIFACT_BASE_DIR="results/${DB_ENGINE}/${RLS_POLICY}/${EXISTENCE_JOB}"
for artifact in ${artifacts[@]+"${artifacts[@]}"}; do
  [[ -z "${artifact}" ]] && continue
  if [[ "${artifact}" == *"=>"* ]]; then
    remote_artifact="${artifact%%=>*}"; local_artifact="${artifact#*=>}"
  else
    remote_artifact="${artifact}"; local_artifact="${artifact}"
  fi
  if [[ "${local_artifact}" = /* ]]; then
    local_dest="${local_artifact}"
  else
    local_dest="${ARTIFACT_BASE_DIR}/${local_artifact}"
  fi
  log "  download $(remote_path "${remote_artifact}") -> ${local_dest}"
  transport_fetch attacker "$(remote_path "${remote_artifact}")" "${local_dest}"
done

# ---- Stage 7: collect THIS run's artifacts into a tidy local dir ----------
# Copy ONLY the figure + CSV this job just produced. (Never blanket-copy the
# shared download dir — it can hold stale files from earlier runs of the same
# job, which would land next to this run's outputs.)
log "Stage 7: collect artifacts into ${LOCAL_OUTPUT_DIR}/"
mkdir -p "${LOCAL_OUTPUT_DIR}"
SRC_DIR="${ARTIFACT_BASE_DIR}/results"
for f in "${JOB_FIGURE}" "${JOB_CSV}"; do
  if [[ -f "${SRC_DIR}/${f}" ]]; then
    cp "${SRC_DIR}/${f}" "${LOCAL_OUTPUT_DIR}/"
    log "  ${LOCAL_OUTPUT_DIR}/${f}"
  else
    log "  WARNING: expected artifact missing: ${SRC_DIR}/${f}"
  fi
done

log "=== Existence-family experiment ${RUN_ID} complete (job=${EXISTENCE_JOB}, mode=${MODE}) ==="
log "  Figure: ${LOCAL_OUTPUT_DIR}/${JOB_FIGURE}"
log "  Output: ${LOCAL_OUTPUT_DIR}/"
