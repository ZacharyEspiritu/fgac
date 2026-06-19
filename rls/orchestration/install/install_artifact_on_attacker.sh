#!/usr/bin/env bash
# Sync the repo + build the Python venv on the attacker machine.
#
# This is the "set up the experiment code on the machines" step, split out of
# provisioning: orchestration/provision/provision_vms.sh creates bare VMs (no repo, no
# data) and writes a machine descriptor; this script consumes that descriptor
# and pushes the repo + builds the venv over the provider-agnostic transport
# (orchestration/util/_remote_transport.sh). orchestration/run_existence_experiment.sh then runs the
# experiment. Because it goes through the transport, it works against GCP VMs
# (gcloud descriptor) or any SSH-reachable machine (ssh descriptor) alike — so
# "bring your own machines" only requires bare boxes; this builds the venv for
# you.
#
# Stage 0 installs the attacker's OS packages (python3-venv/pip, git,
# postgresql-client, and texlive for xelatex) over the transport, so a
# freshly-provisioned **bare** VM works with no toolchain baked in. This needs
# sudo + apt (Debian/Ubuntu). The package set, and the repo that is pushed (this
# checkout), are FIXED in the script — the caller cannot change them.
#
# Usage (--config <file> and --machines <file> are REQUIRED):
#   bash orchestration/install/install_artifact_on_attacker.sh --config orchestration/config/shared_config.yml --machines results/machines/${RUN_ID}.yml
#
# Inputs:
#   --config <file>    - REQUIRED: config.yml to read (e.g. orchestration/config/shared_config.yml)
#   --machines <file>  - REQUIRED: machine descriptor (e.g. results/machines/<RUN_ID>.yml)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATION_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${ORCHESTRATION_DIR}/.." && pwd)"
cd "${REPO_ROOT}"   # tar of the local working tree resolves from here

MACHINES_FILE_ARG=""
CONFIG_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machines) MACHINES_FILE_ARG="$2"; shift 2 ;;
    --config)   CONFIG_ARG="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Load shared infra/plumbing config from --config (REQUIRED; any claim's config.yml);
# sets the env + CONFIG_FILE=/dev/null so the shared GCP scripts use it.
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_load_gcloud_config.sh" "${CONFIG_ARG}"
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_remote_transport.sh"

# Fixed (not caller-overridable): always push this checkout.
LOCAL_REPO_DIR="${REPO_ROOT}"

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] $*"; }

if [[ -z "${MACHINES_FILE_ARG}" ]]; then
  echo "--machines <file> is required (e.g. --machines results/machines/${RUN_ID:-<id>}.yml)." >&2
  exit 1
fi
MACHINES_FILE="${MACHINES_FILE_ARG}"
transport_load "${MACHINES_FILE}"

REMOTE_DIR="$(_tv MACHINES_REMOTE_DIR)"
REMOTE_DIR="${REMOTE_DIR:-${REMOTE_BASE_DIR:-rls-dir}/scratch}"
REMOTE_PARENT_DIR="$(dirname "${REMOTE_DIR}")"

log "=== Sync repo + venv onto the attacker ==="
log "  Machines:   ${MACHINES_FILE}"
transport_summary
log "  Remote dir: ${REMOTE_DIR}"

# ---- Stage 0: install the attacker's OS dependencies ----------------------
# These used to be baked into the attacker VM's cloud-init startup script (in
# orchestration/provision/util/setup_gcloud_attacker_vm.sh); they live here now so
# provisioning hands over a bare VM. Needs sudo + apt (Debian/Ubuntu). The
# package set is FIXED here (not caller-overridable): python3-venv/pip + git +
# postgresql-client + texlive.
#   texlive-* provides xelatex, which matplotlib's pgf backend shells out to when
#   the attack writes its --plot-format pgf figure (text metrics during
#   tight_layout); without it the attack crashes after measuring (see git log).
# The fallback drops the debian-12-specific python3.11-venv for other distros.
log "Stage 0: install OS packages"
transport_exec attacker \
  "export DEBIAN_FRONTEND=noninteractive && sudo apt-get update && (sudo apt-get install -y python3-venv python3.11-venv python3-pip git postgresql-client texlive-xetex texlive-latex-extra texlive-fonts-recommended || sudo apt-get install -y python3-venv python3-pip git postgresql-client texlive-xetex texlive-latex-extra texlive-fonts-recommended)"

# ---- Stage 1: push the local repo (this checkout) -------------------------
if command -v git >/dev/null 2>&1 && git -C "${LOCAL_REPO_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "Stage 1: push local working tree subtree (git-tracked files under ${LOCAL_REPO_DIR})"
  TMP_TAR="$(mktemp -t rls_repo_XXXXXX.tgz)"
  TMP_LIST="$(mktemp -t rls_repo_XXXXXX.list)"
  cleanup_temp() { rm -f "${TMP_TAR}" "${TMP_LIST}"; }
  trap cleanup_temp EXIT
  # LOCAL_REPO_DIR may be a subdirectory of the Git worktree (the artifact used
  # to be the repository root). `git -C` emits paths relative to that directory,
  # so extracting this tarball still puts requirements.txt, src/, and orchestration/
  # directly at REMOTE_DIR.
  # Exclude .claude/ (Claude Code worktrees/state): git ls-files lists those
  # worktree paths but `tar -T` cannot stat them, which under `set -e` aborts
  # the whole sync. The attacker never needs .claude/.
  git -C "${LOCAL_REPO_DIR}" ls-files -co --exclude-standard | grep -vE '^\.claude/' > "${TMP_LIST}"
  tar --no-xattrs -czf "${TMP_TAR}" -C "${LOCAL_REPO_DIR}" -T "${TMP_LIST}"
  transport_push attacker "${TMP_TAR}" "/tmp/rls_repo.tgz"
  transport_exec attacker \
    "mkdir -p '${REMOTE_DIR}' && tar -xzf /tmp/rls_repo.tgz -C '${REMOTE_DIR}' && rm -f /tmp/rls_repo.tgz"
  cleanup_temp
  trap - EXIT
else
  log "Stage 1: ${LOCAL_REPO_DIR} is not inside a Git worktree; pushing the entire directory"
  transport_exec attacker "mkdir -p '${REMOTE_PARENT_DIR}'"
  transport_push attacker "${LOCAL_REPO_DIR}" "${REMOTE_PARENT_DIR}/"
fi

# ---- Stage 2: build the venv ----------------------------------------------
# OS packages (python3-venv/pip) were installed in Stage 0.
log "Stage 2: build venv + install artifact"
transport_exec attacker \
  "cd '${REMOTE_DIR}' && python3 -m venv --clear venv && venv/bin/pip install -r requirements.txt && venv/bin/pip install --no-deps -e ."

# ---- Stage 3: verify ------------------------------------------------------
if ! transport_exec attacker "test -x '${REMOTE_DIR}/venv/bin/python' && echo OK" 2>/dev/null | grep -q OK; then
  log "ERROR: venv not found on the attacker at ${REMOTE_DIR}/venv/bin/python" >&2
  exit 1
fi
log "=== Attacker repo + venv ready (${REMOTE_DIR}) ==="
