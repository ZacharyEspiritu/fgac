# Install Scripts

This directory prepares machines after provisioning. The scripts assume a
machine descriptor already exists and use `orchestration/util/_remote_transport.sh` to run
commands on the appropriate role.

## Contents

|                                  File | Purpose                                                                             |
| ------------------------------------: | ----------------------------------------------------------------------------------- |
|     `install_artifact_on_database.sh` | Install PostgreSQL on the DB VM and configure access for the artifact workload.     |
| `install_artifact_on_database_tde.sh` | Install PostgreSQL on a LUKS-backed data disk for the C-R5 TDE stack.               |
|     `install_artifact_on_attacker.sh` | Install local tools on the attacker VM, copy the repo, and build the remote venv.   |
|        `install_artifact_on_noise.sh` | Install the repo and Python environment on the noise VM for background-load claims. |

## Developer Notes

Provisioning intentionally does not install PostgreSQL, Python dependencies, or
the dataset. Keeping installation separate lets the same experiment drivers run
against either GCP-created machines or a BYO descriptor using `transport: ssh`.
