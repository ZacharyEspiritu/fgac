# RLS CLI Reference

After `./setup.sh`, `unfilter-rls` can be run from any directory. The setup
script installs it as a uv tool, so no `uv run` prefix or virtualenv activation
is required. Relative result paths are resolved first from the current directory
and then from the RLS artifact root; relative config paths for claim runs are
resolved from the RLS artifact root.

## Health Check

```bash
unfilter-rls doctor
```

Checks the local Python package, Python version, required shell tools, TeX
tools, and Google Cloud authentication/project state. Use this before
provisioning VMs. For local-only checks:

```bash
unfilter-rls doctor --skip-gcloud --skip-tex
```

## Claims

```bash
unfilter-rls claims list
```

Prints the claim registry: claim id, title, underlying runner, and published
output paths.

```bash
unfilter-rls claims inspect C-R9
```

Shows focused metadata for one claim: the public run command, underlying bash
driver, manifest location, output paths, and what a human should inspect. Claim
ids accept `C-R9`, `CR9`, or `9`.

```bash
unfilter-rls claims run C-R9
unfilter-rls claims run 1,2,3,4,5,6,7,8,9
```

Runs one or more claims through the artifact orchestration drivers. Same-zone
claims are coalesced into one
`orchestration/run_samezone_exps.sh --experiments ...` command; C-R4 maps to
`orchestration/run_crosszone_exps.sh`; C-R5 maps to
`orchestration/run_tde_exps.sh`.

Useful options:

```bash
unfilter-rls claims run C-R9 --dry-run
unfilter-rls claims run 1,2,3,4,5,6,7,8,9 --dry-run
unfilter-rls claims run C-R9 --run-id cr9-review
unfilter-rls claims run C-R9 --config orchestration/config/shared_config.yml
unfilter-rls claims run C-R6 --set singleattr.reps 10
unfilter-rls claims run C-R7 --set tuplext.reps 3
unfilter-rls claims run C-R1 --machines my-machines.yml
```

`--dry-run` prints the underlying command without launching GCP. When `--run-id`
is used for claims spanning multiple top-level runners, the CLI suffixes it per
runner, for example `review-samezone`, `review-tde`, and `review-crosszone`.
`--set KEY VALUE` applies a scalar override to the YAML config for one runner
and can be repeated. Without `--config`, overrides use the selected runner's
default config; with `--config`, they are applied on top of that file. The CLI
writes the generated config to `results/config-overrides/` and passes that file
to the underlying scripts.
`--machines` uses an existing machine descriptor instead of provisioning GCP
VMs. The runner installs the artifact on the described hosts, runs in attached
mode, renders/publishes outputs, records a manifest, and leaves the machines
untouched.

## Results

Top-level runs write `results/runs/<RUN_ID>/manifest.yml`. The manifest records
the run id, claims, runner, command, config, git commit/dirty state, timestamps,
environment knobs, discovered run outputs, and published output paths.

```bash
unfilter-rls results list
```

Lists recorded manifests under `results/runs/`.

```bash
unfilter-rls results inspect <RUN_ID>
unfilter-rls results inspect results/runs/<RUN_ID>/manifest.yml
```

Prints a compact run summary with published outputs and per-run outputs.

```bash
unfilter-rls results inspect <RUN_ID> --raw
```

Prints the underlying manifest YAML.
