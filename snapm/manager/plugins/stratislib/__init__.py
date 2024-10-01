# Copyright 2016 Red Hat, Inc.
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
"""
Package mediating dbus actions.
"""

from ._stratisd_version import check_stratisd_version
from ._stratisd_constants import StratisdErrors
from ._errors import (
    StratisCliGenerationError,
    StratisCliEnvironmentError,
    StratisCliStratisdVersionError,
)
from ._connection import get_object
from ._constants import TOP_OBJECT, Id, IdType
from ._data import (
    MOFilesystem,
    MOPool,
    ObjectManager,
    Pool,
    Filesystem,
    filesystems,
    pools,
)

__all__ = [
    "check_stratisd_version",
    "StratisdErrors",
    "StratisCliGenerationError",
    "StratisCliEnvironmentError",
    "StratisCliStratisdVersionError",
    "get_object",
    "TOP_OBJECT",
    "Id",
    "IdType",
    "MOFilesystem",
    "MOPool",
    "ObjectManager",
    "Pool",
    "Filesystem",
    "filesystems",
    "pools",
]
