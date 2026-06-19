#!/usr/bin/env bash
# Cross-zone (cross-region) Table-1 experiment driver (C-R4, Table 2) — split
# (provision/install/run). The C-R4 counterpart of orchestration/run_oracle_accuracy.sh.
#
# It runs the EXPERIMENT against machines described by a "machine descriptor";
# provisioning is the separate provider-specific step (orchestration/provision/provision_vms.sh,
# fed orchestration/config/crosszone_config.yml, which places the attacker in a DIFFERENT
# zone). This driver drives the DB + attacker + noise over the provider-agnostic
# transport (orchestration/util/_remote_transport.sh), which already routes each role to its own
# zone, so the attack traverses the WAN as in the original C-R4 flow.
#
# It reuses the C-R3 Table-1 machinery (orchestration/noise/run_oracle_accuracy_with_noise_controller.sh + the Python
# drivers), running ONE join sweep whose noise sweep is the two C-R4 cells:
#   baseline:0:0                   -> Base  (no background load, controller off)
#   cpu50:96:5000:50               -> cpu50 (50% DB CPU via 96 noise clients, controller on)
# Then it assembles the comparison_input/ layout and renders Table 2
# (cross_zone_comparison.py, cross-region vs same-zone paired diff).
#
# MODES (identical to run_oracle_accuracy.sh)
#   attached (descriptor exists at --machines): use those machines, never tear down.
#   managed  (--machines absent and CROSSZONE_AUTO_PROVISION!=0, the default):
#            provision the cross-zone stack (DB + attacker[remote] + noise) via
#            provision/provision_vms.sh, install all three roles, run, and tear down on
#            exit unless CROSSZONE_SKIP_TEARDOWN=1.
#
# Usage (--config <file> and --machines <file> are REQUIRED):
#   CFG=orchestration/config/crosszone_config.yml
#   export RUN_ID="cz-$(date +%s | tail -c8)"; M=results/machines/${RUN_ID}.yml
#   # One command (managed): provision -> install x3 -> run Base+cpu50 -> render -> teardown.
#   bash orchestration/run_crosszone_experiment.sh --config "$CFG" --machines "$M"
#
#   # Separated (attached): provision the cross-zone stack, install, then run.
#   bash orchestration/provision/provision_vms.sh            --config "$CFG" --output "$M"
#   bash orchestration/install/install_artifact_on_database.sh --config "$CFG" --machines "$M"
#   bash orchestration/install/install_artifact_on_attacker.sh --config "$CFG" --machines "$M"
#   bash orchestration/install/install_artifact_on_noise.sh    --config "$CFG" --machines "$M"
#   bash orchestration/run_crosszone_experiment.sh     --config "$CFG" --machines "$M"
#   bash orchestration/provision/cleanup_vms.sh    --machines "$M"
#
# Inputs:
#   --config <file>            - REQUIRED: config.yml (orchestration/config/crosszone_config.yml)
#   --machines <file>          - REQUIRED: machine descriptor (must include a noise role)
#   --noise-sweep <spec>       - override the configured Base+cpu50 sweep
#   CROSSZONE_AUTO_PROVISION   - 1 = provision when no descriptor (default); 0 = require one
#   CROSSZONE_SKIP_TEARDOWN    - 1 = leave managed VMs running after the run (default 0)
#   CROSSZONE_SKIP_RENDER      - 1 = run the cells but skip the Table 2 render (default 0)
#   CROSSZONE_LOCAL_OUTPUT_PARENT - local artifact parent (default results/table2)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"   # relative paths (artifact download, local render) resolve here

MACHINES_FILE_ARG=""
CONFIG_ARG=""
NOISE_SWEEP_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machines)    MACHINES_FILE_ARG="$2"; shift 2 ;;
    --config)      CONFIG_ARG="$2"; shift 2 ;;
    --noise-sweep) NOISE_SWEEP_OVERRIDE="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_load_crosszone_config.sh" "${CONFIG_ARG}"
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_remote_transport.sh"
# shellcheck source=/dev/null
. "${SCRIPT_DIR}/util/_local_preflight.sh"

RLS_POLICY="${RLS_POLICY:-join}"   # the only policy exploitable per Table 1
DB_ENGINE="postgres"
CROSSZONE_AUTO_PROVISION="${CROSSZONE_AUTO_PROVISION:-1}"
CROSSZONE_SKIP_TEARDOWN="${CROSSZONE_SKIP_TEARDOWN:-0}"
CROSSZONE_SKIP_RENDER="${CROSSZONE_SKIP_RENDER:-0}"
ATTACK_DB="${ATTACK_DB:-rls}"
ADMIN_DB="${ADMIN_DB:-rls}"
LOCAL_OUTPUT_PARENT="${CROSSZONE_LOCAL_OUTPUT_PARENT:-results/table2}"
NOISE_SWEEP="${NOISE_SWEEP_OVERRIDE:-${CZ_NOISE_SWEEP}}"

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] $*"; }

if [[ -z "${RUN_ID:-}" ]]; then
  echo "RUN_ID must be set (e.g. export RUN_ID=\"cz-\$(date +%s | tail -c8)\")." >&2
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
elif [[ "${CROSSZONE_AUTO_PROVISION}" != "0" ]]; then
  MODE="managed"; OWNED=1
else
  echo "No machine descriptor at '${MACHINES_FILE}' and CROSSZONE_AUTO_PROVISION=0." >&2
  echo "Provision first, then re-run:" >&2
  echo "    bash orchestration/provision/provision_vms.sh --config ${CROSSZONE_CONFIG} --output ${MACHINES_FILE}" >&2
  echo "    bash orchestration/run_crosszone_experiment.sh --config ${CROSSZONE_CONFIG} --machines ${MACHINES_FILE}" >&2
  exit 1
fi

teardown() {
  local rc=$?
  if [[ "${OWNED}" -eq 1 && "${CROSSZONE_SKIP_TEARDOWN}" -eq 0 ]]; then
    log "Teardown: removing provisioned cross-zone stack (${RUN_ID})"
    if [[ -f "${MACHINES_FILE}" ]]; then
      bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --machines "${MACHINES_FILE}" --delete-network \
        || log "  WARNING: cleanup reported an error"
    else
      RUN_ID="${RUN_ID}" bash "${SCRIPT_DIR}/provision/cleanup_vms.sh" --config "${CROSSZONE_CONFIG}" --delete-network \
        || log "  WARNING: cleanup reported an error"
    fi
    rm -f "${MACHINES_FILE}"
  elif [[ "${OWNED}" -eq 1 ]]; then
    log "Teardown: CROSSZONE_SKIP_TEARDOWN=1 — leaving provisioned VMs + descriptor running"
    log "  Clean up later: bash orchestration/provision/cleanup_vms.sh --machines ${MACHINES_FILE} --delete-network"
  else
    log "Teardown: attached to externally-provisioned machines — leaving them untouched."
  fi
  exit "${rc}"
}
trap teardown EXIT

# ---- Provision + install (managed mode only) — DB + attacker[remote] + noise --
if [[ "${MODE}" == "managed" ]]; then
  log "No descriptor at ${MACHINES_FILE}; provisioning a cross-zone GCP stack (managed mode)."
  bash "${SCRIPT_DIR}/provision/provision_vms.sh" --config "${CROSSZONE_CONFIG}" --output "${MACHINES_FILE}"
  if [[ "${DB_INSTALL_BUNDLED:-0}" == "1" ]]; then
    log "DB install is bundled with provisioning; skipping the separate DB install."
  else
    log "Installing the database (${DB_INSTALL_SCRIPT:-install_artifact_on_database.sh})."
    bash "${SCRIPT_DIR}/install/${DB_INSTALL_SCRIPT:-install_artifact_on_database.sh}" --config "${CROSSZONE_CONFIG}" --machines "${MACHINES_FILE}"
  fi
  log "Installing repo + building venv on the attacker."
  bash "${SCRIPT_DIR}/install/install_artifact_on_attacker.sh" --config "${CROSSZONE_CONFIG}" --machines "${MACHINES_FILE}"
  log "Installing repo + building venv on the noise VM."
  bash "${SCRIPT_DIR}/install/install_artifact_on_noise.sh" --config "${CROSSZONE_CONFIG}" --machines "${MACHINES_FILE}"
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
  echo "Descriptor ${MACHINES_FILE} has no 'noise:' role — the cpu50 cell needs a noise generator." >&2
  echo "Re-provision WITHOUT --no-noise:  bash orchestration/provision/provision_vms.sh --config ${CROSSZONE_CONFIG} --output ${MACHINES_FILE}" >&2
  exit 1
fi

log "=== Cross-zone Table-1 experiment (C-R4, mode=${MODE}) ==="
log "  RUN_ID:        ${RUN_ID}"
log "  Machines:      ${MACHINES_FILE}"
transport_summary
log "  DB address:    ${DB_ADDR}  (attacker[cross-region]/noise -> DB)"
log "  Cells (sweep): ${NOISE_SWEEP}"
log "  Probes/cell:   ${CZ_PROBES}   k=${CZ_K_VALUES}   calibration=${CZ_CALIBRATION_MODE}"

# Sanity-check the attacker + noise have the repo + venv.
for role in attacker noise; do
  if ! transport_exec "${role}" "test -x '${REMOTE_DIR}/venv/bin/python' && echo OK" 2>/dev/null | grep -q OK; then
    echo "${role} venv not found at ${REMOTE_DIR}/venv/bin/python (machines not prepared?)." >&2
    echo "Install first:  bash orchestration/install/install_artifact_on_${role}.sh --config ${CROSSZONE_CONFIG} --machines ${MACHINES_FILE}" >&2
    exit 1
  fi
done

# ---- Ensure the dataset is loaded from the DB-local noise VM ----------------
# The C-R4 attacker is deliberately cross-region from the DB. Loading the 1M-row
# dataset from that attacker would stream the COPY data over the WAN before the
# experiment even starts. The noise VM is co-located with the DB and already has
# the repo + venv, so use it for setup only; the actual attack still runs from
# the cross-region attacker below.
ADMIN_DSN="postgresql://postgres:${POSTGRES_PASSWORD}@${DB_ADDR}/${ADMIN_DB}"
log "Stage: ensure dataset is loaded (${PATIENTS} patients, ${DOCTORS} doctors, ${SITES} sites)"
ALREADY="$(transport_exec noise \
  "PGPASSWORD='${POSTGRES_PASSWORD}' psql -h '${DB_ADDR}' -U postgres -d ${ADMIN_DB} -tAc 'SELECT count(*) FROM patients;' 2>/dev/null || echo 0" \
  2>/dev/null | tr -d '\r ')" || ALREADY="0"
ALREADY="${ALREADY//[!0-9]/}"; [[ -z "${ALREADY}" ]] && ALREADY=0
log "  patients table has ${ALREADY} rows (target ${PATIENTS})"
if [[ "${ALREADY}" -lt "${PATIENTS}" ]]; then
  log "  loading dataset on the noise VM (same zone as DB)..."
  transport_exec noise "cd '${REMOTE_DIR}' && venv/bin/python -m patients.setup_db \
    --dsn '${ADMIN_DSN}' \
    --create-db \
    --patients ${PATIENTS} \
    --doctors ${DOCTORS} \
    --sites ${SITES} \
    --seed ${CZ_SEED} \
    --rls-policy ${RLS_POLICY} \
    --reset \
    --analyze"
else
  log "  dataset already present; skipping load."
fi

# ---- Stage the doctor user pool on both experiment VMs ---------------------
USERS_REL="data/doctors.csv"
USERS_REMOTE="${REMOTE_DIR}/${USERS_REL}"
log "Stage: stage ${USERS_REL} on attacker + noise"
log "  refreshing ${USERS_REL} on noise from the doctors table"
transport_exec noise "mkdir -p '$(dirname "${USERS_REMOTE}")' && \
  PGPASSWORD='${POSTGRES_PASSWORD}' psql -h '${DB_ADDR}' -U postgres -d ${ADMIN_DB} \
    -c \"COPY (SELECT user_name, user_name AS password, site_id FROM doctors ORDER BY user_name) TO STDOUT WITH CSV HEADER\" \
    > '${USERS_REMOTE}'"
if ! transport_exec noise "test -f '${USERS_REMOTE}'" >/dev/null 2>&1; then
  echo "Expected ${USERS_REMOTE} on the noise VM after dataset setup, but it is missing." >&2
  exit 1
fi
USERS_TMP="$(mktemp -t rls_doctors_XXXXXX.csv)"
transport_fetch noise "${USERS_REMOTE}" "${USERS_TMP}"
transport_exec attacker "mkdir -p '$(dirname "${USERS_REMOTE}")'" >/dev/null
transport_push attacker "${USERS_TMP}" "${USERS_REMOTE}"
rm -f "${USERS_TMP}"

# ---- Run the Base + cpu50 cells (one join sweep) via the wrapper ----------
# The wrapper drives the VMs over the transport because MACHINES_FILE is exported;
# the cross-region attacker is reached at its own zone by the transport. The cpu50
# cell engages the closed-loop DB-CPU controller (range-query noise, target 50%).
export MACHINES_FILE REMOTE_BASE_DIR DB_ENGINE
export ATTACKER_USER ATTACKER_PASSWORD ATTACK_DB ADMIN_DB POSTGRES_PASSWORD
export TABLE1_USERS_FILE="data/doctors.csv"
export TABLE1_FAST="1" TABLE1_WARM_CACHE="1" TABLE1_ENABLE_DB_METRICS="1"
export TABLE1_SKIP_WARMUP="${CZ_SKIP_WARMUP}"
export TABLE1_SEED="${CZ_SEED}" TABLE1_NONEXISTENT_OFFSET="${CZ_NONEXISTENT_OFFSET}"
export TABLE1_NOISE_QUERY_MODE="${CZ_NOISE_QUERY_MODE}"
export TABLE1_NOISE_RANGE_WIDTH="${CZ_NOISE_RANGE_WIDTH}"
export TABLE1_NOISE_DB_CPU_TOLERANCE_PCT="${CZ_CPU_TOLERANCE_PCT}"
export TABLE1_NOISE_READY_TIMEOUT="${CZ_CPU_READY_TIMEOUT}"
export TABLE1_NOISE_DB_CPU_SAMPLE_SECONDS="${CZ_CPU_SAMPLE_SECONDS}"
export TABLE1_NOISE_DB_CPU_READY_STREAK="${CZ_CPU_READY_STREAK}"
export TABLE1_NOISE_DB_CPU_GAIN="${CZ_CPU_GAIN}"

OUTPUT_DIR="${LOCAL_OUTPUT_PARENT}/${RUN_ID}"
log "=== Running cells ${NOISE_SWEEP} -> ${OUTPUT_DIR} ==="
bash "${SCRIPT_DIR}/noise/run_oracle_accuracy_with_noise_controller.sh" \
  --db-engine postgres \
  --policies "${RLS_POLICY}" \
  --k-values "${CZ_K_VALUES}" \
  --probes "${CZ_PROBES}" \
  --calibration-mode "${CZ_CALIBRATION_MODE}" \
  --local-output-dir "${OUTPUT_DIR}" \
  --noise-sweep "${NOISE_SWEEP}"
log "  cells complete -> ${OUTPUT_DIR}"

# ---- Assemble comparison_input/ + render Table 2 --------------------------
# cross_zone_comparison.py reads <scenario>/tab1cross_<scenario>_summary.csv
# from the cross-region dir; the wrapper wrote <scenario>/table1_summary.csv, so
# copy them into the layout it expects, then render the cross-vs-same-zone diff.
CIN="${OUTPUT_DIR}/comparison_input"
log "Stage: assemble comparison_input/ (${CZ_RENDER_SCENARIOS})"
for cell in ${CZ_RENDER_SCENARIOS//,/ }; do
  if [[ -f "${OUTPUT_DIR}/${cell}/table1_summary.csv" ]]; then
    mkdir -p "${CIN}/${cell}"
    cp "${OUTPUT_DIR}/${cell}/table1_summary.csv" "${CIN}/${cell}/tab1cross_${cell}_summary.csv"
    [[ -f "${OUTPUT_DIR}/${cell}/table1_noise.json" ]] && cp "${OUTPUT_DIR}/${cell}/table1_noise.json" "${CIN}/${cell}/table1_noise.json"
  else
    log "  WARNING: ${OUTPUT_DIR}/${cell}/table1_summary.csv missing — render may be incomplete."
  fi
done

if [[ "${CROSSZONE_SKIP_RENDER}" == "1" ]]; then
  log "Stage: render SKIPPED (CROSSZONE_SKIP_RENDER=1)"
elif [[ ! -d "${CZ_SAME_ZONE_BASELINE_DIR}" ]]; then
  log "Stage: render SKIPPED — same-zone baseline dir not found: ${CZ_SAME_ZONE_BASELINE_DIR}"
  log "  Build it with: unfilter-rls claims run C-R3"
  log "  Then render with:"
  log "    uv run python -m renderers.cross_zone_comparison --cross-region-dir ${CIN} --same-zone-dir ${CZ_SAME_ZONE_BASELINE_DIR} --cross-region-scenarios ${CZ_RENDER_SCENARIOS} --same-zone-scenarios ${CZ_RENDER_SCENARIOS} --k-values ${CZ_K_VALUES} --mode paired-diff --output-prefix ${OUTPUT_DIR}/table2"
else
  log "Stage: render Table 2 (cross-region vs same-zone paired diff)"
  if rls_uv_run_module renderers.cross_zone_comparison \
       --cross-region-dir "${CIN}" \
       --same-zone-dir "${CZ_SAME_ZONE_BASELINE_DIR}" \
       --cross-region-scenarios "${CZ_RENDER_SCENARIOS}" \
       --same-zone-scenarios "${CZ_RENDER_SCENARIOS}" \
       --k-values "${CZ_K_VALUES}" --mode paired-diff \
       --output-prefix "${OUTPUT_DIR}/table2"; then
    log "  rendered ${OUTPUT_DIR}/table2.{md,csv,png,pdf,pgf}"
    # Publish the camera-ready Table 2 up to the RUN_ID-free results/table2/ path,
    # so the file a reviewer (or the paper) opens is always at the same location.
    for __ext in pgf png pdf; do
      if [[ -f "${OUTPUT_DIR}/table2.${__ext}" ]]; then
        cp -f "${OUTPUT_DIR}/table2.${__ext}" "${LOCAL_OUTPUT_PARENT}/table2.${__ext}"
        log "  published -> ${LOCAL_OUTPUT_PARENT}/table2.${__ext}"
      fi
    done
  else
    log "  WARNING: Table 2 render failed (cells are saved; rerun the render locally)."
  fi
fi

log "=== Cross-zone experiment ${RUN_ID} complete (mode=${MODE}) ==="
log "  Cells:  ${OUTPUT_DIR}/{baseline,cpu50}/table1_summary.csv"
log "  Table 2: ${OUTPUT_DIR}/table2.* (if rendered)"
