# Row-Level Security (RLS)

This directory contains the artifact for the Row-Level Security section of the
paper.

## Artifact Layout

| Path             | Purpose                                                                                                                                                    |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `orchestration/` | Shell orchestration: GCP provisioning, BYO-machine install/run paths, claim drivers, YAML configs, VM runners, and SSH transport helpers.                  |
| `src/`           | Python package for data loading, timing attacks, reconstruction, mitigations, rendering, and the `unfilter-rls` CLI. See `src/README.md` and each submodule README. |
| `latex/`         | Paper/artifact-appendix LaTeX entry point and compile script.                                                                                              |
| `results/`       | Generated outputs and run manifests; ignored by git.                                                                                                       |
| `docs/`          | User-facing CLI notes.                                                                                                                                     |

The shell orchestration layer is documented in
[`orchestration/README.md`](orchestration/README.md), and Python module
organization is documented in the `README.md` files under `src/`. Our artifact
is designed to be extensible and easy to read for future development. The
typechecking and linting commands are documented in
[`src/README.md`](src/README.md#quality-checks). The local pytest suite lives in
[`src/tests/`](src/tests/) and is documented in
[`src/tests/README.md`](src/tests/README.md).

## Supported Environments

Local driver: macOS or Debian/Ubuntu Linux with Bash, SSH/SCP, git, tar, and
network access to Google Cloud. No special local hardware is required.

Experiment VMs: Google Compute Engine Debian 12 images on C4 machines with
Hyperdisk Balanced boot disks. The checked-in configs are the authors' exact
experiment environment:

| Claim set                                | Config                                      | Zones                                        | VM hardware                                                                |
| ---------------------------------------- | ------------------------------------------- | -------------------------------------------- | -------------------------------------------------------------------------- |
| C-R1, C-R2, C-R3, C-R6, C-R7, C-R8, C-R9 | `orchestration/config/shared_config.yml`    | `us-central1-a`                              | `c4-standard-16`, noise `c4-standard-96` for C-R3 only; 200 GB boot disks. |
| C-R4                                     | `orchestration/config/crosszone_config.yml` | DB/noise `us-east1-b`, attacker `us-west3-a` | DB/attacker `c4-standard-16`, noise `c4-standard-96`; 200 GB boot disks.   |
| C-R5                                     | `orchestration/config/tde_config.yml`       | `us-central1-a`                              | `c4-standard-16`; 200 GB boot disks plus a 200 GB raw encrypted data disk. |

## Quick Install

Install all required dependencies:

```bash
./setup.sh
```

This installs the system tools used by the reproduce scripts:

- `uv`, which installs Python 3.10 and syncs `.venv` with the packages in
  `pyproject.toml` (also mirrored in `requirements.txt` for VM installs),
  including `ruff`, `mypy`, and `vulture` for local quality checks).
- `yq`, for reading YAML experiment configuration files.
- `shellcheck`, for linting the shell drivers.
- Google Cloud CLI (`gcloud`), for launching GCP experiments.
- TeX toolchain for rendering figures and compiling `latex/main.tex`.

The top-level run scripts invoke Python through uv internally, so no manual
virtualenv activation is needed. If `.venv` is missing or a dependency is
missing, they fail with the setup command to rerun.

After setup, you can sanity-check the local artifact environment with:

```bash
unfilter-rls doctor
```

You can also inspect the paper-claim registry and run a claim through the
reviewer-facing CLI:

```bash
unfilter-rls claims list
unfilter-rls claims inspect C-R9
```

### Minimal Working Example

After `./setup.sh`, to make sure your setup is working and that your account has
the ability to provision Google Compute Engine VMs, first check that your
environment has been properly setup:

```bash
unfilter-rls doctor
```

If all rows print "OK", then you can run a quick experiment (**C-R9**, the
fastest experiment):

```bash
unfilter-rls claims run C-R9
```

This will take about 20-25 compute-minutes to run (most of this time is spent
provisioning and tearing down Google Cloud VMs.) You can see some information
about the experiment run by running

```bash
unfilter-rls results list
```

This will show you a table of all the experiment runs you have run, organized by
a unique `RUN_ID` (where `RUN_ID` is an auto-generated, unique tag used to
ensure that experiment VMs do not conflict with each other). You can inspect the
specific run using

```bash
unfilter-rls results inspect [RUN_ID]
```

When the run is finished, `unfilter-rls results list` should show the run with
status `success`, and the experiment should have written a file to
`rls/results/dbsize/summary.txt`. Verify that the numbers align with the
statistics reported in the "Schema and data" paragraph in Section 3.3 of the
paper. If this works, you should be ready to run all the experiments.

For convenience, each script will copy the final figures/tables for each claim
to the constant paths listed below in [Main Claims](#main-claims).

## Main Claims

Use the claim CLI for individual claims:

```bash
unfilter-rls claims list
unfilter-rls claims inspect C-R3
unfilter-rls claims run C-R3
unfilter-rls claims run 1,2,3,4,5,6,7,8,9  # run multiple claims at once
```

You can override scalar config values for a claim run without copying the YAML
file. For example, C-R6 and C-R7 repetitions can be increased with:

```bash
unfilter-rls claims run C-R6 --set singleattr.reps 10
unfilter-rls claims run C-R7 --set tuplext.reps 3
```

`--set` can be repeated and can also be combined with `--config` to apply
overrides on top of a custom base config. Generated config files are written to
`results/config-overrides/`.

Each claim requires a certain set of VMs (which will be automatically
provisioned by the script if you are using the default `gcloud` auto-spawning
functionality).

Note that the "Compute Time" estimates below do not include the `gcloud`
spawning / teardown steps, which can take about 25 minutes.

| Claim | Figure / Table                      | Command                      | Attack VM? | DB VM? | Noise VM? |  Est. Compute Time | Est. Human Time | Main output                                                                                  |
| ----: | ----------------------------------- | ---------------------------- | :--------: | :----: | :-------: | -----------------: | --------------: | -------------------------------------------------------------------------------------------- |
|  C-R1 | Figure 2                            | `unfilter-rls claims run C-R1` |     ✅     |   ✅   |    ⬜     |             ~5 min |          ~2 min | `results/existence/existence_kde_figure.tex`                                                 |
|  C-R2 | Figure 3                            | `unfilter-rls claims run C-R2` |     ✅     |   ✅   |    ⬜     |             ~5 min |          ~2 min | `results/range/existence_range_kde_figure.tex`                                               |
|  C-R3 | Table 1                             | `unfilter-rls claims run C-R3` |     ✅     |   ✅   |    ✅     |               ~4 h |          ~5 min | `results/table1/table1.{png,pdf,pgf}`                                                        |
|  C-R4 | Table 2                             | `unfilter-rls claims run C-R4` |     ✅     |   ✅   |    ✅     | ~12 h (after C-R3) |          ~5 min | `results/table2/table2.{png,pdf,pgf}`                                                        |
|  C-R5 | _no figure_ (see Artifact Appendix) | `unfilter-rls claims run C-R5` |     ✅     |   ✅   |    ⬜     |            ~20 min |          ~5 min | `results/existence/existence_tde_figure.tex`, `results/range/existence_range_tde_figure.tex` |
|  C-R6 | Table 3                             | `unfilter-rls claims run C-R6` |     ✅     |   ✅   |    ⬜     |               ~8 h |          ~5 min | `results/table3/table3.tex`                                                                  |
|  C-R7 | Table 4                             | `unfilter-rls claims run C-R7` |     ✅     |   ✅   |    ⬜     |              ~24 h |          ~2 min | `results/table4/table4.tex`                                                                  |
|  C-R8 | Table 5                             | `unfilter-rls claims run C-R8` |     ✅     |   ✅   |    ⬜     |             ~5 min |          ~2 min | `results/table5/table5.{png,pdf,pgf}`                                                        |
|  C-R9 | _no figure_ (see Artifact Appendix) | `unfilter-rls claims run C-R9` |     ✅     |   ✅   |    ⬜     |             ~1 min |          ~2 min | `results/dbsize/summary.txt`                                                                 |

The requirements for each VM are as follows:

|   VM Role |       Default GCP shape       | Requirements                                                                                                                                                                                 |
| --------: | :---------------------------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Attack VM | `c4-standard-16`; 200 GB disk | SSH from the local driver, sudo+apt, Python venv installed by the artifact, PostgreSQL client tools, and network access to the DB on port 5432.                                              |
|     DB VM | `c4-standard-16`; 200 GB disk | SSH from the local driver, sudo+apt, PostgreSQL 18 installed by the artifact, and inbound PostgreSQL access from Attack/noise roles. C-R5 also needs a 200 GB raw data disk or `tde.device`. |
|  Noise VM | `c4-standard-96`; 200 GB disk | SSH from the local driver, sudo+apt, artifact venv installed by the artifact, and network access to the DB on port 5432 for background load generation.                                      |

See "Bring Your Own Machines" below if you want to use your own machines instead
of using the auto-spawning GCP functionality in the artifact.

## Cleanup

Every top-level runner and claim CLI invocation that provisions GCP **tears its
stack down automatically on exit**: on success _or_ failure. However, if you
accidentally kill the script in the middle of the teardown process, VMs can be
unintentionally left running which will incur unnecessary billing costs. If this
happens, use this script to force-remove a VM stack that was left up:

```bash
bash orchestration/provision/cleanup_vms.sh --machines results/machines/<RUN_ID>.yml
```

## Configuration Options

> **Note:** If you are evaluating the artifact on the author-provided GCP
> infrastructure, you shouldn't need to do any of the below steps.

### Choosing a GCP Project

If you are using the automatic GCP-spawning functionality of our artifact,
authenticate to GCP and select a project before provisioning:

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

The configs in `orchestration/config/` can be used to specify which zone /
region machines should be spawned in as well as machine configuration options
(e.g., type, storage, RAM, etc.).

### Changing the Datasets

- The benchmark dataset is generated by `python -m patients.setup_db`. To
  analyze a different synthetic scale, edit the `dataset:` block in the relevant
  config.

- To use a different database schema, update `src/patients/sql/`,
  `src/patients/setup_db.py`, and the query builders in
  `src/patients/queries.py`.

## Bring Your Own Machines (no Google Cloud)

The machine descriptor decouples each experiment from the provider, so GCP only
appears in the **provision** and **teardown** steps. To run a claim on machines
you provisioned yourself (any cloud or bare metal), hand-write a YAML descriptor
with `transport: ssh` and run the install/run/render steps directly (e.g., skip
provisioning and teardown).

```yaml
# my-machines.yml
transport: ssh
remote_dir: /home/ubuntu/rls # repo checkout path on the attacker
db:
  internal_addr: 10.0.0.5 # DB address as seen from the attacker
  host: 10.0.0.5 # SSH host for install_artifact_on_database
  user: ubuntu
  key: ~/.ssh/id_ed25519 # SSH key
attacker:
  host: 203.0.113.9
  user: ubuntu
  key: ~/.ssh/id_ed25519 # SSH key
# noise:                              # ONLY for C-R3 / C-R4 (the noise generator)
#   host: 203.0.113.10
#   user: ubuntu
#   key: ~/.ssh/id_ed25519
```

> **C-R3 / C-R4** additionally need the `noise:` host. For **C-R5 (TDE)**, its
> DB install (`orchestration/install/install_artifact_on_database_tde.sh`)
> LUKS-encrypts a spare raw block device on the DB host, so point it at one (or
> pre-encrypt the data directory yourself).

For BYO runs, pass the descriptor to the claim CLI:

```bash
unfilter-rls claims run C-R1 --machines my-machines.yml
```

The claim CLI will then skip GCP provisioning/cleanup, install the artifact on
the described hosts, and run the experiments as normal on the hosts.
