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

# One-shot for C-R5 (TDE): provision a LUKS-encrypted DB stack, run the
# existence-timing microbenchmark (+ the range variant) on it, render the TDE
# figures, then tear the stack down.
#
# Claim: Transparent data encryption (LUKS full-disk encryption at rest) does NOT
# change the timing gap the existence attack exploits. C-R5 is verified VISUALLY:
# the TDE Figure 2/3 (rendered here with existence_figure.py --variant tde)
# must show the same trimodal class separation as the non-TDE baseline (C-R1/C-R2).
#
# Sequence (against an OWN LUKS stack — NOT the shared same-zone stack):
#   1. Provision a BARE DB VM + a RAW secondary disk + the attacker (no noise VM)
#      (orchestration/config/tde_config.yml pins the LUKS disk + the LUKS installer).
#   2. Install: LUKS-encrypt the data disk + PostgreSQL on it (DB), repo + venv (attacker).
#   3. Run the equality-probe (existence) microbenchmark under TDE (attached).
#   4. Run the range variant under TDE on the SAME machines (skip with RUN_TDE_SKIP_RANGE=1).
#   5. Render the TDE figures with --variant tde (best-effort; assumes local matplotlib).
#   6. Tear the stack down (unless RUN_TDE_SKIP_TEARDOWN=1).
#
# Usage:
#   bash orchestration/run_tde_exps.sh                       # RUN_ID auto-generated (tde-<ts>)
#   bash orchestration/run_tde_exps.sh --machines my-machines.yml
#   RUN_ID=tde-myrun bash orchestration/run_tde_exps.sh      # pin the RUN_ID
#
# Env knobs:
#   CONFIG                 - TDE config.yml (default orchestration/config/tde_config.yml)
#   RUN_TDE_SKIP_RANGE     - 1 = run only the existence job, skip the range variant (default 0)
#   RUN_TDE_SKIP_RENDER    - 1 = run the experiment but skip the figure renders (default 0)
#   RUN_TDE_SKIP_TEARDOWN  - 1 = leave the stack + descriptor up after the run (default 0)
set -euo pipefail

ORCHESTRATION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${ORCHESTRATION_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"
ORIGINAL_ARGS=("$@")

CONFIG="${CONFIG:-orchestration/config/tde_config.yml}"
RUN_TDE_SKIP_RANGE="${RUN_TDE_SKIP_RANGE:-0}"
RUN_TDE_SKIP_RENDER="${RUN_TDE_SKIP_RENDER:-0}"
RUN_TDE_SKIP_TEARDOWN="${RUN_TDE_SKIP_TEARDOWN:-0}"
MACHINES_FILE_ARG=""

usage() {
  cat <<EOF
Usage:
  bash orchestration/run_tde_exps.sh [--machines FILE]

Options:
  --machines FILE   Use an existing machine descriptor instead of provisioning
                    GCP VMs. The descriptor is left untouched on exit.
  -h, --help        Show this help.

Env:
  CONFIG=${CONFIG}
  RUN_TDE_SKIP_RANGE=${RUN_TDE_SKIP_RANGE}
  RUN_TDE_SKIP_RENDER=${RUN_TDE_SKIP_RENDER}
  RUN_TDE_SKIP_TEARDOWN=${RUN_TDE_SKIP_TEARDOWN}
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

# RUN_ID is optional here (unlike orchestration/run_samezone_exps.sh): default to a
# fresh tde-<timestamp> so `bash orchestration/run_tde_exps.sh` just works.
RUN_ID_PREFIX="tde"
[[ -n "${MACHINES_FILE_ARG}" ]] && RUN_ID_PREFIX="byo-tde"
RUN_ID="${RUN_ID:-${RUN_ID_PREFIX}-$(date +%s | tail -c8)}"
export RUN_ID   # the provision / install / run children all read it

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID}] run_tde: $*"; }

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

# Best-effort figure render. Local plotting deps (matplotlib + a TeX install for
# .pgf) are assumed PRESENT, so a render failure is a real error: it is shown and
# recorded, but does NOT abort the run (the experiment CSVs are already saved and
# renders are local, so a failed one can be rerun). The run exits non-zero at the
# end if any render failed. Set RUN_TDE_SKIP_RENDER=1 to skip renders.
RENDER_FAILS=0
FAILED_RENDERS=()
render_or_warn() {
  local label="$1"; shift
  if [[ "${RUN_TDE_SKIP_RENDER}" == "1" ]]; then
    log "  render ${label}: SKIPPED (RUN_TDE_SKIP_RENDER=1)"; return 0
  fi
  log "  rendering ${label}..."
  if "$@"; then
    log "  rendered ${label}"
  else
    log "  ERROR: ${label} render FAILED (output above). Experiment results are saved; rerun the render locally. The run will exit non-zero."
    RENDER_FAILS=$((RENDER_FAILS + 1)); FAILED_RENDERS+=("${label}")
  fi
}

# After a successful render, copy the camera-ready artifact(s) out of the per-run
# results/<sub>/<RUN_ID>/ dir up to the RUN_ID-free results/<sub>/ path, so the file a
# reviewer (or the paper) opens is always at the same constant location. The first arg
# is the primary artifact: if it is missing (render skipped/failed) nothing is published;
# the rest are its dependencies (e.g. the .pgf a .tex \input{}s).
publish_camera_ready() {  # <results-subdir> <primary-file> [dep-file ...]
  [[ "${RUN_TDE_SKIP_RENDER}" == "1" ]] && return 0
  local sub="$1" primary="$2"; shift 2
  local src="results/${sub}/${RUN_ID}" dst="results/${sub}" f target
  [[ -f "${src}/${primary}" ]] || return 0
  for f in "${primary}" "$@"; do
    if [[ -f "${src}/${f}" ]]; then
      target="${dst}/${f}"
      mkdir -p "$(dirname "${target}")"
      cp -f "${src}/${f}" "${target}"
      log "  published -> ${target}"
    fi
  done
}

stage_tex_input_pgf() {  # <results-subdir> <tex-file> <generated-pgf-file>
  [[ "${RUN_TDE_SKIP_RENDER}" == "1" ]] && return 0
  local sub="$1" tex_file="$2" generated_pgf="$3"
  local dir="results/${sub}/${RUN_ID}" input_path target
  [[ -f "${dir}/${tex_file}" && -f "${dir}/${generated_pgf}" ]] || return 0
  input_path="$(sed -n 's/.*\\input{\([^}]*\.pgf\)}.*/\1/p' "${dir}/${tex_file}" | head -n 1)"
  if [[ -z "${input_path}" ]]; then
    log "  WARNING: no PGF \\input{} found in ${dir}/${tex_file}; not staging ${generated_pgf}"
    return 0
  fi
  if [[ "${input_path}" = /* || "${input_path}" == *".."* ]]; then
    log "  WARNING: refusing unsafe PGF \\input{} path in ${dir}/${tex_file}: ${input_path}"
    return 0
  fi
  target="${dir}/${input_path}"
  mkdir -p "$(dirname "${target}")"
  cp -f "${dir}/${generated_pgf}" "${target}"
  log "  staged ${generated_pgf} as ${input_path} for ${tex_file}"
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

# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_local_preflight.sh"
REQUIRE_GCLOUD=1
[[ "${ATTACHED_MACHINES}" == "1" && "${MACHINES_TRANSPORT}" == "ssh" ]] && REQUIRE_GCLOUD=0
rls_local_preflight "${CONFIG}" 1 "${REQUIRE_GCLOUD}"

manifest_start() {
  local command_line="bash orchestration/run_tde_exps.sh"
  if [[ "${#ORIGINAL_ARGS[@]}" -gt 0 ]]; then
    command_line="${command_line} ${ORIGINAL_ARGS[*]}"
  fi
  rls_uv_run_module rls_artifact.manifest start \
    --path "${MANIFEST}" \
    --run-id "${RUN_ID}" \
    --runner "orchestration/run_tde_exps.sh" \
    --config "${CONFIG}" \
    --command-line "${command_line}" \
    --claim "C-R5" \
    --env "RUN_TDE_SKIP_RANGE=${RUN_TDE_SKIP_RANGE}" \
    --env "RUN_TDE_SKIP_RENDER=${RUN_TDE_SKIP_RENDER}" \
    --env "RUN_TDE_SKIP_TEARDOWN=${RUN_TDE_SKIP_TEARDOWN}" \
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

# The run step must ATTACH to the stack we provision first (never provision its own,
# never tear it down): we own the stack and the EXIT trap cleans it up. AUTO_PROVISION=0
# makes a *missing* descriptor a hard error instead of a rogue self-torn-down stack.
export EXISTENCE_AUTO_PROVISION=0

teardown() {
  local rc=$?
  local status="success"
  [[ "${rc}" -ne 0 ]] && status="failed"
  manifest_finish "${status}"
  if [[ "${ATTACHED_MACHINES}" -eq 1 ]]; then
    log "Teardown: attached to externally-provisioned machines — leaving them untouched."
  elif [[ "${RUN_TDE_SKIP_TEARDOWN}" -eq 0 ]]; then
    log "Teardown: removing the TDE stack"
    bash "${ORCHESTRATION_DIR}/provision/cleanup_vms.sh" --machines "${M}" || log "  WARNING: cleanup reported an error"
    rm -f "${M}"
  else
    log "Teardown: RUN_TDE_SKIP_TEARDOWN=1 -- leaving the stack up (descriptor ${M})"
    log "  Clean up later: bash orchestration/provision/cleanup_vms.sh --machines ${M}"
  fi
  exit "${rc}"
}
trap teardown EXIT
manifest_start

# ---- Step 1: Provision the TDE VMs (bare DB VM + raw data disk + attacker; no noise) -----
if [[ "${ATTACHED_MACHINES}" -eq 1 ]]; then
  log "=== C-R5 TDE BYO attached mode: using existing descriptor ${M}; skipping GCP provision ==="
else
  log "=== C-R5 TDE: provision (config=${CONFIG}, descriptor=${M}) ==="
  bash "${ORCHESTRATION_DIR}/provision/provision_vms.sh" --config "${CONFIG}" --output "${M}" --no-noise
fi

# ---- Step 2: Install — LUKS-encrypt the disk + PostgreSQL (DB), repo + venv (attacker)
log "=== Install LUKS + PostgreSQL on the DB VM ==="
bash "${ORCHESTRATION_DIR}/install/install_artifact_on_database_tde.sh" --config "${CONFIG}" --machines "${M}"
log "=== Install repo + venv on the attacker VM ==="
bash "${ORCHESTRATION_DIR}/install/install_artifact_on_attacker.sh" --config "${CONFIG}" --machines "${M}"

# ---- Step 3: Run the equality-probe (existence) microbenchmark under TDE -------
log "=== Run existence microbenchmark under TDE (looks like Figure 2) ==="
bash "${ORCHESTRATION_DIR}/run_existence_experiment.sh" --config "${CONFIG}" --machines "${M}" --job existence
render_or_warn "TDE existence figure (.tex)" rls_uv_run_module renderers.existence_figure \
  --input "results/existence/${RUN_ID}/existence_latency.csv" \
  --variant tde \
  --output "results/existence/${RUN_ID}/existence_tde_figure.tex"
stage_tex_input_pgf existence existence_tde_figure.tex existence_kde.pgf
publish_camera_ready existence existence_tde_figure.tex existence_tde_figure.pgf

# ---- Step 4: Run the range variant under TDE on the SAME machines --------------
if [[ "${RUN_TDE_SKIP_RANGE}" != "1" ]]; then
  log "=== Run range microbenchmark under TDE (looks like Figure 3) ==="
  bash "${ORCHESTRATION_DIR}/run_existence_experiment.sh" --config "${CONFIG}" --machines "${M}" --job range
  render_or_warn "TDE range figure (.tex)" rls_uv_run_module renderers.existence_figure \
    --input "results/range/${RUN_ID}/existence_range_latency.csv" \
    --variant tde \
    --output "results/range/${RUN_ID}/existence_range_tde_figure.tex"
  stage_tex_input_pgf range existence_range_tde_figure.tex existence_range_kde.pgf
  publish_camera_ready range existence_range_tde_figure.tex existence_range_tde_figure.pgf
else
  log "=== Range variant SKIPPED (RUN_TDE_SKIP_RANGE=1) ==="
fi

if [[ "${RENDER_FAILS}" -gt 0 ]]; then
  log "=== ${RENDER_FAILS} render(s) FAILED: ${FAILED_RENDERS[*]} — experiment CSVs are saved; rerun those renders locally. ==="
fi
# Exit non-zero if any render failed (after teardown, which the EXIT trap handles).
if [[ "${RENDER_FAILS}" -gt 0 ]]; then exit 1; fi
compile_latex_appendix
log "=== C-R5 TDE complete (tearing down unless RUN_TDE_SKIP_TEARDOWN=1) ==="
log "  Verify: open the TDE figure(s) next to the non-TDE baseline (C-R1/C-R2) — same trimodal shape."
