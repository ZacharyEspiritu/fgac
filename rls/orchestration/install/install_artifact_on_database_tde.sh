#!/usr/bin/env bash
# Install + configure PostgreSQL on a LUKS-encrypted data volume, over the transport.
#
# The TDE (C-R5) counterpart of orchestration/install/install_artifact_on_database.sh.
# Provisioning (orchestration/provision/provision_vms.sh) creates a BARE DB VM with an
# attached RAW, unformatted secondary disk; this script consumes the machine
# descriptor and, over the provider-agnostic transport (orchestration/util/_remote_transport.sh), does the
# transparent-data-encryption setup on the DB host:
#   * LUKS2-formats + opens the raw device (64-byte keyfile at /etc/pg-luks.key,
#     mirroring KMS-managed TDE: the key lives outside the encrypted volume),
#   * installs PostgreSQL 18 and relocates its data directory onto the encrypted
#     ext4 volume mounted at /mnt/pg-data,
#   * configures listen_addresses / pg_stat_statements / a pg_hba host line for
#     DB_ALLOW_CIDR, installs a systemd unit that re-opens the LUKS volume before
#     PostgreSQL on every boot, and sets the postgres password.
# Because it runs over the transport, it works against a GCP VM (gcloud descriptor)
# OR any SSH-reachable machine (ssh descriptor) with a spare block device — so a
# reviewer can stand up the TDE DB on their own hardware.
#
# The target block device:
#   * gcloud descriptor: defaults to /dev/disk/by-id/google-<tde.disk_device_name>.
#   * ssh / bring-your-own: set `tde.device` in the config to your spare device
#     (e.g. /dev/sdb). DESTRUCTIVE — the device is LUKS-formatted (guarded: an
#     existing LUKS container is reused, never reformatted).
#
# Usage (--config <file> and --machines <file> are REQUIRED):
#   bash orchestration/install/install_artifact_on_database_tde.sh --config orchestration/config/tde_config.yml --machines results/machines/${RUN_ID}.yml
#
# Inputs:
#   --config <file>   - REQUIRED: config.yml to read (supplies postgres_password,
#                       subnet_range, and the tde.* device parameters)
#   --machines <file> - REQUIRED: machine descriptor (e.g. results/machines/<RUN_ID>.yml)
#   DB_ALLOW_CIDR     - CIDR allowed in pg_hba (default: subnet_range from the config)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATION_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${ORCHESTRATION_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

MACHINES_FILE_ARG=""
CONFIG_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machines) MACHINES_FILE_ARG="$2"; shift 2 ;;
    --config)   CONFIG_ARG="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Load shared infra/plumbing config from --config (REQUIRED; provides POSTGRES_PASSWORD,
# SUBNET_RANGE, TDE_DISK_DEVICE_NAME, TDE_DEVICE); sets CONFIG_FILE=/dev/null.
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_load_gcloud_config.sh" "${CONFIG_ARG}"
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_remote_transport.sh"

DB_ALLOW_CIDR="${DB_ALLOW_CIDR:-${SUBNET_RANGE:-}}"

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] $*"; }

if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
  echo "POSTGRES_PASSWORD is required (set postgres_password in the config)." >&2
  exit 1
fi
if [[ -z "${MACHINES_FILE_ARG}" ]]; then
  echo "--machines <file> is required (e.g. --machines results/machines/${RUN_ID:-<id>}.yml)." >&2
  exit 1
fi
MACHINES_FILE="${MACHINES_FILE_ARG}"
transport_load "${MACHINES_FILE}"

# Resolve the target block device. gcloud: stable by-id path from the disk's
# device-name; ssh/BYO: must be given via tde.device (TDE_DEVICE).
TDE_DEVICE="${TDE_DEVICE:-}"
if [[ -z "${TDE_DEVICE}" ]]; then
  if [[ "$(_tv MACHINES_TRANSPORT gcloud)" == "gcloud" ]]; then
    if [[ -z "${TDE_DISK_DEVICE_NAME:-}" ]]; then
      echo "tde.disk_device_name (or tde.device) is required to locate the encrypted volume." >&2
      exit 1
    fi
    TDE_DEVICE="/dev/disk/by-id/google-${TDE_DISK_DEVICE_NAME}"
  else
    echo "tde.device is required for the ssh transport (your spare block device, e.g. /dev/sdb)." >&2
    exit 1
  fi
fi

log "=== Install PostgreSQL on a LUKS volume (TDE) on the database host ==="
log "  Machines:     ${MACHINES_FILE}"
transport_summary
log "  TDE device:   ${TDE_DEVICE}"
log "  pg_hba allow: ${DB_ALLOW_CIDR:-<none>}"

# Self-contained installer (no local expansion); reads TDE_DEVICE / POSTGRES_PASSWORD
# / DB_ALLOW_CIDR from its environment, set at invocation below. Destructive LUKS
# steps are guarded so a re-run never reformats an already-encrypted volume.
TDE_INSTALL_SH="$(mktemp -t pg_tde_install_XXXXXX.sh)"
cleanup_temp() { rm -f "${TDE_INSTALL_SH}"; }
trap cleanup_temp EXIT
cat > "${TDE_INSTALL_SH}" <<'TDE_INSTALL'
#!/usr/bin/env bash
set -euo pipefail
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"
: "${TDE_DEVICE:?TDE_DEVICE must be set}"
DB_ALLOW_CIDR="${DB_ALLOW_CIDR:-}"

MAPPER_NAME="pg-data"
KEY_FILE="/etc/pg-luks.key"
MOUNT_POINT="/mnt/pg-data"
NEW_DATA="${MOUNT_POINT}/data"

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update
sudo apt-get install -y wget gnupg lsb-release ca-certificates cryptsetup

# PGDG repo for PostgreSQL 18.
sudo install -d /usr/share/keyrings
wget -qO- https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  | sudo gpg --dearmor -o /usr/share/keyrings/pgdg.gpg
echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  | sudo tee /etc/apt/sources.list.d/pgdg.list >/dev/null
sudo apt-get update
PG_PKG_VER="$(apt-cache madison postgresql-18 | awk '/18\.[0-9]+-/{print $3; exit}')"
if [[ -z "${PG_PKG_VER}" ]]; then
  echo "Could not resolve postgresql-18 package version from apt-cache madison" >&2
  exit 1
fi
sudo apt-get install -y "postgresql-18=${PG_PKG_VER}" postgresql-contrib-18

PG_VERSION="18"
PG_CONF="/etc/postgresql/${PG_VERSION}/main/postgresql.conf"
PG_HBA="/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"
OLD_DATA="/var/lib/postgresql/${PG_VERSION}/main"

# Wait for the device to appear (GCP attaches before boot, but udev may lag).
w=0; until [[ -e "${TDE_DEVICE}" ]] || [[ ${w} -ge 60 ]]; do sleep 5; w=$((w + 1)); done
if [[ ! -e "${TDE_DEVICE}" ]]; then
  echo "ERROR: TDE device ${TDE_DEVICE} did not appear after 5 minutes" >&2
  exit 1
fi

# PostgreSQL auto-starts on install; stop it before relocating its data dir.
sudo systemctl stop postgresql

# ---- LUKS setup (idempotent: never reformat an existing LUKS container) -----
if ! sudo cryptsetup isLuks "${TDE_DEVICE}"; then
  sudo dd if=/dev/urandom of="${KEY_FILE}" bs=64 count=1 status=none
  sudo chmod 600 "${KEY_FILE}"
  sudo cryptsetup luksFormat "${TDE_DEVICE}" --type luks2 --key-file "${KEY_FILE}" --batch-mode
fi
if [[ ! -e "/dev/mapper/${MAPPER_NAME}" ]]; then
  sudo cryptsetup luksOpen "${TDE_DEVICE}" "${MAPPER_NAME}" --key-file "${KEY_FILE}"
fi
if ! sudo blkid "/dev/mapper/${MAPPER_NAME}" >/dev/null 2>&1; then
  sudo mkfs.ext4 -q "/dev/mapper/${MAPPER_NAME}"
fi
sudo mkdir -p "${MOUNT_POINT}"
mountpoint -q "${MOUNT_POINT}" || sudo mount "/dev/mapper/${MAPPER_NAME}" "${MOUNT_POINT}"

# Relocate the freshly-initialized cluster onto the encrypted volume (only once).
# -a preserves ownership/permissions (pg refuses to start on a world-readable dir).
if [[ ! -s "${NEW_DATA}/PG_VERSION" ]]; then
  sudo mkdir -p "${NEW_DATA}"
  sudo chown postgres:postgres "${NEW_DATA}"
  sudo chmod 700 "${NEW_DATA}"
  sudo cp -a "${OLD_DATA}/." "${NEW_DATA}/"
fi

# ---- PostgreSQL configuration -----------------------------------------------
sudo sed -i "s|^[[:space:]]*#\?[[:space:]]*data_directory.*|data_directory = '${NEW_DATA}'|" "${PG_CONF}"
sudo sed -i "s/^#listen_addresses.*/listen_addresses = '*'/" "${PG_CONF}"
if sudo grep -Eq '^[[:space:]]*#?[[:space:]]*shared_preload_libraries[[:space:]]*=' "${PG_CONF}"; then
  sudo sed -i "s|^[[:space:]]*#\?[[:space:]]*shared_preload_libraries[[:space:]]*=.*|shared_preload_libraries = 'pg_stat_statements'|" "${PG_CONF}"
else
  echo "shared_preload_libraries = 'pg_stat_statements'" | sudo tee -a "${PG_CONF}" >/dev/null
fi
if [[ -n "${DB_ALLOW_CIDR}" ]] && ! sudo grep -q "${DB_ALLOW_CIDR}" "${PG_HBA}"; then
  echo "host all all ${DB_ALLOW_CIDR} md5" | sudo tee -a "${PG_HBA}" >/dev/null
fi

# ---- Persistence: re-open the LUKS volume before PostgreSQL on every reboot --
sudo tee /etc/systemd/system/pg-luks-data.service >/dev/null <<UNIT
[Unit]
Description=Open LUKS-encrypted volume for PostgreSQL TDE data directory
DefaultDependencies=no
Before=postgresql.service
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/cryptsetup luksOpen ${TDE_DEVICE} ${MAPPER_NAME} --key-file ${KEY_FILE}
ExecStart=/bin/mount /dev/mapper/${MAPPER_NAME} ${MOUNT_POINT}
ExecStop=/bin/umount -l ${MOUNT_POINT}
ExecStop=/sbin/cryptsetup luksClose ${MAPPER_NAME}

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable pg-luks-data.service

sudo systemctl restart postgresql
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD '${POSTGRES_PASSWORD}';"
TDE_INSTALL

log "Stage 1: push the TDE PostgreSQL installer to the DB host"
transport_push db "${TDE_INSTALL_SH}" "/tmp/install_db_tde.sh"

log "Stage 2: install + configure PostgreSQL on the LUKS volume"
transport_exec db \
  "POSTGRES_PASSWORD='${POSTGRES_PASSWORD}' DB_ALLOW_CIDR='${DB_ALLOW_CIDR}' TDE_DEVICE='${TDE_DEVICE}' bash /tmp/install_db_tde.sh && rm -f /tmp/install_db_tde.sh"

log "Stage 3: verify PostgreSQL is responding on the DB host"
if ! transport_exec db "sudo -u postgres psql -tAc 'SELECT 1' 2>/dev/null" 2>/dev/null | grep -q 1; then
  log "ERROR: PostgreSQL did not come up on the DB host after the TDE install" >&2
  exit 1
fi
log "=== PostgreSQL installed on a LUKS volume and running on the DB host ==="
