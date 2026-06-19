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

usage() {
  cat <<'EOF'
Tear down local OpenSearch/Elasticsearch Docker containers.

By default this stops and removes the local backend containers and networks, but
keeps Docker volumes so indexed data can be reused on the next startup.

Options:
  --backend NAME   Backend to tear down: all, opensearch, elasticsearch, or elastic.
                   Default: all
  --volumes        Also remove Docker volumes for the selected backend(s).
  -h, --help       Show this help

Examples:
  ./teardown.sh
  ./teardown.sh --backend opensearch
  ./teardown.sh --backend elastic --volumes
EOF
}

log() {
  echo "==> $*"
}

die() {
  echo "error: $*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

compose_file_for() {
  case "$1" in
    opensearch)
      echo "${ROOT_DIR}/config/docker-compose.opensearch.yml"
      ;;
    elasticsearch)
      echo "${ROOT_DIR}/config/docker-compose.elastic.yml"
      ;;
    *)
      die "unknown backend: $1"
      ;;
  esac
}

label_for() {
  case "$1" in
    opensearch) echo "OpenSearch" ;;
    elasticsearch) echo "Elasticsearch" ;;
    *) die "unknown backend: $1" ;;
  esac
}

teardown_backend() {
  local backend="$1"
  local compose_file
  local label
  local args=(down --remove-orphans)

  compose_file="$(compose_file_for "${backend}")"
  label="$(label_for "${backend}")"
  [[ -f "${compose_file}" ]] || die "missing ${compose_file}"

  if [[ "${REMOVE_VOLUMES}" -eq 1 ]]; then
    args+=(--volumes)
  fi

  log "Tearing down ${label}"
  docker compose -f "${compose_file}" "${args[@]}"
}

BACKEND="all"
REMOVE_VOLUMES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend)
      BACKEND="${2:?missing value for --backend}"
      shift 2
      ;;
    --volumes)
      REMOVE_VOLUMES=1
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

case "${BACKEND}" in
  all)
    ;;
  opensearch)
    BACKEND="opensearch"
    ;;
  elastic|elasticsearch)
    BACKEND="elasticsearch"
    ;;
  *)
    die "--backend must be all, opensearch, elasticsearch, or elastic"
    ;;
esac

have docker || die "Docker is required"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"

if [[ "${BACKEND}" == "all" ]]; then
  teardown_backend opensearch
  teardown_backend elasticsearch
else
  teardown_backend "${BACKEND}"
fi

if [[ "${REMOVE_VOLUMES}" -eq 1 ]]; then
  log "Teardown complete; selected volumes were removed"
else
  log "Teardown complete; Docker volumes were preserved"
fi
