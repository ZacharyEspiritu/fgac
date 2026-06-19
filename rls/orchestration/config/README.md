# Orchestration Configs

This directory contains the YAML files that define machine shapes, zones,
network names, dataset sizes, and claim-specific experiment parameters. Shell
loaders in `orchestration/util/` read these files with `yq` and export variables
for the provision, install, and run scripts.

Defaults below are the loader defaults used when a key is omitted. The
checked-in YAML files usually set the camera-ready values explicitly. Scalar
values can be overridden from the claim CLI with `--set`, for example:

```bash
unfilter-rls claims run C-R6 --set singleattr.reps 10
unfilter-rls claims run C-R7 --set tuplext.reps 3
```

## Files

| File                   | Purpose                                                                                   |
| ---------------------- | ----------------------------------------------------------------------------------------- |
| `shared_config.yml`    | Canonical same-zone stack for C-R1, C-R2, C-R3, C-R6, C-R7, C-R8, and C-R9.               |
| `crosszone_config.yml` | C-R4 cross-zone stack: DB/noise in one zone and attacker in another, plus Table 2 params. |
| `tde_config.yml`       | C-R5 TDE stack: same-zone attacker/DB with a raw data disk used for LUKS encryption.      |

## Common Infrastructure

These keys are consumed by `orchestration/util/_load_gcloud_config.sh` and may
be used by any config file.

| Key                     | Default                           | Description                                                                                                                       |
| ----------------------- | --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `project`               | empty                             | GCP project. Empty means use the active `gcloud` project.                                                                         |
| `zone`                  | `us-central1-a`                   | Default zone for DB, attacker, and noise roles unless a role overrides it.                                                        |
| `network`               | `rls-net`                         | VPC network name created or reused by provisioning.                                                                               |
| `subnet`                | `rls-subnet`                      | Default subnet name for same-zone roles.                                                                                          |
| `subnet_range`          | `10.10.0.0/24`                    | Default subnet CIDR.                                                                                                              |
| `db_allow_cidr`         | empty                             | CIDR added to PostgreSQL `pg_hba.conf`. Empty falls back to `subnet_range`. Cross-zone runs set a supernet covering both subnets. |
| `boot_disk_type`        | `hyperdisk-balanced`              | Boot disk type for provisioned VMs. C4 machine types require Hyperdisk.                                                           |
| `image_family`          | `debian-12`                       | Image family for GCP VM boot disks.                                                                                               |
| `image_project`         | `debian-cloud`                    | GCP image project for `image_family`.                                                                                             |
| `postgres_password`     | empty                             | Password for the PostgreSQL admin user. Required for managed DB installs and all experiment connections.                          |
| `rls_policy`            | `join`                            | Policy loaded into the patient dataset unless a driver temporarily swaps policies.                                                |
| `postgres_vm`           | `rls-postgres`                    | Base DB VM name before the run-id suffix. TDE uses a distinct value to avoid colliding with baseline DBs.                         |
| `postgres_setup_script` | `setup_gcloud_postgres_vm.sh`     | Provision-time DB setup script basename under `orchestration/provision/util/`.                                                    |
| `db_install_script`     | `install_artifact_on_database.sh` | Install-time DB script basename under `orchestration/install/`. TDE selects `install_artifact_on_database_tde.sh`.                |
| `db_install_bundled`    | `0`                               | Set to `1` when the provisioner already installed the DB software and the separate DB install step should be skipped.             |

## Role Blocks

### `db`

| Key               | Default          | Description                     |
| ----------------- | ---------------- | ------------------------------- |
| `db.machine_type` | `c4-standard-16` | GCP machine type for the DB VM. |
| `db.disk_size`    | `200GB`          | Boot disk size for the DB VM.   |

### `attacker`

| Key                     | Default                      | Description                                                            |
| ----------------------- | ---------------------------- | ---------------------------------------------------------------------- |
| `attacker.machine_type` | `c4-standard-16`             | GCP machine type for the attacker VM.                                  |
| `attacker.disk_size`    | `200GB`                      | Boot disk size for the attacker VM.                                    |
| `attacker.zone`         | `zone`                       | Attacker zone. Cross-zone C-R4 sets this to a different region.        |
| `attacker.region`       | derived from `attacker.zone` | Attacker region, used when creating an attacker-side subnet.           |
| `attacker.subnet`       | `subnet`                     | Attacker subnet. Cross-zone C-R4 sets a second subnet in the same VPC. |
| `attacker.subnet_range` | `subnet_range`               | CIDR for the attacker subnet.                                          |

### `noise`

Noise is required for C-R3 and C-R4. Same-zone claims that do not need
background load can omit this block.

| Key                  | Default | Description                                                                                                                                  |
| -------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `noise.machine_type` | empty   | GCP machine type for the noise VM. If a noise VM is requested and this is empty, provisioning utilities fall back to attacker-like defaults. |
| `noise.disk_size`    | empty   | Boot disk size for the noise VM.                                                                                                             |
| `noise.zone`         | `zone`  | Noise VM zone. C-R4 keeps noise co-located with the DB so the CPU controller drives DB-local load.                                           |

## Dataset

The `dataset` block is consumed by the shared loader and used by the setup
drivers that load the patients/doctors/sites tables.

| Key                | Default   | Description                           |
| ------------------ | --------- | ------------------------------------- |
| `dataset.patients` | `1000000` | Number of synthetic patients to load. |
| `dataset.doctors`  | `10000`   | Number of synthetic doctors to load.  |
| `dataset.sites`    | `5`       | Number of synthetic sites to load.    |

## TDE Block

The `tde` block is used by C-R5 through `tde_config.yml` and
`install_artifact_on_database_tde.sh`.

| Key                    | Default | Description                                                                                                                      |
| ---------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `tde.disk_device_name` | empty   | GCP raw disk device name. The installer resolves it under `/dev/disk/by-id/google-*`.                                            |
| `tde.data_disk_size`   | empty   | Size, in GB, of the raw secondary disk attached for encrypted PostgreSQL data.                                                   |
| `tde.data_disk_type`   | empty   | GCP disk type for the raw secondary disk.                                                                                        |
| `tde.device`           | empty   | BYO/SSH transport block device to encrypt, for example `/dev/sdb`. If omitted on GCP, the installer uses `tde.disk_device_name`. |

## C-R1 / C-R2: Existence And Range

The `existence` and `range` blocks are consumed by
`orchestration/util/_load_existence_config.sh`.

### `existence`

| Key                 | Default | Description                                |
| ------------------- | ------- | ------------------------------------------ |
| `existence.samples` | `1000`  | Number of equality-probe samples for C-R1. |
| `existence.seed`    | `1`     | Random seed for C-R1 sample generation.    |

### `range`

| Key                        | Default | Description                                        |
| -------------------------- | ------- | -------------------------------------------------- |
| `range.samples`            | `1000`  | Number of range-query samples for C-R2.            |
| `range.width`              | `4`     | Width of each range predicate.                     |
| `range.max_tries`          | `50000` | Maximum attempts when finding range-query samples. |
| `range.nonexistent_offset` | `1000`  | Offset used to generate non-existent patient ids.  |
| `range.seed`               | `1`     | Random seed for C-R2 sample generation.            |

## C-R3: Table 1 Background-Load Sweep

The `table1` block is consumed by `orchestration/util/_load_table1_config.sh`.
`table1.join.*` controls the join-policy half of Table 1; `table1.inline.*`
controls the inline-policy half.

`noise_sweep` entries are comma-separated. Each entry is either
`label:clients:qps` or `label:clients:initial_qps:target_db_cpu_pct`.
`clients=0,qps=0` is the baseline/no-background-load scenario.

| Key                               | Default                | Description                                                              |
| --------------------------------- | ---------------------- | ------------------------------------------------------------------------ |
| `table1.probes`                   | `100000`               | Timing-oracle probes per policy/k/noise cell.                            |
| `table1.seed`                     | `1`                    | Random seed for Table 1 timing/noise clients.                            |
| `table1.calibration_mode`         | `trial`                | Threshold calibration mode. Supported values are `trial` and `scenario`. |
| `table1.nonexistent_offset`       | `1000`                 | Offset used to generate non-existent patient ids.                        |
| `table1.join.policies`            | `join`                 | Comma-separated policy names for the join-policy sweep.                  |
| `table1.join.k_values`            | `1,2,3,4,5,6,7,8,9,10` | Comma-separated k values for the join-policy sweep.                      |
| `table1.join.noise_query_mode`    | `point`                | Noise query mode for join-policy scenarios.                              |
| `table1.join.noise_range_width`   | empty                  | Range width when join noise uses range queries.                          |
| `table1.join.noise_sweep`         | empty                  | Comma-separated background-load scenarios for the join-policy sweep.     |
| `table1.inline.policies`          | `inline`               | Comma-separated policy names for the inline-policy sweep.                |
| `table1.inline.k_values`          | `1,2,4,8`              | Comma-separated k values for the inline-policy sweep.                    |
| `table1.inline.noise_query_mode`  | `range`                | Noise query mode for inline-policy scenarios.                            |
| `table1.inline.noise_range_width` | `1000`                 | Range width when inline noise uses range queries.                        |
| `table1.inline.noise_sweep`       | empty                  | Comma-separated background-load scenarios for the inline-policy sweep.   |

## C-R6: Single-Attribute Reconstruction

The `singleattr` block is consumed by
`orchestration/util/_load_singleattr_config.sh`.

| Key                                       | Default                                           | Description                                                                                                         |
| ----------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `singleattr.columns`                      | `ssn,age,zip`                                     | Comma-separated attribute tokens to reconstruct. The driver maps `zip` to the DB column `zip_code`.                 |
| `singleattr.workers`                      | `1,2,4,8,16`                                      | Comma-separated binary-search worker counts.                                                                        |
| `singleattr.reps`                         | `1`                                               | Repetitions per `(column, worker)` cell. Values above 1 add `-r<rep>` output suffixes that the renderer aggregates. |
| `singleattr.probe_rounds`                 | `1`                                               | Overrides `num_queries_per_probe` for each reconstruction probe.                                                    |
| `singleattr.run_linear`                   | `true`                                            | Whether to run the LinearProber baseline for each column.                                                           |
| `singleattr.binary_reconstruction_config` | `src/reconstruction/config/singleattr_binary.yml` | Reconstruction config path for binary-search cells, relative to the RLS project root/remote workspace.              |
| `singleattr.linear_reconstruction_config` | `src/reconstruction/config/singleattr_linear.yml` | Reconstruction config path for LinearProber cells, relative to the RLS project root/remote workspace.               |

## C-R7: Tuple-Extension Reconstruction

The `tuplext` block is consumed by `orchestration/util/_load_tuplext_config.sh`.

| Key                                  | Default                                 | Description                                                                                                           |
| ------------------------------------ | --------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `tuplext.orderings`                  | `sza,saz,asz,azs,zsa,zas`               | Comma-separated orderings over `ssn`, `zip`, and `age`.                                                               |
| `tuplext.workers`                    | `1`                                     | Comma-separated attacker worker counts.                                                                               |
| `tuplext.reps`                       | `1`                                     | Repetitions per `(ordering, worker)` cell. Values above 1 add `-r<rep>` output suffixes that the renderer aggregates. |
| `tuplext.sample_tuples`              | `100000`                                | Value passed as `--sample-tuples` to tuple-extension reconstruction.                                                  |
| `tuplext.tuple_extension_mode`       | `between`                               | Value passed as `--tuple-extension-mode`.                                                                             |
| `tuplext.tuple_recompute_cal_rounds` | `1`                                     | Value passed as `--tuple-recompute-cal-rounds`; enables the recompute threshold.                                      |
| `tuplext.probe_rounds`               | `1`                                     | Overrides `num_queries_per_probe` for each reconstruction probe.                                                      |
| `tuplext.reconstruction_config`      | `src/reconstruction/config/tuplext.yml` | Tuple-extension reconstruction config path, relative to the RLS project root/remote workspace.                        |

## C-R8: Mitigations

The `mitigations` block is consumed by
`orchestration/util/_load_mitigations_config.sh`.

| Key                         | Default                                                     | Description                                         |
| --------------------------- | ----------------------------------------------------------- | --------------------------------------------------- |
| `mitigations.configs`       | `baseline,plpgsql_composite,subq_inline_single,subq_inline` | Comma-separated mitigation configurations to sweep. |
| `mitigations.probes`        | `2000`                                                      | Timing-oracle probes per mitigation/k cell.         |
| `mitigations.kvalues`       | `1,2,3,4`                                                   | Comma-separated k values for the mitigation sweep.  |
| `mitigations.attribute`     | `ssn`                                                       | Attribute used for the mitigation timing attack.    |
| `mitigations.run_timeout_s` | `14400`                                                     | Per-run timeout, in seconds.                        |

## C-R4: Cross-Zone Table 2

The `crosszone` block is consumed by
`orchestration/util/_load_crosszone_config.sh`. C-R4 also uses the common role
options above to place `attacker` in another zone/subnet while keeping DB and
noise co-located.

`crosszone.noise_sweep` uses the same entry format as `table1.*.noise_sweep`.

| Key                                | Default                            | Description                                                                                   |
| ---------------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------- |
| `crosszone.noise_sweep`            | `baseline:0:0,cpu50:96:5000:50`    | Comma-separated Base/cpu-load scenarios for C-R4.                                             |
| `crosszone.probes`                 | `10000`                            | Timing-oracle probes per policy/k/noise cell.                                                 |
| `crosszone.k_values`               | `1,2,4,8`                          | Comma-separated k values for C-R4.                                                            |
| `crosszone.calibration_mode`       | `trial`                            | Threshold calibration mode. Supported values are `trial` and `scenario`.                      |
| `crosszone.seed`                   | `1`                                | Random seed for C-R4 timing/noise clients.                                                    |
| `crosszone.nonexistent_offset`     | `1000`                             | Offset used to generate non-existent patient ids.                                             |
| `crosszone.skip_warmup`            | `1`                                | Whether to skip the extra cache-warmup scenario before the sweep.                             |
| `crosszone.noise_query_mode`       | `range`                            | Noise query mode for cross-zone load.                                                         |
| `crosszone.noise_range_width`      | `100`                              | Range width when cross-zone noise uses range queries.                                         |
| `crosszone.cpu_tolerance_pct`      | `2`                                | Allowed DB CPU error band for the closed-loop noise controller.                               |
| `crosszone.cpu_ready_timeout`      | `1200`                             | Seconds to wait for DB CPU to converge before running the timing probes.                      |
| `crosszone.cpu_sample_seconds`     | `3`                                | Sampling interval for DB CPU measurements.                                                    |
| `crosszone.cpu_ready_streak`       | `2`                                | Consecutive in-band samples required before a scenario is ready.                              |
| `crosszone.cpu_gain`               | `0.75`                             | Controller gain used when adjusting noise QPS toward the target CPU load.                     |
| `crosszone.render_scenarios`       | `baseline,cpu50`                   | Cross-zone scenarios included in the Table 2 renderer.                                        |
| `crosszone.same_zone_baseline_dir` | `results/table1/samezone-baseline` | Directory containing same-zone C-R3 baseline/cpu50 artifacts used for the Table 2 comparison. |
