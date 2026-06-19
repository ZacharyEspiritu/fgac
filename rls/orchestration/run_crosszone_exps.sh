#!/usr/bin/env bash
# Copyright 2026 MongoDB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# One-shot for C-R4 (cross-region / cross-zone, Table 2): provision a cross-zone
# stack (DB + noise in one zone, attacker in another), install all three roles, run
# the Base + cpu50 cells in sequence on the provisioned VMs, render Table 2, then
# tear the stack down.
#
# Claim: cross-region accuracy is recoverable by increasing k. The attacker runs in
# a DIFFERENT zone from the DB (WAN, ~49ms RTT); accuracy degrades vs same-zone but
# is recovered at higher k. C-R4 reuses the C-R3 Table-1 machinery, cross-zone.
# The Table 2 render expects the same-zone C-R3 join baseline at
# results/table1/samezone-baseline, which orchestration/run_samezone_exps.sh --experiments 3
# publishes.
#
# Sequence (against an OWN cross-zone stack — NOT the shared same-zone stack):
#   1. Provision DB + noise (zone A) + attacker (zone B) — orchestration/config/crosszone_config.yml
#      pins the per-role zones/subnets (a second subnet in the same VPC).
#   2. Install PostgreSQL (DB) + repo/venv (attacker + noise).
#   3. Run the Base + cpu50 cells in sequence + render Table 2 (run_crosszone_experiment.sh).
#   4. Tear the stack down (VPC + both subnets), unless RUN_CZ_SKIP_TEARDOWN=1.
#
# Usage:
#   bash orchestration/run_crosszone_exps.sh                  # RUN_ID auto-generated (cz-<ts>)
#   bash orchestration/run_crosszone_exps.sh --machines my-machines.yml
#   RUN_ID=cz-myrun bash orchestration/run_crosszone_exps.sh  # pin the RUN_ID
#
# Env knobs:
#   CONFIG                 - cross-zone config.yml (default orchestration/config/crosszone_config.yml)
#   RUN_CZ_SKIP_RENDER     - 1 = run the cells but skip the Table 2 render (default 0)
#   RUN_CZ_SKIP_TEARDOWN   - 1 = leave the stack + descriptor up after the run (default 0)
set -euo pipefail

ORCHESTRATION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${ORCHESTRATION_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"
ORIGINAL_ARGS=("$@")

CONFIG="${CONFIG:-orchestration/config/crosszone_config.yml}"
RUN_CZ_SKIP_RENDER="${RUN_CZ_SKIP_RENDER:-0}"
RUN_CZ_SKIP_TEARDOWN="${RUN_CZ_SKIP_TEARDOWN:-0}"
MACHINES_FILE_ARG=""

usage() {
  cat <<EOF
Usage:
  bash orchestration/run_crosszone_exps.sh [--machines FILE]

Options:
  --machines FILE   Use an existing DB+attacker+noise descriptor instead of
                    provisioning GCP VMs. The descriptor is left untouched on exit.
  -h, --help        Show this help.

Env:
  CONFIG=${CONFIG}
  RUN_CZ_SKIP_RENDER=${RUN_CZ_SKIP_RENDER}
  RUN_CZ_SKIP_TEARDOWN=${RUN_CZ_SKIP_TEARDOWN}
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --machines)
      if [[ $# -lt 2 ]]; then
        echo "--machines requires a machine descriptor path." >&2
        exit 1
      fi
      MACHINES_FILE_ARG="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

# RUN_ID is optional here: default to a fresh cz-<timestamp> so `bash orchestration/run_crosszone_exps.sh` just works.
RUN_ID_PREFIX="cz"
[[ -n "${MACHINES_FILE_ARG}" ]] && RUN_ID_PREFIX="byo-cz"
RUN_ID="${RUN_ID:-${RUN_ID_PREFIX}-$(date +%s | tail -c8)}"
export RUN_ID   # the provision / install / run children all read it

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID}] run_crosszone: $*"; }

LATEX_APPENDIX_FAILED=0
compile_latex_appendix() {
  log "=== Compile LaTeX artifact appendix ==="
  if bash "${PROJECT_DIR}/latex/compile.sh"; then
    return 0
  fi
  LATEX_APPENDIX_FAILED=1
  log "  WARNING: LaTeX artifact appendix compile FAILED (output above). Claim pass/fail ignores appendix compile errors; rerun bash latex/compile.sh locally."
  return 0
}

if [[ ! -f "${CONFIG}" ]]; then
  echo "config not found: ${CONFIG}" >&2
  exit 1
fi
M="${MACHINES_FILE_ARG:-results/machines/${RUN_ID}.yml}"
ATTACHED_MACHINES=0
MACHINES_TRANSPORT="managed"
if [[ -n "${MACHINES_FILE_ARG}" ]]; then
  ATTACHED_MACHINES=1
  if [[ ! -f "${M}" ]]; then
    echo "machine descriptor not found: ${M}" >&2
    exit 1
  fi
  MACHINES_TRANSPORT="$(sed -n 's/^[[:space:]]*transport:[[:space:]]*//p' "${M}" | head -n 1)"
  MACHINES_TRANSPORT="${MACHINES_TRANSPORT%%#*}"
  MACHINES_TRANSPORT="${MACHINES_TRANSPORT//\"/}"
  MACHINES_TRANSPORT="${MACHINES_TRANSPORT//\'/}"
  MACHINES_TRANSPORT="$(printf '%s' "${MACHINES_TRANSPORT}" | tr -d '[:space:]')"
  MACHINES_TRANSPORT="${MACHINES_TRANSPORT:-gcloud}"
fi
MANIFEST="results/runs/${RUN_ID}/manifest.yml"

REQUIRE_LOCAL_TEX=1
REQUIRE_GCLOUD=1
[[ "${ATTACHED_MACHINES}" == "1" && "${MACHINES_TRANSPORT}" == "ssh" ]] && REQUIRE_GCLOUD=0
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_local_preflight.sh"
rls_local_preflight "${CONFIG}" "${REQUIRE_LOCAL_TEX}" "${REQUIRE_GCLOUD}"

manifest_start() {
  local command_line="bash orchestration/run_crosszone_exps.sh"
  if [[ "${#ORIGINAL_ARGS[@]}" -gt 0 ]]; then
    command_line="${command_line} ${ORIGINAL_ARGS[*]}"
  fi
  rls_uv_run_module rls_artifact.manifest start \
    --path "${MANIFEST}" \
    --run-id "${RUN_ID}" \
    --runner "orchestration/run_crosszone_exps.sh" \
    --config "${CONFIG}" \
    --command-line "${command_line}" \
    --claim "C-R4" \
    --env "RUN_CZ_SKIP_RENDER=${RUN_CZ_SKIP_RENDER}" \
    --env "RUN_CZ_SKIP_TEARDOWN=${RUN_CZ_SKIP_TEARDOWN}" \
    --env "ATTACHED_MACHINES=${ATTACHED_MACHINES}" \
    --env "MACHINES_TRANSPORT=${MACHINES_TRANSPORT}" \
    --env "MACHINES=${M}"
  log "Manifest: ${MANIFEST}"
}

manifest_finish() {
  local status="$1"
  if [[ "${LATEX_APPENDIX_FAILED:-0}" == "1" ]]; then
    if ! rls_uv_run_module rls_artifact.manifest finish \
        --path "${MANIFEST}" \
        --run-id "${RUN_ID}" \
        --status "${status}" \
        --note "LaTeX appendix compile failed after experiment stages completed; claim status ignores appendix compile errors."; then
      log "  WARNING: could not finalize manifest ${MANIFEST}"
    fi
    return 0
  fi
  if ! rls_uv_run_module rls_artifact.manifest finish \
      --path "${MANIFEST}" \
      --run-id "${RUN_ID}" \
      --status "${status}"; then
    log "  WARNING: could not finalize manifest ${MANIFEST}"
  fi
}

# The run step ATTACHES to the stack we provision first (we own it; the EXIT trap
# cleans it up). AUTO_PROVISION=0 makes a *missing* descriptor a hard error instead
# of a rogue self-torn-down stack.
export CROSSZONE_AUTO_PROVISION=0
export CROSSZONE_SKIP_RENDER="${RUN_CZ_SKIP_RENDER}"

teardown() {
  local rc=$?
  local status="success"
  [[ "${rc}" -ne 0 ]] && status="failed"
  manifest_finish "${status}"
  if [[ "${ATTACHED_MACHINES}" -eq 1 ]]; then
    log "Teardown: attached to externally-provisioned machines — leaving them untouched."
  elif [[ "${RUN_CZ_SKIP_TEARDOWN}" -eq 0 ]]; then
    log "Teardown: removing the cross-zone stack (VPC + both subnets)"
    bash "${ORCHESTRATION_DIR}/provision/cleanup_vms.sh" --machines "${M}" --delete-network || log "  WARNING: cleanup reported an error"
    rm -f "${M}"
  else
    log "Teardown: RUN_CZ_SKIP_TEARDOWN=1 -- leaving the stack up (descriptor ${M})"
    log "  Clean up later: bash orchestration/provision/cleanup_vms.sh --machines ${M} --delete-network"
  fi
  exit "${rc}"
}
trap teardown EXIT
manifest_start

# ---- Step 1: Provision the cross-zone stack (DB + noise + remote attacker) -----
if [[ "${ATTACHED_MACHINES}" -eq 1 ]]; then
  log "=== C-R4 BYO attached mode: using existing descriptor ${M}; skipping GCP provision ==="
else
  log "=== C-R4 cross-zone: provision (config=${CONFIG}, descriptor=${M}) ==="
  bash "${ORCHESTRATION_DIR}/provision/provision_vms.sh" --config "${CONFIG}" --output "${M}"
fi

# ---- Step 2: Install PostgreSQL (DB) + repo/venv (attacker + noise) ------------
log "=== Install PostgreSQL on the DB VM ==="
bash "${ORCHESTRATION_DIR}/install/install_artifact_on_database.sh" --config "${CONFIG}" --machines "${M}"
log "=== Install repo + venv on the attacker VM (cross-region) ==="
bash "${ORCHESTRATION_DIR}/install/install_artifact_on_attacker.sh" --config "${CONFIG}" --machines "${M}"
log "=== Install repo + venv on the noise VM ==="
bash "${ORCHESTRATION_DIR}/install/install_artifact_on_noise.sh" --config "${CONFIG}" --machines "${M}"

# ---- Step 3: Run the Base + cpu50 cells in sequence + render Table 2 -----------
log "=== Run Base + cpu50 cells on the provisioned VMs + render Table 2 ==="
bash "${ORCHESTRATION_DIR}/run_crosszone_experiment.sh" --config "${CONFIG}" --machines "${M}"

compile_latex_appendix
log "=== C-R4 cross-zone complete (tearing down unless RUN_CZ_SKIP_TEARDOWN=1) ==="
log "  Table 2: results/table2/${RUN_ID}/table2.* ; cells: results/table2/${RUN_ID}/{baseline,cpu50}/"
