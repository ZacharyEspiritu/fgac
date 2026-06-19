#!/usr/bin/env bash
set -euo pipefail

# Tear down resources provisioned by the YAML-driven GCP split.
#
# --machines <descriptor.yml>: read RUN_ID / PROJECT / ZONE and the VM names
#              from a machine descriptor (written by orchestration/provision/provision_vms.sh)
#              instead of the RUN_ID env var. Deletes exactly the db + attacker
#              VMs the descriptor names, plus the RUN_ID-derived network, then
#              removes the now-stale descriptor. An ssh-transport descriptor is a
#              no-op (those machines were not provisioned by gcloud).
#
# --config <config.yml>: clean a RUN_ID-derived stack when the descriptor was not
#              written (for example, a failed provision step). RUN_ID must be set
#              in the environment or supplied with --run-id.
#
# --all-stale: scans the project for every VM labelled
#              experiment=${RUN_LABEL} and deletes them all (use to mop
#              up runs whose RUN_ID you no longer remember). Network,
#              subnet, and firewall rules are not removed in this mode.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATION_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ALL_STALE=0
DELETE_NETWORK=1
MACHINES_FILE=""
CONFIG_ARG=""
RUN_ID_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machines)         MACHINES_FILE="$2"; shift 2 ;;
    --config)           CONFIG_ARG="$2"; shift 2 ;;
    --run-id)           RUN_ID_ARG="$2"; shift 2 ;;
    --all-stale)        ALL_STALE=1; shift ;;
    --delete-network)   DELETE_NETWORK=1; shift ;;
    --keep-network)     DELETE_NETWORK=0; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# --machines: take RUN_ID / PROJECT / ZONE (and the VM names) from a machine
# descriptor instead of the RUN_ID env var. Parse it before the run-id overlay
# so the overlay derives this run's network/subnet/firewall names.
if [[ -n "${MACHINES_FILE}" ]]; then
  # shellcheck source=/dev/null
  . "${ORCHESTRATION_DIR}/util/_remote_transport.sh"
  transport_load "${MACHINES_FILE}"
  if [[ "$(_tv MACHINES_TRANSPORT gcloud)" != "gcloud" ]]; then
    echo "cleanup: ${MACHINES_FILE} has transport=$(_tv MACHINES_TRANSPORT) — those machines were not provisioned by gcloud; nothing to delete." >&2
    exit 0
  fi
  CONFIG_ARG="$(_tv MACHINES_CONFIG_FILE)"
  if [[ -z "${CONFIG_ARG}" ]]; then
    echo "cleanup: descriptor ${MACHINES_FILE} has no config_file; cannot derive network names." >&2
    exit 1
  fi
  # shellcheck source=/dev/null
  . "${ORCHESTRATION_DIR}/util/_load_gcloud_config.sh" "${CONFIG_ARG}"
  RUN_ID="$(_tv MACHINES_RUN_ID)"
  PROJECT="$(_tv MACHINES_PROJECT "${PROJECT:-}")"
  ZONE="$(_tv MACHINES_ZONE "${ZONE:-}")"
  if [[ -z "${RUN_ID}" ]]; then
    echo "cleanup: descriptor ${MACHINES_FILE} has no run_id; cannot derive the network to remove." >&2
    exit 1
  fi
elif [[ -n "${CONFIG_ARG}" ]]; then
  # shellcheck source=/dev/null
  . "${ORCHESTRATION_DIR}/util/_load_gcloud_config.sh" "${CONFIG_ARG}"
  RUN_ID="${RUN_ID_ARG:-${RUN_ID:-}}"
  if [[ -z "${RUN_ID}" && "${ALL_STALE}" -eq 0 ]]; then
    echo "cleanup: --config requires RUN_ID to be set or --run-id <id>." >&2
    exit 1
  fi
elif [[ "${ALL_STALE}" -eq 0 ]]; then
  echo "cleanup requires --machines <descriptor.yml> or --config <config.yml> --run-id <id>." >&2
  exit 1
else
  RUN_LABEL="${RUN_LABEL:-rls-experiment}"
fi

if [[ "${ALL_STALE}" -eq 0 ]]; then
  # shellcheck source=/dev/null
  . "${ORCHESTRATION_DIR}/util/_run_id_overlay.sh"
fi

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
if [[ -z "${REGION:-}" && -n "${ZONE:-}" ]]; then
  REGION="${ZONE%-*}"
fi

if [[ -z "${PROJECT}" ]]; then
  echo "PROJECT is not set. Run 'gcloud config set project <id>' or set PROJECT." >&2
  exit 1
fi

command -v gcloud >/dev/null 2>&1 || {
  echo "gcloud CLI not found in PATH." >&2
  exit 1
}

gcloud config set project "${PROJECT}" >/dev/null

if [[ "${ALL_STALE}" -eq 1 ]]; then
  echo "Stage: list VMs with label experiment=${RUN_LABEL}"
  ENTRIES=()
  while IFS= read -r entry; do
    [[ -n "${entry}" ]] && ENTRIES+=("${entry}")
  done < <(gcloud compute instances list \
    --project "${PROJECT}" \
    --filter="labels.experiment=${RUN_LABEL}" \
    --format="value(name,zone.basename())")
  if [[ ${#ENTRIES[@]} -eq 0 ]]; then
    echo "No VMs found with label experiment=${RUN_LABEL}."
  else
    for entry in "${ENTRIES[@]}"; do
      name="${entry%%	*}"
      zone="${entry##*	}"
      echo "Stage: delete ${name} (${zone})"
      gcloud compute instances delete "${name}" --zone "${zone}" --quiet || true
    done
  fi
else
  echo "Stage: delete VMs"
  # Build the (name, zone) list to delete. With --machines, delete exactly the
  # db + attacker VMs the descriptor names, at their own zones. Otherwise delete
  # the RUN_ID-derived role VMs at ZONE (noise is an optional role an experiment
  # may never have provisioned).
  VM_NAMES=(); VM_ZONES=()
  if [[ -n "${MACHINES_FILE}" ]]; then
    # DB + attacker + the optional noise role, each at its OWN zone from the
    # descriptor (a cross-region stack like C-R4 has the attacker in a different
    # zone; _tv returns empty for an absent noise role, which is skipped).
    for role in DB ATTACK NOISE; do
      vm="$(_tv "MACHINES_${role}_VM")"
      [[ -z "${vm}" ]] && continue
      VM_NAMES+=("${vm}"); VM_ZONES+=("$(_tv "MACHINES_${role}_ZONE" "${ZONE}")")
    done
  else
    for vm in "${POSTGRES_VM}" "${ATTACK_VM}" "${NOISE_VM}"; do
      [[ -z "${vm}" ]] && continue
      VM_NAMES+=("${vm}"); VM_ZONES+=("${ZONE}")
    done
  fi
  # Delete each VM individually so a single missing name doesn't abort the batch.
  for i in "${!VM_NAMES[@]}"; do
    vm="${VM_NAMES[$i]}"; z="${VM_ZONES[$i]}"
    if gcloud compute instances describe "${vm}" --zone "${z}" >/dev/null 2>&1; then
      echo "  deleting ${vm} (${z})"
      gcloud compute instances delete "${vm}" --zone "${z}" --quiet || true
    fi
  done
fi

if [[ "${DELETE_NETWORK}" -eq 1 && "${ALL_STALE}" -eq 0 ]]; then
  echo "Stage: delete firewall rules"
  gcloud compute firewall-rules delete \
    "${NETWORK}-allow-client-ssh" \
    "${NETWORK}-allow-postgres" \
    "${NETWORK}-allow-postgres-ssh" \
    --quiet || true

  echo "Stage: delete subnets"
  # Delete EVERY subnet in this VPC — a cross-region stack (C-R4) has two: the
  # DB-side subnet and the attacker's subnet in another region. Listing them
  # generically covers both the one-subnet (same-zone) and multi-subnet cases;
  # fall back to the configured SUBNET if the list comes back empty.
  _CLEANUP_SUBNETS=()
  while IFS= read -r _entry; do
    [[ -n "${_entry}" ]] && _CLEANUP_SUBNETS+=("${_entry}")
  done < <(gcloud compute networks subnets list \
    --filter="network:${NETWORK}" --format="value(name,region.basename())" 2>/dev/null || true)
  if [[ ${#_CLEANUP_SUBNETS[@]} -eq 0 ]]; then
    gcloud compute networks subnets delete "${SUBNET}" --region "${REGION}" --quiet || true
  else
    for _entry in "${_CLEANUP_SUBNETS[@]}"; do
      _sname="${_entry%%	*}"; _sregion="${_entry##*	}"
      echo "  deleting subnet ${_sname} (${_sregion})"
      gcloud compute networks subnets delete "${_sname}" --region "${_sregion}" --quiet || true
    done
  fi

  echo "Stage: delete network"
  gcloud compute networks delete "${NETWORK}" --quiet || true
fi

if [[ -n "${MACHINES_FILE}" && "${ALL_STALE}" -eq 0 && -f "${MACHINES_FILE}" ]]; then
  rm -f "${MACHINES_FILE}"
  echo "Removed stale machine descriptor ${MACHINES_FILE} (its VMs are gone)."
fi

echo "Cleanup complete."
