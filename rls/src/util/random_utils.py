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

import random
from typing import Sequence, Tuple


def choose_weighted(rng: random.Random, weights: Sequence[Tuple[str, float]]) -> str:
    total = sum(weight for _, weight in weights)
    r = rng.random() * total
    upto = 0.0
    for label, weight in weights:
        upto += weight
        if r <= upto:
            return label
    return weights[-1][0]
