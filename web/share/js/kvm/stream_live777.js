/*****************************************************************************
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2024  Maxim Devaev <mdevaev@gmail.com>               #
#                                                                            #
#    This program is free software: you can redistribute it and/or modify    #
#    it under the terms of the GNU General Public License as published by    #
#    the Free Software Foundation, either version 3 of the License, or       #
#    (at your option) any later version.                                    #
#                                                                            #
#    This program is distributed in the hope that it will be useful,        #
#    but WITHOUT ANY WARRANTY; without even the implied warranty of          #
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           #
#    GNU General Public License for more details.                            #
#                                                                            #
#    You should have received a copy of the GNU General Public License       #
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.  #
#                                                                            #
*****************************************************************************/


"use strict";


import {tools, $} from "../tools.js";


export function Live777Streamer(__setActive, __setInactive, __setInfo, __orient, __allow_audio, __allow_mic) {
    var self = this;

    /************************************************************************/

    __allow_mic = (__allow_audio && __allow_mic); // Mic only with audio

    var __stop = false;
    var __ensuring = false;
    var __ws = null;
    var __pc = null;

    var __retry_ensure_timeout = null;
    var __info_interval = null;

    var __state = null;
    var __frames = 0;

    /************************************************************************/

    self.getOrientation = () => __orient;
    self.isAudioAllowed = () => __allow_audio;
    self.isMicAllowed = () => __allow_mic;

    self.getName = function() {
        let name = "Live777 H.264";
        if (__allow_audio) {
            name += " + Audio";
            if (__allow_mic) {
                name += " + Mic";
            }
        }
        return name;
    };
    self.getMode = () => "live777";

    self.getResolution = function() {
        let el = $("stream-video");
        return {
            "real_width": (el.videoWidth || el.offsetWidth),
            "real_height": (el.videoHeight || el.offsetHeight),
            "view_width": el.offsetWidth,
            "view_height": el.offsetHeight,
        };
    };

    self.ensureStream = function(state) {
        __state = state;
        __stop = false;
        __ensureLive777(false);
    };

    self.stopStream = function() {
        __stop = true;
        __destroyLive777();
    };

    var __ensureLive777 = function(internal) {
        if (__ws === null && !__stop && (!__ensuring || internal)) {
            __ensuring = true;
            __setInactive();
            __setInfo(false, false, "");
            __logInfo("Starting Live777 connection ...");
            
            // 创建WebSocket连接
            __ws = new WebSocket(tools.makeWsUrl("live777/ws"));
            
            __ws.onopen = function() {
                __logInfo("Live777 WebSocket connected");
                __setupPeerConnection();
            };

            __ws.onclose = function() {
                __logError("Live777 WebSocket closed");
                __finishLive777();
            };

            __ws.onerror = function(error) {
                __logError("Live777 WebSocket error:", error);
                __setInfo(false, false, "WebSocket error");
                __finishLive777();
            };

            __ws.onmessage = function(event) {
                __handleMessage(event.data);
            };
        }
    };

    var __setupPeerConnection = function() {
        if (__pc === null) {
            __pc = new RTCPeerConnection({
                iceServers: []  // 可以根据需要添加ICE服务器
            });

            __pc.ontrack = function(event) {
                __logInfo("Got track:", event.track.kind);
                __addTrack(event.track);
                if (event.track.kind === "video") {
                    __startInfoInterval();
                }
            };

            __pc.oniceconnectionstatechange = function() {
                __logInfo("ICE connection state:", __pc.iceConnectionState);
                if (__pc.iceConnectionState === "failed") {
                    __destroyLive777();
                }
            };

            // 发送观看请求
            __sendWatch();
        }
    };

    var __handleMessage = function(data) {
        try {
            let msg = JSON.parse(data);
            __logInfo("Got message:", msg);

            if (msg.type === "offer") {
                __handleOffer(msg);
            } else if (msg.type === "ice-candidate") {
                __handleIceCandidate(msg);
            }
        } catch (error) {
            __logError("Error handling message:", error);
        }
    };

    var __handleOffer = function(offer) {
        if (__pc) {
            __pc.setRemoteDescription(new RTCSessionDescription(offer))
                .then(() => __pc.createAnswer())
                .then(answer => {
                    return __pc.setLocalDescription(answer);
                })
                .then(() => {
                    __ws.send(JSON.stringify({
                        type: "answer",
                        sdp: __pc.localDescription
                    }));
                })
                .catch(error => {
                    __logError("Error handling offer:", error);
                });
        }
    };

    var __handleIceCandidate = function(candidate) {
        if (__pc) {
            __pc.addIceCandidate(new RTCIceCandidate(candidate))
                .catch(error => {
                    __logError("Error adding ICE candidate:", error);
                });
        }
    };

    var __finishLive777 = function() {
        if (__stop) {
            if (__retry_ensure_timeout !== null) {
                clearTimeout(__retry_ensure_timeout);
                __retry_ensure_timeout = null;
            }
            __ensuring = false;
        } else {
            if (__retry_ensure_timeout === null) {
                __retry_ensure_timeout = setTimeout(function() {
                    __retry_ensure_timeout = null;
                    __ensureLive777(true);
                }, 5000);
            }
        }
        __stopInfoInterval();
        if (__pc) {
            __pc.close();
            __pc = null;
        }
        if (__ws) {
            __ws.close();
            __ws = null;
        }
        __setInactive();
        if (__stop) {
            __setInfo(false, false, "");
        }
    };

    var __destroyLive777 = function() {
        __finishLive777();
        let stream = $("stream-video").srcObject;
        if (stream) {
            for (let track of stream.getTracks()) {
                __removeTrack(track);
            }
        }
    };

    var __addTrack = function(track) {
        let el = $("stream-video");
        if (el.srcObject) {
            for (let tr of el.srcObject.getTracks()) {
                if (tr.kind === track.kind && tr.id !== track.id) {
                    __removeTrack(tr);
                }
            }
        }
        if (!el.srcObject) {
            el.srcObject = new MediaStream();
        }
        el.srcObject.addTrack(track);
    };

    var __removeTrack = function(track) {
        let el = $("stream-video");
        if (!el.srcObject) {
            return;
        }
        track.stop();
        el.srcObject.removeTrack(track);
        if (el.srcObject.getTracks().length === 0) {
            el.srcObject = null;
        }
    };

    var __startInfoInterval = function() {
        __stopInfoInterval();
        __setActive();
        __updateInfo();
        __info_interval = setInterval(__updateInfo, 1000);
    };

    var __stopInfoInterval = function() {
        if (__info_interval !== null) {
            clearInterval(__info_interval);
        }
        __info_interval = null;
    };

    var __updateInfo = function() {
        if (__pc !== null) {
            let info = "";
            let frames = null;
            let el = $("stream-video");
            if (el.webkitDecodedFrameCount !== undefined) {
                frames = el.webkitDecodedFrameCount;
            } else if (el.mozPaintedFrames !== undefined) {
                frames = el.mozPaintedFrames;
            }
            if (frames !== null) {
                info += `${Math.max(0, frames - __frames)} fps dynamic`;
                __frames = frames;
            }
            __setInfo(true, __isOnline(), info);
        }
    };

    var __isOnline = function() {
        return !!(__state && __state.source.online);
    };

    var __sendWatch = function() {
        if (__ws && __ws.readyState === WebSocket.OPEN) {
            __logInfo(`Sending WATCH(orient=${__orient}, audio=${__allow_audio}, mic=${__allow_mic}) ...`);
            __ws.send(JSON.stringify({
                type: "watch",
                params: {
                    orientation: __orient,
                    audio: __allow_audio,
                    mic: __allow_mic
                }
            }));
        }
    };

    var __logInfo = (...args) => tools.info("Stream [Live777]:", ...args);
    var __logError = (...args) => tools.error("Stream [Live777]:", ...args);
}

Live777Streamer.is_webrtc_available = function() {
    return !!window.RTCPeerConnection;
}; 