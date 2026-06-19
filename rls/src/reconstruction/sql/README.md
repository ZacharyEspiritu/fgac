# SQL Helpers

This folder contains database-facing helpers: SQL string builders, timed query
wrappers, and small admin-side sampling/value helpers. Identifier validation
should happen here or before SQL construction.

## Contents

|          File | Purpose                                                                |
| ------------: | ---------------------------------------------------------------------- |
| `__init__.py` | Package marker.                                                        |
|  `queries.py` | SQL builders for attribute, range, tuple, `ANY`, and parts predicates. |
|       `db.py` | Timed query helpers and small DB metadata/value sampling utilities.    |
