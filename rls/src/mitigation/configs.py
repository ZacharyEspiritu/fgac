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

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class MitigationConfig:
    """A single (policy form, index layout) configuration to evaluate."""

    name: str
    description: str
    policy_form: str
    index_layout: str
    expected_outcome: str

    @property
    def label(self) -> str:
        return self.name


ALL_CONFIGS: Tuple[MitigationConfig, ...] = (
    MitigationConfig(
        name="baseline",
        description=(
            "plpgsql site_policy_join (current production), single-column "
            "(attr) index. Status quo; attack expected to succeed."
        ),
        policy_form="plpgsql",
        index_layout="single",
        expected_outcome="attack succeeds",
    ),
    MitigationConfig(
        name="plpgsql_composite",
        description=(
            "plpgsql site_policy_join + (site_id, attr) composite index. "
            "Tests whether adding the composite index alone is sufficient. "
            "Expected to STILL leak: plpgsql wrappers are opaque to the "
            "planner, so site_id never enters Index Cond."
        ),
        policy_form="plpgsql",
        index_layout="composite",
        expected_outcome="attack expected to succeed (index unused without policy refactor)",
    ),
    MitigationConfig(
        name="subq_inline",
        description=(
            "Direct subquery in USING clause (no function wrapper) + "
            "composite. The planner sees `site_id = (SELECT ... FROM doctors)` "
            "as an InitPlan; combined with the composite index it should "
            "land both predicates in Index Cond."
        ),
        policy_form="subq_inline",
        index_layout="composite",
        expected_outcome="attack expected to fail (accuracy ~50%)",
    ),
    MitigationConfig(
        name="subq_inline_single",
        description=(
            "Direct subquery in USING clause (no function wrapper) + "
            "single-column (attr) index ONLY -- no composite. Completes the "
            "2x2 necessity argument (policy-form x index-layout) by isolating "
            "the access-path axis: the policy predicate is now planner-visible "
            "(unlike plpgsql_composite), but there is no index that positions "
            "site_id for index-time evaluation. Expected to STILL leak: the "
            "planner sees `site_id = $0` but, lacking a (site_id, attr) access "
            "path, applies it as a post-scan Filter over rows the attr index "
            "has already matched across all tenants -- the same channel as "
            "baseline. Demonstrates that the policy rewrite alone is "
            "insufficient; the composite index is also necessary."
        ),
        policy_form="subq_inline",
        index_layout="single",
        expected_outcome="attack expected to succeed (predicate visible but no access path)",
    ),
)


CONFIGS_BY_NAME: Dict[str, MitigationConfig] = {cfg.name: cfg for cfg in ALL_CONFIGS}
