# DLS on Google Compute Engine

## Launch

This launches fixed-seed runs for each `D`. The prefixes are used in all status,
download, and cleanup commands below.

```bash
while read -r d r seed; do
  prefix="repro-d${d}-r${r}"
  bash gcp/run_opensearch_two_vm_experiment.sh \
    --project "$PROJECT_ID" \
    --zone "$ZONE" \
    --prefix "$prefix" \
    --d "$d" \
    -- \
    --random-seed "$seed"
done <<'EOF'
1 1 12532882246817266601
10 1 12532882246817266554
100 1 12532882246817266701
1000 1 12532882246817266801
EOF
```

The attack configuration is read from `config/config.yml` by
`src/util/build_enumerator_command.py`, which is used by both `run.sh` and
`gcp/run_opensearch_two_vm_experiment.sh`. The GCP launcher adds
`--progress-interval 30` and writes stats to the per-run GCP result directory.
Arguments after `--` are appended to the generated attack command, so the fixed
seed commands above override the generated seed with `--random-seed`.

## Check Status

Run this periodically. `stats=present` means the run completed and the stats
JSON is ready to download.

```bash
for d in 1 10 100 1000; do
  for r in 1 2 3; do
    prefix="repro-d${d}-r${r}"
    remote_dir="results/gcp/${prefix}-d${d}"
    log="${remote_dir}/opensearch_d${d}_sasy_ngram4.log"
    stats="${remote_dir}/opensearch_d${d}_sasy_ngram4_stats.json"
    pid="${remote_dir}/attack.pid"

    gcloud --project "$PROJECT_ID" compute ssh "${prefix}-attacker" \
      --zone "$ZONE" \
      --command "cd ~/opensearch-experiment && echo ${prefix} && if kill -0 \$(cat ${pid}) 2>/dev/null; then echo status=running; else echo status=done; fi; if test -f ${stats}; then echo stats=present; else echo stats=missing; fi; grep -E '\\[progress\\]|Wrote run statistics|Traceback|ERROR|ConnectionTimeout' ${log} | tail -n 20"
  done
done
```

To watch one run continuously:

```bash
d=1000
r=1
prefix="repro-d${d}-r${r}"
gcloud --project "$PROJECT_ID" compute ssh "${prefix}-attacker" \
  --zone "$ZONE" \
  --command "tail -f ~/opensearch-experiment/results/gcp/${prefix}-d${d}/opensearch_d${d}_sasy_ngram4.log"
```

## Download Results

Run this after the status command shows `stats=present` for the runs you want to
download. Local filenames include the run prefix, so repeated downloads for
different runs do not overwrite each other.

```bash
mkdir -p gcpresults/reproduce

for d in 1 10 100 1000; do
  for r in 1 2 3; do
    prefix="repro-d${d}-r${r}"
    remote_dir="~/opensearch-experiment/results/gcp/${prefix}-d${d}"

    gcloud --project "$PROJECT_ID" compute scp \
      "${prefix}-attacker:${remote_dir}/opensearch_d${d}_sasy_ngram4_stats.json" \
      "gcpresults/reproduce/${prefix}_stats.json" \
      --zone "$ZONE"

    gcloud --project "$PROJECT_ID" compute scp \
      "${prefix}-attacker:${remote_dir}/opensearch_d${d}_sasy_ngram4.log" \
      "gcpresults/reproduce/${prefix}.log" \
      --zone "$ZONE"
  done
done
```
