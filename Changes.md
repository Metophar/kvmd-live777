# kvmd-live777 修改日志

## 概述

本项目基于PiKVM的kvmd项目，修改了视频流处理部分，使用ffmpeg+live777替代原有的ustreamer+janus方案。
这种修改带来的好处包括更低的延迟、更灵活的视频处理选项和更简单的架构。

## 主要修改

### 1. 核心组件修改

#### kvmd/apps/kvmd/streamer.py
- 修改了`_StreamerParams`类，添加了live777相关参数（whip_url、whip_token等）
- 更新了`__make_cmd`方法，支持通过配置文件参数生成ffmpeg和whipinto命令
- 修改了`__start_streamer_proc`方法，支持使用shell执行管道命令
- 修改了`__get_streamer_state`方法，适配新的流媒体状态报告
- 暂时禁用了`take_snapshot`功能，将来可能实现直接从ffmpeg获取快照

```python
class _StreamerParams:
    # ...
    def __init__(  # pylint: disable=too-many-arguments
        # ...
        whip_url: str = "http://localhost:7777/whip/pikvm",  # live777的WHIP地址
        whip_token: str = "",       # 可选的认证token
        ffmpeg_input_format: str = "v4l2",  # 输入格式
        ffmpeg_codec: str = "libvpx-vp8",   # WebRTC编码器
    ) -> None:
        # ...
        # 添加live777相关参数
        self.__params.update({
            "whip_url": whip_url,
            "whip_token": whip_token,
            "ffmpeg_input_format": ffmpeg_input_format,
            "ffmpeg_codec": ffmpeg_codec,
        })
    
    # 新的make_cmd方法
    def __make_cmd(self, cmd: list[str]) -> list[str]:
        params = self.__params.get_params()
        cmd = list(cmd)  # Create a copy to avoid modifying the original
        
        # 替换参数占位符
        for (index, arg) in enumerate(cmd):
            for (name, value) in params.items():
                if isinstance(arg, str) and "{" + name + "}" in arg:
                    cmd[index] = arg.format(**{name: value})
        
        return cmd

    # 新的进程启动方法
    async def __start_streamer_proc(self) -> None:
        assert self.__streamer_proc is None
        cmd = self.__make_cmd(self.__cmd)
        
        # 如果命令行中有管道符"|"，需要使用shell执行
        if "|" in cmd:
            shell_cmd = " ".join(cmd)
            self.__streamer_proc = await asyncio.create_subprocess_shell(
                shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            get_logger(0).info(
                "Started ffmpeg+whipinto streamer with shell pid=%d: %s",
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

    # 修改的状态获取方法
    async def __get_streamer_state(self) -> (dict | None):
        if self.__streamer_proc:
            try:
                # 检查ffmpeg进程状态
                if self.__streamer_proc.returncode is None:
                    params = self.__params.get_params()
                    return {
                        "online": True,
                        "encoder": params.get("ffmpeg_codec", "libvpx-vp8").split("-")[-1],  # 提取编码器名称
                        "resolution": params.get("resolution", "1920x1080"),
                        "fps": params.get("desired_fps", 30),
                    }
            except Exception:
                get_logger().exception("Failed to get streamer state")
        return None
```

### 2. 配置文件修改

#### configs/kvmd/main/v3-hdmi-rpi4.yaml
- 替换了ustreamer命令行参数为ffmpeg+whipinto参数
- 添加了新的配置选项：whip_url、whip_token、ffmpeg_input_format和ffmpeg_codec
- 修改了命令执行顺序：先启动whipinto监听RTSP，然后启动ffmpeg推送视频到RTSP地址

```yaml
kvmd:
    # ...
    streamer:
        h264_bitrate:
            default: 5000
        # 添加live777相关配置
        whip_url: "http://localhost:7777/whip/pikvm"
        whip_token: ""
        ffmpeg_input_format: "v4l2"
        ffmpeg_codec: "libvpx"
        rtsp_port: 8554
        cmd:
            - "/usr/bin/whipinto"
            - "-w"
            - "{whip_url}"
            - "-t"
            - "{whip_token}"
            - "-i"
            - "rtsp-listen://127.0.0.1:{rtsp_port}"
            - "--process-name-prefix={process_name_prefix}"
            - "&"
            - "/usr/bin/ffmpeg"
            - "-f"
            - "{ffmpeg_input_format}"
            - "-i"
            - "/dev/video0"
            - "-video_size"
            - "{resolution}"
            - "-r"
            - "{desired_fps}"
            - "-c:v"
            - "{ffmpeg_codec}"
            - "-b:v"
            - "{h264_bitrate}k"
            - "-deadline"
            - "realtime"
            - "-cpu-used"
            - "4"
            - "-f"
            - "rtsp"
            - "-pkt_size"
            - "1200"
            - "rtsp://127.0.0.1:{rtsp_port}"
```

#### configs/kvmd/live777.yaml (新增)
```yaml
http:
  listen: "0.0.0.0:7777"
  cors: true

ice_servers:
  - urls: 
    - "stun:stun.l.google.com:19302"

auth:
  secret: ""
  tokens: []

log:
  level: "info"

webhook:
  webhooks: []
```

### 3. 服务配置修改

#### configs/os/services/kvmd-live777.service (新增)
```ini
[Unit]
Description=PiKVM - Live777 WebRTC Server
After=network.target

[Service]
Type=simple
Restart=always
RestartSec=3
ExecStart=/usr/bin/live777 -c /etc/kvmd/live777.yaml
User=kvmd
Group=kvmd

# Hardening
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

#### configs/os/services/kvmd.service
```ini
[Unit]
Description=PiKVM - The main daemon
After=network.target
Requires=kvmd-live777.service
After=kvmd-live777.service

[Service]
Type=simple
Restart=always
RestartSec=3
ExecStart=/usr/bin/kvmd --run
User=kvmd

[Install]
WantedBy=multi-user.target
```

### 4. 依赖和安装修改

#### setup.py
```python
# 添加ffmpeg和live777依赖
install_requires=[
    "aiohttp",
    "aiofiles",
    "passlib",
    "pyotp",
    "PyYAML",
    "psutil",
    "pyserial",
    "PyQRCode",
    "pypng",
    "Pillow",
    "setproctitle",
    "hidapi",
    "bleak"
],

# 添加ffmpeg和live777的系统依赖提示
extras_require={
    "live777": ["ffmpeg>=4.0", "live777>=0.1.0", "whipinto>=0.1.0"],
},
```

#### PKGBUILD
```bash
# 添加ffmpeg和live777依赖
depends=(
    # ...其他依赖
    "ustreamer>=6.33"

    # 添加ffmpeg和live777依赖
    "ffmpeg>=4.0"
    "live777>=0.1.0"
    "whipinto>=0.1.0"

    # ...其他系统依赖
)

# 备份配置文件列表
backup=(
    # ...其他配置文件
    etc/kvmd/web.css
    etc/kvmd/live777.yaml
)

# 安装live777配置文件
# 确保live777配置文件在默认配置目录中
mkdir -p "$_cfg_default/kvmd"
cp "$srcdir/../configs/kvmd/live777.yaml" "$_cfg_default/kvmd/"

# 安装live777配置文件到etc/kvmd
install -Dm644 -t "$pkgdir/etc/kvmd" "$_cfg_default/kvmd"/live777.yaml
```

## 使用说明

1. 安装依赖：
```bash
sudo pacman -S ffmpeg
sudo pip install live777 whipinto
```

2. 配置系统：
```bash
# 复制配置文件
sudo cp -f configs/kvmd/live777.yaml /etc/kvmd/
sudo cp -f configs/os/services/kvmd-live777.service /etc/systemd/system/

# 重新加载systemd配置
sudo systemctl daemon-reload
```

3. 启动服务：
```bash
sudo systemctl enable --now kvmd-live777
sudo systemctl restart kvmd
```

4. 访问服务：
- 使用live777的Web界面(http://pikvm:7777)测试视频流

## 后续计划

1. 实现ffmpeg的快照功能，替代原有的ustreamer快照
2. 修改kvmd的Web客户端，使其使用WHEP从live777获取视频流
3. 优化视频流参数，提高质量和降低延迟
4. 添加更多的ffmpeg滤镜和处理选项

## 已知问题

1. 快照功能暂时不可用
2. 需要手动设置live777权限和配置
3. 打包过程可能需要额外的验证和测试

## 贡献者

- 本修改基于PiKVM项目(https://github.com/pikvm/kvmd)
- live777项目提供了WebRTC SFU服务器 