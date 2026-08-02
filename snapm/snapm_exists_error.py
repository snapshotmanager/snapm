# Copyright Red Hat
#
# snapm/snapm_exists_error.py - SnapmExistsError with structured name parameter
#
# SPDX-License-Identifier: Apache-2.0
"""Structured SnapmExistsError raised when a snapshot set already exists."""


class SnapmExistsError(Exception):
    """Raised when a snapshot set or snapshot with the given name already exists.

    Parameters
    ----------
    name:
        The snapshot set or snapshot name that already exists.  Stored on
        ``self.name`` so callers can inspect it without parsing the message.
    msg:
        Optional custom message.  When omitted a default human-readable
        message is generated from *name*.

    Examples
    --------
    Raising with just a name (recommended)::

        raise SnapmExistsError("my-snapset")
        # SnapmExistsError: Snapshot set 'my-snapset' already exists

    Raising with a custom message::

        raise SnapmExistsError("my-snapset", "my-snapset is taken; choose another name")
    """

    _default_template = "Snapshot set {name!r} already exists"

    def __init__(self, name: str, msg: str = None) -> None:
        self.name = name
        super().__init__(msg if msg is not None else self._default_template.format(name=name))
