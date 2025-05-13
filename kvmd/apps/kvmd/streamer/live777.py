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

from aiohttp import web
from aiohttp import WSMsgType

from kvmd.apps.kvmd.streamer import Streamer
from kvmd.apps.kvmd.streamer import StreamerState


# =====
class Live777Handler:
    def __init__(self, streamer: Streamer) -> None:
        self.__streamer = streamer
        self.__logger = logging.getLogger("kvmd.streamer.live777")
        self.__clients: Dict[str, web.WebSocketResponse] = {}

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        client_id = id(ws)
        self.__clients[client_id] = ws
        self.__logger.info("New WebSocket client connected: %d", client_id)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self.__handle_message(ws, data)
                    except json.JSONDecodeError:
                        self.__logger.error("Invalid JSON from client %d", client_id)
                elif msg.type == WSMsgType.ERROR:
                    self.__logger.error("WebSocket connection closed with exception: %s", ws.exception())
                    break
        finally:
            self.__clients.pop(client_id, None)
            self.__logger.info("WebSocket client disconnected: %d", client_id)
            return ws

    async def __handle_message(self, ws: web.WebSocketResponse, data: Dict[str, Any]) -> None:
        msg_type = data.get("type")
        if msg_type == "watch":
            await self.__handle_watch(ws, data)
        elif msg_type == "answer":
            await self.__handle_answer(ws, data)
        else:
            self.__logger.error("Unknown message type: %s", msg_type)

    async def __handle_watch(self, ws: web.WebSocketResponse, data: Dict[str, Any]) -> None:
        params = data.get("params", {})
        orientation = params.get("orientation", 0)
        audio = params.get("audio", False)
        mic = params.get("mic", False)

        self.__logger.info("Watch request: orient=%d, audio=%s, mic=%s", orientation, audio, mic)

        # 获取当前流状态
        state = self.__streamer.get_state()
        if not state:
            await ws.send_json({
                "type": "error",
                "error": "Streamer not ready"
            })
            return

        # 创建WebRTC offer
        offer = await self.__create_offer(state)
        if offer:
            await ws.send_json({
                "type": "offer",
                "sdp": offer
            })
        else:
            await ws.send_json({
                "type": "error",
                "error": "Failed to create offer"
            })

    async def __handle_answer(self, ws: web.WebSocketResponse, data: Dict[str, Any]) -> None:
        sdp = data.get("sdp")
        if not sdp:
            self.__logger.error("No SDP in answer")
            return

        self.__logger.info("Received answer")
        # 处理WebRTC answer
        # TODO: 实现answer处理逻辑

    async def __create_offer(self, state: StreamerState) -> Optional[str]:
        # TODO: 实现offer创建逻辑
        return None

    def broadcast_state(self, state: StreamerState) -> None:
        """广播流状态到所有连接的客户端"""
        for client_id, ws in self.__clients.items():
            if not ws.closed:
                asyncio.create_task(self.__send_state(ws, state))

    async def __send_state(self, ws: web.WebSocketResponse, state: StreamerState) -> None:
        try:
            await ws.send_json({
                "type": "state",
                "state": {
                    "online": state.source.online,
                    "resolution": {
                        "width": state.source.resolution.width,
                        "height": state.source.resolution.height
                    }
                }
            })
        except Exception as e:
            self.__logger.error("Failed to send state: %s", e) 