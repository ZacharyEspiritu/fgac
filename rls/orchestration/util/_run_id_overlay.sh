# shellcheck shell=bash
# Run-ID overlay for YAML-derived GCP environments.
#
# Source after loading a config YAML into the environment. When RUN_ID is
# non-empty, every shared VM / network / subnet / firewall-tag identifier is
# suffixed with -${RUN_ID} so concurrent agents can run independent DB +
# attacker + noise stacks in the same GCP project without colliding.
#
# Empty RUN_ID preserves the base shared names (rls-postgres, rls-attacker,
# rls-noise, rls-net, rls-subnet). Experiment drivers require RUN_ID; this
# behavior is mainly for internal utility use.
#
# Usage from a provision / cleanup script:
#
#     . "${SCRIPT_DIR}/util/_load_gcloud_config.sh" "${CONFIG_ARG}"
#     . "${SCRIPT_DIR}/util/_run_id_overlay.sh"
#
# After sourcing, scripts use the same variable names (POSTGRES_VM, ATTACK_VM,
# NETWORK, ...) and they automatically point at this run's resources. Pass
# "${RUN_LABELS_ARGS[@]}" into each
# `gcloud compute instances create` so the resulting VMs are discoverable
# by label.
#
# A unique RUN_ID can be generated once and exported across the chain:
#
#     export RUN_ID="agent-a"   # or "$(date +%s)" for unattended runs
#     bash orchestration/provision/provision_vms.sh --config orchestration/config/shared_config.yml --output results/machines/${RUN_ID}.yml
#     bash orchestration/provision/cleanup_vms.sh --machines results/machines/${RUN_ID}.yml

RUN_ID="${RUN_ID:-}"
RUN_LABEL="${RUN_LABEL:-rls-experiment}"

if [[ -n "${RUN_ID}" ]]; then
  # GCP labels accept [a-z][a-z0-9_-]{0,62}. VM / network names accept
  # [a-z]([-a-z0-9]*[a-z0-9])?, length ≤ 63. Cap RUN_ID at 40 so suffixed
  # names stay under the 63-char limit.
  if ! [[ "${RUN_ID}" =~ ^[a-z][a-z0-9-]{0,39}$ ]]; then
    echo "RUN_ID must match [a-z][a-z0-9-]{0,39} (got: ${RUN_ID})" >&2
    exit 1
  fi

  # Suffix every identifier with -${RUN_ID}, but ONLY if it is not already
  # suffixed. This makes the overlay idempotent and, crucially, correct under
  # nested provisioner calls:
  #   * orchestration/provision/provision_vms.sh sources this overlay (suffixing the names)
  #     and then invokes orchestration/provision/util/setup_gcloud_*_vm.sh as subprocesses.
  #     The child inherits already-suffixed, exported names. A blind re-suffix
  #     here would yield rls-postgres-<id>-<id>; the per-variable check leaves
  #     them single.
  # Skip role vars this flow does not define: not every config sets every role,
  # and referencing an unset var under `set -u` aborts.
  # `${!NAME+x}` tests set-ness without tripping set -u and works on Bash 3.2
  # (macOS's default Bash), unlike the newer Bash variable-existence test.
  for _v in POSTGRES_VM ATTACK_VM NOISE_VM \
            NETWORK SUBNET \
            POSTGRES_TAG ATTACK_TAG NOISE_TAG; do
    [[ "${!_v+x}" ]] || continue
    if [[ "${!_v}" != *-"${RUN_ID}" ]]; then
      printf -v "${_v}" '%s-%s' "${!_v}" "${RUN_ID}"
    fi
  done
  unset _v
fi

# Build the labels flag for `gcloud compute instances create`. Every VM is
# tagged with the experiment label; when RUN_ID is set, run-id is added so
# cleanup --all-stale can list a single run's resources.
_RUN_LABELS_VALUE="experiment=${RUN_LABEL}"
if [[ -n "${RUN_ID}" ]]; then
  _RUN_LABELS_VALUE="${_RUN_LABELS_VALUE},run-id=${RUN_ID}"
fi
RUN_LABELS_ARGS=("--labels" "${_RUN_LABELS_VALUE}")

# Short status line so the user sees which run they're operating on.
_status_postgres="${POSTGRES_VM:-<unset>}"
_status_attack="${ATTACK_VM:-<unset>}"
_status_noise="${NOISE_VM:-<unset>}"
_status_network="${NETWORK:-<unset>}"
if [[ -n "${RUN_ID}" ]]; then
  echo "Run: id=${RUN_ID} label=${RUN_LABEL}  vms=(${_status_postgres} ${_status_attack} ${_status_noise}) net=${_status_network}"
else
  echo "Run: id=<shared> label=${RUN_LABEL}  vms=(${_status_postgres} ${_status_attack} ${_status_noise}) net=${_status_network}"
fi
unset _status_postgres _status_attack _status_noise _status_network
