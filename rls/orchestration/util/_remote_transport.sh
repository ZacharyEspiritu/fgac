# shellcheck shell=bash
# Transport abstraction: drive experiment machines independently of whoever
# (or whatever) provisioned them.
#
# WHY THIS EXISTS
# ---------------
# The existence / reconstruction experiments only ever execute commands on the
# ATTACKER machine (which holds the repo + venv) and reach the DATABASE *from*
# the attacker over the network — they never SSH the DB directly. So an
# experiment body needs only these things, none of which are provider-specific:
#   * run a command on a named role,
#   * copy a result file back from a role / push a file up to a role,
#   * the database's address as seen from the attacker.
# This file provides exactly that over a pluggable backend, so the experiment
# scripts contain no `gcloud` calls and can run against GCP VMs, AWS instances,
# bare-metal boxes, or anything else reachable by SSH.
#
# A "machine descriptor" (a small YAML file, parsed with `yq`) says how to reach
# the two roles. The provisioner writes it; the experiment scripts consume it.
#
# DESCRIPTOR FORMAT (.yml; requires `yq` — mikefarah v4 or python-yq — on PATH)
# ----------------------------------------------------------------------------
#   transport: gcloud|ssh        # default: gcloud
#   project: <gcp project>       # gcloud backend
#   zone: <default zone>         # gcloud backend (a per-role zone overrides it)
#   remote_dir: <path>           # repo checkout on the attacker
#   run_id: <id>                 # bookkeeping (managed teardown); optional
#   config_file: <path>          # bookkeeping; optional
#   provisioner: <path>          # informational; optional
#   db:
#     internal_addr: <ip|host>   # DB address the attacker connects to (required)
#     vm: <instance>             # gcloud backend
#     zone: <zone>               # gcloud backend (optional; else top-level zone)
#     host: <ip|host>            # ssh backend (rarely needed; DB reached via attacker)
#     user: <user>               # ssh backend (optional)
#     port: <port>               # ssh backend (optional, default 22)
#     key: <identity file>       # ssh backend (optional)
#   attacker:
#     vm: <instance>             # gcloud backend
#     zone: <zone>               # gcloud backend (optional; else top-level zone)
#     host: <ip|host>            # ssh backend
#     user: <user>               # ssh backend (optional)
#     port: <port>               # ssh backend (optional, default 22)
#     key: <identity file>       # ssh backend (optional)
#   noise:                       # OPTIONAL — only claims with a noise generator (e.g. Table 1)
#     vm: <instance>             # gcloud backend
#     zone: <zone>               # gcloud backend (optional; else top-level zone)
#     host: <ip|host>            # ssh backend
#     user: <user>               # ssh backend (optional)
#     port: <port>               # ssh backend (optional, default 22)
#     key: <identity file>       # ssh backend (optional)
#
# To run an experiment on two machines you set up yourself (no GCP), write a
# descriptor with `transport: ssh` and the attacker's host/user/port/key + the
# DB's address, and point the scripts at it with `--machines <file>`; nothing
# else changes.
#
# API (after `transport_load <descriptor.yml>`)
# ---------------------------------------------
#   transport_exec      <role> <command>          run command, inherit stdio
#   transport_exec_tty  <role> <command>          run with a PTY allocated (-t)
#   transport_fetch     <role> <remote> <local>   copy a remote file -> local
#   transport_push      <role> <local> <remote>   copy a local file/dir -> remote
#   transport_db_addr                              echo the DB internal address
#   transport_summary                              one-line description (stderr)
# Roles: "attacker"/"attack", "db"/"database"/"postgres", or "noise" (optional).

# Resolve a variable by name with an optional default; safe under `set -u`.
_tv() { local _n="$1"; printf '%s' "${!_n:-${2:-}}"; }

# Extract a scalar from the loaded YAML descriptor with yq. Works with both
# mikefarah yq (raw output) and python-yq (JSON output) by stripping a single
# layer of surrounding double-quotes; missing/null -> the optional default.
_yq() {
  local path="$1" def="${2:-}" out
  out="$(yq "${path}" "${TRANSPORT_DESCRIPTOR}" 2>/dev/null)" || out=""
  out="${out%\"}"; out="${out#\"}"
  if [[ -z "${out}" || "${out}" == "null" ]]; then printf '%s' "${def}"; else printf '%s' "${out}"; fi
}

# Map a role name to the descriptor variable prefix it uses.
_transport_prefix() {
  case "$1" in
    attacker|attack)      printf 'ATTACK' ;;
    db|database|postgres) printf 'DB' ;;
    noise)                printf 'NOISE' ;;
    *) echo "transport: unknown role '$1' (expected attacker|db|noise)" >&2; return 1 ;;
  esac
}

# Load and validate a YAML descriptor. Must be called before any other function.
transport_load() {
  local descriptor="$1"
  if [[ -z "${descriptor}" || ! -f "${descriptor}" ]]; then
    echo "transport: machine descriptor not found: '${descriptor}'" >&2
    return 1
  fi
  command -v yq >/dev/null 2>&1 || {
    echo "transport: yq not found in PATH (required to parse ${descriptor}). Install mikefarah yq or python-yq." >&2
    return 1
  }
  TRANSPORT_DESCRIPTOR="${descriptor}"

  # Parse the descriptor once into the variables the functions below read. The
  # MACHINES_* names are kept so callers can also read them via _tv MACHINES_*.
  MACHINES_TRANSPORT="$(_yq '.transport' gcloud)"
  MACHINES_PROJECT="$(_yq '.project')"
  MACHINES_ZONE="$(_yq '.zone')"
  MACHINES_REMOTE_DIR="$(_yq '.remote_dir')"
  MACHINES_RUN_ID="$(_yq '.run_id')"
  MACHINES_CONFIG_FILE="$(_yq '.config_file')"
  MACHINES_DB_INTERNAL_ADDR="$(_yq '.db.internal_addr')"
  MACHINES_DB_VM="$(_yq '.db.vm')"
  MACHINES_DB_ZONE="$(_yq '.db.zone')"
  MACHINES_DB_HOST="$(_yq '.db.host')"
  MACHINES_DB_USER="$(_yq '.db.user')"
  MACHINES_DB_PORT="$(_yq '.db.port')"
  MACHINES_DB_KEY="$(_yq '.db.key')"
  MACHINES_ATTACK_VM="$(_yq '.attacker.vm')"
  MACHINES_ATTACK_ZONE="$(_yq '.attacker.zone')"
  MACHINES_ATTACK_HOST="$(_yq '.attacker.host')"
  MACHINES_ATTACK_USER="$(_yq '.attacker.user')"
  MACHINES_ATTACK_PORT="$(_yq '.attacker.port')"
  MACHINES_ATTACK_KEY="$(_yq '.attacker.key')"
  # Optional noise role (claims with a noise generator, e.g. Table 1); empty when absent.
  MACHINES_NOISE_VM="$(_yq '.noise.vm')"
  MACHINES_NOISE_ZONE="$(_yq '.noise.zone')"
  MACHINES_NOISE_HOST="$(_yq '.noise.host')"
  MACHINES_NOISE_USER="$(_yq '.noise.user')"
  MACHINES_NOISE_PORT="$(_yq '.noise.port')"
  MACHINES_NOISE_KEY="$(_yq '.noise.key')"

  case "${MACHINES_TRANSPORT}" in
    gcloud) command -v gcloud >/dev/null 2>&1 || { echo "transport: gcloud not found in PATH." >&2; return 1; } ;;
    ssh)    command -v ssh    >/dev/null 2>&1 || { echo "transport: ssh not found in PATH." >&2; return 1; } ;;
    *) echo "transport: unsupported transport='${MACHINES_TRANSPORT}' in ${descriptor} (expected gcloud|ssh)." >&2; return 1 ;;
  esac
}

# Echo the DB address the attacker should connect to.
transport_db_addr() { printf '%s' "$(_tv MACHINES_DB_INTERNAL_ADDR)"; }

# One-line human summary (to stderr) of what we're driving.
transport_summary() {
  if [[ "$(_tv MACHINES_TRANSPORT gcloud)" == "gcloud" ]]; then
    local noise_part=""
    [[ -n "$(_tv MACHINES_NOISE_VM)" ]] && noise_part=" noise=$(_tv MACHINES_NOISE_VM)@$(_tv MACHINES_NOISE_ZONE "$(_tv MACHINES_ZONE)")"
    echo "transport=gcloud project=$(_tv MACHINES_PROJECT)" \
         "db=$(_tv MACHINES_DB_VM)@$(_tv MACHINES_DB_ZONE "$(_tv MACHINES_ZONE)")" \
         "attacker=$(_tv MACHINES_ATTACK_VM)@$(_tv MACHINES_ATTACK_ZONE "$(_tv MACHINES_ZONE)")${noise_part}" \
         "db_addr=$(_tv MACHINES_DB_INTERNAL_ADDR)" >&2
  else
    echo "transport=ssh" \
         "attacker=$(_tv MACHINES_ATTACK_USER)@$(_tv MACHINES_ATTACK_HOST):$(_tv MACHINES_ATTACK_PORT 22)" \
         "db_addr=$(_tv MACHINES_DB_INTERNAL_ADDR)" >&2
  fi
}

# Internal: run a command on a role, with or without a PTY.
_transport_run() {
  local want_tty="$1" role="$2" cmd="$3"
  local pfx; pfx="$(_transport_prefix "${role}")" || return 1
  if [[ "${MACHINES_TRANSPORT}" == "gcloud" ]]; then
    local vm zone project
    vm="$(_tv "MACHINES_${pfx}_VM")"
    zone="$(_tv "MACHINES_${pfx}_ZONE" "$(_tv MACHINES_ZONE)")"
    project="$(_tv MACHINES_PROJECT)"
    local args=(compute ssh "${vm}" --zone "${zone}")
    [[ -n "${project}" ]] && args+=(--project "${project}")
    [[ -n "${want_tty}" ]] && args+=(--ssh-flag="-t")
    gcloud "${args[@]}" --command "${cmd}"
  else
    local host user port key
    host="$(_tv "MACHINES_${pfx}_HOST")"
    user="$(_tv "MACHINES_${pfx}_USER")"
    port="$(_tv "MACHINES_${pfx}_PORT" 22)"
    key="$(_tv "MACHINES_${pfx}_KEY")"
    local args=(-p "${port}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
    [[ -n "${key}" ]] && args+=(-i "${key}")
    [[ -n "${want_tty}" ]] && args+=(-t)
    local target="${host}"; [[ -n "${user}" ]] && target="${user}@${host}"
    ssh "${args[@]}" "${target}" "${cmd}"
  fi
}

# Run a command on a role (stdio inherited).
transport_exec()     { _transport_run ""    "$1" "$2"; }
# Run a command on a role with a PTY allocated (mirrors the attack job's -t).
transport_exec_tty() { _transport_run "tty" "$1" "$2"; }

# Copy a file from a role back to the local path (parents created).
transport_fetch() {
  local role="$1" remote="$2" local_path="$3"
  local pfx; pfx="$(_transport_prefix "${role}")" || return 1
  local d; d="$(dirname "${local_path}")"
  [[ -n "${d}" && "${d}" != "." ]] && mkdir -p "${d}"
  if [[ "${MACHINES_TRANSPORT}" == "gcloud" ]]; then
    local vm zone project
    vm="$(_tv "MACHINES_${pfx}_VM")"
    zone="$(_tv "MACHINES_${pfx}_ZONE" "$(_tv MACHINES_ZONE)")"
    project="$(_tv MACHINES_PROJECT)"
    local args=(compute scp "${vm}:${remote}" "${local_path}" --zone "${zone}")
    [[ -n "${project}" ]] && args+=(--project "${project}")
    gcloud "${args[@]}"
  else
    local host user port key
    host="$(_tv "MACHINES_${pfx}_HOST")"
    user="$(_tv "MACHINES_${pfx}_USER")"
    port="$(_tv "MACHINES_${pfx}_PORT" 22)"
    key="$(_tv "MACHINES_${pfx}_KEY")"
    local args=(-P "${port}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
    [[ -n "${key}" ]] && args+=(-i "${key}")
    local target="${host}"; [[ -n "${user}" ]] && target="${user}@${host}"
    scp "${args[@]}" "${target}:${remote}" "${local_path}"
  fi
}

# Copy a local file (or directory) up to a role's remote path.
transport_push() {
  local role="$1" local_path="$2" remote="$3"
  local pfx; pfx="$(_transport_prefix "${role}")" || return 1
  local recurse=""; [[ -d "${local_path}" ]] && recurse=1
  if [[ "${MACHINES_TRANSPORT}" == "gcloud" ]]; then
    local vm zone project
    vm="$(_tv "MACHINES_${pfx}_VM")"
    zone="$(_tv "MACHINES_${pfx}_ZONE" "$(_tv MACHINES_ZONE)")"
    project="$(_tv MACHINES_PROJECT)"
    local args=(compute scp)
    [[ -n "${recurse}" ]] && args+=(--recurse)
    args+=("${local_path}" "${vm}:${remote}" --zone "${zone}")
    [[ -n "${project}" ]] && args+=(--project "${project}")
    gcloud "${args[@]}"
  else
    local host user port key
    host="$(_tv "MACHINES_${pfx}_HOST")"
    user="$(_tv "MACHINES_${pfx}_USER")"
    port="$(_tv "MACHINES_${pfx}_PORT" 22)"
    key="$(_tv "MACHINES_${pfx}_KEY")"
    local args=(-P "${port}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
    [[ -n "${recurse}" ]] && args+=(-r)
    [[ -n "${key}" ]] && args+=(-i "${key}")
    local target="${host}"; [[ -n "${user}" ]] && target="${user}@${host}"
    scp "${args[@]}" "${local_path}" "${target}:${remote}"
  fi
}
