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

resolve_config_path() {
  local config_path="$1"
  local fallback
  if [[ -f "${config_path}" ]]; then
    printf '%s\n' "${config_path}"
    return 0
  fi
  fallback="${ORCHESTRATION_DIR}/config/$(basename "${config_path}")"
  if [[ -f "${fallback}" ]]; then
    echo "cleanup: config ${config_path} not found; using ${fallback} instead." >&2
    printf '%s\n' "${fallback}"
    return 0
  fi
  printf '%s\n' "${config_path}"
}

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
  CONFIG_ARG="$(resolve_config_path "${CONFIG_ARG}")"
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
  CONFIG_ARG="$(resolve_config_path "${CONFIG_ARG}")"
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

delete_vms_by_zone() {
  local verify_existing="${1:-1}"
  local -a zones=()
  local zone existing seen i vm

  if [[ ${#VM_ZONES[@]} -gt 0 ]]; then
    for zone in "${VM_ZONES[@]}"; do
      [[ -z "${zone}" ]] && continue
      seen=0
      if [[ ${#zones[@]} -gt 0 ]]; then
        for existing in "${zones[@]}"; do
          if [[ "${existing}" == "${zone}" ]]; then
            seen=1
            break
          fi
        done
      fi
      [[ "${seen}" -eq 0 ]] && zones+=("${zone}")
    done
  fi

  if [[ ${#zones[@]} -eq 0 ]]; then
    return
  fi
  for zone in "${zones[@]}"; do
    local -a batch=()
    if [[ ${#VM_NAMES[@]} -gt 0 ]]; then
      for i in "${!VM_NAMES[@]}"; do
        [[ "${VM_ZONES[$i]}" == "${zone}" ]] || continue
        vm="${VM_NAMES[$i]}"
        [[ -z "${vm}" ]] && continue
        if [[ "${verify_existing}" == "1" ]]; then
          if gcloud compute instances describe "${vm}" --zone "${zone}" >/dev/null 2>&1; then
            batch+=("${vm}")
          fi
        else
          batch+=("${vm}")
        fi
      done
    fi
    if [[ ${#batch[@]} -gt 0 ]]; then
      echo "  deleting ${batch[*]} (${zone})"
      gcloud compute instances delete "${batch[@]}" --zone "${zone}" --quiet || true
    fi
  done
}

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
    VM_NAMES=(); VM_ZONES=()
    for entry in "${ENTRIES[@]}"; do
      VM_NAMES+=("${entry%%	*}")
      VM_ZONES+=("${entry##*	}")
    done
    echo "Stage: delete stale VMs in zone batches"
    delete_vms_by_zone 0
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
  # Batch deletions by zone. A cross-zone run still needs one delete request per
  # zone, but same-zone stacks delete DB/attacker/noise with one gcloud call.
  delete_vms_by_zone 1
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
