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
Error hierarchy for stratis cli.
"""


class StratisCliError(Exception):
    """
    Top-level stratis cli error.
    """


class StratisCliRuntimeError(StratisCliError):
    """
    Exception raised while an action is being performed and as a result of
    the requested action.
    """


class StratisCliGenerationError(StratisCliError):
    """
    Exception that occurs during generation of classes.
    """


class StratisCliEnvironmentError(StratisCliError):
    """
    Exception that occurs during processing of environment variables.
    """


class StratisCliStratisdVersionError(StratisCliRuntimeError):
    """
    Raised if stratisd version does not meet CLI version requirements.
    """

    def __init__(self, actual_version, minimum_version, maximum_version):
        """
        Initializer.
        :param tuple actual_version: stratisd's actual version
        :param tuple minimum_version: the minimum version required
        :param tuple maximum_version: the maximum version allowed
        """
        # pylint: disable=super-init-not-called
        self.actual_version = actual_version
        self.minimum_version = minimum_version
        self.maximum_version = maximum_version

    def __str__(self):
        fmt_str = (
            "stratisd version %s does not meet stratis version "
            "requirements; the version must be at least %s and no more "
            "than %s"
        )
        return fmt_str % (
            self.actual_version,
            self.minimum_version,
            self.maximum_version,
        )
