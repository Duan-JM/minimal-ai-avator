###############################################################################
#  Copyright (C) 2024 LiveTalking@lipku https://github.com/lipku/LiveTalking
#  email: lipku@foxmail.com
# 
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  
#       http://www.apache.org/licenses/LICENSE-2.0
# 
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

import asyncio
import threading
import time
from typing import Tuple, Dict, Optional, Set, Union
from av.frame import Frame
from av.packet import Packet
import fractions
from aiortc import MediaStreamTrack

from src.log import logger
from src.audio_monitor import get_monitor

AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCK_RATE = 90000
VIDEO_PTIME = 0.040  # 0.040 (25fps)
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)
SAMPLE_RATE = 16000
AUDIO_TIME_BASE = fractions.Fraction(1, SAMPLE_RATE)


class PlayerStreamTrack(MediaStreamTrack):
    """
    A video track that returns an animated flag.
    """

    def __init__(self, player, kind):
        super().__init__()  # don't forget this!
        self.kind = kind
        self._player = player
        # 增加音频队列大小,避免TTS高速产生时队列满导致阻塞和掉帧
        # 音频: 500帧 = 10秒缓冲 (20ms/帧)
        # 视频: 100帧 = 4秒缓冲 (40ms/帧)
        maxsize = 500 if kind == 'audio' else 100
        self._queue = asyncio.Queue(maxsize=maxsize)
        self.timelist = []  # 记录最近包的时间戳
        self.current_frame_count = 0
        self.dropped_frames = 0  # 统计丢帧数
        if self.kind == 'video':
            self.framecount = 0
            self.lasttime = time.perf_counter()
            self.totaltime = 0

    _start: float
    _timestamp: int

    async def next_timestamp(self) -> Tuple[int, fractions.Fraction]:
        if self.readyState != "live":
            raise Exception

        if self.kind == 'video':
            if hasattr(self, "_timestamp"):
                self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)
                self.current_frame_count += 1
                wait = self._start + self.current_frame_count * VIDEO_PTIME - time.time()
                if wait < -0.2:  # 延迟超过200ms，重新校准避免累积漂移
                    self.dropped_frames += 1
                    if self.dropped_frames % 100 == 1:
                        logger.warning(f'Video frame delay: {wait*1000:.1f}ms, total_dropped: {self.dropped_frames}')
                    self._start = time.time() - self.current_frame_count * VIDEO_PTIME
                elif wait > 0:
                    await asyncio.sleep(wait)
            else:
                self._start = time.time()
                self._timestamp = 0
                self.timelist.append(self._start)
                date_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._start))
                logger.debug(f'video start:{self._start}, date str:{date_str}')
            return self._timestamp, VIDEO_TIME_BASE
        else:  # audio
            if hasattr(self, "_timestamp"):
                self._timestamp += int(AUDIO_PTIME * SAMPLE_RATE)
                self.current_frame_count += 1
                wait = self._start + self.current_frame_count * AUDIO_PTIME - time.time()
                
                # 音频帧时间同步优化 - 更宽松的策略减少卡顿
                if wait < -0.2:  # 延迟超过200ms (增加容忍度)
                    self.dropped_frames += 1
                    monitor = get_monitor(enable=False)  # 使用全局实例
                    if monitor:
                        monitor.record_delay_warning()
                    # 每200帧报告一次,减少日志量
                    if self.dropped_frames % 200 == 1:
                        logger.warning(f'Audio frame delay: {wait*1000:.1f}ms, total_dropped: {self.dropped_frames}')
                    # 重新校准时间基准,避免累积延迟
                    self._start = time.time() - self.current_frame_count * AUDIO_PTIME
                elif wait > 0:
                    await asyncio.sleep(wait)
                # 负延迟在-200ms内不做处理,允许自然追赶
            else:
                self._start = time.time()
                self._timestamp = 0
                self.timelist.append(self._start)
                date_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._start))
                logger.debug(f'audio start:{self._start}, date str:{date_str}')
            return self._timestamp, AUDIO_TIME_BASE

    async def recv(self) -> Union[Frame, Packet]:
        self._player._start(self)
        frame, eventpoint = await self._queue.get()
        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        if eventpoint and self._player is not None:
            if self.kind == 'audio' and isinstance(eventpoint, dict):
                logger.debug(
                    f"Audio eventpoint consumed: status={eventpoint.get('status')} "
                    f"text={eventpoint.get('text', '')[:50]}"
                )
            self._player.notify(eventpoint)
        if frame is None:
            self.stop()
            raise Exception
        
        # 记录音频帧消费
        if self.kind == 'audio':
            monitor = get_monitor(enable=False)  # 使用全局实例,不重新创建
            if monitor:
                monitor.record_frame_consumed()
            if self.current_frame_count <= 3 or self.current_frame_count % 200 == 0:
                logger.debug(
                    f"Audio frame consumed: count={self.current_frame_count}, "
                    f"queue_size={self._queue.qsize()}, samples={getattr(frame, 'samples', 'n/a')}"
                )
        
        if self.kind == 'video':
            self.totaltime += (time.perf_counter() - self.lasttime)
            self.framecount += 1
            self.lasttime = time.perf_counter()
            if self.framecount == 1000:
                logger.debug(f"actual avg final fps:{self.framecount / self.totaltime:.4f}")
                self.framecount = 0
                self.totaltime = 0
        return frame

    def stop(self):
        super().stop()
        # Drain & delete remaining frames
        while not self._queue.empty():
            item = self._queue.get_nowait()
            del item
        if self._player is not None:
            self._player._stop(self)
            self._player = None


def player_worker_thread(
        quit_event,
        loop,
        container,
        audio_track,
        video_track
):
    container.render(quit_event, loop, audio_track, video_track)


class HumanPlayer:

    def __init__(
            self, nerfreal, format=None, options=None, timeout=None, loop=False, decode=True
    ):
        self.__thread: Optional[threading.Thread] = None
        self.__thread_quit: Optional[threading.Event] = None

        # examine streams
        self.__started: Set[PlayerStreamTrack] = set()
        self.__audio: Optional[PlayerStreamTrack] = None
        self.__video: Optional[PlayerStreamTrack] = None

        self.__audio = PlayerStreamTrack(self, kind="audio")
        self.__video = PlayerStreamTrack(self, kind="video")

        self.__container = nerfreal
        self._data_channel = None  # 数据通道
        self._tts_active = False
        self._last_tts_text = None

    def _emit_tts_start(self, text: str):
        if not self._data_channel or self._data_channel.readyState != 'open':
            return
        if self._tts_active and self._last_tts_text == text:
            return

        import json

        self._data_channel.send(json.dumps({
            'type': 'llm',
            'text': text
        }))
        self._data_channel.send(json.dumps({'type': 'tts_start'}))
        logger.debug(f"Sent tts_start for text: {text[:60]}")
        self._tts_active = True
        self._last_tts_text = text

    def notify(self, eventpoint):
        if self.__container is not None:
            self.__container.notify(eventpoint)
            
            # 如果是TTS文本通知，发送到数据通道
            if isinstance(eventpoint, dict) and 'text' in eventpoint and self._data_channel:
                try:
                    status = eventpoint.get('status')
                    text = eventpoint.get('text', '')
                    
                    # 发送LLM回答到前端（仅发送start状态的完整文本）
                    if status == 'start':
                        self._emit_tts_start(text)
                    
                    # 发送 tts_end 事件
                    elif status == 'end':
                        import json
                        if not self._tts_active:
                            self._emit_tts_start(text)
                        tts_end_msg = {'type': 'tts_end'}
                        if self._data_channel.readyState == 'open':
                            self._data_channel.send(json.dumps(tts_end_msg))
                            logger.debug(f"Sent tts_end for text: {text[:60]}")
                        self._tts_active = False
                        self._last_tts_text = None
                             
                except Exception as e:
                    logger.error(f"Failed to send data channel message: {e}")

    def set_data_channel(self, data_channel):
        """设置数据通道"""
        self._data_channel = data_channel
        logger.info("Data channel set for HumanPlayer")

    @property
    def audio(self) -> MediaStreamTrack:
        """
        A :class:`aiortc.MediaStreamTrack` instance if the file contains audio.
        """
        return self.__audio

    @property
    def video(self) -> MediaStreamTrack:
        """
        A :class:`aiortc.MediaStreamTrack` instance if the file contains video.
        """
        return self.__video

    def _start(self, track: PlayerStreamTrack) -> None:
        self.__started.add(track)
        if self.__thread is None:
            self.__log_debug("Starting worker thread")
            self.__thread_quit = threading.Event()
            self.__thread = threading.Thread(
                name="media-player",
                target=player_worker_thread,
                args=(
                    self.__thread_quit,
                    asyncio.get_event_loop(),
                    self.__container,
                    self.__audio,
                    self.__video
                ),
            )
            self.__thread.start()

    def _stop(self, track: PlayerStreamTrack) -> None:
        self.__started.discard(track)

        if not self.__started and self.__thread is not None:
            self.__log_debug("Stopping worker thread")
            self.__thread_quit.set()
            self.__thread.join()
            self.__thread = None

        if not self.__started and self.__container is not None:
            self.__container = None

    def __log_debug(self, msg: str, *args) -> None:
        logger.debug(f"HumanPlayer {msg}", *args)
