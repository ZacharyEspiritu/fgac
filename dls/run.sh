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

set -euo pipefail

usage() {
  cat <<EOF
Run the local search-backend experiments needed to produce a recovery table and
greedy de Bruijn reconstructions.

By default this runs one trial each for D1, D10, D100, and D1000 against both
single-node OpenSearch and Elasticsearch Docker setups. Outputs are written
under ${ROOT_DIR}/results/reviewer:

  ${ROOT_DIR}/results/reviewer/stats/*_stats.json
  ${ROOT_DIR}/results/reviewer/logs/*.log
  ${ROOT_DIR}/results/reviewer/reconstructions/*_debruijn_greedy.{txt,json}
  ${ROOT_DIR}/results/reviewer/opensearch_table.tex
  ${ROOT_DIR}/results/reviewer/elasticsearch_table.tex

Options:
  --backend NAME          Backend to use: opensearch, elasticsearch, or elastic.
                          Default: run both opensearch and elasticsearch
  --trials N              Trials per corpus size, default: 1
  --datasets LIST         Corpus sizes to run, default: d1,d10,d100,d1000
  --output-dir PATH       Output directory, default: ${ROOT_DIR}/results/reviewer
  --venv PATH             uv project environment created by setup.sh, default: ${ROOT_DIR}/.venv
  --config PATH           Experiment config, default: ${ROOT_DIR}/config/config.yml
  --doctor-only           Run dependency/config/output/backend checks and exit
  --skip-docker-start     Do not run docker compose up before experiments
  --destructive-rerun-existing-results
                          Rerun attacks even when stats and reconstruction JSONs exist
  -h, --help              Show this help

Examples:
  ./run.sh
  ./run.sh --backend elastic
  ./run.sh --datasets d1
  ./run.sh --datasets d1,d10
  ./run.sh --trials 3
  ./run.sh --output-dir ${ROOT_DIR}/results/reviewer-3x --trials 3
EOF
}

log() {
  echo "==> $*"
}

warn() {
  echo "warning: $*" >&2
}

die() {
  echo "error: $*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

require_positive_integer() {
  local name="$1"
  local value="$2"
  if [[ -z "${value}" || "${value}" == *[!0-9]* || "${value}" -lt 1 ]]; then
    die "${name} must be a positive integer"
  fi
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

absolute_path() {
  local path="$1"
  case "${path}" in
    /*) printf '%s\n' "${path}" ;;
    *) printf '%s/%s\n' "${ROOT_DIR}" "${path}" ;;
  esac
}

TRIALS=1
BACKEND=""
BACKEND_SPEC=""
BACKENDS=()
DATASET_SPEC="d1,d10,d100,d1000"
OUTPUT_DIR="results/reviewer"
VENV_DIR=".venv"
CONFIG_FILE="config/config.yml"
SKIP_DOCKER_START=0
DESTRUCTIVE_RERUN_EXISTING_RESULTS=0
PREFLIGHT_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend)
      BACKEND_SPEC="${2:?missing value for --backend}"
      shift 2
      ;;
    --trials)
      TRIALS="${2:?missing value for --trials}"
      shift 2
      ;;
    --datasets)
      DATASET_SPEC="${2:?missing value for --datasets}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:?missing value for --output-dir}"
      shift 2
      ;;
    --venv)
      VENV_DIR="${2:?missing value for --venv}"
      shift 2
      ;;
    --config)
      CONFIG_FILE="${2:?missing value for --config}"
      shift 2
      ;;
    --doctor-only|--preflight-only)
      PREFLIGHT_ONLY=1
      shift
      ;;
    --skip-docker-start)
      SKIP_DOCKER_START=1
      shift
      ;;
    --destructive-rerun-existing-results)
      DESTRUCTIVE_RERUN_EXISTING_RESULTS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

require_positive_integer "--trials" "${TRIALS}"

OUTPUT_DIR="$(absolute_path "${OUTPUT_DIR}")"
VENV_DIR="$(absolute_path "${VENV_DIR}")"
CONFIG_FILE="$(absolute_path "${CONFIG_FILE}")"
STATS_DIR="${OUTPUT_DIR}/stats"
LOG_DIR="${OUTPUT_DIR}/logs"
RECON_DIR="${OUTPUT_DIR}/reconstructions"
TABLE_OUTPUTS=()
DATASETS=()
STATS_FILES=()

normalize_backend() {
  case "$1" in
    opensearch)
      printf '%s\n' "opensearch"
      ;;
    elastic|elasticsearch)
      printf '%s\n' "elasticsearch"
      ;;
    *)
      die "--backend must be opensearch, elasticsearch, or elastic"
      ;;
  esac
}

configure_backends() {
  if [[ -n "${BACKEND_SPEC}" ]]; then
    BACKENDS=("$(normalize_backend "${BACKEND_SPEC}")")
  else
    BACKENDS=("opensearch" "elasticsearch")
  fi
}

append_dataset_once() {
  local dataset="$1"
  local existing
  if [[ "${#DATASETS[@]}" -gt 0 ]]; then
    for existing in "${DATASETS[@]}"; do
      [[ "${existing}" == "${dataset}" ]] && return
    done
  fi
  DATASETS+=("${dataset}")
}

parse_datasets() {
  local spec="$1"
  local normalized
  local item
  local dataset

  normalized="$(printf '%s' "${spec}" | tr '[:upper:]' '[:lower:]' | tr ',' ' ')"
  for item in ${normalized}; do
    dataset="${item#d}"
    case "${dataset}" in
      1|10|100|1000)
        append_dataset_once "${dataset}"
        ;;
      "")
        ;;
      *)
        die "--datasets only supports d1,d10,d100,d1000; got ${item}"
        ;;
    esac
  done

  [[ "${#DATASETS[@]}" -gt 0 ]] || die "--datasets did not contain any datasets"
}

format_datasets() {
  local output=""
  local dataset
  for dataset in "${DATASETS[@]}"; do
    output="${output}${output:+ }D${dataset}"
  done
  printf '%s' "${output}"
}

check_file() {
  local path="$1"
  [[ -f "${path}" ]] || die "missing ${path}"
}

ensure_writable_output_file() {
  local path="$1"
  local dir
  dir="$(dirname "${path}")"
  mkdir -p "${dir}"

  if [[ -e "${path}" && ! -f "${path}" ]]; then
    die "output path exists but is not a regular file: ${path}"
  fi
  if [[ -e "${path}" && ! -w "${path}" ]]; then
    die "output path is not writable: ${path}"
  fi
  if [[ ! -e "${path}" && ! -w "${dir}" ]]; then
    die "output directory is not writable: ${dir}"
  fi
}

uv_run_command() {
  if [[ "${VENV_DIR}" == "${ROOT_DIR}/.venv" ]]; then
    uv --project "${ROOT_DIR}" run --frozen "$@"
  else
    UV_PROJECT_ENVIRONMENT="${VENV_DIR}" uv --project "${ROOT_DIR}" run --frozen "$@"
  fi
}

uv_run_command_unbuffered() {
  if [[ "${VENV_DIR}" == "${ROOT_DIR}/.venv" ]]; then
    PYTHONUNBUFFERED=1 uv --project "${ROOT_DIR}" run --frozen "$@"
  else
    PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT="${VENV_DIR}" uv --project "${ROOT_DIR}" run --frozen "$@"
  fi
}

uv_run_python() {
  uv_run_command python "$@"
}

uv_run_python_unbuffered() {
  uv_run_command python -u "$@"
}

uv_run_dls() {
  uv_run_command unfilter-dls "$@"
}

uv_run_dls_unbuffered() {
  uv_run_command_unbuffered unfilter-dls "$@"
}

check_python() {
  have uv || die "uv is missing; run ${ROOT_DIR}/setup.sh first"
  [[ -x "${VENV_DIR}/bin/python3" || -x "${VENV_DIR}/bin/python" ]] || die \
    "missing project environment at ${VENV_DIR}; run ${ROOT_DIR}/setup.sh first or pass --venv PATH"
}

preflight_python() {
  if [[ -x "${VENV_DIR}/bin/python3" ]]; then
    printf '%s\n' "${VENV_DIR}/bin/python3"
  elif [[ -x "${VENV_DIR}/bin/python" ]]; then
    printf '%s\n' "${VENV_DIR}/bin/python"
  elif have python3; then
    command -v python3
  elif have python; then
    command -v python
  else
    die "python3 is required to run doctor checks"
  fi
}

run_preflight() {
  local python_bin
  local backend
  local -a preflight_cmd

  python_bin="$(preflight_python)"
  check_file "${ROOT_DIR}/src/util/preflight.py"

  preflight_cmd=(
    "${python_bin}" "${ROOT_DIR}/src/util/preflight.py"
    --root-dir "${ROOT_DIR}"
    --config "${CONFIG_FILE}"
    --venv "${VENV_DIR}"
    --output-dir "${OUTPUT_DIR}"
    --datasets "${DATASET_SPEC}"
    --trials "${TRIALS}"
  )
  for backend in "${BACKENDS[@]}"; do
    preflight_cmd+=(--backend "${backend}")
  done
  if [[ "${SKIP_DOCKER_START}" -eq 1 ]]; then
    preflight_cmd+=(--skip-docker-start)
  fi
  if [[ "${DESTRUCTIVE_RERUN_EXISTING_RESULTS}" -eq 1 ]]; then
    preflight_cmd+=(--destructive-rerun-existing-results)
  fi

  "${preflight_cmd[@]}"
}

check_backend_dependencies() {
  check_file "$(backend_compose_file)"
}

check_docker_dependencies() {
  have docker || die "Docker is missing; run ${ROOT_DIR}/setup.sh"
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is missing"
  docker info >/dev/null 2>&1 || die "Docker daemon is not running"
}

run_prefix() {
  local d="$1"
  local trial="$2"
  echo "${BACKEND}-d${d}-r${trial}"
}

stats_file_for_prefix() {
  local prefix="$1"
  echo "${STATS_DIR}/${prefix}_stats.json"
}

reconstruction_output_prefix() {
  local prefix="$1"
  echo "${RECON_DIR}/${prefix}_debruijn_greedy"
}

reconstruction_json_for_prefix() {
  local prefix="$1"
  echo "$(reconstruction_output_prefix "${prefix}").json"
}

table_output_for_backend() {
  echo "${OUTPUT_DIR}/${BACKEND}_table.tex"
}

format_table_outputs() {
  local table_output
  for table_output in "${TABLE_OUTPUTS[@]}"; do
    echo "  ${table_output}"
  done
}

backend_required() {
  local d
  local trial
  local prefix
  local stats_file

  if [[ "${DESTRUCTIVE_RERUN_EXISTING_RESULTS}" -eq 1 ]]; then
    return 0
  fi

  for d in "${DATASETS[@]}"; do
    for ((trial = 1; trial <= TRIALS; trial++)); do
      prefix="$(run_prefix "${d}" "${trial}")"
      stats_file="$(stats_file_for_prefix "${prefix}")"
      [[ -f "${stats_file}" ]] || return 0
    done
  done

  return 1
}

backend_config_value_for() {
  local backend="$1"
  local key="$2"
  local value

  value="$(yq -r ".backends.${backend}.${key} | tostring" "${CONFIG_FILE}")"
  if [[ -z "${value}" || "${value}" == "null" ]]; then
    die "missing .backends.${backend}.${key} in ${CONFIG_FILE}"
  fi
  printf '%s\n' "${value}"
}

backend_config_value() {
  backend_config_value_for "${BACKEND}" "$1"
}

backend_config_path_value_for() {
  absolute_path "$(backend_config_value_for "$1" "$2")"
}

backend_config_path_value() {
  backend_config_path_value_for "${BACKEND}" "$1"
}

config_value() {
  local expression="$1"
  local value

  value="$(yq -r "${expression} | tostring" "${CONFIG_FILE}")"
  if [[ -z "${value}" || "${value}" == "null" ]]; then
    die "missing ${expression} in ${CONFIG_FILE}"
  fi
  printf '%s\n' "${value}"
}

reconstruction_config_value() {
  config_value ".reconstruction.${1}"
}

backend_label() {
  backend_config_value "label"
}

backend_compose_file() {
  backend_config_path_value "compose_file"
}

backend_service() {
  backend_config_value "service"
}

backend_url() {
  if [[ "${BACKEND}" == "elasticsearch" ]]; then
    echo "${ELASTICSEARCH_SCHEME}://${ELASTICSEARCH_HOST}:${ELASTICSEARCH_PORT}"
  else
    echo "${OPENSEARCH_SCHEME}://${OPENSEARCH_HOST}:${OPENSEARCH_PORT}"
  fi
}

configure_backend_env() {
  local env_name
  local value

  while IFS= read -r env_name; do
    [[ -n "${env_name}" ]] || continue
    if [[ -n "${!env_name-}" ]]; then
      continue
    fi
    value="$(backend_config_value "env.${env_name}")"
    export "${env_name}=${value}"
  done < <(yq -r ".backends.${BACKEND}.env | keys | .[]" "${CONFIG_FILE}")
}

start_backend() {
  if [[ "${SKIP_DOCKER_START}" -eq 1 ]]; then
    log "Skipping docker compose startup"
    return
  fi
  log "Starting single-node $(backend_label) Docker container"
  docker compose -f "$(backend_compose_file)" up -d "$(backend_service)"
}

stop_inactive_backends() {
  local candidate
  local compose_file
  local service
  local label

  if [[ "${SKIP_DOCKER_START}" -eq 1 || "${#BACKENDS[@]}" -le 1 ]]; then
    return
  fi

  for candidate in "${BACKENDS[@]}"; do
    [[ "${candidate}" == "${BACKEND}" ]] && continue
    compose_file="$(backend_config_path_value_for "${candidate}" "compose_file")"
    service="$(backend_config_value_for "${candidate}" "service")"
    label="$(backend_config_value_for "${candidate}" "label")"
    log "Stopping inactive ${label} Docker container to reduce memory pressure"
    docker compose -f "${compose_file}" stop "${service}" >/dev/null || \
      warn "failed to stop inactive ${label} container"
  done
}

wait_for_backend() {
  local prefix
  local host_var
  local port_var
  local scheme_var
  local username_var
  local password_var
  local verify_certs_var

  if [[ "${BACKEND}" == "elasticsearch" ]]; then
    prefix="ELASTICSEARCH"
  else
    prefix="OPENSEARCH"
  fi

  host_var="${prefix}_HOST"
  port_var="${prefix}_PORT"
  scheme_var="${prefix}_SCHEME"
  username_var="${prefix}_ADMIN_USERNAME"
  password_var="${prefix}_ADMIN_PASSWORD"
  verify_certs_var="${prefix}_VERIFY_CERTS"

  log "Waiting for $(backend_label) at $(backend_url)"
  uv_run_dls wait-backend \
    --backend "${BACKEND}" \
    --host "${!host_var}" \
    --port "${!port_var}" \
    --scheme "${!scheme_var}" \
    --username "${!username_var}" \
    --password "${!password_var}" \
    --verify-certs "${!verify_certs_var}"
}

documented_seed_for() {
  local d="$1"
  local trial="$2"
  local seed

  seed="$(yq -r ".seeds[\"${d}:${trial}\"] // \"\"" "${CONFIG_FILE}")"
  [[ -n "${seed}" && "${seed}" != "null" ]] || return 1
  printf '%s\n' "${seed}"
}

generate_random_seed() {
  od -An -N8 -tu8 /dev/urandom | tr -d '[:space:]'
}

seed_for() {
  local d="$1"
  local trial="$2"

  documented_seed_for "${d}" "${trial}" || generate_random_seed
}

run_attack() {
  local d="$1"
  local trial="$2"
  local prefix
  local stats_file
  local log_file
  local reconstruction_json
  local seed
  local attack_arg
  local attack_cmd
  local attack_command_lines

  prefix="$(run_prefix "${d}" "${trial}")"
  stats_file="$(stats_file_for_prefix "${prefix}")"
  log_file="${LOG_DIR}/${prefix}.log"
  reconstruction_json="$(reconstruction_json_for_prefix "${prefix}")"
  seed="$(seed_for "${d}" "${trial}")"

  if [[ "${DESTRUCTIVE_RERUN_EXISTING_RESULTS}" -eq 0 && -f "${stats_file}" && -f "${reconstruction_json}" ]]; then
    log "Skipping D${d} trial ${trial}; found ${stats_file} and ${reconstruction_json}"
    STATS_FILES+=("${stats_file}")
    return
  fi

  if [[ "${DESTRUCTIVE_RERUN_EXISTING_RESULTS}" -eq 0 && -f "${stats_file}" ]]; then
    log "Skipping attack for D${d} trial ${trial}; regenerating missing reconstruction from ${stats_file}"
    STATS_FILES+=("${stats_file}")
    run_reconstruction "${stats_file}" "${prefix}"
    return
  fi

  log "Running D${d} trial ${trial} (seed ${seed})"
  log "Showing live progress for D${d} trial ${trial}; full log: ${log_file}"
  attack_cmd=()
  attack_command_lines="$(uv_run_dls build-command \
    --config "${CONFIG_FILE}" \
    --arguments-only \
    --backend "${BACKEND}" \
    --corpus-file "${ROOT_DIR}/dataset/enron_d${d}.jsonl" \
    --stats-file "${stats_file}" \
    --random-seed "${seed}" \
    --attack-log-file "${log_file}" \
    --rich-progress
  )"
  while IFS= read -r attack_arg; do
    [[ -n "${attack_arg}" ]] && attack_cmd+=("${attack_arg}")
  done <<< "${attack_command_lines}"
  attack_cmd=(uv_run_dls_unbuffered enumerate "${attack_cmd[@]}")

  if ! "${attack_cmd[@]}" >"${log_file}" 2>>"${log_file}"; then
    warn "Attack failed for D${d} trial ${trial}; log: ${log_file}"
    tail -n 80 "${log_file}" >&2 || true
    exit 1
  fi

  STATS_FILES+=("${stats_file}")
  log "Wrote ${stats_file}"
  run_reconstruction "${stats_file}" "${prefix}"
}

run_reconstruction() {
  local stats_file="$1"
  local prefix="$2"
  local output_prefix
  local text_output
  local json_output
  local reconstruction_k
  local reconstruction_source
  local reconstruction_traversal

  log "Running greedy de Bruijn reconstruction for ${prefix}"
  output_prefix="$(reconstruction_output_prefix "${prefix}")"
  text_output="${output_prefix}.txt"
  json_output="${output_prefix}.json"
  reconstruction_k="$(reconstruction_config_value "k")"
  reconstruction_source="$(reconstruction_config_value "source")"
  reconstruction_traversal="$(reconstruction_config_value "traversal")"

  uv_run_dls debruijn \
    "${stats_file}" \
    --k "${reconstruction_k}" \
    --source "${reconstruction_source}" \
    --traversal "${reconstruction_traversal}" \
    --output "${text_output}" \
    --json-output "${json_output}"
}

render_table() {
  local table_output="$1"
  local cmd=(uv_run_dls table)
  local stats_file

  log "Rendering ${table_output}"
  ensure_writable_output_file "${table_output}"
  for stats_file in "${STATS_FILES[@]}"; do
    cmd+=("${stats_file}")
  done
  cmd+=(--output "${table_output}")
  "${cmd[@]}"
  TABLE_OUTPUTS+=("${table_output}")
}

main() {
  parse_datasets "${DATASET_SPEC}"
  configure_backends
  run_preflight
  if [[ "${PREFLIGHT_ONLY}" -eq 1 ]]; then
    log "Doctor checks passed"
    exit 0
  fi
  check_python

  mkdir -p "${STATS_DIR}" "${LOG_DIR}" "${RECON_DIR}"
  log "Selected datasets: $(format_datasets)"

  for BACKEND in "${BACKENDS[@]}"; do
    STATS_FILES=()
    check_backend_dependencies
    log "Selected backend: $(backend_label)"
    if backend_required; then
      check_docker_dependencies
      configure_backend_env
      stop_inactive_backends
      start_backend
      wait_for_backend
    else
      log "All selected trials have existing stats; skipping $(backend_label) startup"
    fi

    for d in "${DATASETS[@]}"; do
      for ((trial = 1; trial <= TRIALS; trial++)); do
        run_attack "${d}" "${trial}"
      done
    done

    render_table "$(table_output_for_backend)"
  done

  cat <<EOF

Done.

Stats:
  ${STATS_DIR}

Logs:
  ${LOG_DIR}

Greedy reconstructions:
  ${RECON_DIR}

Tables:
EOF
  format_table_outputs
}

main
