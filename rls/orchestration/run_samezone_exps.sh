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

# One-shot: provision a single shared GCP stack, run every migrated non-TDE
# shared-stack experiment against it, then tear the stack down.
#
# Realizes the "provision shared machines -> run all -> teardown" workflow. The
# DB + attacker are provisioned first from orchestration/config/shared_config.yml; the noise
# VM is added only when C-R3 is about to run. The DB and attacker repo/venv are
# installed once; each experiment runs in ATTACHED mode against the shared machine
# descriptor, so it reuses the stack and never tears it down. The 1M-patients/join
# dataset is loaded once (the first experiment loads it; the rest reuse it). Cleanup
# happens once, at the end.
#
# Experiments (against the shared stack):
#   C-R9  db size       (orchestration/run_db_size_experiment.sh)            -- first; measures the pristine load
#   C-R1  existence     (orchestration/run_existence_experiment.sh --job existence)
#   C-R2  range         (orchestration/run_existence_experiment.sh --job range)
#   C-R6  single-attr   (orchestration/run_singleattr_experiment.sh)         -- Table 3; binary sweep + linear baseline
#   C-R7  tuple-ext     (orchestration/run_tuplext_experiment.sh)            -- Table 4; orderings x workers
#   C-R8  mitigations   (orchestration/run_join_mitigations_experiment.sh)   -- mutates + restores the schema
#   C-R3  Table 1       (orchestration/run_oracle_accuracy.sh --variant both) -- last; the ONLY
#                       experiment that uses the noise VM. The noise VM is provisioned
#                       just-in-time here (skipped if RUN_ALL_NO_NOISE=1).
#
# After each experiment, its paper figure/table is rendered (into the claim's
# results/<...>/<RUN_ID>/ dir): C-R1/C-R2 figure .tex, C-R6 Table 3 (.tex), C-R7
# Table 4 (.tex), C-R8 Table 5 heatmap, C-R3 Table 1 heatmap. C-R9 is measurement-only.
# Local plotting deps (matplotlib etc.) are assumed present, so a render failure is a
# real error: it is reported and recorded, but does NOT abort the run (experiments are
# already saved; renders are local and can be rerun) — instead the run exits non-zero
# at the end if any render failed. Set RUN_ALL_SKIP_RENDER=1 to skip renders.
#
# NOT included (provisioned/run separately):
#   C-R5 TDE (own LUKS DB), C-R4 (cross-region).
#
# Usage:
#   bash orchestration/run_samezone_exps.sh                         # RUN_ID auto-generated (all-<ts>)
#   bash orchestration/run_samezone_exps.sh --kick-the-tires        # C-R9 only; DB+attacker smoke check
#   bash orchestration/run_samezone_exps.sh --experiments 3         # run only C-R3 (Table 1)
#   bash orchestration/run_samezone_exps.sh --experiments 1 --machines my-machines.yml
#   bash orchestration/run_samezone_exps.sh --experiments 1,2,6,9   # run a subset, in canonical order
#   RUN_ID=all-myrun bash orchestration/run_samezone_exps.sh        # pin it (any [a-z][a-z0-9-]{0,39})
#
# Env knobs:
#   CONFIG                 - shared config.yml (default orchestration/config/shared_config.yml)
#   RUN_ALL_SKIP_TEARDOWN  - 1 = leave the stack + descriptor up after the run (default 0)
#   RUN_ALL_NO_NOISE       - 1 = never provision the noise VM and skip C-R3 (Table 1).
#                            By default, the noise VM is provisioned just-in-time before
#                            C-R3 so C-R9/C-R1/C-R2/C-R6/C-R7/C-R8 do not pay for it.
#   RUN_ALL_SKIP_RENDER    - 1 = run the experiments but skip the per-claim figure/table renders.
set -euo pipefail

ORCHESTRATION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${ORCHESTRATION_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"
ORIGINAL_ARGS=("$@")

CONFIG="${CONFIG:-orchestration/config/shared_config.yml}"
RUN_ALL_SKIP_TEARDOWN="${RUN_ALL_SKIP_TEARDOWN:-0}"
RUN_ALL_NO_NOISE="${RUN_ALL_NO_NOISE:-0}"
RUN_ALL_SKIP_RENDER="${RUN_ALL_SKIP_RENDER:-0}"
KICK_THE_TIRES=0
EXPERIMENTS_ARG="1,2,3,6,7,8,9"
EXPERIMENTS_EXPLICIT=0
MACHINES_FILE_ARG=""

usage() {
  cat <<EOF
Usage:
  bash orchestration/run_samezone_exps.sh [--kick-the-tires] [--experiments 1,2,3,6,7,8,9] [--machines FILE]

Options:
  --kick-the-tires  Provision the same-zone DB+attacker stack, run only C-R9,
                    then tear it down. This skips the noise VM and all long
                    claim experiments.
  --experiments LIST
                    Comma-separated same-zone claims to run. Allowed values:
                    1,2,3,6,7,8,9. The default is all; selected claims still
                    run in the canonical same-zone order, with C-R3 last.
  --machines FILE   Use an existing machine descriptor instead of provisioning
                    GCP VMs. The descriptor is left untouched on exit.
  -h, --help        Show this help.

Env:
  CONFIG=${CONFIG}
  RUN_ALL_SKIP_TEARDOWN=${RUN_ALL_SKIP_TEARDOWN}
  RUN_ALL_NO_NOISE=${RUN_ALL_NO_NOISE}
  RUN_ALL_SKIP_RENDER=${RUN_ALL_SKIP_RENDER}
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kick-the-tires) KICK_THE_TIRES=1; shift ;;
    --experiments)
      if [[ $# -lt 2 ]]; then
        echo "--experiments requires a comma-separated list (e.g. --experiments 1,2,3,6,7,8,9)." >&2
        exit 1
      fi
      EXPERIMENTS_ARG="$2"
      EXPERIMENTS_EXPLICIT=1
      shift 2
      ;;
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
if [[ "${KICK_THE_TIRES}" == "1" && "${EXPERIMENTS_EXPLICIT}" == "1" ]]; then
  echo "--kick-the-tires cannot be combined with --experiments." >&2
  exit 1
fi
if [[ "${KICK_THE_TIRES}" == "1" ]]; then
  EXPERIMENTS_ARG="9"
fi

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] run_all: $*"; }

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

SELECTED_EXPERIMENTS=""
parse_experiments() {
  local raw="${1// /}" token
  local -a tokens
  IFS=',' read -r -a tokens <<< "${raw}"
  if [[ "${#tokens[@]}" -eq 0 ]]; then
    echo "--experiments cannot be empty." >&2
    exit 1
  fi
  for token in "${tokens[@]}"; do
    case "${token}" in
      1|2|3|6|7|8|9) ;;
      "")
        echo "--experiments cannot contain an empty entry." >&2
        exit 1
        ;;
      *)
        echo "Unknown same-zone experiment '${token}'. Use only: 1,2,3,6,7,8,9." >&2
        exit 1
        ;;
    esac
    case " ${SELECTED_EXPERIMENTS} " in
      *" ${token} "*) ;;
      *) SELECTED_EXPERIMENTS="${SELECTED_EXPERIMENTS:+${SELECTED_EXPERIMENTS} }${token}" ;;
    esac
  done
}

should_run() {
  local exp="$1"
  [[ " ${SELECTED_EXPERIMENTS} " == *" ${exp} "* ]]
}

format_selected_experiments() {
  local exp out=""
  for exp in 9 1 2 6 7 8 3; do
    if should_run "${exp}"; then
      out="${out:+${out}, }C-R${exp}"
    fi
  done
  echo "${out}"
}

parse_experiments "${EXPERIMENTS_ARG}"
if [[ "${EXPERIMENTS_EXPLICIT}" == "1" && "${RUN_ALL_NO_NOISE}" == "1" ]] && should_run 3; then
  echo "C-R3 was requested by --experiments, but RUN_ALL_NO_NOISE=1 disables the required noise VM." >&2
  exit 1
fi

# Render a claim's figure/table after its experiment. Local plotting deps
# (matplotlib etc.) are assumed PRESENT, so a render failure is a real error, not a
# benign missing-dep — its output is shown and the failure is recorded. It is still
# non-aborting (it does NOT tear down the stack mid-run): the expensive experiment
# results are already saved, and renders are local so a failed one can be rerun
# without the stack. Recorded failures make the whole run exit non-zero at the end.
# Usage:  render_or_warn "<label>" rls_uv_run_module renderers.<module> ...
RENDER_FAILS=0
FAILED_RENDERS=()
render_or_warn() {
  local label="$1"; shift
  if [[ "${RUN_ALL_SKIP_RENDER}" == "1" ]]; then
    log "  render ${label}: SKIPPED (RUN_ALL_SKIP_RENDER=1)"; return 0
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
  [[ "${RUN_ALL_SKIP_RENDER}" == "1" ]] && return 0
  local sub="$1" primary="$2"; shift 2
  local src="results/${sub}/${RUN_ID}" dst="results/${sub}" f
  [[ -f "${src}/${primary}" ]] || return 0
  for f in "${primary}" "$@"; do
    if [[ -f "${src}/${f}" ]]; then
      cp -f "${src}/${f}" "${dst}/${f}"
      log "  published -> ${dst}/${f}"
    fi
  done
}

publish_table1_samezone_baseline() {
  local src="results/table1/join-${RUN_ID}"
  local dst="results/table1/samezone-baseline"
  local scenario file
  for scenario in baseline cpu50; do
    if [[ ! -f "${src}/${scenario}/table1_summary.csv" ]]; then
      log "  WARNING: C-R4 same-zone baseline not published; missing ${src}/${scenario}/table1_summary.csv"
      return 0
    fi
  done
  for scenario in baseline cpu50; do
    mkdir -p "${dst}/${scenario}"
    for file in table1_summary.csv table1_summary.md table1_noise.json; do
      if [[ -f "${src}/${scenario}/${file}" ]]; then
        cp -f "${src}/${scenario}/${file}" "${dst}/${scenario}/${file}"
      fi
    done
  done
  log "  published C-R4 same-zone baseline -> ${dst}"
}

# RUN_ID is optional: default to a fresh all-<timestamp> so `bash orchestration/run_samezone_exps.sh`
# just works (mirrors orchestration/run_tde_exps.sh / orchestration/run_crosszone_exps.sh). Kick-the-tires runs
# use kick-<timestamp> unless RUN_ID is pinned. Pin it to group a run's outputs under
# a known results/<dir>/<RUN_ID>/.
RUN_ID_PREFIX="all"
[[ "${KICK_THE_TIRES}" == "1" ]] && RUN_ID_PREFIX="kick"
[[ -n "${MACHINES_FILE_ARG}" && "${KICK_THE_TIRES}" != "1" ]] && RUN_ID_PREFIX="byo"
RUN_ID="${RUN_ID:-${RUN_ID_PREFIX}-$(date +%s | tail -c8)}"
export RUN_ID   # the provision / install / run children all read it
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
log "Selected experiments: $(format_selected_experiments)"

REQUIRE_LOCAL_TEX=1
REQUIRE_GCLOUD=1
[[ "${ATTACHED_MACHINES}" == "1" && "${MACHINES_TRANSPORT}" == "ssh" ]] && REQUIRE_GCLOUD=0
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_local_preflight.sh"
rls_local_preflight "${CONFIG}" "${REQUIRE_LOCAL_TEX}" "${REQUIRE_GCLOUD}"

manifest_start() {
  local exp command_line
  local -a claim_args
  claim_args=()
  for exp in 9 1 2 6 7 8 3; do
    if should_run "${exp}"; then
      claim_args+=(--claim "C-R${exp}")
    fi
  done
  command_line="bash orchestration/run_samezone_exps.sh"
  if [[ "${#ORIGINAL_ARGS[@]}" -gt 0 ]]; then
    command_line="${command_line} ${ORIGINAL_ARGS[*]}"
  fi
  rls_uv_run_module rls_artifact.manifest start \
    --path "${MANIFEST}" \
    --run-id "${RUN_ID}" \
    --runner "orchestration/run_samezone_exps.sh" \
    --config "${CONFIG}" \
    --command-line "${command_line}" \
    "${claim_args[@]}" \
    --env "RUN_ALL_SKIP_TEARDOWN=${RUN_ALL_SKIP_TEARDOWN}" \
    --env "RUN_ALL_NO_NOISE=${RUN_ALL_NO_NOISE}" \
    --env "RUN_ALL_SKIP_RENDER=${RUN_ALL_SKIP_RENDER}" \
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

# Each experiment driver must ATTACH to the shared stack (never provision its own,
# never tear it down). The descriptor exists (we provision first) so they go
# attached; AUTO_PROVISION=0 makes a *missing* descriptor a hard error instead of a
# rogue per-experiment stack that would then tear itself (and our run) down.
export EXISTENCE_AUTO_PROVISION=0 MIT_AUTO_PROVISION=0 DBSIZE_AUTO_PROVISION=0 TABLE1_AUTO_PROVISION=0 SINGLEATTR_AUTO_PROVISION=0 TUPLEXT_AUTO_PROVISION=0

teardown() {
  local rc=$?
  local status="success"
  [[ "${rc}" -ne 0 ]] && status="failed"
  manifest_finish "${status}"
  if [[ "${ATTACHED_MACHINES}" -eq 1 ]]; then
    log "Teardown: attached to externally-provisioned machines — leaving them untouched."
  elif [[ "${RUN_ALL_SKIP_TEARDOWN}" -eq 0 ]]; then
    log "Teardown: removing the shared stack"
    RUN_ID="${RUN_ID}" bash "${ORCHESTRATION_DIR}/provision/cleanup_vms.sh" --config "${CONFIG}" || log "  WARNING: cleanup reported an error"
    rm -f "${M}"
  else
    log "Teardown: RUN_ALL_SKIP_TEARDOWN=1 -- leaving the stack up (descriptor ${M})"
    log "  Clean up later: RUN_ID=${RUN_ID} bash orchestration/provision/cleanup_vms.sh --config ${CONFIG}"
  fi
  exit "${rc}"
}
trap teardown EXIT
manifest_start

# ---- Provision the shared DB + attacker stack first -----------------------
if [[ "${ATTACHED_MACHINES}" -eq 1 ]]; then
  log "=== BYO attached mode: using existing descriptor ${M}; skipping GCP provision ==="
else
  log "=== Provision shared DB+attacker stack (config=${CONFIG}, descriptor=${M}; no noise yet) ==="
  bash "${ORCHESTRATION_DIR}/provision/provision_vms.sh" --config "${CONFIG}" --output "${M}" --no-noise
fi

# ---- Install the DB + the attacker artifact (once) ------------------------
log "=== Install PostgreSQL on the DB VM ==="
bash "${ORCHESTRATION_DIR}/install/install_artifact_on_database.sh" --config "${CONFIG}" --machines "${M}"
log "=== Install repo + venv on the attacker VM ==="
bash "${ORCHESTRATION_DIR}/install/install_artifact_on_attacker.sh" --config "${CONFIG}" --machines "${M}"

# ---- Run the selected shared-stack experiments ATTACHED -------------------
# db size runs FIRST: its driver loads the 1M-patients/join dataset, and it measures
# the pristine baseline schema before any later experiment mutates it, when C-R9 is
# selected. Each driver also ensures the dataset is loaded, so subsets can run on
# their own.
# Each experiment is followed by a best-effort render of its paper figure/table
# (render_or_warn never aborts the run). Set RUN_ALL_SKIP_RENDER=1 to skip renders.
if should_run 9; then
  log "=== C-R9: database physical-size measurement ==="
  bash "${ORCHESTRATION_DIR}/run_db_size_experiment.sh" --config "${CONFIG}" --machines "${M}"
  publish_camera_ready dbsize summary.txt db_sizes.json
  # C-R9 is measurement-only (no figure); the driver prints the size breakdown and
  # validates it against the C-R9 paper claim (§1.1) via validate_cr9_db_sizes.py.
  if [[ "${KICK_THE_TIRES}" == "1" ]]; then
    compile_latex_appendix
    log "=== Kick-the-tires complete: C-R9 succeeded; skipping the remaining same-zone claims. ==="
    exit 0
  fi
fi

if should_run 1; then
  log "=== C-R1: existence microbenchmark (Figure 2) ==="
  bash "${ORCHESTRATION_DIR}/run_existence_experiment.sh" --config "${CONFIG}" --machines "${M}" --job existence
  render_or_warn "C-R1 Figure 2 (.tex)" rls_uv_run_module renderers.existence_figure \
    --input "results/existence/${RUN_ID}/existence_latency.csv" \
    --output "results/existence/${RUN_ID}/existence_kde_figure.tex"
  publish_camera_ready existence existence_kde_figure.tex existence_kde.pgf
fi

if should_run 2; then
  log "=== C-R2: range microbenchmark (Figure 3) ==="
  bash "${ORCHESTRATION_DIR}/run_existence_experiment.sh" --config "${CONFIG}" --machines "${M}" --job range
  render_or_warn "C-R2 Figure 3 (.tex)" rls_uv_run_module renderers.existence_figure \
    --input "results/range/${RUN_ID}/existence_range_latency.csv" \
    --output "results/range/${RUN_ID}/existence_range_kde_figure.tex"
  publish_camera_ready range existence_range_kde_figure.tex existence_range_kde.pgf
fi

# C-R6 runs on the join baseline (read-only reconstruction; does not mutate the
# schema/policy) and needs no noise VM. It runs before the mitigation sweep, which
# swaps policies. (Long: a binary cell per column x worker + a linear baseline per
# column. Trim singleattr.workers/columns in the config for a quick run.)
if should_run 6; then
  log "=== C-R6: single-attribute reconstruction sweep (Table 3) ==="
  bash "${ORCHESTRATION_DIR}/run_singleattr_experiment.sh" --config "${CONFIG}" --machines "${M}"
  render_or_warn "C-R6 Table 3 (.tex)" rls_uv_run_module renderers.single_attribute_table \
    --results-dir "results/table3/${RUN_ID}" --out "results/table3/${RUN_ID}/table3.tex"
  publish_camera_ready table3 table3.tex
fi

# C-R7 (tuple-extension) also runs read-only on the join baseline, no noise VM,
# before the mitigation sweep. (Long: orderings x workers; ssn-leading orderings
# are slow. Trim tuplext.orderings/workers in the config for a quick run.)
if should_run 7; then
  log "=== C-R7: tuple-extension reconstruction sweep (Table 4) ==="
  bash "${ORCHESTRATION_DIR}/run_tuplext_experiment.sh" --config "${CONFIG}" --machines "${M}"
  render_or_warn "C-R7 Table 4 (.tex)" rls_uv_run_module renderers.tuple_extension_table \
    --results-dir "results/table4/${RUN_ID}" --workers 1 \
    --out "results/table4/${RUN_ID}/table4.tex"
  publish_camera_ready table4 table4.tex
fi

# mitigations runs before Table 1: it swaps policies/indexes during the sweep and
# restores the join baseline on exit.
if should_run 8; then
  log "=== C-R8: join-policy mitigation sweep ==="
  bash "${ORCHESTRATION_DIR}/run_join_mitigations_experiment.sh" --config "${CONFIG}" --machines "${M}"
  # the driver writes results/table5/${RUN_ID}/summary.csv; render the Table 5 heatmap there.
  render_or_warn "C-R8 Table 5 heatmap" rls_uv_run_module renderers.join_mitigation_heatmap \
    --summary "results/table5/${RUN_ID}/summary.csv" \
    --output-basename "results/table5/${RUN_ID}/table5"
  publish_camera_ready table5 table5.png table5.pgf table5.pdf
fi

# C-R3 (Table 1) runs LAST and is the ONLY experiment that uses the noise VM. Add
# the noise VM just-in-time so the earlier claims run on only DB + attacker.
if should_run 3; then
  if [[ "${RUN_ALL_NO_NOISE}" != "1" ]]; then
    if [[ "${ATTACHED_MACHINES}" -eq 1 ]]; then
      log "=== C-R3: using noise role from existing descriptor ==="
    else
      log "=== C-R3: provision noise VM just-in-time ==="
      bash "${ORCHESTRATION_DIR}/provision/provision_vms.sh" --config "${CONFIG}" --output "${M}"
    fi
    if ! grep -qE '^[[:space:]]*noise:' "${M}"; then
      echo "Expected ${M} to contain a noise role for C-R3." >&2
      exit 1
    fi
    log "=== C-R3: Table 1 background-load sweep (install noise artifact, then run join+inline) ==="
    bash "${ORCHESTRATION_DIR}/install/install_artifact_on_noise.sh" --config "${CONFIG}" --machines "${M}"
    bash "${ORCHESTRATION_DIR}/run_oracle_accuracy.sh" --config "${CONFIG}" --machines "${M}" --variant both
    mkdir -p "results/table1/${RUN_ID}"
    render_or_warn "C-R3 Table 1 heatmap" rls_uv_run_module renderers.policy_heatmap \
      --policy-dir "join=results/table1/join-${RUN_ID}" \
      --policy-dir "inline=results/table1/inline-${RUN_ID}" \
      --policy-k-values inline=1,2,4,8 \
      --omit-qps join,inline --omit-cost join,inline --omit-summary inline \
      --output-prefix "results/table1/${RUN_ID}/table1" --heatmap-suffix "" \
      --cpu-loads Base,15,25,35,45,55,65,75,85,95 # omit 50 intentionally as it is only used for the cross-zone figure
    publish_camera_ready table1 table1.png table1.pgf table1.pdf
    publish_table1_samezone_baseline
  else
    log "=== C-R3: SKIPPED (RUN_ALL_NO_NOISE=1; noise VM was never provisioned) ==="
  fi
fi

if [[ "${RENDER_FAILS}" -gt 0 ]]; then
  log "=== ${RENDER_FAILS} render(s) FAILED: ${FAILED_RENDERS[*]} — experiments are saved; rerun those renders locally. ==="
fi
# Exit non-zero if any render failed (after teardown, which the EXIT trap handles).
if [[ "${RENDER_FAILS}" -gt 0 ]]; then exit 1; fi
compile_latex_appendix
log "=== Selected experiments complete (tearing down unless RUN_ALL_SKIP_TEARDOWN=1) ==="
