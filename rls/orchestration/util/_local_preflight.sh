# shellcheck shell=bash

# Read-only local dependency preflight for the top-level artifact run scripts.
# Keep this separate from setup.sh: setup installs dependencies, while the run
# scripts should only fail early with a clear fix before provisioning cloud VMs.

rls_local_preflight() {  # <config.yml> <require-tex:0|1> [require-gcloud:0|1]
  local config="${1:-}"
  local require_tex="${2:-0}"
  local require_gcloud="${3:-1}"
  local root_dir
  root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

  local expected_venv="${RLS_VENV_DIR:-${root_dir}/.venv}"
  local failures=()

  _rls_pf_add_failure() {
    failures+=("$1")
  }

  _rls_pf_real_dir() {
    local path="$1"
    [[ -d "${path}" ]] || return 1
    (cd "${path}" && pwd -P)
  }

  _rls_pf_have() {
    command -v "$1" >/dev/null 2>&1
  }

  _rls_pf_uv_run_python() {
    local venv="$1"
    shift
    if [[ "${venv}" == "${root_dir}/.venv" ]]; then
      uv --project "${root_dir}" run --frozen python "$@"
    else
      UV_PROJECT_ENVIRONMENT="${venv}" uv --project "${root_dir}" run --frozen python "$@"
    fi
  }

  _rls_pf_yq_scalar() {
    local expr="$1" file="$2" out
    out="$(yq "${expr}" "${file}" 2>/dev/null)" || return 1
    out="${out%\"}"
    out="${out#\"}"
    [[ "${out}" == "null" ]] && out=""
    printf '%s\n' "${out}"
  }

  local tool
  for tool in bash tar ssh scp git yq uv; do
    if ! _rls_pf_have "${tool}"; then
      _rls_pf_add_failure "missing '${tool}' on PATH"
    fi
  done
  if [[ "${require_gcloud}" == "1" ]] && ! _rls_pf_have gcloud; then
    _rls_pf_add_failure "missing 'gcloud' on PATH"
  fi

  if [[ "${require_tex}" == "1" ]]; then
    if ! _rls_pf_have xelatex; then
      _rls_pf_add_failure "missing 'xelatex' on PATH (needed for local PGF figure rendering)"
    fi
  fi

  if [[ -z "${config}" ]]; then
    _rls_pf_add_failure "internal error: local preflight called without a config path"
  elif [[ ! -f "${config}" ]]; then
    _rls_pf_add_failure "config not found: ${config}"
  elif _rls_pf_have yq && [[ "${require_gcloud}" == "1" ]]; then
    local configured_project active_project
    if ! configured_project="$(_rls_pf_yq_scalar '.project' "${config}")"; then
      _rls_pf_add_failure "could not parse ${config} with yq"
    elif [[ -z "${configured_project}" ]] && _rls_pf_have gcloud; then
      active_project="$(gcloud config get-value project 2>/dev/null || true)"
      active_project="${active_project//$'\r'/}"
      active_project="${active_project//$'\n'/}"
      if [[ -z "${active_project}" || "${active_project}" == "(unset)" ]]; then
        _rls_pf_add_failure "no GCP project configured; set project: in ${config} or run 'gcloud config set project <project-id>'"
      fi
    fi
  fi

  if [[ "${require_gcloud}" == "1" ]] && _rls_pf_have gcloud; then
    local active_account
    active_account="$(gcloud auth list --filter='status:ACTIVE' --format='value(account)' 2>/dev/null | head -n 1 || true)"
    if [[ -z "${active_account}" ]]; then
      _rls_pf_add_failure "no active gcloud account; run 'gcloud auth login'"
    fi
  fi

  if [[ ! -x "${expected_venv}/bin/python3" ]]; then
    _rls_pf_add_failure "project virtualenv missing at ${expected_venv}; run './setup.sh'"
  fi

  local expected_real=""
  if [[ -d "${expected_venv}" ]]; then
    expected_real="$(_rls_pf_real_dir "${expected_venv}" || true)"
  fi

  if _rls_pf_have uv && [[ -x "${expected_venv}/bin/python3" ]]; then
    local pycheck
    if ! pycheck="$(_rls_pf_uv_run_python "${expected_venv}" "${root_dir}/orchestration/util/check_deps.py" \
        --requirements "${root_dir}/requirements.txt" \
        --expected-venv "${expected_real}" \
        --min-python 3.10 2>&1)"; then
      while IFS= read -r line; do
        [[ -n "${line}" ]] && _rls_pf_add_failure "${line}"
      done <<< "${pycheck}"
    fi
  fi

  if [[ "${#failures[@]}" -gt 0 ]]; then
    {
      echo "Local dependency preflight failed:"
      local failure
      for failure in "${failures[@]}"; do
        echo "  - ${failure}"
      done
      echo
      echo "Run from the rls directory:"
      echo "  ./setup.sh"
    } >&2
    return 1
  fi
}

rls_uv_run_python() {
  local root_dir expected_venv
  root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  expected_venv="${RLS_VENV_DIR:-${root_dir}/.venv}"
  if [[ "${expected_venv}" == "${root_dir}/.venv" ]]; then
    uv --project "${root_dir}" run --frozen python "$@"
  else
    UV_PROJECT_ENVIRONMENT="${expected_venv}" uv --project "${root_dir}" run --frozen python "$@"
  fi
}

rls_uv_run_module() {
  rls_uv_run_python -m "$@"
}
