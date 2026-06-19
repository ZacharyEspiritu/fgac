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

LOCAL_REPO_DIR="${LOCAL_REPO_DIR:-$(pwd)}"
REMOTE_DIR="${REMOTE_DIR:-${REMOTE_BASE_DIR}/scratch}"
REMOTE_PARENT_DIR="$(dirname "${REMOTE_DIR}")"

NOISE_MACHINE_TYPE_LOCAL="${NOISE_MACHINE_TYPE}"
if [[ -z "${NOISE_MACHINE_TYPE_LOCAL}" ]]; then
  NOISE_MACHINE_TYPE_LOCAL="${ATTACK_MACHINE_TYPE}"
fi
NOISE_DISK_SIZE_LOCAL="${NOISE_DISK_SIZE}"
if [[ -z "${NOISE_DISK_SIZE_LOCAL}" ]]; then
  NOISE_DISK_SIZE_LOCAL="${ATTACK_DISK_SIZE}"
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

echo "Stage: client SSH firewall setup"
if gcloud compute firewall-rules describe "${NETWORK}-allow-client-ssh" >/dev/null 2>&1; then
  gcloud compute firewall-rules update "${NETWORK}-allow-client-ssh" \
    --allow tcp:22 \
    --source-ranges "${SSH_CIDR}" \
    --target-tags "${ATTACK_TAG},${NOISE_TAG}"
else
  gcloud compute firewall-rules create "${NETWORK}-allow-client-ssh" \
    --network "${NETWORK}" \
    --allow tcp:22 \
    --source-ranges "${SSH_CIDR}" \
    --target-tags "${ATTACK_TAG},${NOISE_TAG}"
fi

echo "Stage: noise VM create"
# NOISE_INSTALL_DEPS (default 1) controls whether the VM's cloud-init startup
# installs the experiment toolchain. Callers that defer dependency installation
# to a later step set it to 0 to get a bare VM — e.g. orchestration/provision/provision_vms.sh
# provisions bare and hands the toolchain + repo + venv to
# orchestration/install/install_artifact_on_noise.sh. Default 1 keeps the original behaviour.
NOISE_STARTUP="$(mktemp)"
if [[ "${NOISE_INSTALL_DEPS:-1}" == "1" ]]; then
cat > "${NOISE_STARTUP}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git curl gnupg2 apt-transport-https postgresql-client \
  texlive-xetex texlive-latex-extra texlive-fonts-recommended
EOF
else
cat > "${NOISE_STARTUP}" <<'EOF'
#!/usr/bin/env bash
# Bare VM (NOISE_INSTALL_DEPS=0): the experiment toolchain is installed later by
# orchestration/install/install_artifact_on_noise.sh. Nothing to do at boot beyond the image default.
true
EOF
fi

# Optional boot-disk-type override (required for c4/c3, which only support
# Hyperdisk). Empty BOOT_DISK_TYPE => omit the flag and keep gcloud's default.
DISK_TYPE_ARGS=()
if [[ -n "${BOOT_DISK_TYPE:-}" ]]; then
  DISK_TYPE_ARGS=(--boot-disk-type "${BOOT_DISK_TYPE}")
fi

if ! gcloud compute instances describe "${NOISE_VM}" --zone "${ZONE}" >/dev/null 2>&1; then
  gcloud compute instances create "${NOISE_VM}" \
    --zone "${ZONE}" \
    --machine-type "${NOISE_MACHINE_TYPE_LOCAL}" \
    --boot-disk-size "${NOISE_DISK_SIZE_LOCAL}" \
    ${DISK_TYPE_ARGS[@]+"${DISK_TYPE_ARGS[@]}"} \
    --image-family "${IMAGE_FAMILY}" \
    --image-project "${IMAGE_PROJECT}" \
    --network "${NETWORK}" \
    --subnet "${SUBNET}" \
    --tags "${NOISE_TAG}" \
    ${RUN_LABELS_ARGS[@]+"${RUN_LABELS_ARGS[@]}"} \
    --metadata-from-file startup-script="${NOISE_STARTUP}"
fi

rm -f "${NOISE_STARTUP}"

# Repo sync + venv build. Gated by NOISE_SETUP_REPO (default 1) so a caller can
# create the VM only and defer the repo/venv to a separate step (e.g.
# orchestration/provision/provision_vms.sh provisions VM-only, then orchestration/install/install_artifact_on_noise.sh
# pushes the repo). Default preserves the original create-VM-and-sync behaviour.
if [[ "${NOISE_SETUP_REPO:-1}" == "1" ]]; then
echo "Stage: sync repo"
if [[ -n "${REPO_URL}" ]]; then
  gcloud compute ssh "${NOISE_VM}" --zone "${ZONE}" \
    --command "mkdir -p '${REMOTE_PARENT_DIR}' && git clone '${REPO_URL}' '${REMOTE_DIR}' || (cd '${REMOTE_DIR}' && git pull)"
else
  gcloud compute ssh "${NOISE_VM}" --zone "${ZONE}" \
    --command "mkdir -p '${REMOTE_BASE_DIR}'"
  if command -v git >/dev/null 2>&1 && git -C "${LOCAL_REPO_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    TMP_TAR="$(mktemp -t rls_repo_XXXXXX.tgz)"
    TMP_LIST="$(mktemp -t rls_repo_XXXXXX.list)"
    cleanup_temp() {
      rm -f "${TMP_TAR}" "${TMP_LIST}"
    }
    trap cleanup_temp EXIT
    # LOCAL_REPO_DIR may be a subdirectory of the Git worktree (the artifact used
    # to be the repository root). `git -C` emits paths relative to that directory,
    # so extracting this tarball still puts requirements.txt, src/, and orchestration/
    # directly at REMOTE_DIR.
    git -C "${LOCAL_REPO_DIR}" ls-files -co --exclude-standard | grep -vE '^\.claude/' > "${TMP_LIST}"
    tar --no-xattrs -czf "${TMP_TAR}" -C "${LOCAL_REPO_DIR}" -T "${TMP_LIST}"
    gcloud compute scp "${TMP_TAR}" \
      "${NOISE_VM}:/tmp/rls_repo.tgz" \
      --zone "${ZONE}"
    gcloud compute ssh "${NOISE_VM}" --zone "${ZONE}" \
      --command "mkdir -p '${REMOTE_DIR}' && tar -xzf /tmp/rls_repo.tgz -C '${REMOTE_DIR}' && rm -f /tmp/rls_repo.tgz"
    cleanup_temp
    trap - EXIT
  else
    echo "Warning: ${LOCAL_REPO_DIR} is not inside a Git worktree; copying entire directory." >&2
    gcloud compute scp --recurse "${LOCAL_REPO_DIR}" \
      "${NOISE_VM}:${REMOTE_BASE_DIR}" \
      --zone "${ZONE}"
  fi
fi

echo "Stage: install python deps"
gcloud compute ssh "${NOISE_VM}" --zone "${ZONE}" \
  --command "export DEBIAN_FRONTEND=noninteractive && sudo apt-get update && (sudo apt-get install -y python3-venv python3.11-venv python3-pip || sudo apt-get install -y python3-venv python3-pip) && cd '${REMOTE_DIR}' && python3 -m venv --clear venv && venv/bin/pip install -r requirements.txt && venv/bin/pip install --no-deps -e ."
else
  echo "Stage: skipping repo sync + venv (NOISE_SETUP_REPO=${NOISE_SETUP_REPO:-1}; VM-only provisioning)"
fi

echo "Noise VM: ${NOISE_VM}"
echo "Repo path on noise VM: ${REMOTE_DIR}"
