#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =============================================================================
#  Copyright (C) 2018-2024  Maxim Devaev <mdevaev@gmail.com>
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
# =============================================================================


import json
import asyncio
import logging
from typing import Dict
from typing import Optional
from typing import Any
from typing import List

from aiohttp import web
from aiohttp import WSMsgType

from kvmd.apps.kvmd.streamer import Streamer
from kvmd.apps.kvmd.streamer import StreamerState


# =====
class Live777Handler:
    def __init__(self, streamer: Streamer) -> None:
        self.__streamer = streamer
        self.__logger = logging.getLogger("kvmd.live777")
        self.__clients: Dict[str, web.WebSocketResponse] = {}
        self.__sessions: Dict[str, Dict[str, Any]] = {}

    # ... rest of the code ... 