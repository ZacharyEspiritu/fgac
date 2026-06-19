#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATION_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
if [[ "${CONFIG_FILE:-}" != "/dev/null" ]]; then
  echo "This setup utility is internal to orchestration/provision/provision_vms.sh; standalone direct-GCloud setup is no longer supported." >&2
  echo "Run: bash orchestration/provision/provision_vms.sh --config <config.yml> --output <machines.yml>" >&2
  exit 1
fi
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_run_id_overlay.sh"

project_label="${PROJECT}"
if [[ -z "${project_label}" ]]; then
  project_label="unset"
fi
echo "Config: project=${project_label} zone=${ZONE} network=${NETWORK} subnet=${SUBNET}"

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-${ZONE%-*}}"

if [[ -z "${PROJECT}" ]]; then
  echo "PROJECT is not set. Run 'gcloud config set project <id>' or set PROJECT." >&2
  exit 1
fi
if [[ -z "${POSTGRES_PASSWORD}" ]]; then
  echo "POSTGRES_PASSWORD is required (set postgres_password in the YAML config)." >&2
  exit 1
fi

command -v gcloud >/dev/null 2>&1 || {
  echo "gcloud CLI not found in PATH." >&2
  exit 1
}

gcloud config set project "${PROJECT}" >/dev/null

echo "Stage: network setup"
if ! gcloud compute networks describe "${NETWORK}" >/dev/null 2>&1; then
  gcloud compute networks create "${NETWORK}" --subnet-mode=custom
fi

echo "Stage: subnet setup"
if ! gcloud compute networks subnets describe "${SUBNET}" --region "${REGION}" >/dev/null 2>&1; then
  gcloud compute networks subnets create "${SUBNET}" \
    --region "${REGION}" \
    --network "${NETWORK}" \
    --range "${SUBNET_RANGE}"
fi

echo "Stage: firewall setup"
if gcloud compute firewall-rules describe "${NETWORK}-allow-postgres-ssh" >/dev/null 2>&1; then
  gcloud compute firewall-rules update "${NETWORK}-allow-postgres-ssh" \
    --allow tcp:22 \
    --source-ranges "${SSH_CIDR}" \
    --target-tags "${POSTGRES_TAG}"
else
  gcloud compute firewall-rules create "${NETWORK}-allow-postgres-ssh" \
    --network "${NETWORK}" \
    --allow tcp:22 \
    --source-ranges "${SSH_CIDR}" \
    --target-tags "${POSTGRES_TAG}"
fi

if gcloud compute firewall-rules describe "${NETWORK}-allow-postgres" >/dev/null 2>&1; then
  gcloud compute firewall-rules update "${NETWORK}-allow-postgres" \
    --allow tcp:5432 \
    --source-tags "${ATTACK_TAG},${NOISE_TAG}" \
    --target-tags "${POSTGRES_TAG}"
else
  gcloud compute firewall-rules create "${NETWORK}-allow-postgres" \
    --network "${NETWORK}" \
    --allow tcp:5432 \
    --source-tags "${ATTACK_TAG},${NOISE_TAG}" \
    --target-tags "${POSTGRES_TAG}"
fi

echo "Stage: postgres VM create"
# POSTGRES_INSTALL_DEPS (default 1) controls whether the VM's cloud-init startup
# installs + configures PostgreSQL. Callers that defer the DB install to a later
# step set it to 0 to get a bare VM, as orchestration/provision/provision_vms.sh does.
POSTGRES_STARTUP="$(mktemp)"
if [[ "${POSTGRES_INSTALL_DEPS:-1}" == "1" ]]; then
cat > "${POSTGRES_STARTUP}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
PASSWORD="$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/POSTGRES_PASSWORD)"
SUBNET_RANGE="$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/SUBNET_RANGE)"

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update
sudo apt-get install -y wget gnupg lsb-release ca-certificates

# PGDG repo for PostgreSQL 18.1
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
# postgresql-contrib-18 is a virtual package provided by postgresql-18 and has
# no version of its own, so it cannot take a `=${PG_PKG_VER}` pin. The pin on
# postgresql-18 is what actually fixes the upstream version.
sudo apt-get install -y "postgresql-18=${PG_PKG_VER}" postgresql-contrib-18

PG_VERSION="18"
PG_CONF="/etc/postgresql/${PG_VERSION}/main/postgresql.conf"
PG_HBA="/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"

sudo sed -i "s/^#listen_addresses.*/listen_addresses = '*'/" "${PG_CONF}"
if grep -Eq '^[[:space:]]*#?[[:space:]]*shared_preload_libraries[[:space:]]*=' "${PG_CONF}"; then
  sudo sed -i "s|^[[:space:]]*#\?[[:space:]]*shared_preload_libraries[[:space:]]*=.*|shared_preload_libraries = 'pg_stat_statements'|" "${PG_CONF}"
else
  echo "shared_preload_libraries = 'pg_stat_statements'" | sudo tee -a "${PG_CONF}" >/dev/null
fi
if ! grep -q "${SUBNET_RANGE}" "${PG_HBA}"; then
  echo "host all all ${SUBNET_RANGE} md5" | sudo tee -a "${PG_HBA}" >/dev/null
fi

sudo systemctl restart postgresql
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD '${PASSWORD}';"
EOF
else
cat > "${POSTGRES_STARTUP}" <<'EOF'
#!/usr/bin/env bash
# Bare VM (POSTGRES_INSTALL_DEPS=0): PostgreSQL is installed + configured later
# by orchestration/install/install_artifact_on_database.sh. Nothing to do at boot beyond the image default.
true
EOF
fi

# Optional boot-disk-type override (required for c4/c3 which only support
# Hyperdisk). Empty BOOT_DISK_TYPE => omit the flag and keep gcloud's default.
DISK_TYPE_ARGS=()
if [[ -n "${BOOT_DISK_TYPE:-}" ]]; then
  DISK_TYPE_ARGS=(--boot-disk-type "${BOOT_DISK_TYPE}")
fi

# Optional RAW secondary data disk (TDE/LUKS): when TDE_DATA_DISK_SIZE is set, attach
# an unformatted disk that orchestration/install/install_artifact_on_database_tde.sh later LUKS-formats
# and uses for the PostgreSQL data directory. Non-TDE callers leave it unset => no disk.
DATA_DISK_ARGS=()
if [[ -n "${TDE_DATA_DISK_SIZE:-}" ]]; then
  DATA_DISK_ARGS=(--create-disk="auto-delete=yes,boot=no,device-name=${TDE_DISK_DEVICE_NAME:-pg-tde-data},mode=rw,size=${TDE_DATA_DISK_SIZE},type=${TDE_DATA_DISK_TYPE:-${BOOT_DISK_TYPE:-pd-ssd}}")
fi

if ! gcloud compute instances describe "${POSTGRES_VM}" --zone "${ZONE}" >/dev/null 2>&1; then
  gcloud compute instances create "${POSTGRES_VM}" \
    --zone "${ZONE}" \
    --machine-type "${DB_MACHINE_TYPE}" \
    --boot-disk-size "${DB_DISK_SIZE}" \
    ${DISK_TYPE_ARGS[@]+"${DISK_TYPE_ARGS[@]}"} \
    ${DATA_DISK_ARGS[@]+"${DATA_DISK_ARGS[@]}"} \
    --image-family "${IMAGE_FAMILY}" \
    --image-project "${IMAGE_PROJECT}" \
    --network "${NETWORK}" \
    --subnet "${SUBNET}" \
    --tags "${POSTGRES_TAG}" \
    ${RUN_LABELS_ARGS[@]+"${RUN_LABELS_ARGS[@]}"} \
    --metadata "POSTGRES_PASSWORD=${POSTGRES_PASSWORD},SUBNET_RANGE=${SUBNET_RANGE}" \
    --metadata-from-file startup-script="${POSTGRES_STARTUP}"
fi

rm -f "${POSTGRES_STARTUP}"

echo "Stage: resolve Postgres IP"
POSTGRES_IP="$(gcloud compute instances describe "${POSTGRES_VM}" \
  --zone "${ZONE}" \
  --format='get(networkInterfaces[0].networkIP)')"

echo "Postgres internal IP: ${POSTGRES_IP}"
echo "Admin DSN: postgresql://postgres:${POSTGRES_PASSWORD}@${POSTGRES_IP}/postgres"
