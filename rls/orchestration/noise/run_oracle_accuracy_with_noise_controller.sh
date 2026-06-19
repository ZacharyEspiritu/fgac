#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATION_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${ORCHESTRATION_DIR}/.." && pwd)"
DB_ENGINE_FLAG=""
POLICIES_FLAG=""
K_VALUES_FLAG=""
PROBES_FLAG=""
CALIBRATION_MODE_FLAG=""
CALIBRATION_CADENCES_FLAG=""
NOISE_SWEEP_FLAG=""
LOCAL_OUTPUT_DIR_FLAG=""
PASSTHRU_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db-engine)
      DB_ENGINE_FLAG="$2"
      shift 2
      ;;
    --policies)
      POLICIES_FLAG="$2"
      shift 2
      ;;
    --k-values)
      K_VALUES_FLAG="$2"
      shift 2
      ;;
    --probes)
      PROBES_FLAG="$2"
      shift 2
      ;;
    --calibration-mode)
      CALIBRATION_MODE_FLAG="$2"
      shift 2
      ;;
    --calibration-cadences)
      CALIBRATION_CADENCES_FLAG="$2"
      shift 2
      ;;
    --noise-sweep)
      NOISE_SWEEP_FLAG="$2"
      shift 2
      ;;
    --local-output-dir)
      LOCAL_OUTPUT_DIR_FLAG="$2"
      shift 2
      ;;
    *)
      PASSTHRU_ARGS+=("$1")
      shift
      ;;
  esac
done

# ---- Backend: machine descriptor transport --------------------------------
if [[ -z "${MACHINES_FILE:-}" || ! -f "${MACHINES_FILE:-}" ]]; then
  echo "MACHINES_FILE must point at a machine descriptor; direct-gcloud mode has been removed." >&2
  echo "Run through orchestration/run_oracle_accuracy.sh or orchestration/run_crosszone_experiment.sh." >&2
  exit 1
fi
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_remote_transport.sh"
transport_load "${MACHINES_FILE}"
PROJECT="$(_tv MACHINES_PROJECT)"
REMOTE_DIR="${REMOTE_DIR:-$(_tv MACHINES_REMOTE_DIR)}"
ZONE="$(_tv MACHINES_ZONE)"
ATTACK_ZONE="$(_tv MACHINES_ATTACK_ZONE "${ZONE}")"
POSTGRES_VM="$(_tv MACHINES_DB_VM)"; DB_VM="${POSTGRES_VM}"
ATTACK_VM="$(_tv MACHINES_ATTACK_VM)"; NOISE_VM="$(_tv MACHINES_NOISE_VM)"
DB_IP="$(transport_db_addr)"

REMOTE_DIR="${REMOTE_DIR:-}"
if [[ -z "${REMOTE_DIR}" ]]; then
  REMOTE_DIR="${REMOTE_BASE_DIR:-rls-dir}/scratch"
fi

DB_ENGINE="${DB_ENGINE:-}"
if [[ -n "${DB_ENGINE_FLAG}" ]]; then
  DB_ENGINE="${DB_ENGINE_FLAG}"
fi
if [[ -z "${DB_ENGINE}" ]]; then
  echo "--db-engine is required (postgres)." >&2
  exit 1
fi
if [[ "${DB_ENGINE}" != "postgres" ]]; then
  echo "Unsupported --db-engine value: ${DB_ENGINE} (expected postgres)." >&2
  exit 1
fi
if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
  echo "POSTGRES_PASSWORD is required (set postgres_password in the YAML config)." >&2
  exit 1
fi
if [[ -z "${DB_IP}" ]]; then
  echo "Failed to resolve the DB internal IP (db.internal_addr / Postgres VM ${POSTGRES_VM:-?})." >&2
  exit 1
fi

# Role-routing remote-I/O shims.
rt_exec() {
  local role="$1" cmd="$2"
  transport_exec "${role}" "${cmd}"
}
rt_exec_tty() {
  local role="$1" cmd="$2"
  transport_exec_tty "${role}" "${cmd}"
}
rt_fetch() {
  local role="$1" remote="$2" local_path="$3"
  local d; d="$(dirname "${local_path}")"
  [[ -n "${d}" && "${d}" != "." ]] && mkdir -p "${d}"
  transport_fetch "${role}" "${remote}" "${local_path}"
}
rt_push() {
  local role="$1" local_path="$2" remote="$3"
  transport_push "${role}" "${local_path}" "${remote}"
}

if [[ "${ATTACKER_PASSWORD:-}" != "${ATTACKER_USER:-}" ]]; then
  echo "Note: using patient dataset login convention for ${ATTACKER_USER:-} (password=user_name)." >&2
fi
ATTACKER_DSN="postgresql://${ATTACKER_USER}:${ATTACKER_USER}@${DB_IP}/${ATTACK_DB}"
ADMIN_DSN="postgresql://postgres:${POSTGRES_PASSWORD}@${DB_IP}/${ADMIN_DB}"

TABLE1_POLICIES="${TABLE1_POLICIES:-join,inline}"
if [[ -n "${POLICIES_FLAG}" ]]; then
  TABLE1_POLICIES="${POLICIES_FLAG}"
fi
TABLE1_K_VALUES="${TABLE1_K_VALUES:-1,2,3,4,5,6,7,8,9,10}"
if [[ -n "${K_VALUES_FLAG}" ]]; then
  TABLE1_K_VALUES="${K_VALUES_FLAG}"
fi
TABLE1_PROBES="${TABLE1_PROBES:-10000}"
if [[ -n "${PROBES_FLAG}" ]]; then
  TABLE1_PROBES="${PROBES_FLAG}"
fi
TABLE1_SEED="${TABLE1_SEED:-1}"
TABLE1_NONEXISTENT_OFFSET="${TABLE1_NONEXISTENT_OFFSET:-1000}"
TABLE1_OUTPUT="${TABLE1_OUTPUT:-results/table1_summary.csv}"
TABLE1_TABLE_OUTPUT="${TABLE1_TABLE_OUTPUT:-results/table1_summary.md}"
TABLE1_NOISE_OUTPUT="${TABLE1_NOISE_OUTPUT:-results/table1_noise.json}"
TABLE1_METRICS_OUTPUT="${TABLE1_METRICS_OUTPUT:-results/table1_metrics.json}"
TABLE1_LOCAL_OUTPUT_DIR="${TABLE1_LOCAL_OUTPUT_DIR:-results/${DB_ENGINE}/table1}"
if [[ -n "${LOCAL_OUTPUT_DIR_FLAG}" ]]; then
  TABLE1_LOCAL_OUTPUT_DIR="${LOCAL_OUTPUT_DIR_FLAG}"
fi
TABLE1_NOISE_SWEEP="${TABLE1_NOISE_SWEEP:-}"
if [[ -n "${NOISE_SWEEP_FLAG}" ]]; then
  TABLE1_NOISE_SWEEP="${NOISE_SWEEP_FLAG}"
fi
TABLE1_THRESHOLDS_FROM="${TABLE1_THRESHOLDS_FROM:-}"
TABLE1_REUSE_BASELINE_THRESHOLDS="${TABLE1_REUSE_BASELINE_THRESHOLDS:-0}"
TABLE1_CALIBRATION_MODE="${TABLE1_CALIBRATION_MODE:-trial}"
if [[ -n "${CALIBRATION_MODE_FLAG}" ]]; then
  TABLE1_CALIBRATION_MODE="${CALIBRATION_MODE_FLAG}"
fi
TABLE1_CALIBRATION_CADENCES="${TABLE1_CALIBRATION_CADENCES:-}"
if [[ -n "${CALIBRATION_CADENCES_FLAG}" ]]; then
  TABLE1_CALIBRATION_CADENCES="${CALIBRATION_CADENCES_FLAG}"
fi
if [[ -n "${TABLE1_CALIBRATION_CADENCES}" && "${TABLE1_CALIBRATION_MODE}" == "scenario" ]]; then
  echo "TABLE1_CALIBRATION_CADENCES is not compatible with --calibration-mode scenario." >&2
  exit 1
fi
TABLE1_FAST="${TABLE1_FAST:-1}"
TABLE1_WARM_CACHE="${TABLE1_WARM_CACHE:-1}"
TABLE1_USERS_FILE="${TABLE1_USERS_FILE:-data/doctors.csv}"
TABLE1_NOISE_CLIENTS="${TABLE1_NOISE_CLIENTS:-0}"
TABLE1_NOISE_TOTAL_QPS="${TABLE1_NOISE_TOTAL_QPS:-0}"
TABLE1_NOISE_WARMUP_SECONDS="${TABLE1_NOISE_WARMUP_SECONDS:-2}"
TABLE1_NOISE_AUTHORIZED_RATIO="${TABLE1_NOISE_AUTHORIZED_RATIO:-0.90}"
TABLE1_NOISE_UNAUTHORIZED_RATIO="${TABLE1_NOISE_UNAUTHORIZED_RATIO:-0.05}"
TABLE1_NOISE_NONEXISTENT_RATIO="${TABLE1_NOISE_NONEXISTENT_RATIO:-0.05}"
TABLE1_NOISE_POOL_SIZE="${TABLE1_NOISE_POOL_SIZE:-256}"
TABLE1_NOISE_READY_FILE="${TABLE1_NOISE_READY_FILE:-results/table1_noise.ready}"
TABLE1_NOISE_PID_FILE="${TABLE1_NOISE_PID_FILE:-results/table1_noise.pid}"
TABLE1_NOISE_LOG_FILE="${TABLE1_NOISE_LOG_FILE:-results/table1_noise_remote.log}"
TABLE1_NOISE_CONTROL_FILE="${TABLE1_NOISE_CONTROL_FILE:-results/table1_noise_control.qps}"
TABLE1_NOISE_READY_TIMEOUT="${TABLE1_NOISE_READY_TIMEOUT:-120}"
TABLE1_NOISE_STOP_TIMEOUT="${TABLE1_NOISE_STOP_TIMEOUT:-120}"
TABLE1_NOISE_DB_CPU_TARGET_PCT="${TABLE1_NOISE_DB_CPU_TARGET_PCT:-0}"
TABLE1_NOISE_DB_CPU_TOLERANCE_PCT="${TABLE1_NOISE_DB_CPU_TOLERANCE_PCT:-2}"
TABLE1_NOISE_DB_CPU_SAMPLE_SECONDS="${TABLE1_NOISE_DB_CPU_SAMPLE_SECONDS:-3}"
TABLE1_NOISE_DB_CPU_READY_STREAK="${TABLE1_NOISE_DB_CPU_READY_STREAK:-2}"
TABLE1_NOISE_DB_CPU_GAIN="${TABLE1_NOISE_DB_CPU_GAIN:-0.75}"
TABLE1_NOISE_DB_CPU_MIN_STEP_QPS="${TABLE1_NOISE_DB_CPU_MIN_STEP_QPS:-5}"
TABLE1_NOISE_DB_CPU_INITIAL_QPS="${TABLE1_NOISE_DB_CPU_INITIAL_QPS:-50}"
TABLE1_NOISE_DB_CPU_MAX_QPS="${TABLE1_NOISE_DB_CPU_MAX_QPS:-0}"
TABLE1_ENABLE_DB_METRICS="${TABLE1_ENABLE_DB_METRICS:-1}"

if [[ "${TABLE1_CALIBRATION_MODE}" != "trial" && "${TABLE1_CALIBRATION_MODE}" != "scenario" ]]; then
  echo "TABLE1_CALIBRATION_MODE must be 'trial' or 'scenario'." >&2
  exit 1
fi
if [[ "${TABLE1_CALIBRATION_MODE}" == "trial" ]]; then
  if [[ -n "${TABLE1_THRESHOLDS_FROM}" ]]; then
    echo "TABLE1_THRESHOLDS_FROM requires TABLE1_CALIBRATION_MODE=scenario." >&2
    exit 1
  fi
  if [[ "${TABLE1_REUSE_BASELINE_THRESHOLDS}" == "1" ]]; then
    echo "TABLE1_REUSE_BASELINE_THRESHOLDS=1 requires TABLE1_CALIBRATION_MODE=scenario." >&2
    exit 1
  fi
fi

mkdir -p "${TABLE1_LOCAL_OUTPUT_DIR}"

args_to_string() {
  local result=""
  if (($#)); then
    result="$(printf ' %q' "$@")"
  fi
  printf '%s' "${result}"
}

remote_path() {
  local path="$1"
  if [[ "${path}" = /* ]]; then
    printf '%s' "${path}"
  else
    printf '%s/%s' "${REMOTE_DIR}" "${path}"
  fi
}

NOISE_RUNNING=0
NOISE_CPU_CONTROLLER_PID=""
NOISE_CPU_CONTROLLER_READY_FILE=""
NOISE_CPU_CONTROLLER_STOP_FILE=""
CURRENT_LOCAL_OUTPUT_DIR=""
CURRENT_NOISE_ARGS_STR=""
# Whether the caller's stdout is a terminal: gates the interactive progress
# displays — the DB-CPU convergence indicator (in-place \r on a TTY; throttled
# plain lines otherwise) AND timing_oracle's per-policy probe bar
# (RLS_PROGRESS). On a non-TTY (e.g. nohup -> logfile) both are quiet so the log
# stays readable.
PROGRESS_TTY=0
[[ -t 1 ]] && PROGRESS_TTY=1

fetch_remote_file() {
  local role="$1"
  local remote_file="$2"
  local local_file="$3"
  local remote_ref out
  remote_ref="$(remote_path "${remote_file}")"
  # Quiet on success: capture rt_fetch's chatter (scp's per-file progress meter)
  # and discard it, so the artifact-download block prints a single summary line
  # instead of a "Stage: download ..." + meter per file. Surface the captured
  # output only when the fetch fails, so genuine errors are not hidden.
  if ! out="$(rt_fetch "${role}" "${remote_ref}" "${local_file}" 2>&1)"; then
    echo "  WARNING: failed to download ${role}:${remote_file} -> ${local_file}" >&2
    [[ -n "${out}" ]] && printf '%s\n' "${out}" >&2
    return 1
  fi
  return 0
}

sync_remote_file() {
  local source_role="$1"
  local source_file="$2"
  local dest_role="$3"
  local dest_file="$4"
  local source_ref
  local dest_ref
  local dest_dir
  local tmp_file

  source_ref="$(remote_path "${source_file}")"
  dest_ref="$(remote_path "${dest_file}")"
  dest_dir="$(dirname "${dest_ref}")"
  tmp_file="$(mktemp -t rls_remote_sync_XXXXXX)"

  echo "Stage: sync ${source_role}:${source_file} -> ${dest_role}:${dest_file}"
  rt_fetch "${source_role}" "${source_ref}" "${tmp_file}"
  rt_exec "${dest_role}" "mkdir -p $(printf '%q' "${dest_dir}")" >/dev/null
  rt_push "${dest_role}" "${tmp_file}" "${dest_ref}"

  rm -f "${tmp_file}"
}

sync_local_file_to_vm() {
  local local_file="$1"
  local role="$2"
  local remote_file="$3"
  local local_ref="${REPO_ROOT}/${local_file}"
  local remote_ref
  local remote_dir

  if [[ ! -f "${local_ref}" ]]; then
    echo "Local file missing: ${local_ref}" >&2
    exit 1
  fi

  remote_ref="$(remote_path "${remote_file}")"
  remote_dir="$(dirname "${remote_ref}")"

  rt_exec "${role}" "mkdir -p $(printf '%q' "${remote_dir}")" >/dev/null
  rt_push "${role}" "${local_ref}" "${remote_ref}"
}

sync_table1_code() {
  local role="$1"
  # orchestration/install/install_artifact_on_{attacker,noise}.sh already pushed the repo.
  return 0
}

require_remote_workspace() {
  local role="$1"
  local entrypoint="$2"
  local setup_hint="$3"
  if ! rt_exec "${role}" \
    "test -d '${REMOTE_DIR}' && test -f '${REMOTE_DIR}/${entrypoint}' && test -x '${REMOTE_DIR}/venv/bin/python'" \
    >/dev/null 2>&1; then
    echo "Remote workspace missing on ${role} at ${REMOTE_DIR}." >&2
    echo "Run '${setup_hint}' to sync the repo and create the venv." >&2
    exit 1
  fi
}

prepare_remote_dirs() {
  local role="$1"
  shift
  local remote_cmd="cd '${REMOTE_DIR}' && mkdir -p"
  local path=""
  local dir=""
  for path in "$@"; do
    dir="$(dirname "${path}")"
    remote_cmd+=" $(printf '%q' "${dir}")"
  done
  rt_exec "${role}" "${remote_cmd}" >/dev/null
}

cpu_target_enabled() {
  local target="${1:-${TABLE1_NOISE_DB_CPU_TARGET_PCT}}"
  awk -v target="${target}" 'BEGIN { exit !(target > 0) }'
}

write_noise_control_qps() {
  local qps="$1"
  local remote_control
  local remote_tmp
  remote_control="$(remote_path "${TABLE1_NOISE_CONTROL_FILE}")"
  remote_tmp="${remote_control}.tmp"
  rt_exec noise \
    "printf '%s\n' '${qps}' > '${remote_tmp}' && mv '${remote_tmp}' '${remote_control}'" \
    >/dev/null
}

sample_db_cpu_pct() {
  rt_exec db \
    "vmstat ${TABLE1_NOISE_DB_CPU_SAMPLE_SECONDS} 2 | tail -1 | awk '{printf \"%.2f\", 100 - \$15}'" \
    2>/dev/null | tr -d '\r'
}

adjust_qps_for_cpu_target() {
  local current_qps="$1"
  local cpu_pct="$2"
  local target_pct="$3"
  awk \
    -v current="${current_qps}" \
    -v cpu="${cpu_pct}" \
    -v target="${target_pct}" \
    -v gain="${TABLE1_NOISE_DB_CPU_GAIN}" \
    -v min_step="${TABLE1_NOISE_DB_CPU_MIN_STEP_QPS}" \
    -v max_qps="${TABLE1_NOISE_DB_CPU_MAX_QPS}" \
    'BEGIN {
      if (current < 0) current = 0;
      err = target - cpu;
      next_qps = current * (1.0 + (gain * err / 100.0));
      if (err > 0 && next_qps < current + min_step) next_qps = current + min_step;
      if (err < 0 && next_qps > current - min_step) next_qps = current - min_step;
      if (next_qps < 0) next_qps = 0;
      if (max_qps > 0 && next_qps > max_qps) next_qps = max_qps;
      printf "%.2f", next_qps;
    }'
}

cpu_pct_in_band() {
  local cpu_pct="$1"
  local target_pct="$2"
  awk \
    -v cpu="${cpu_pct}" \
    -v target="${target_pct}" \
    -v tolerance="${TABLE1_NOISE_DB_CPU_TOLERANCE_PCT}" \
    'BEGIN { exit !((cpu >= target - tolerance) && (cpu <= target + tolerance)) }'
}

trace_max_cpu_pct() {
  local trace_file="$1"
  awk -F, 'NR > 1 && $2 != "" { if ($2 > max) max = $2 } END { if (max == "") max = 0; printf "%.2f", max }' "${trace_file}"
}

trace_last_target_qps() {
  local trace_file="$1"
  awk -F, 'NR > 1 && $4 != "" { value = $4 } END { if (value == "") value = 0; printf "%.2f", value }' "${trace_file}"
}

ensure_postgres_metrics_extensions() {
  if [[ "${DB_ENGINE}" != "postgres" || "${TABLE1_ENABLE_DB_METRICS}" != "1" ]]; then
    return
  fi

  echo "Stage: ensure Postgres cache/statistics extensions"
  rt_exec db \
    "set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! dpkg -s postgresql-contrib-18 >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y postgresql-contrib-18
fi
PG_VERSION=\"18\"
PG_CONF=\"/etc/postgresql/\${PG_VERSION}/main/postgresql.conf\"
if ! sudo -u postgres psql -Atqc \"SHOW shared_preload_libraries\" | tr ',' '\n' | grep -qx \"pg_stat_statements\"; then
  if grep -Eq '^[[:space:]]*#?[[:space:]]*shared_preload_libraries[[:space:]]*=' \"\${PG_CONF}\"; then
    sudo sed -i \"s|^[[:space:]]*#\\?[[:space:]]*shared_preload_libraries[[:space:]]*=.*|shared_preload_libraries = 'pg_stat_statements'|\" \"\${PG_CONF}\"
  else
    printf \"\\nshared_preload_libraries = 'pg_stat_statements'\\n\" | sudo tee -a \"\${PG_CONF}\" >/dev/null
  fi
  sudo systemctl restart postgresql
fi
sudo -u postgres psql -d '${ADMIN_DB}' -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements;' >/dev/null
sudo -u postgres psql -d '${ADMIN_DB}' -c 'CREATE EXTENSION IF NOT EXISTS pg_buffercache;' >/dev/null"
}

start_noise_cpu_controller() {
  local initial_qps="$1"
  local trace_file="$2"
  local target_pct="$3"
  local current_qps="${initial_qps}"
  local ready_streak=0
  local cpu_pct=""
  local next_qps=""
  local timestamp=""

  NOISE_CPU_CONTROLLER_READY_FILE="${trace_file}.ready"
  NOISE_CPU_CONTROLLER_STOP_FILE="${trace_file}.stop"
  rm -f "${NOISE_CPU_CONTROLLER_READY_FILE}" "${NOISE_CPU_CONTROLLER_STOP_FILE}"
  printf 'timestamp_utc,cpu_pct,target_cpu_pct,target_total_qps\n' > "${trace_file}"

  write_noise_control_qps "${current_qps}"

  (
    while [[ ! -f "${NOISE_CPU_CONTROLLER_STOP_FILE}" ]]; do
      cpu_pct="$(sample_db_cpu_pct)"
      if [[ -z "${cpu_pct}" ]]; then
        sleep 1
        continue
      fi

      timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
      printf '%s,%s,%s,%s\n' \
        "${timestamp}" \
        "${cpu_pct}" \
        "${target_pct}" \
        "${current_qps}" \
        >> "${trace_file}"

      if cpu_pct_in_band "${cpu_pct}" "${target_pct}"; then
        ready_streak=$((ready_streak + 1))
        if [[ ! -f "${NOISE_CPU_CONTROLLER_READY_FILE}" ]] && (( ready_streak >= TABLE1_NOISE_DB_CPU_READY_STREAK )); then
          printf 'ready\n' > "${NOISE_CPU_CONTROLLER_READY_FILE}"
        fi
      else
        ready_streak=0
      fi

      next_qps="$(adjust_qps_for_cpu_target "${current_qps}" "${cpu_pct}" "${target_pct}")"
      if [[ "${next_qps}" != "${current_qps}" ]]; then
        write_noise_control_qps "${next_qps}"
        current_qps="${next_qps}"
      fi
    done
  ) &
  NOISE_CPU_CONTROLLER_PID=$!
}

# Render one DB-CPU convergence progress update from the latest controller trace
# row (timestamp,cpu_pct,target_cpu_pct,target_total_qps). On a TTY it rewrites a
# single line in place (\r + clear-to-EOL); otherwise it prints a plain line the
# caller throttles. ${6} = state ("waiting" | "converged"). Pure display — reading
# the trace file the controller already writes, so it adds no remote calls.
render_cpu_progress() {
  local trace_file="$1" target_pct="$2" elapsed="$3" timeout="$4" scenario="$5" state="${6:-waiting}"
  local last cpu qps delta bar tag=""
  last="$(tail -n 1 "${trace_file}" 2>/dev/null || true)"
  if [[ -z "${last}" || "${last}" == timestamp_utc,* ]]; then
    cpu="  ?"; qps="?"; delta="    ?"; bar="$(printf '%20s' '')"
  else
    cpu="$(awk -v x="$(printf '%s' "${last}" | cut -d, -f2)" 'BEGIN{printf "%.1f", x}')"
    qps="$(awk -v x="$(printf '%s' "${last}" | cut -d, -f4)" 'BEGIN{printf "%d", x}')"
    delta="$(awk -v c="${cpu}" -v t="${target_pct}" 'BEGIN{printf "%+.1f", c - t}')"
    # 20-cell 0..100% gauge: '#' filled to current CPU, '|' marks the target cell.
    bar="$(awk -v c="${cpu}" -v t="${target_pct}" 'BEGIN{
      w=20; ci=int(c/100*w+0.5); ti=int(t/100*w+0.5);
      if(ci>w)ci=w; if(ci<0)ci=0; if(ti>w)ti=w; if(ti<1)ti=1;
      for(i=1;i<=w;i++){ if(i==ti) printf "|"; else if(i<=ci) printf "#"; else printf "." } }')"
  fi
  [[ "${state}" == "converged" ]] && tag=" ✓ converged"
  local line
  line="$(printf '  [%s] cpu=%5s%% [%s] target=%s%% Δ=%6s qps=%-6s ±%s%% %3ds/%ds%s' \
    "${scenario}" "${cpu}" "${bar}" "${target_pct}" "${delta}" "${qps}" \
    "${TABLE1_NOISE_DB_CPU_TOLERANCE_PCT}" "${elapsed}" "${timeout}" "${tag}")"
  if [[ "${PROGRESS_TTY}" == "1" ]]; then
    printf '\r%s\033[K' "${line}"
    [[ "${state}" == "converged" ]] && printf '\n'
  else
    printf '%s\n' "${line}"
  fi
}

wait_for_noise_cpu_ready() {
  local trace_file="$1"
  local target_pct="$2"
  local scenario_label="${3:-cpu}"
  local ready_timeout="${TABLE1_NOISE_READY_TIMEOUT}"
  local max_cpu_pct=""
  local last_target_qps=""
  if cpu_target_enabled "${target_pct}" && (( ready_timeout < 300 )); then
    ready_timeout=300
  fi
  echo "Stage: driving DB CPU toward ${target_pct}% (±${TABLE1_NOISE_DB_CPU_TOLERANCE_PCT}%) for ${scenario_label} (live below)"
  for ((attempt = 0; attempt < ready_timeout; attempt++)); do
    if [[ -f "${NOISE_CPU_CONTROLLER_READY_FILE}" ]]; then
      render_cpu_progress "${trace_file}" "${target_pct}" "${attempt}" "${ready_timeout}" "${scenario_label}" "converged"
      return 0
    fi
    if [[ -n "${NOISE_CPU_CONTROLLER_PID}" ]] && ! kill -0 "${NOISE_CPU_CONTROLLER_PID}" 2>/dev/null; then
      [[ "${PROGRESS_TTY}" == "1" ]] && printf '\n'
      echo "DB CPU controller exited before reaching target utilization." >&2
      echo "See ${trace_file} for the recorded utilization trace." >&2
      return 1
    fi
    # TTY: refresh every second; non-TTY: log every ~10s so a logfile stays readable.
    if [[ "${PROGRESS_TTY}" == "1" ]] || (( attempt % 10 == 0 )); then
      render_cpu_progress "${trace_file}" "${target_pct}" "${attempt}" "${ready_timeout}" "${scenario_label}" "waiting"
    fi
    sleep 1
  done
  [[ "${PROGRESS_TTY}" == "1" ]] && printf '\n'
  max_cpu_pct="$(trace_max_cpu_pct "${trace_file}")"
  last_target_qps="$(trace_last_target_qps "${trace_file}")"
  echo "Timed out waiting for DB VM CPU to reach ${target_pct}% +/- ${TABLE1_NOISE_DB_CPU_TOLERANCE_PCT}% after ${ready_timeout}s." >&2
  echo "Max observed CPU was ${max_cpu_pct}% while commanded target QPS reached ${last_target_qps}." >&2
  echo "This usually means the current noise client count is too low for the requested CPU target; increase noise_clients or use a heavier noise query mix." >&2
  echo "See ${trace_file} for the recorded utilization trace." >&2
  return 1
}

stop_noise_cpu_controller() {
  if [[ -n "${NOISE_CPU_CONTROLLER_STOP_FILE}" ]]; then
    : > "${NOISE_CPU_CONTROLLER_STOP_FILE}"
  fi
  if [[ -n "${NOISE_CPU_CONTROLLER_PID}" ]]; then
    wait "${NOISE_CPU_CONTROLLER_PID}" || true
  fi
  NOISE_CPU_CONTROLLER_PID=""
  NOISE_CPU_CONTROLLER_READY_FILE=""
  NOISE_CPU_CONTROLLER_STOP_FILE=""
}

cleanup_noise() {
  if [[ "${NOISE_RUNNING}" != "1" ]]; then
    return
  fi

  stop_noise_cpu_controller

  echo "Stage: stop remote noise"
  rt_exec noise \
    "cd '${REMOTE_DIR}' && if [[ -f '${TABLE1_NOISE_PID_FILE}' ]]; then kill -TERM \$(cat '${TABLE1_NOISE_PID_FILE}') 2>/dev/null || true; fi" \
    >/dev/null

  for ((attempt = 0; attempt < TABLE1_NOISE_STOP_TIMEOUT; attempt++)); do
    status="$(rt_exec noise \
      "cd '${REMOTE_DIR}' && if [[ -f '${TABLE1_NOISE_OUTPUT}' ]]; then echo done; elif [[ -f '${TABLE1_NOISE_PID_FILE}' ]] && kill -0 \$(cat '${TABLE1_NOISE_PID_FILE}') 2>/dev/null; then echo running; else echo stopped; fi" \
      2>/dev/null || true)"
    if [[ "${status}" == *"done"* || "${status}" == *"stopped"* ]]; then
      break
    fi
    sleep 1
  done

  NOISE_RUNNING=0
}

run_single_scenario() {
  local scenario_label="$1"
  local scenario_clients="$2"
  local scenario_qps="$3"
  local scenario_output_dir="$4"
  local scenario_thresholds_from="${5:-}"
  local scenario_cpu_target_pct="${6:-${TABLE1_NOISE_DB_CPU_TARGET_PCT}}"
  local cpu_trace_file="${scenario_output_dir}/table1_noise_cpu.csv"
  local scenario_initial_qps="${scenario_qps}"

  echo "Stage: scenario=${scenario_label} policies=${TABLE1_POLICIES} noise_clients=${scenario_clients} noise_qps=${scenario_qps}"
  if [[ -n "${scenario_thresholds_from}" ]]; then
    echo "Stage: scenario=${scenario_label} thresholds_from=${scenario_thresholds_from}"
  fi
  if cpu_target_enabled "${scenario_cpu_target_pct}" && (( scenario_clients > 0 )); then
    if awk -v value="${scenario_initial_qps}" 'BEGIN { exit !(value <= 0) }'; then
      scenario_initial_qps="${TABLE1_NOISE_DB_CPU_INITIAL_QPS}"
    fi
    echo "Stage: scenario=${scenario_label} db_cpu_target=${scenario_cpu_target_pct}% initial_qps=${scenario_initial_qps}"
  fi

  local attack_args=(
    --policies "${TABLE1_POLICIES}"
    --k-values "${TABLE1_K_VALUES}"
    --probes "${TABLE1_PROBES}"
    --seed "${TABLE1_SEED}"
    --nonexistent-offset "${TABLE1_NONEXISTENT_OFFSET}"
    --output "${TABLE1_OUTPUT}"
    --table-output "${TABLE1_TABLE_OUTPUT}"
    --noise-output "${TABLE1_NOISE_OUTPUT}"
    --metrics-output "${TABLE1_METRICS_OUTPUT}"
    --calibration-mode "${TABLE1_CALIBRATION_MODE}"
    --noise-clients "${scenario_clients}"
    --noise-total-qps "${scenario_initial_qps}"
  )
  if [[ -n "${scenario_thresholds_from}" ]]; then
    attack_args+=(--thresholds-from "${scenario_thresholds_from}")
  fi
  if [[ -n "${TABLE1_CALIBRATION_CADENCES}" ]]; then
    attack_args+=(--calibration-cadences "${TABLE1_CALIBRATION_CADENCES}")
  fi
  local noise_args=(
    --admin-dsn "${ADMIN_DSN}"
    --base-dsn "${ATTACKER_DSN}"
    --attacker-user "${ATTACKER_USER}"
    --users-file "${TABLE1_USERS_FILE}"
    --noise-clients "${scenario_clients}"
    --noise-total-qps "${scenario_initial_qps}"
    --noise-warmup-seconds "${TABLE1_NOISE_WARMUP_SECONDS}"
    --noise-authorized-ratio "${TABLE1_NOISE_AUTHORIZED_RATIO}"
    --noise-unauthorized-ratio "${TABLE1_NOISE_UNAUTHORIZED_RATIO}"
    --noise-nonexistent-ratio "${TABLE1_NOISE_NONEXISTENT_RATIO}"
    --noise-pool-size "${TABLE1_NOISE_POOL_SIZE}"
    --nonexistent-offset "${TABLE1_NONEXISTENT_OFFSET}"
    --output "${TABLE1_NOISE_OUTPUT}"
    --ready-file "${TABLE1_NOISE_READY_FILE}"
    --seed "${TABLE1_SEED}"
  )
  if cpu_target_enabled "${scenario_cpu_target_pct}" && (( scenario_clients > 0 )); then
    noise_args+=(--control-file "${TABLE1_NOISE_CONTROL_FILE}")
  fi

  if [[ -n "${TABLE1_NOISE_QUERY_MODE:-}" ]]; then
    noise_args+=(--noise-query-mode "${TABLE1_NOISE_QUERY_MODE}")
  fi
  if [[ -n "${TABLE1_NOISE_RANGE_WIDTH:-}" ]]; then
    noise_args+=(--noise-range-width "${TABLE1_NOISE_RANGE_WIDTH}")
  fi

  if [[ "${TABLE1_FAST}" == "1" ]]; then
    attack_args+=(--fast)
    noise_args+=(--fast)
  fi
  if [[ "${TABLE1_WARM_CACHE}" == "1" ]]; then
    attack_args+=(--warm-cache)
  fi
  if ((${#PASSTHRU_ARGS[@]})); then
    attack_args+=("${PASSTHRU_ARGS[@]}")
  fi

  local attack_args_str
  attack_args_str="$(args_to_string "${attack_args[@]}")"
  CURRENT_NOISE_ARGS_STR="$(args_to_string "${noise_args[@]}")"
  CURRENT_LOCAL_OUTPUT_DIR="${scenario_output_dir}"

  mkdir -p "${scenario_output_dir}"
  NOISE_RUNNING=0
  trap cleanup_noise EXIT

  require_remote_workspace "attacker" "src/timing_oracle/__main__.py" "bash orchestration/install/install_artifact_on_attacker.sh"
  sync_table1_code "attacker"

  if (( scenario_clients > 0 )); then
    if [[ -z "$(_tv MACHINES_NOISE_VM)$(_tv MACHINES_NOISE_HOST)" ]]; then
      echo "Machine descriptor has no 'noise:' role; cannot run a noise scenario." >&2
      exit 1
    fi
    require_remote_workspace "noise" "src/noise/__main__.py" "bash orchestration/install/install_artifact_on_noise.sh"
    sync_table1_code "noise"
    prepare_remote_dirs "noise" \
      "${TABLE1_NOISE_READY_FILE}" \
      "${TABLE1_NOISE_OUTPUT}" \
      "${TABLE1_NOISE_PID_FILE}" \
      "${TABLE1_NOISE_LOG_FILE}" \
      "${TABLE1_NOISE_CONTROL_FILE}"
    # Stage the noise user pool (data/doctors.csv) on the noise VM once. Full-sweep
    # callers stage it before the run, so this is usually a no-op after the first cell.
    if ! rt_exec "noise" "test -f '$(remote_path "${TABLE1_USERS_FILE}")'" >/dev/null 2>&1; then
      if ! rt_exec "attacker" "test -f '$(remote_path "${TABLE1_USERS_FILE}")'" >/dev/null 2>&1; then
        echo "Noise users file ${TABLE1_USERS_FILE} is on neither the noise VM nor the attacker." >&2
        echo "Stage the dataset first (the run driver writes or copies data/doctors.csv)." >&2
        exit 1
      fi
      sync_remote_file "attacker" "${TABLE1_USERS_FILE}" "noise" "${TABLE1_USERS_FILE}"
    fi

    echo "Stage: start remote noise on ${NOISE_VM} for ${scenario_label}"
    rt_exec noise \
      "cd '${REMOTE_DIR}' && rm -f '${TABLE1_NOISE_READY_FILE}' '${TABLE1_NOISE_OUTPUT}' '${TABLE1_NOISE_PID_FILE}' '${TABLE1_NOISE_LOG_FILE}' '${TABLE1_NOISE_CONTROL_FILE}' && { nohup venv/bin/python -m noise${CURRENT_NOISE_ARGS_STR} > '${TABLE1_NOISE_LOG_FILE}' 2>&1 < /dev/null & echo \$! > '${TABLE1_NOISE_PID_FILE}'; }"

    for ((attempt = 0; attempt < TABLE1_NOISE_READY_TIMEOUT; attempt++)); do
      status="$(rt_exec noise \
        "cd '${REMOTE_DIR}' && if [[ -f '${TABLE1_NOISE_READY_FILE}' ]]; then echo ready; elif [[ -f '${TABLE1_NOISE_PID_FILE}' ]] && kill -0 \$(cat '${TABLE1_NOISE_PID_FILE}') 2>/dev/null; then echo waiting; else echo failed; fi" \
        2>/dev/null || true)"
      if [[ "${status}" == *"ready"* ]]; then
        NOISE_RUNNING=1
        break
      fi
      if [[ "${status}" == *"failed"* ]]; then
        echo "Remote noise process exited before reaching ready state." >&2
        fetch_remote_file "noise" "${TABLE1_NOISE_LOG_FILE}" "${scenario_output_dir}/$(basename "${TABLE1_NOISE_LOG_FILE}")" || true
        exit 1
      fi
      sleep 1
    done

    if [[ "${NOISE_RUNNING}" != "1" ]]; then
      echo "Timed out waiting for remote noise to become ready." >&2
      fetch_remote_file "noise" "${TABLE1_NOISE_LOG_FILE}" "${scenario_output_dir}/$(basename "${TABLE1_NOISE_LOG_FILE}")" || true
      exit 1
    fi

    if cpu_target_enabled "${scenario_cpu_target_pct}"; then
      start_noise_cpu_controller "${scenario_initial_qps}" "${cpu_trace_file}" "${scenario_cpu_target_pct}"
      if ! wait_for_noise_cpu_ready "${cpu_trace_file}" "${scenario_cpu_target_pct}" "${scenario_label}"; then
        fetch_remote_file "noise" "${TABLE1_NOISE_LOG_FILE}" "${scenario_output_dir}/$(basename "${TABLE1_NOISE_LOG_FILE}")" || true
        exit 1
      fi
    fi
  fi

  prepare_remote_dirs "attacker" "${TABLE1_OUTPUT}" "${TABLE1_TABLE_OUTPUT}" "${TABLE1_NOISE_OUTPUT}" "${TABLE1_METRICS_OUTPUT}"
  echo "Stage: run Table 1 experiment on attacker VM (${scenario_label})"
  # Show timing_oracle's per-policy probe progress bar
  # (util.progress.ProgressBar -> stderr, throttled, in-place) ONLY when the caller's
  # terminal is interactive: rt_exec_tty allocates a PTY so it renders live, and the
  # bar's stderr never mixes into the CSV/JSON result artifacts (those go to files).
  # On a non-TTY (e.g. nohup -> logfile) leave it off so the log stays readable.
  local rls_progress=0
  [[ "${PROGRESS_TTY}" == "1" ]] && rls_progress=1
  rt_exec_tty "attacker" \
    "export TERM=xterm-256color RLS_PROGRESS=${rls_progress}; cd '${REMOTE_DIR}' && venv/bin/python -m timing_oracle \
      --attacker-dsn \"${ATTACKER_DSN}\" \
      --admin-dsn \"${ADMIN_DSN}\" \
      --attacker-user \"${ATTACKER_USER}\"${attack_args_str}"

  cleanup_noise
  trap - EXIT

  fetch_remote_file "attacker" "${TABLE1_OUTPUT}" "${scenario_output_dir}/$(basename "${TABLE1_OUTPUT}")"
  fetch_remote_file "attacker" "${TABLE1_TABLE_OUTPUT}" "${scenario_output_dir}/$(basename "${TABLE1_TABLE_OUTPUT}")"
  fetch_remote_file "attacker" "${TABLE1_METRICS_OUTPUT}" "${scenario_output_dir}/$(basename "${TABLE1_METRICS_OUTPUT}")"

  if (( scenario_clients > 0 )); then
    fetch_remote_file "noise" "${TABLE1_NOISE_OUTPUT}" "${scenario_output_dir}/$(basename "${TABLE1_NOISE_OUTPUT}")"
    fetch_remote_file "noise" "${TABLE1_NOISE_LOG_FILE}" "${scenario_output_dir}/$(basename "${TABLE1_NOISE_LOG_FILE}")"
  else
    fetch_remote_file "attacker" "${TABLE1_NOISE_OUTPUT}" "${scenario_output_dir}/$(basename "${TABLE1_NOISE_OUTPUT}")"
  fi

  echo "Stage: downloaded ${scenario_label} artifacts -> ${scenario_output_dir}/"
}

run_warmup_scenario() {
  if [[ "${TABLE1_SKIP_WARMUP:-0}" == "1" ]]; then
    echo "Stage: cache warmup skipped (TABLE1_SKIP_WARMUP=1)"
    return
  fi
  local warmup_output_dir="${TABLE1_LOCAL_OUTPUT_DIR%/}/_warmup"
  echo "Stage: cache warmup -- running a baseline experiment first (artifacts in _warmup/, not in sweep manifest)"
  run_single_scenario "_warmup" "0" "0" "${warmup_output_dir}" "" "0"
}

ensure_postgres_metrics_extensions
run_warmup_scenario

if [[ -n "${TABLE1_NOISE_SWEEP}" ]]; then
  SWEEP_MANIFEST="${TABLE1_LOCAL_OUTPUT_DIR%/}/table1_noise_sweep.csv"
  printf 'label,noise_clients,noise_total_qps,db_cpu_target_pct,output_dir\n' > "${SWEEP_MANIFEST}"
  SWEEP_BASELINE_THRESHOLDS_REMOTE="${TABLE1_THRESHOLDS_FROM}"

  IFS=',' read -r -a SWEEP_SCENARIOS <<< "${TABLE1_NOISE_SWEEP}"
  for scenario in "${SWEEP_SCENARIOS[@]}"; do
    scenario="${scenario//[[:space:]]/}"
    if [[ -z "${scenario}" ]]; then
      continue
    fi

    IFS=':' read -r field1 field2 field3 field4 extra <<< "${scenario}"
    if [[ -n "${extra}" ]]; then
      echo "Invalid TABLE1_NOISE_SWEEP entry: ${scenario}" >&2
      exit 1
    fi

    label=""
    clients=""
    qps=""
    cpu_target_pct="${TABLE1_NOISE_DB_CPU_TARGET_PCT}"
    if [[ -n "${field4}" ]]; then
      label="${field1}"
      clients="${field2}"
      qps="${field3}"
      cpu_target_pct="${field4}"
    elif [[ -n "${field2}" ]]; then
      if [[ "${field1}" =~ ^[0-9]+$ ]]; then
        clients="${field1}"
        qps="${field2}"
        if [[ -n "${field3}" ]]; then
          cpu_target_pct="${field3}"
        fi
        if [[ "${clients}" == "0" && "${qps}" == "0" ]]; then
          label="baseline"
        else
          safe_qps="${qps//./p}"
          if cpu_target_enabled "${cpu_target_pct}"; then
            safe_cpu="${cpu_target_pct//./p}"
            label="c${clients}_q${safe_qps}_cpu${safe_cpu}"
          else
            label="c${clients}_q${safe_qps}"
          fi
        fi
      else
        label="${field1}"
        clients="${field2}"
        qps="${field3}"
      fi
    else
      echo "Invalid TABLE1_NOISE_SWEEP entry: ${scenario}" >&2
      exit 1
    fi

    safe_label="${label//[^A-Za-z0-9._-]/_}"
    if [[ -z "${safe_label}" ]]; then
      echo "Invalid TABLE1_NOISE_SWEEP label: ${label}" >&2
      exit 1
    fi
    if ! [[ "${clients}" =~ ^[0-9]+$ ]]; then
      echo "Invalid noise client count in TABLE1_NOISE_SWEEP: ${clients}" >&2
      exit 1
    fi
    if [[ "${clients}" == "0" ]]; then
      cpu_target_pct="0"
    fi

    scenario_output_dir="${TABLE1_LOCAL_OUTPUT_DIR%/}/${safe_label}"
    scenario_thresholds_from="${TABLE1_THRESHOLDS_FROM}"
    if [[ -n "${SWEEP_BASELINE_THRESHOLDS_REMOTE}" ]]; then
      scenario_thresholds_from="${SWEEP_BASELINE_THRESHOLDS_REMOTE}"
    fi

    run_single_scenario "${safe_label}" "${clients}" "${qps}" "${scenario_output_dir}" "${scenario_thresholds_from}" "${cpu_target_pct}"

    if [[ "${TABLE1_REUSE_BASELINE_THRESHOLDS}" == "1" && "${clients}" == "0" && "${qps}" == "0" ]]; then
      SWEEP_BASELINE_THRESHOLDS_REMOTE="${TABLE1_OUTPUT}"
    fi
    printf '%s,%s,%s,%s,%s\n' "${safe_label}" "${clients}" "${qps}" "${cpu_target_pct}" "${scenario_output_dir}" >> "${SWEEP_MANIFEST}"
  done

  echo "Sweep manifest written to ${SWEEP_MANIFEST}"
else
  run_single_scenario "single" "${TABLE1_NOISE_CLIENTS}" "${TABLE1_NOISE_TOTAL_QPS}" "${TABLE1_LOCAL_OUTPUT_DIR}" "${TABLE1_THRESHOLDS_FROM}"
fi
