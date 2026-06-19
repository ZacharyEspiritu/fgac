# shellcheck shell=bash
# Join-policy RLS mitigation (C-R8, Table 5) config loader.
#
# Sources the shared GCP config loader (orchestration/util/_load_gcloud_config.sh) for the common
# infra + plumbing, then adds the mitigation-sweep parameters read from the
# config's mitigations: block. This is the C-R8 thin loader (config strategy B);
# the canonical config is orchestration/config/shared_config.yml.
#
# The config path is REQUIRED: the sourcing script passes it as the first argument
# (from its --config flag), e.g. `. util/_load_mitigations_config.sh "${CONFIG_ARG}"`.

_LMC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "${_LMC_DIR}/_load_gcloud_config.sh" "${1:-}" || return 1 2>/dev/null || exit 1

# Bookkeeping alias (written into the descriptor / forwarded to child steps).
MITIGATIONS_CONFIG="${GCLOUD_CONFIG}"

set -a
# ---- tunable: mitigations: block of the config ----------------------------
MIT_CONFIGS="$(_cfg_yq '.mitigations.configs' 'baseline,plpgsql_composite,subq_inline_single,subq_inline')"
MIT_PROBES="$(_cfg_yq '.mitigations.probes' 2000)"
MIT_KVALUES="$(_cfg_yq '.mitigations.kvalues' '1,2,3,4')"
MIT_ATTRIBUTE="$(_cfg_yq '.mitigations.attribute' ssn)"
MIT_RUN_TIMEOUT_S="$(_cfg_yq '.mitigations.run_timeout_s' 14400)"
set +a

# Make the shared scripts skip their .env sourcing and use this environment.
export CONFIG_FILE=/dev/null
