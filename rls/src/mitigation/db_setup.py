# Copyright 2026 MongoDB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Dict, Sequence

from util.sql_utils import validate_identifier


SITE_POLICY_FUNCTIONS: Dict[str, str] = {
    "plpgsql": """
CREATE OR REPLACE FUNCTION site_policy_join(row_site BIGINT, curr_user TEXT)
RETURNS BOOLEAN
AS $$
BEGIN
    RETURN ((SELECT site_id FROM doctors WHERE user_name = curr_user) = row_site);
END;
$$ LANGUAGE plpgsql;
""",
}

BASELINE_PLPGSQL_FUNCTION = SITE_POLICY_FUNCTIONS["plpgsql"]
DOCTORS_INDEX_NAME = "doctors_user_name_c_idx"


def drop_all_policies(cur) -> None:
    cur.execute("DROP POLICY IF EXISTS doctor_read ON patients")


def apply_policy(
    cur,
    *,
    policy_form: str,
    attacker_user: str,
) -> None:
    """Install the policy variant for this configuration."""
    validate_identifier(attacker_user)
    drop_all_policies(cur)
    cur.execute("DROP FUNCTION IF EXISTS site_policy_join(BIGINT, TEXT)")
    cur.execute("ALTER TABLE patients ENABLE ROW LEVEL SECURITY")

    if policy_form in SITE_POLICY_FUNCTIONS:
        cur.execute(SITE_POLICY_FUNCTIONS[policy_form])
        cur.execute(
            "CREATE POLICY doctor_read ON patients FOR SELECT "
            "USING (site_policy_join(site_id, current_user))"
        )
        return

    if policy_form == "subq_inline":
        cur.execute(
            "CREATE POLICY doctor_read ON patients FOR SELECT "
            "USING (site_id = (SELECT site_id FROM doctors "
            "                   WHERE user_name = current_user))"
        )
        return

    raise ValueError(f"Unknown policy_form: {policy_form}")


def apply_index_layout(cur, attribute: str, layout: str) -> None:
    validate_identifier(attribute)
    single_name = f"patients_{attribute}_idx"
    composite_name = f"patients_site_{attribute}_idx"
    cur.execute(f"DROP INDEX IF EXISTS {single_name}")
    cur.execute(f"DROP INDEX IF EXISTS {composite_name}")
    if layout in ("single", "both"):
        cur.execute(f"CREATE INDEX {single_name} ON patients ({attribute})")
    if layout in ("composite", "both"):
        cur.execute(f"CREATE INDEX {composite_name} ON patients (site_id, {attribute})")
    cur.execute("ANALYZE patients")


def restore_baseline(
    cur,
    *,
    attributes: Sequence[str],
) -> None:
    drop_all_policies(cur)
    cur.execute("DROP FUNCTION IF EXISTS site_policy_join(BIGINT, TEXT)")
    cur.execute("ALTER TABLE patients ENABLE ROW LEVEL SECURITY")
    cur.execute(BASELINE_PLPGSQL_FUNCTION)
    cur.execute(
        "CREATE POLICY doctor_read ON patients FOR SELECT "
        "USING (site_policy_join(site_id, current_user))"
    )
    for attribute in attributes:
        validate_identifier(attribute)
        cur.execute(f"DROP INDEX IF EXISTS patients_site_{attribute}_idx")
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS patients_{attribute}_idx ON patients ({attribute})"
        )
    cur.execute("ANALYZE patients")


def create_doctors_index(cur) -> None:
    cur.execute(
        f'CREATE INDEX IF NOT EXISTS {DOCTORS_INDEX_NAME} '
        f'ON doctors (user_name COLLATE "C")'
    )
    cur.execute("ANALYZE doctors")


def drop_doctors_index(cur) -> None:
    cur.execute(f"DROP INDEX IF EXISTS {DOCTORS_INDEX_NAME}")
