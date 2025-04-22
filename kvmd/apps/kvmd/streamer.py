# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2024  Maxim Devaev <mdevaev@gmail.com>               #
#                                                                            #
#    This program is free software: you can redistribute it and/or modify    #
#    it under the terms of the GNU General Public License as published by    #
#    the Free Software Foundation, either version 3 of the License, or       #
#    (at your option) any later version.                                     #
#                                                                            #
#    This program is distributed in the hope that it will be useful,         #
#    but WITHOUT ANY WARRANTY; without even the implied warranty of          #
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           #
#    GNU General Public License for more details.                            #
#                                                                            #
#    You should have received a copy of the GNU General Public License       #
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.  #
#                                                                            #
# ========================================================================== #


import signal
import asyncio
import asyncio.subprocess
import dataclasses
import copy

from typing import AsyncGenerator
from typing import Any

import aiohttp

from ...logging import get_logger

from ...clients.streamer import StreamerSnapshot
from ...clients.streamer import HttpStreamerClient
from ...clients.streamer import HttpStreamerClientSession

from ... import tools
from ... import aiotools
from ... import aioproc
from ... import htclient


# =====
class _StreamerParams:
    __DESIRED_FPS = "desired_fps"

    __QUALITY = "quality"

    __RESOLUTION = "resolution"
    __AVAILABLE_RESOLUTIONS = "available_resolutions"

    __H264_BITRATE = "h264_bitrate"
    __H264_GOP = "h264_gop"

    def __init__(  # pylint: disable=too-many-arguments
        self,
        quality: int,

        resolution: str,
        available_resolutions: list[str],

        desired_fps: int,
        desired_fps_min: int,
        desired_fps_max: int,

        h264_bitrate: int,
        h264_bitrate_min: int,
        h264_bitrate_max: int,

        h264_gop: int,
        h264_gop_min: int,
        h264_gop_max: int,

        whip_url: str = "http://localhost:7777/whip/pikvm",  # live777的WHIP地址
        whip_token: str = "",       # 可选的认证token
        ffmpeg_input_format: str = "v4l2",  # 输入格式
        ffmpeg_codec: str = "libvpx",   # WebRTC编码器
        rtsp_port: int = 8554,     # RTSP监听端口
    ) -> None:

        self.__has_quality = bool(quality)
        self.__has_resolution = bool(resolution)
        self.__has_h264 = bool(h264_bitrate)

        self.__params: dict = {self.__DESIRED_FPS: min(max(desired_fps, desired_fps_min), desired_fps_max)}
        self.__limits: dict = {self.__DESIRED_FPS: {"min": desired_fps_min, "max": desired_fps_max}}

        if self.__has_quality:
            self.__params[self.__QUALITY] = quality

        if self.__has_resolution:
            self.__params[self.__RESOLUTION] = resolution
            self.__limits[self.__AVAILABLE_RESOLUTIONS] = available_resolutions

        if self.__has_h264:
            self.__params[self.__H264_BITRATE] = min(max(h264_bitrate, h264_bitrate_min), h264_bitrate_max)
            self.__limits[self.__H264_BITRATE] = {"min": h264_bitrate_min, "max": h264_bitrate_max}
            self.__params[self.__H264_GOP] = min(max(h264_gop, h264_gop_min), h264_gop_max)
            self.__limits[self.__H264_GOP] = {"min": h264_gop_min, "max": h264_gop_max}

        # 添加live777相关参数
        self.__params.update({
            "whip_url": whip_url,
            "whip_token": whip_token,
            "ffmpeg_input_format": ffmpeg_input_format,
            "ffmpeg_codec": ffmpeg_codec,
            "rtsp_port": rtsp_port,
            "process_name_prefix": "",  # 将在Streamer.__init__中设置
        })

    def get_features(self) -> dict:
        return {
            self.__QUALITY: self.__has_quality,
            self.__RESOLUTION: self.__has_resolution,
            "h264": self.__has_h264,
        }

    def get_limits(self) -> dict:
        limits = copy.deepcopy(self.__limits)
        if self.__has_resolution:
            limits[self.__AVAILABLE_RESOLUTIONS] = list(limits[self.__AVAILABLE_RESOLUTIONS])
        return limits

    def get_params(self) -> dict:
        return dict(self.__params)

    def set_params(self, params: dict) -> None:
        new_params = dict(self.__params)

        if self.__QUALITY in params and self.__has_quality:
            new_params[self.__QUALITY] = min(max(params[self.__QUALITY], 1), 100)

        if self.__RESOLUTION in params and self.__has_resolution:
            if params[self.__RESOLUTION] in self.__limits[self.__AVAILABLE_RESOLUTIONS]:
                new_params[self.__RESOLUTION] = params[self.__RESOLUTION]

        for (key, enabled) in [
            (self.__DESIRED_FPS, True),
            (self.__H264_BITRATE, self.__has_h264),
            (self.__H264_GOP, self.__has_h264),
        ]:
            if key in params and enabled:
                if self.__check_limits_min_max(key, params[key]):
                    new_params[key] = params[key]

        # 处理live777相关参数
        for key in ["whip_url", "whip_token", "ffmpeg_input_format", "ffmpeg_codec", "rtsp_port"]:
            if key in params:
                new_params[key] = params[key]

        self.__params = new_params

    def __check_limits_min_max(self, key: str, value: int) -> bool:
        return (self.__limits[key]["min"] <= value <= self.__limits[key]["max"])


class Streamer:  # pylint: disable=too-many-instance-attributes
    __ST_FULL     = 0xFF
    __ST_PARAMS   = 0x01
    __ST_STREAMER = 0x02
    __ST_SNAPSHOT = 0x04

    def __init__(  # pylint: disable=too-many-arguments,too-many-locals
        self,

        reset_delay: float,
        shutdown_delay: float,
        state_poll: float,

        unix_path: str,
        timeout: float,
        snapshot_timeout: float,

        process_name_prefix: str,

        pre_start_cmd: list[str],
        pre_start_cmd_remove: list[str],
        pre_start_cmd_append: list[str],

        cmd: list[str],
        cmd_remove: list[str],
        cmd_append: list[str],

        post_stop_cmd: list[str],
        post_stop_cmd_remove: list[str],
        post_stop_cmd_append: list[str],

        **params_kwargs: Any,
    ) -> None:

        self.__reset_delay = reset_delay
        self.__shutdown_delay = shutdown_delay
        self.__state_poll = state_poll

        self.__unix_path = unix_path
        self.__snapshot_timeout = snapshot_timeout

        self.__process_name_prefix = process_name_prefix

        self.__pre_start_cmd = tools.build_cmd(pre_start_cmd, pre_start_cmd_remove, pre_start_cmd_append)
        self.__cmd = tools.build_cmd(cmd, cmd_remove, cmd_append)
        self.__post_stop_cmd = tools.build_cmd(post_stop_cmd, post_stop_cmd_remove, post_stop_cmd_append)

        self.__params = _StreamerParams(**params_kwargs)
        # 设置process_name_prefix参数到params中
        params = self.__params.get_params()
        params["process_name_prefix"] = self.__process_name_prefix
        self.__params.set_params(params)

        self.__stop_task: (asyncio.Task | None) = None
        self.__stop_wip = False

        self.__streamer_task: (asyncio.Task | None) = None
        self.__streamer_proc: (asyncio.subprocess.Process | None) = None  # pylint: disable=no-member

        self.__client = HttpStreamerClient(
            name="jpeg",
            unix_path=self.__unix_path,
            timeout=timeout,
            user_agent=htclient.make_user_agent("KVMD"),
        )
        self.__client_session: (HttpStreamerClientSession | None) = None

        self.__snapshot: (StreamerSnapshot | None) = None

        self.__notifier = aiotools.AioNotifier()

    # =====

    @aiotools.atomic_fg
    async def ensure_start(self, reset: bool) -> None:
        if not self.__streamer_task or self.__stop_task:
            logger = get_logger(0)

            if self.__stop_task:
                if not self.__stop_wip:
                    self.__stop_task.cancel()
                    await asyncio.gather(self.__stop_task, return_exceptions=True)
                    logger.info("Streamer stop cancelled")
                    return
                else:
                    await asyncio.gather(self.__stop_task, return_exceptions=True)

            if reset and self.__reset_delay > 0:
                logger.info("Waiting %.2f seconds for reset delay ...", self.__reset_delay)
                await asyncio.sleep(self.__reset_delay)
            logger.info("Starting streamer ...")
            await self.__inner_start()

    @aiotools.atomic_fg
    async def ensure_stop(self, immediately: bool) -> None:
        if self.__streamer_task:
            logger = get_logger(0)

            if immediately:
                if self.__stop_task:
                    if not self.__stop_wip:
                        self.__stop_task.cancel()
                        await asyncio.gather(self.__stop_task, return_exceptions=True)
                        logger.info("Stopping streamer immediately ...")
                        await self.__inner_stop()
                    else:
                        await asyncio.gather(self.__stop_task, return_exceptions=True)
                else:
                    logger.info("Stopping streamer immediately ...")
                    await self.__inner_stop()

            elif not self.__stop_task:

                async def delayed_stop() -> None:
                    try:
                        await asyncio.sleep(self.__shutdown_delay)
                        self.__stop_wip = True
                        logger.info("Stopping streamer after delay ...")
                        await self.__inner_stop()
                    finally:
                        self.__stop_task = None
                        self.__stop_wip = False

                logger.info("Planning to stop streamer in %.2f seconds ...", self.__shutdown_delay)
                self.__stop_task = asyncio.create_task(delayed_stop())

    def is_working(self) -> bool:
        # 检查streamer是否正在运行且没有计划停止
        return bool(self.__streamer_task and not self.__stop_task)

    # =====

    def set_params(self, params: dict) -> None:
        assert not self.__streamer_task
        self.__notifier.notify(self.__ST_PARAMS)
        return self.__params.set_params(params)

    def get_params(self) -> dict:
        return self.__params.get_params()

    # =====

    async def get_state(self) -> dict:
        return {
            "features": self.__params.get_features(),
            "limits": self.__params.get_limits(),
            "params": self.__params.get_params(),
            "streamer": (await self.__get_streamer_state()),
            "snapshot": self.__get_snapshot_state(),
        }

    async def trigger_state(self) -> None:
        self.__notifier.notify(self.__ST_FULL)

    async def poll_state(self) -> AsyncGenerator[dict, None]:
        # ==== Granularity table ====
        #   - features -- Full
        #   - limits   -- Partial, paired with params
        #   - params   -- Partial, paired with limits
        #   - streamer -- Partial, nullable
        #   - snapshot -- Partial
        # ===========================

        def signal_handler(*_: Any) -> None:
            get_logger(0).info("Got SIGUSR2, checking the stream state ...")
            self.__notifier.notify(self.__ST_STREAMER)

        get_logger(0).info("Installing SIGUSR2 streamer handler ...")
        asyncio.get_event_loop().add_signal_handler(signal.SIGUSR2, signal_handler)

        prev: dict = {}
        while True:
            new: dict = {}

            mask = await self.__notifier.wait(timeout=self.__state_poll)
            if mask == self.__ST_FULL:
                new = await self.get_state()
                prev = copy.deepcopy(new)
                yield new
                continue

            if mask < 0:
                mask = self.__ST_STREAMER

            def check_update(key: str, value: (dict | None)) -> None:
                if prev.get(key) != value:
                    new[key] = value

            if mask & self.__ST_PARAMS:
                check_update("params", self.__params.get_params())
            if mask & self.__ST_STREAMER:
                check_update("streamer", await self.__get_streamer_state())
            if mask & self.__ST_SNAPSHOT:
                check_update("snapshot", self.__get_snapshot_state())

            if new and prev != new:
                prev.update(copy.deepcopy(new))
                yield new

    async def __get_streamer_state(self) -> (dict | None):
        if self.__streamer_proc:
            try:
                # 检查ffmpeg进程状态
                if self.__streamer_proc.returncode is None:
                    params = self.__params.get_params()
                    return {
                        "online": True,
                        "encoder": params.get("ffmpeg_codec", "libvpx").split("-")[-1],  # 提取编码器名称
                        "resolution": params.get("resolution", "1920x1080"),
                        "fps": params.get("desired_fps", 30),
                    }
            except Exception:
                get_logger().exception("Failed to get streamer state")
        return None

    def __get_snapshot_state(self) -> dict:
        if self.__snapshot:
            snapshot = dataclasses.asdict(self.__snapshot)
            del snapshot["headers"]
            del snapshot["data"]
            return {"saved": snapshot}
        return {"saved": None}

    # =====

    async def take_snapshot(self, save: bool, load: bool, allow_offline: bool) -> (StreamerSnapshot | None):
        # 注意：使用ffmpeg+live777时，快照功能可能不可用
        # 我们需要重新实现或返回一个模拟值
        logger = get_logger()
        logger.warning("Snapshot feature is not available with ffmpeg+live777 streaming method")
        
        if load:
            return self.__snapshot
            
        # 这里可以考虑实现从ffmpeg获取单帧图像的逻辑
        # 但目前先返回None，表示不支持此功能
        return None

    def remove_snapshot(self) -> None:
        self.__snapshot = None

    # =====

    @aiotools.atomic_fg
    async def cleanup(self) -> None:
        await self.ensure_stop(immediately=True)
        if self.__client_session:
            await self.__client_session.close()
            self.__client_session = None

    def __ensure_client_session(self) -> HttpStreamerClientSession:
        if not self.__client_session:
            self.__client_session = self.__client.make_session()
        return self.__client_session

    # =====

    @aiotools.atomic_fg
    async def __inner_start(self) -> None:
        assert not self.__streamer_task
        await self.__run_hook("PRE-START-CMD", self.__pre_start_cmd)
        self.__streamer_task = asyncio.create_task(self.__streamer_task_loop())

    @aiotools.atomic_fg
    async def __inner_stop(self) -> None:
        assert self.__streamer_task
        self.__streamer_task.cancel()
        await asyncio.gather(self.__streamer_task, return_exceptions=True)
        await self.__kill_streamer_proc()
        await self.__run_hook("POST-STOP-CMD", self.__post_stop_cmd)
        self.__streamer_task = None

    # =====

    async def __streamer_task_loop(self) -> None:
        logger = get_logger(0)
        while True:
            try:
                await self.__start_streamer_proc()
                assert self.__streamer_proc is not None
                
                # 同时监控stdout和stderr
                await asyncio.gather(
                    aioproc.log_stdout_infinite(self.__streamer_proc, logger),
                    aioproc.log_stderr_infinite(self.__streamer_proc, logger)
                )
                
                raise RuntimeError("Streamer unexpectedly died")
            except asyncio.CancelledError:
                break
            except Exception:
                if self.__streamer_proc:
                    logger.exception(
                        "Unexpected streamer error: pid=%d",
                        self.__streamer_proc.pid
                    )
                else:
                    logger.exception("Can't start streamer")
                await self.__kill_streamer_proc()
                await asyncio.sleep(1)

    async def __run_hook(self, name: str, cmd: list[str]) -> None:
        logger = get_logger()
        cmd = self.__make_cmd(cmd)
        logger.info("%s: %s", name, tools.cmdfmt(cmd))
        try:
            await aioproc.log_process(cmd, logger, prefix=name)
        except Exception as ex:
            logger.exception("Can't execute command: %s", ex)

    def __make_cmd(self, cmd: list[str]) -> list[str]:
        params = self.__params.get_params()
        cmd = list(cmd)  # Create a copy to avoid modifying the original
        
        # 替换参数占位符
        for (index, arg) in enumerate(cmd):
            for (name, value) in params.items():
                if isinstance(arg, str) and "{" + name + "}" in arg:
                    cmd[index] = arg.format(**{name: value})
        
        return cmd

    async def __start_streamer_proc(self) -> None:
        assert self.__streamer_proc is None
        cmd = self.__make_cmd(self.__cmd)
        
        # 如果命令行中有管道符"|"或后台执行符"&"，需要使用shell执行
        if "|" in cmd or "&" in cmd:
            shell_cmd = " ".join(cmd)
            self.__streamer_proc = await asyncio.create_subprocess_shell(
                shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            get_logger(0).info(
                "Started streamer with shell pid=%d: %s",
                self.__streamer_proc.pid,
                shell_cmd
            )
        else:
            self.__streamer_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            get_logger(0).info(
                "Started streamer pid=%d: %s",
                self.__streamer_proc.pid,
                tools.cmdfmt(cmd)
            )

    async def __kill_streamer_proc(self) -> None:
        if self.__streamer_proc:
            await aioproc.kill_process(self.__streamer_proc, 1, get_logger(0))
        self.__streamer_proc = None
