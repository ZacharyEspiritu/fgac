# Patients Dataset

This package contains the shared patients/doctors benchmark-domain code. It owns
dataset setup, SQL query builders, credential parsing, and sampling helpers used
by the timing-oracle, reconstruction, mitigation, noise, and microbenchmark
runners.

## Contents

|   File or folder | Purpose                                                                                                    |
| ---------------: | ---------------------------------------------------------------------------------------------------------- |
|    `__init__.py` | Package marker.                                                                                            |
| `credentials.py` | Parser for generated doctor/noise-client credential CSVs.                                                  |
|     `queries.py` | Backend-aware point/range patient query builders and parameter helpers.                                    |
|    `sampling.py` | Grounded sampling helpers for attacker site context, authorized/unauthorized keys, and attribute values.   |
|    `setup_db.py` | Dataset setup CLI for `python -m patients.setup_db`; creates schema, roles, credentials, and patient rows. |
|           `sql/` | PostgreSQL schema and RLS policy SQL loaded by `setup_db.py`.                                              |
