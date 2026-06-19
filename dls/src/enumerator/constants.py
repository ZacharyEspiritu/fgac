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

from __future__ import annotations


ADMIN_PASSWORD = "@!A134Kwjdoiwna!"
USER_PASSWORD = "KTrdMBtPB6NmUXP"
INDEX_NAME = "test-index"


def lowercase_letters_in_ranges(*ranges: tuple[int, int]) -> str:
    chars = []
    for start, end in ranges:
        for cp in range(start, end):
            ch = chr(cp)
            if ch.isalpha() and ch == ch.lower() and ch != ch.upper():
                chars.append(ch)
    return "".join(chars)


# Prefix controls must be analyzer-stable tokens with the same length as the
# tested prefix. Cyrillic lowercase gives enough one-character controls even
# when the attack alphabet includes a-z plus digits.
PREFIX_CONTROL_CHARS = lowercase_letters_in_ranges((0x0430, 0x0530))

# Exact-term controls are fresh visible terms used as the baseline in term-query
# score comparisons. Greek lowercase keeps them disjoint from normal Enron text.
EXACT_CONTROL_CHARS = "".join(chr(cp) for cp in range(0x03B1, 0x03CA))

# Each prefix probe uses a shared random context token so the candidate and
# control phrase-prefix queries compare otherwise similar visible documents.
SHARED_CONTEXT_CHARS = "".join(chr(cp) for cp in range(0x0531, 0x0557))

# Span prefix probes use raw term-level span_term clauses for their context.
# These lowercase Armenian tokens remain analyzer-stable under the default text
# analyzer and stay disjoint from the Cyrillic prefix-control alphabet.
SPAN_CONTEXT_CHARS = lowercase_letters_in_ranges((0x0561, 0x0588))

# Prefix probes extend the candidate token to make the visible probe a strict
# extension of the tested prefix. The chosen suffix must be analyzer-stable and
# outside the attack alphabet; otherwise probe docs can become false recovered
# terms, e.g. when digits are part of --chars.
PROBE_SUFFIX_CANDIDATES = lowercase_letters_in_ranges(
    (0x03B1, 0x03CA),
    (0x0430, 0x0530),
)

# The optimized strategy combines the guaranteed exact-probe reductions:
# prefix-negative pruning, level-wide exact batching, and analyzer-aware leaf
# inference. Eager preserves the old per-prefix exact check baseline.
EXACT_STRATEGIES = ("eager", "optimized")

PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX = "match_phrase_prefix"
PREFIX_QUERY_MODE_SPAN_PREFIX = "span_prefix"
PREFIX_QUERY_MODES = (
    PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX,
    PREFIX_QUERY_MODE_SPAN_PREFIX,
)
