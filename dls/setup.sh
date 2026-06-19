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

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

absolute_path() {
  local path="$1"
  case "${path}" in
    /*) printf '%s\n' "${path}" ;;
    *) printf '%s/%s\n' "${ROOT_DIR}" "${path}" ;;
  esac
}

usage() {
  cat <<EOF
Set up local dependencies for the OpenSearch/Elasticsearch experiments.

This script:
  - ensures uv is available and on PATH for future shells
  - uses uv to install Python ${PYTHON_TARGET} and sync the project virtualenv
  - checks for yq, used to read YAML experiment config files
  - checks for Docker and Docker Compose v2
  - starts the local OpenSearch and Elasticsearch Docker containers
  - checks for the Google Cloud CLI

When a dependency is missing, the script attempts to install it with:
  - Homebrew on macOS
  - apt-get on Debian/Ubuntu Linux
  - the official uv standalone installer for uv on Linux

Options:
  --venv PATH        Virtualenv path, default: ${ROOT_DIR}/.venv
  --check-only       Only check dependencies; do not install anything
  --skip-system      Do not install system tools such as Docker or gcloud
  --skip-gcloud      Do not check or install gcloud
  --skip-docker-start
                     Do not start local OpenSearch/Elasticsearch containers
  -h, --help         Show this help

Examples:
  ./setup.sh
  ./setup.sh --check-only
  ./setup.sh --skip-gcloud
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

check_file() {
  local path="$1"
  [[ -f "${path}" ]] || die "missing ${path}"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

sudo_cmd() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

run_or_explain() {
  if [[ "${CHECK_ONLY}" -eq 1 ]]; then
    warn "check-only mode: would run: $*"
    return 1
  fi
  "$@"
}

platform() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux) echo "linux" ;;
    *) echo "unknown" ;;
  esac
}

install_with_brew() {
  local package="$1"
  shift
  have brew || die "Homebrew is required to install ${package} on macOS"
  log "Installing ${package} with Homebrew"
  run_or_explain brew install "$@" || return 1
}

apt_update_once() {
  if [[ "${APT_UPDATED}" -eq 0 ]]; then
    log "Updating apt package lists"
    run_or_explain sudo_cmd apt-get update || return 1
    APT_UPDATED=1
  fi
}

install_with_apt() {
  local package="$1"
  shift
  have apt-get || die "apt-get is required to install ${package} on this Linux host"
  apt_update_once
  log "Installing ${package} with apt-get"
  run_or_explain sudo_cmd apt-get install -y "$@" || return 1
}

uv_bin() {
  if have uv; then
    command -v uv
  elif [[ -x "${HOME}/.local/bin/uv" ]]; then
    printf '%s\n' "${HOME}/.local/bin/uv"
  elif [[ -x "${HOME}/.cargo/bin/uv" ]]; then
    printf '%s\n' "${HOME}/.cargo/bin/uv"
  else
    return 1
  fi
}

install_uv() {
  [[ "${SKIP_SYSTEM}" -eq 0 ]] || die "uv is required to install Python ${PYTHON_TARGET} and Python packages"

  case "$(platform)" in
    macos)
      install_with_brew "uv" uv
      ;;
    linux)
      if ! have curl; then
        install_with_apt "curl" curl
      fi
      log "Installing uv with the official standalone installer"
      run_or_explain bash -o pipefail -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' \
        || die "uv installation failed"
      export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
      ;;
    *)
      die "Install uv manually, then rerun ${ROOT_DIR}/setup.sh"
      ;;
  esac
}

path_contains_dir() {
  local dir="$1"
  case ":${PATH}:" in
    *":${dir}:"*) return 0 ;;
    *) return 1 ;;
  esac
}

uv_path_expr() {
  local dir="$1"
  case "${dir}" in
    "${HOME}"/*) printf '${HOME}%s\n' "${dir#"${HOME}"}" ;;
    *) printf '%s\n' "${dir}" ;;
  esac
}

uv_shell_startup_files() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"
  case "${shell_name}" in
    zsh)
      printf '%s\n' "${HOME}/.zshrc" "${HOME}/.zprofile"
      ;;
    bash)
      if [[ "$(platform)" == "macos" ]]; then
        printf '%s\n' "${HOME}/.bash_profile" "${HOME}/.bashrc"
      else
        printf '%s\n' "${HOME}/.bashrc" "${HOME}/.profile"
      fi
      ;;
    *)
      printf '%s\n' "${HOME}/.profile"
      ;;
  esac
}

ensure_uv_path_configured() {
  local uv_dir="$1"
  local file startup_file path_expr
  local -a startup_files=()

  [[ -d "${uv_dir}" ]] || return

  if ! path_contains_dir "${uv_dir}"; then
    export PATH="${uv_dir}:${PATH}"
    log "Added ${uv_dir} to PATH for this setup run"
  fi

  # The standalone uv installer writes under one of these directories. If uv
  # came from Homebrew/apt/etc., assume that package manager owns shell PATH setup.
  case "${uv_dir}" in
    "${HOME}/.local/bin"|\
    "${HOME}/.cargo/bin") ;;
    *) return ;;
  esac

  path_expr="$(uv_path_expr "${uv_dir}")"
  while IFS= read -r file; do
    startup_files+=("${file}")
  done < <(uv_shell_startup_files)

  for file in "${startup_files[@]}"; do
    if [[ -f "${file}" ]] && { grep -Fq "${uv_dir}" "${file}" || grep -Fq "${path_expr}" "${file}"; }; then
      PATH_STARTUP_FILE="${file}"
      return
    fi
  done

  startup_file=""
  for file in "${startup_files[@]}"; do
    if [[ -f "${file}" ]]; then
      startup_file="${file}"
      break
    fi
  done
  if [[ -z "${startup_file}" && "${#startup_files[@]}" -gt 0 ]]; then
    startup_file="${startup_files[0]}"
  fi
  [[ -n "${startup_file}" ]] || return

  if [[ "${CHECK_ONLY}" -eq 1 ]]; then
    warn "check-only mode: would add ${uv_dir} to ${startup_file}"
    return
  fi

  mkdir -p "$(dirname "${startup_file}")"
  if ! {
    printf '\n'
    printf '# Added by artifact setup: uv\n'
    printf 'case ":%s:" in\n' '${PATH}'
    printf '  *":%s:"*) ;;\n' "${path_expr}"
    printf '  *) export PATH="%s:%s" ;;\n' "${path_expr}" '${PATH}'
    printf 'esac\n'
  } >> "${startup_file}"; then
    warn "could not update ${startup_file}; add ${uv_dir} to PATH manually"
    return
  fi
  PATH_STARTUP_FILE="${startup_file}"
  log "Added ${uv_dir} to ${startup_file} for future shells"
}

ensure_uv() {
  if ! UV_BIN="$(uv_bin)"; then
    if [[ "${CHECK_ONLY}" -eq 1 ]]; then
      warn "check-only mode: uv not found; would install uv and Python ${PYTHON_TARGET}"
      return
    fi
    install_uv
    UV_BIN="$(uv_bin)" || die "uv is still unavailable"
  fi

  ensure_uv_path_configured "$(dirname "${UV_BIN}")"
  log "Found uv: $("${UV_BIN}" --version)"
}

ensure_venv() {
  if [[ "${CHECK_ONLY}" -eq 1 ]]; then
    warn "check-only mode: would run: uv python install ${PYTHON_TARGET}"
    warn "check-only mode: would run: uv sync --locked --project ${ROOT_DIR} --python ${PYTHON_TARGET}"
    return
  fi
  [[ -n "${UV_BIN}" ]] || die "uv not resolved; ensure_uv must run before ensure_venv"

  log "Ensuring Python ${PYTHON_TARGET} is available via uv"
  "${UV_BIN}" python install "${PYTHON_TARGET}"
  log "Syncing Python project environment at ${VENV_DIR} (Python ${PYTHON_TARGET})"
  UV_PROJECT_ENVIRONMENT="${VENV_DIR}" "${UV_BIN}" sync --locked --project "${ROOT_DIR}" --python "${PYTHON_TARGET}"
}

project_python() {
  if [[ -x "${VENV_DIR}/bin/python3" ]]; then
    printf '%s\n' "${VENV_DIR}/bin/python3"
  elif [[ -x "${VENV_DIR}/bin/python" ]]; then
    printf '%s\n' "${VENV_DIR}/bin/python"
  else
    die "missing project Python at ${VENV_DIR}; rerun ${ROOT_DIR}/setup.sh"
  fi
}

install_cli_tool() {
  local python_bin
  local tool_bin_dir
  [[ -n "${UV_BIN}" ]] || die "uv not resolved; ensure_uv must run before install_cli_tool"

  if [[ "${CHECK_ONLY}" -eq 1 ]]; then
    warn "check-only mode: would install unfilter-dls as a uv tool"
    return
  fi

  python_bin="$(project_python)"
  log "Installing unfilter-dls command with uv tool"
  "${UV_BIN}" tool install --force --editable "${ROOT_DIR}" --python "${python_bin}"

  tool_bin_dir="$("${UV_BIN}" tool dir --bin)"
  ensure_uv_path_configured "${tool_bin_dir}"
  if [[ -x "${tool_bin_dir}/unfilter-dls" ]]; then
    log "Installed unfilter-dls: ${tool_bin_dir}/unfilter-dls"
  else
    warn "unfilter-dls was installed, but ${tool_bin_dir}/unfilter-dls was not found"
  fi
}

ensure_docker() {
  if have docker; then
    log "Found Docker: $(docker --version)"
  elif [[ "${SKIP_SYSTEM}" -eq 0 ]]; then
    case "$(platform)" in
      macos)
        install_with_brew "Docker Desktop" --cask docker
        warn "Start Docker Desktop once before running docker compose commands."
        ;;
      linux)
        run_or_explain bash -c "curl -fsSL https://get.docker.com -o get-docker.sh"
        run_or_explain sudo_cmd sh get-docker.sh
        if ! groups "${USER:-}" 2>/dev/null | grep -q '\bdocker\b'; then
          warn "To run Docker without sudo, add your user to the docker group:"
          warn "  sudo usermod -aG docker ${USER:-$LOGNAME}"
          warn "Then log out and back in."
        fi
        ;;
      *)
        die "Install Docker manually, then rerun ${ROOT_DIR}/setup.sh"
        ;;
    esac
  else
    die "Docker is required for local backend containers"
  fi

  if docker compose version >/dev/null 2>&1; then
    log "Found Docker Compose: $(docker compose version)"
  elif [[ "$(platform)" == "linux" && "${SKIP_SYSTEM}" -eq 0 ]]; then
    install_with_apt "Docker Compose plugin" docker-compose-plugin
  else
    die "Docker Compose v2 is required; install the Docker Compose plugin"
  fi
}

ensure_yq() {
  if have yq; then
    log "Found yq: $(yq --version 2>/dev/null | head -n 1)"
    return
  fi

  [[ "${SKIP_SYSTEM}" -eq 0 ]] || die "yq is required to read YAML config files"

  case "$(platform)" in
    macos)
      install_with_brew "yq" yq
      ;;
    linux)
      install_with_apt "yq" yq
      ;;
    *)
      die "Install yq manually, then rerun ${ROOT_DIR}/setup.sh"
      ;;
  esac

  have yq || die "yq is still unavailable"
}

ensure_gcloud_macos() {
  install_with_brew "Google Cloud CLI" --cask google-cloud-sdk
}

ensure_gcloud_linux() {
  have apt-get || die "Install Google Cloud CLI manually on this Linux host"
  apt_update_once
  install_with_apt "Google Cloud CLI prerequisites" apt-transport-https ca-certificates curl gnupg

  if [[ ! -f /etc/apt/sources.list.d/google-cloud-sdk.list ]]; then
    log "Adding Google Cloud CLI apt repository"
    run_or_explain sudo_cmd install -m 0755 -d /usr/share/keyrings || return 1
    run_or_explain bash -c \
      "curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor > /tmp/cloud.google.gpg" || return 1
    run_or_explain sudo_cmd install -m 0644 /tmp/cloud.google.gpg \
      /usr/share/keyrings/cloud.google.gpg || return 1
    run_or_explain sudo_cmd sh -c \
      "echo 'deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main' > /etc/apt/sources.list.d/google-cloud-sdk.list" || return 1
    APT_UPDATED=0
  fi

  install_with_apt "Google Cloud CLI" google-cloud-cli
}

ensure_gcloud() {
  if [[ "${SKIP_GCLOUD}" -eq 1 ]]; then
    log "Skipping gcloud check"
    return
  fi

  if have gcloud; then
    log "Found Google Cloud CLI: $(gcloud --version | head -n 1)"
    return
  fi

  [[ "${SKIP_SYSTEM}" -eq 0 ]] || die "Google Cloud CLI is required for GCP runs"

  case "$(platform)" in
    macos) ensure_gcloud_macos ;;
    linux) ensure_gcloud_linux ;;
    *) die "Install Google Cloud CLI manually, then rerun ${ROOT_DIR}/setup.sh" ;;
  esac

  have gcloud || die "Google Cloud CLI is still unavailable"
}

check_standard_tools() {
  local missing=()
  for tool in bash tar ssh scp; do
    have "${tool}" || missing+=("${tool}")
  done
  if [[ "${#missing[@]}" -gt 0 ]]; then
    die "Missing standard tools: ${missing[*]}"
  fi
  log "Found standard shell tools: bash, tar, ssh, scp"
}

backend_config_value() {
  local backend="$1"
  local key="$2"
  local value

  value="$(yq -r ".backends.${backend}.${key} | tostring" "${CONFIG_FILE}")"
  if [[ -z "${value}" || "${value}" == "null" ]]; then
    die "missing .backends.${backend}.${key} in ${CONFIG_FILE}"
  fi
  printf '%s\n' "${value}"
}

start_local_backend() {
  local backend="$1"
  local label
  local compose_file
  local service

  label="$(backend_config_value "${backend}" "label")"
  compose_file="$(absolute_path "$(backend_config_value "${backend}" "compose_file")")"
  service="$(backend_config_value "${backend}" "service")"

  check_file "${compose_file}"
  log "Starting local ${label}: docker compose -f ${compose_file} up -d ${service}"
  docker compose -f "${compose_file}" up -d "${service}"
}

start_local_backends() {
  if [[ "${CHECK_ONLY}" -eq 1 ]]; then
    warn "check-only mode: would start local OpenSearch and Elasticsearch containers"
    return
  fi
  if [[ "${SKIP_DOCKER_START}" -eq 1 ]]; then
    log "Skipping local Docker backend startup"
    return
  fi

  check_file "${CONFIG_FILE}"
  if ! docker info >/dev/null 2>&1; then
    die "Docker daemon is not running; start Docker Desktop or pass --skip-docker-start"
  fi

  start_local_backend opensearch
  start_local_backend elasticsearch
}

print_shell_restart_panel() {
  local title_color body_color reset source_cmd
  source_cmd="$(shell_profile_source_command)"
  if [[ -t 1 ]]; then
    title_color="$(printf '\033[1;33m')"
    body_color="$(printf '\033[1;36m')"
    reset="$(printf '\033[0m')"
  else
    title_color=""
    body_color=""
    reset=""
  fi

  printf '\n%s+------------------------------------------------------------------------------+%s\n' "${title_color}" "${reset}"
  printf '%s| IMPORTANT: restart your terminal or re-source your shell profile             |%s\n' "${title_color}" "${reset}"
  printf '%s+------------------------------------------------------------------------------+%s\n' "${title_color}" "${reset}"
  printf '%s| setup.sh installed unfilter-dls into the uv tool bin directory.              |%s\n' "${body_color}" "${reset}"
  printf '%s| Your current shell may not see that PATH change yet.                         |%s\n' "${body_color}" "${reset}"
  printf '%s|                                                                              |%s\n' "${body_color}" "${reset}"
  printf '%s| Before running unfilter-dls, either open a new terminal or run:              |%s\n' "${body_color}" "${reset}"
  printf '%s|   %-74s |%s\n' "${body_color}" "${source_cmd}" "${reset}"
  printf '%s+------------------------------------------------------------------------------+%s\n' "${title_color}" "${reset}"
}

shell_profile_source_command() {
  local file startup_file
  local -a startup_files=()

  if [[ -n "${PATH_STARTUP_FILE}" ]]; then
    startup_file="${PATH_STARTUP_FILE}"
  else
    while IFS= read -r file; do
      startup_files+=("${file}")
    done < <(uv_shell_startup_files)

    for file in "${startup_files[@]}"; do
      if [[ -f "${file}" ]]; then
        startup_file="${file}"
        break
      fi
    done
    if [[ -z "${startup_file}" && "${#startup_files[@]}" -gt 0 ]]; then
      startup_file="${startup_files[0]}"
    fi
  fi

  if [[ -z "${startup_file}" ]]; then
    startup_file="${HOME}/.profile"
  fi

  case "${startup_file}" in
    "${HOME}"/*) printf 'source ~/%s\n' "${startup_file#"${HOME}/"}" ;;
    *) printf 'source %q\n' "${startup_file}" ;;
  esac
}

print_next_steps() {
  if [[ "${CHECK_ONLY}" -eq 1 ]]; then
    echo
    echo "Dependency check complete."
    return
  fi

  cat <<EOF

Setup complete.

The run script invokes Python through uv internally; no manual virtualenv activation is needed.

Command-line entry point:
  unfilter-dls --help
  unfilter-dls doctor --config config/config.yml

If unfilter-dls is not found in this shell, follow the terminal restart/source
instruction shown below.

Local backend containers:
  OpenSearch and Elasticsearch have been started.

Stop local backend containers:
  ${ROOT_DIR}/teardown.sh

For GCP experiments, authenticate if needed:
  gcloud auth login
  gcloud auth application-default login
EOF
  print_shell_restart_panel
}

VENV_DIR=".venv"
CONFIG_FILE="config/config.yml"
PYTHON_TARGET="3.10" # major.minor we pin the interpreter to
UV_BIN=""            # resolved uv executable (set by ensure_uv)
PATH_STARTUP_FILE="" # shell startup file containing the uv tool PATH entry
CHECK_ONLY=0
SKIP_SYSTEM=0
SKIP_GCLOUD=0
SKIP_DOCKER_START=0
APT_UPDATED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --venv)
      VENV_DIR="${2:?missing value for --venv}"
      shift 2
      ;;
    --check-only)
      CHECK_ONLY=1
      shift
      ;;
    --skip-system)
      SKIP_SYSTEM=1
      shift
      ;;
    --skip-gcloud)
      SKIP_GCLOUD=1
      shift
      ;;
    --skip-docker-start)
      SKIP_DOCKER_START=1
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

VENV_DIR="$(absolute_path "${VENV_DIR}")"
CONFIG_FILE="$(absolute_path "${CONFIG_FILE}")"

[[ -f "${ROOT_DIR}/requirements.txt" ]] || die "${ROOT_DIR}/requirements.txt not found"
[[ -f "${ROOT_DIR}/pyproject.toml" ]] || die "${ROOT_DIR}/pyproject.toml not found"

check_standard_tools
ensure_uv
ensure_venv
install_cli_tool
ensure_yq
ensure_docker
ensure_gcloud
start_local_backends
print_next_steps
