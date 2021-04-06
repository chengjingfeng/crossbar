###############################################################################
#
# Crossbar.io Shell
# Copyright (c) Crossbar.io Technologies GmbH. All rights reserved.
#
###############################################################################
"""Crossbar.io Shell (cbsh) is a tool belt for crossbar."""

import txaio
txaio.use_twisted()  # noqa

from autobahn import xbr
from crossbar._version import __version__, __build__

__all__ = (
    '__version__',
    '__build__',
    'xbr',
)
