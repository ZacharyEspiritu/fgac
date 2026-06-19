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
from typing import List, Optional

from util.io import load_csv


@dataclass
class UserCred:
    user_name: str
    password: str
    tenant_id: Optional[str] = None


def load_user_creds(path: str) -> List[UserCred]:
    rows = load_csv(path)
    if not rows:
        return []
    header = rows[0]
    has_header = any(h in ("user_name", "password", "tenant_id", "site_id") for h in header)
    data = rows[1:] if has_header else rows
    if has_header:
        index = {name: idx for idx, name in enumerate(header)}
        if "tenant_id" not in index and "site_id" in index:
            index["tenant_id"] = index["site_id"]
    else:
        index = {"user_name": 0, "password": 1, "tenant_id": 2}
    creds: List[UserCred] = []
    for row in data:
        user_name = row[index.get("user_name", 0)]
        password = row[index.get("password", 1)]
        tenant_id = row[index.get("tenant_id", 2)] if "tenant_id" in index else None
        creds.append(UserCred(user_name=user_name, password=password, tenant_id=tenant_id))
    return creds
