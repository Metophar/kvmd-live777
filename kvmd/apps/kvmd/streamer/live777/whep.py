#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import uuid
import time
from typing import Dict, Optional, Any, List

from aiohttp.web import Request
from aiohttp.web import Response
from aiohttp.web import StreamResponse

import aiortc
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaStreamTrack, MediaRelay
from aiortc.mediastreams import MediaStreamError, VideoStreamTrack

from ....validators.basic import valid_bool
from ....validators.basic import valid_int_f0
from ....validators.basic import valid_float_f0

from .... import aiotools
from .... import tools

from ....htserver import exposed_http
from ....htserver import make_json_response
from ....htserver import make_response

from ..streamer import StreamerError


# 日志记录器
_logger = logging.getLogger(__name__)


# 自定义视频轨道，从KVMD流媒体获取帧
class KvmdVideoStreamTrack(VideoStreamTrack):
    def __init__(self, streamer):
        super().__init__()
        self.__streamer = streamer
        self.__frame_queue = asyncio.Queue(maxsize=1)
        self.__running = True
        self.__task = asyncio.create_task(self.__fetch_frames())
        self.__last_pts = 0
        self.__start_time = time.time()

    async def __fetch_frames(self):
        try:
            while self.__running:
                try:
                    # 从KVMD流媒体获取帧
                    snapshot = await self.__streamer.take_snapshot(save=False, load=False, allow_offline=True)
                    if snapshot and snapshot.data:
                        # 将JPEG帧转换为VideoFrame
                        frame = await aiortc.codecs.vpx.create_encoder_context().encode_image(
                            snapshot.data,
                            timestamp=int((time.time() - self.__start_time) * 90000)  # 90kHz时钟
                        )

                        # 更新队列，保持最新帧
                        try:
                            self.__frame_queue.put_nowait(frame)
                        except asyncio.QueueFull:
                            # 如果队列满，移除旧帧
                            try:
                                self.__frame_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                            await self.__frame_queue.put(frame)

                    # 控制帧率
                    await asyncio.sleep(1/30)  # 约30fps
                except Exception as e:
                    _logger.error(f"Error fetching frame: {e}")
                    await asyncio.sleep(1)  # 错误后等待
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _logger.error(f"Frame fetching task failed: {e}")
        finally:
            self.__running = False

    async def recv(self):
        if not self.__running:
            raise MediaStreamError("Track ended")

        # 获取下一帧
        frame = await self.__frame_queue.get()
        self.__last_pts = frame.pts
        return frame

    def stop(self):
        self.__running = False
        if self.__task and not self.__task.done():
            self.__task.cancel()


# WHEP API类
class WhepApi:
    def __init__(self, streamer, ice_servers=None):
        self.__streamer = streamer
        self.__ice_servers = ice_servers or []
        self.__whep_sessions: Dict[str, Dict[str, Any]] = {}  # 存储WHEP会话
        self.__relay = MediaRelay()  # 媒体中继
        self.__cleanup_task = asyncio.create_task(self.__cleanup_sessions())

    async def __cleanup_sessions(self):
        """定期清理过期会话"""
        try:
            while True:
                now = time.time()
                expired_sessions = []

                for session_id, session in self.__whep_sessions.items():
                    # 检查会话是否超过10分钟未活动
                    if now - session["last_activity"] > 600:
                        expired_sessions.append(session_id)

                # 关闭过期会话
                for session_id in expired_sessions:
                    try:
                        await self.__close_session(session_id)
                    except Exception as e:
                        _logger.error(f"Error closing expired session {session_id}: {e}")

                await asyncio.sleep(60)  # 每分钟检查一次
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _logger.error(f"Session cleanup task failed: {e}")

    async def __close_session(self, session_id):
        """关闭并清理会话"""
        if session_id in self.__whep_sessions:
            session = self.__whep_sessions.pop(session_id)

            # 停止视频轨道
            if "track" in session:
                session["track"].stop()

            # 关闭PeerConnection
            if "pc" in session:
                await session["pc"].close()

            _logger.info(f"Closed WHEP session {session_id}")

    # WHEP POST端点 - 处理SDP交换
    @exposed_http("POST", "/api/whep/{source}")
    async def __whep_post_handler(self, request: Request) -> Response:
        source = request.match_info.get("source", "default")

        # 确保流已启动
        try:
            await self.__streamer.ensure_start(reset=False)
        except StreamerError as e:
            _logger.error(f"Failed to start streamer: {e}")
            return make_response(text=f"Failed to start streamer: {e}", status=500)

        # 读取请求体中的SDP
        body = await request.read()
        try:
            offer_sdp_str = body.decode("utf-8")
        except UnicodeDecodeError:
            return make_response(text="Invalid SDP", status=400)

        # 解析SDP
        try:
            offer = RTCSessionDescription(sdp=offer_sdp_str, type="offer")
        except Exception as e:
            _logger.error(f"Invalid SDP: {e}")
            return make_response(text=f"Invalid SDP: {e}", status=400)

        # 创建会话ID
        session_id = str(uuid.uuid4())

        # 创建PeerConnection
        pc_config = RTCConfiguration(
            iceServers=[RTCIceServer(urls=server["urls"]) for server in self.__ice_servers]
        )
        pc = RTCPeerConnection(configuration=pc_config)

        # 创建视频轨道
        video_track = KvmdVideoStreamTrack(self.__streamer)

        # 添加轨道到PeerConnection
        pc.addTrack(video_track)

        # 设置远程描述(offer)
        try:
            await pc.setRemoteDescription(offer)
        except Exception as e:
            video_track.stop()
            await pc.close()
            _logger.error(f"Failed to set remote description: {e}")
            return make_response(text=f"Failed to set remote description: {e}", status=400)

        # 创建应答
        try:
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
        except Exception as e:
            video_track.stop()
            await pc.close()
            _logger.error(f"Failed to create answer: {e}")
            return make_response(text=f"Failed to create answer: {e}", status=500)

        # 等待ICE收集完成
        await self.__gather_candidates(pc)

        # 存储会话
        self.__whep_sessions[session_id] = {
            "pc": pc,
            "track": video_track,
            "source": source,
            "created_at": time.time(),
            "last_activity": time.time(),
        }

        _logger.info(f"Created WHEP session {session_id} for source {source}")

        # 返回SDP应答
        return make_response(
            text=pc.localDescription.sdp,
            content_type="application/sdp",
            headers={
                "Location": f"/api/whep/{source}/{session_id}",
                "Link": f'</api/whep/{source}/{session_id}/ice>; rel="ice-server"',
            },
            status=201,
        )

    # WHEP DELETE端点 - 关闭会话
    @exposed_http("DELETE", "/api/whep/{source}/{session_id}")
    async def __whep_delete_handler(self, request: Request) -> Response:
        source = request.match_info.get("source", "default")
        session_id = request.match_info.get("session_id")

        if session_id in self.__whep_sessions:
            await self.__close_session(session_id)
            return make_response(status=200)

        return make_response(status=404)

    # WHEP ICE端点 - 处理Trickle ICE
    @exposed_http("PATCH", "/api/whep/{source}/{session_id}/ice")
    async def __whep_ice_handler(self, request: Request) -> Response:
        source = request.match_info.get("source", "default")
        session_id = request.match_info.get("session_id")

        if session_id not in self.__whep_sessions:
            return make_response(status=404)

        session = self.__whep_sessions[session_id]
        pc = session["pc"]

        # 更新活动时间
        session["last_activity"] = time.time()

        # 读取ICE候选
        body = await request.read()
        try:
            ice_candidate = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return make_response(text="Invalid ICE candidate", status=400)

        # 添加ICE候选
        try:
            candidate = aiortc.RTCIceCandidate(
                component=ice_candidate.get("component", 1),
                foundation=ice_candidate.get("foundation", ""),
                ip=ice_candidate.get("ip", ""),
                port=ice_candidate.get("port", 0),
                priority=ice_candidate.get("priority", 0),
                protocol=ice_candidate.get("protocol", ""),
                type=ice_candidate.get("type", ""),
                sdpMid=ice_candidate.get("sdpMid", ""),
                sdpMLineIndex=ice_candidate.get("sdpMLineIndex", 0),
            )
            await pc.addIceCandidate(candidate)
        except Exception as e:
            _logger.error(f"Failed to add ICE candidate: {e}")
            return make_response(text=f"Failed to add ICE candidate: {e}", status=400)

        return make_response(status=204)

    # 辅助方法 - 等待ICE候选收集完成
    async def __gather_candidates(self, pc):
        """等待ICE候选收集完成"""
        # 创建一个future来等待收集完成
        @pc.on("icecandidate")
        def on_ice_candidate(candidate):
            if candidate is None:
                future.set_result(None)

        future = asyncio.Future()
        try:
            # 等待收集完成，最多10秒
            await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            _logger.warning("ICE gathering timed out") 