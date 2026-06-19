# Patients SQL

This folder holds the PostgreSQL SQL files used to create the patients/doctors
schema and install the supported RLS policy variants. These files are loaded by
`patients.setup_db` using paths relative to this package.

## Contents

|                  File | Purpose                                                                                           |
| --------------------: | ------------------------------------------------------------------------------------------------- |
| `patients_schema.sql` | Base patients/doctors tables, indexes, and schema objects for the benchmark dataset.              |
|    `patients_rls.sql` | RLS helper functions and policy definitions installed before selecting the active policy variant. |
