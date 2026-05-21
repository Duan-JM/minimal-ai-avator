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

import numpy as np

import subprocess
import os
import time
import cv2
import glob
import resampy

import queue
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import soundfile as sf

import asyncio
from av import AudioFrame, VideoFrame

from src.ttsreal import TencentTTS, DoubaoTTS, DoubaoTTS3, AzureTTS, VllmOmniTTS
from src.log import logger
from src.audio_monitor import get_monitor


def read_imgs(img_list, max_workers=8):
    """并发读取图像列表，提升加载速度
    
    Args:
        img_list: 图像文件路径列表
        max_workers: 最大并发线程数，默认8
        
    Returns:
        成功读取的图像帧列表（保持原始顺序）
    """

    def load_single_image(img_path):
        """加载单张图像"""
        try:
            frame = cv2.imread(img_path)
            if frame is None:
                logger.warning(f"Failed to load image: {img_path}")
                return None
            return frame
        except Exception as e:
            logger.error(f"Error loading image {img_path}: {e}")
            return None

    frames = [None] * len(img_list)
    failed_count = 0

    logger.info(f'Reading {len(img_list)} images with {max_workers} workers...')
    start_time = time.perf_counter()

    # 使用线程池并发读取图像
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务，保存索引
        future_to_index = {
            executor.submit(load_single_image, img_path): idx
            for idx, img_path in enumerate(img_list)
        }

        # 使用tqdm显示进度
        with tqdm(total=len(img_list), desc="Loading images") as pbar:
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    frame = future.result()
                    if frame is not None:
                        frames[idx] = frame
                    else:
                        failed_count += 1
                except Exception as e:
                    logger.error(f"Unexpected error for image {idx}: {e}")
                    failed_count += 1
                pbar.update(1)

    # 过滤掉失败的图像
    frames = [f for f in frames if f is not None]

    elapsed = time.perf_counter() - start_time
    logger.info(f'Loaded {len(frames)} images in {elapsed:.2f}s ({len(frames) / elapsed:.1f} imgs/s)')

    if failed_count > 0:
        logger.warning(f'Failed to load {failed_count} images')

    if len(frames) == 0:
        raise ValueError("No images were successfully loaded")

    return frames


def play_audio(quit_event, queue):
    import pyaudio
    p = pyaudio.PyAudio()
    stream = p.open(
        rate=16000,
        channels=1,
        format=8,
        output=True,
        output_device_index=1,
    )
    stream.start_stream()
    # while queue.qsize() <= 0:
    #     time.sleep(0.1)
    while not quit_event.is_set():
        stream.write(queue.get(block=True))
    stream.close()


class BaseReal:
    def __init__(self, opt):
        self.opt = opt
        self.sample_rate = 16000
        self.chunk = self.sample_rate // opt.fps  # 320 samples per chunk (20ms * 16000 / 1000)
        self.sessionid = self.opt.sessionid

        if opt.tts == "tencent":
            self.tts = TencentTTS(opt, self)
        elif opt.tts == "doubao":
            self.tts = DoubaoTTS(opt, self)
        elif opt.tts == "doubao3":
            self.tts = DoubaoTTS3(opt, self)
        elif opt.tts == "azuretts":
            self.tts = AzureTTS(opt, self)
        elif opt.tts == "vllm_omni":
            self.tts = VllmOmniTTS(opt, self)

        self.speaking = False

        self.recording = False
        self._record_video_pipe = None
        self._record_audio_pipe = None
        self.width = self.height = 0

        self.curr_state = 0
        self.custom_img_cycle = {}
        self.custom_audio_cycle = {}
        self.custom_audio_index = {}
        self.custom_index = {}
        self.custom_opt = {}
        self.__loadcustom()

        # 初始化音频监控 (可通过环境变量AUDIO_MONITOR=0关闭，=1开启)
        enable_monitor = os.getenv('AUDIO_MONITOR', '0') == '1'
        self.audio_monitor = get_monitor(enable=enable_monitor)

    def put_msg_txt(self, msg, datainfo: dict = {}):
        self.tts.put_msg_txt(msg, datainfo)

    def put_audio_frame(self, audio_chunk, datainfo: dict = {}):  # 16khz 20ms pcm
        self.asr.put_audio_frame(audio_chunk, datainfo)

    def put_audio_file(self, filebyte, datainfo: dict = {}):
        input_stream = BytesIO(filebyte)
        stream = self.__create_bytes_stream(input_stream)
        streamlen = stream.shape[0]
        idx = 0
        while streamlen >= self.chunk:  # and self.state==State.RUNNING
            self.put_audio_frame(stream[idx:idx + self.chunk], datainfo)
            streamlen -= self.chunk
            idx += self.chunk

    def __create_bytes_stream(self, byte_stream):
        # byte_stream=BytesIO(buffer)
        stream, sample_rate = sf.read(byte_stream)  # [T*sample_rate,] float64
        logger.info(f'[INFO]put audio stream {sample_rate}: {stream.shape}')
        stream = stream.astype(np.float32)

        if stream.ndim > 1:
            logger.info(f'[WARN] audio has {stream.shape[1]} channels, only use the first.')
            stream = stream[:, 0]

        if sample_rate != self.sample_rate and stream.shape[0] > 0:
            logger.info(f'[WARN] audio sample rate is {sample_rate}, resampling into {self.sample_rate}.')
            stream = resampy.resample(x=stream, sr_orig=sample_rate, sr_new=self.sample_rate)

        return stream

    def flush_talk(self):
        self.tts.flush_talk()
        self.asr.flush_talk()

    def is_speaking(self) -> bool:
        return self.speaking

    def __loadcustom(self):
        for item in self.opt.customopt:
            logger.info(item)
            input_img_list = glob.glob(os.path.join(item['imgpath'], '*.[jpJP][pnPN]*[gG]'))
            input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
            self.custom_img_cycle[item['audiotype']] = read_imgs(input_img_list)
            self.custom_audio_cycle[item['audiotype']], sample_rate = sf.read(item['audiopath'], dtype='float32')
            self.custom_audio_index[item['audiotype']] = 0
            self.custom_index[item['audiotype']] = 0
            self.custom_opt[item['audiotype']] = item

    def init_customindex(self):
        self.curr_state = 0
        for key in self.custom_audio_index:
            self.custom_audio_index[key] = 0
        for key in self.custom_index:
            self.custom_index[key] = 0

    def notify(self, eventpoint):
        # logger.debug(f"{eventpoint}") 
        pass

    def start_recording(self):
        """开始录制视频"""
        if self.recording:
            return

        command = ['ffmpeg',
                   '-y', '-an',
                   '-f', 'rawvideo',
                   '-vcodec', 'rawvideo',
                   '-pix_fmt', 'bgr24',  # 像素格式
                   '-s', "{}x{}".format(self.width, self.height),
                   '-r', str(25),
                   '-i', '-',
                   '-pix_fmt', 'yuv420p',
                   '-vcodec', "h264",
                   # '-f' , 'flv',
                   f'temp{self.opt.sessionid}.mp4']
        self._record_video_pipe = subprocess.Popen(command, shell=False, stdin=subprocess.PIPE)

        acommand = ['ffmpeg',
                    '-y', '-vn',
                    '-f', 's16le',
                    # '-acodec','pcm_s16le',
                    '-ac', '1',
                    '-ar', '16000',
                    '-i', '-',
                    '-acodec', 'aac',
                    # '-f' , 'wav',
                    f'temp{self.opt.sessionid}.aac']
        self._record_audio_pipe = subprocess.Popen(acommand, shell=False, stdin=subprocess.PIPE)

        self.recording = True
        # self.recordq_video.queue.clear()
        # self.recordq_audio.queue.clear()
        # self.container = av.open(path, mode="w")

        # process_thread = Thread(target=self.record_frame, args=())
        # process_thread.start()

    def record_video_data(self, image):
        if self.width == 0:
            # print("image.shape:", image.shape)
            self.height, self.width, _ = image.shape
        if self.recording:
            self._record_video_pipe.stdin.write(image.tostring())

    def record_audio_data(self, frame):
        if self.recording:
            self._record_audio_pipe.stdin.write(frame.tostring())

    def stop_recording(self):
        """停止录制视频"""
        if not self.recording:
            return
        self.recording = False
        self._record_video_pipe.stdin.close()  # wait()
        self._record_video_pipe.wait()
        self._record_audio_pipe.stdin.close()
        self._record_audio_pipe.wait()
        audio_path = f"temp{self.opt.sessionid}.aac"
        video_path = f"temp{self.opt.sessionid}.mp4"
        output_path = os.path.join("data", "record.mp4")
        cmd_combine = [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-i", video_path,
            "-c:v", "copy",
            "-c:a", "copy",
            output_path,
        ]
        try:
            subprocess.run(cmd_combine, check=True, shell=False)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(f"ffmpeg combine failed: {e}")

    def mirror_index(self, size, index):
        # size = len(self.coord_list_cycle)
        turn = index // size
        res = index % size
        if turn % 2 == 0:
            return res
        else:
            return size - res - 1

    def get_audio_stream(self, audiotype):
        idx = self.custom_audio_index[audiotype]
        stream = self.custom_audio_cycle[audiotype][idx:idx + self.chunk]
        self.custom_audio_index[audiotype] += self.chunk
        if self.custom_audio_index[audiotype] >= self.custom_audio_cycle[audiotype].shape[0]:
            self.curr_state = 1  # 当前视频不循环播放，切换到静音状态
        return stream

    def set_custom_state(self, audiotype, reinit=True):
        print('set_custom_state:', audiotype)
        if self.custom_audio_index.get(audiotype) is None:
            return
        self.curr_state = audiotype
        if reinit:
            self.custom_audio_index[audiotype] = 0
            self.custom_index[audiotype] = 0

    def process_frames(self, quit_event, loop=None, audio_track=None, video_track=None):
        enable_transition = False  # 设置为False禁用过渡效果，True启用

        if enable_transition:
            _last_speaking = False
            _transition_start = time.time()
            _transition_duration = 0.1  # 过渡时间
            _last_silent_frame = None  # 静音帧缓存
            _last_speaking_frame = None  # 说话帧缓存

        while not quit_event.is_set():
            try:
                res_frame, idx, audio_frames = self.res_frame_queue.get(block=True, timeout=1)
            except queue.Empty:
                continue

            if enable_transition:
                # 检测状态变化
                current_speaking = not (audio_frames[0][1] != 0 and audio_frames[1][1] != 0)
                if current_speaking != _last_speaking:
                    logger.info(
                        f"状态切换：{'说话' if _last_speaking else '静音'} → {'说话' if current_speaking else '静音'}")
                    _transition_start = time.time()
                _last_speaking = current_speaking

            if audio_frames[0][1] != 0 and audio_frames[1][1] != 0:  # 全为静音数据，只需要取fullimg
                self.speaking = False
                audiotype = audio_frames[0][1]
                if self.custom_index.get(audiotype) is not None:  # 有自定义视频
                    mirindex = self.mirror_index(len(self.custom_img_cycle[audiotype]), self.custom_index[audiotype])
                    target_frame = self.custom_img_cycle[audiotype][mirindex]
                    self.custom_index[audiotype] += 1
                else:
                    target_frame = self.frame_list_cycle[idx]

                if enable_transition:
                    # 说话→静音过渡
                    if time.time() - _transition_start < _transition_duration and _last_speaking_frame is not None:
                        alpha = min(1.0, (time.time() - _transition_start) / _transition_duration)
                        combine_frame = cv2.addWeighted(_last_speaking_frame, 1 - alpha, target_frame, alpha, 0)
                    else:
                        combine_frame = target_frame
                    # 缓存静音帧
                    _last_silent_frame = combine_frame.copy()
                else:
                    combine_frame = target_frame
            else:
                self.speaking = True
                try:
                    current_frame = self.paste_back_frame(res_frame, idx)
                except Exception as e:
                    logger.warning(f"paste_back_frame error: {e}")
                    continue
                if enable_transition:
                    # 静音→说话过渡
                    if time.time() - _transition_start < _transition_duration and _last_silent_frame is not None:
                        alpha = min(1.0, (time.time() - _transition_start) / _transition_duration)
                        combine_frame = cv2.addWeighted(_last_silent_frame, 1 - alpha, current_frame, alpha, 0)
                    else:
                        combine_frame = current_frame
                    # 缓存说话帧
                    _last_speaking_frame = combine_frame.copy()
                else:
                    combine_frame = current_frame

            # 添加logo：如CCTV5, 默认是“”
            cv2.putText(combine_frame, "", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (128, 128, 128), 1)
            image = combine_frame
            new_frame = VideoFrame.from_ndarray(image, format="bgr24")
            asyncio.run_coroutine_threadsafe(video_track._queue.put((new_frame, None)), loop)
            self.record_video_data(combine_frame)

            for audio_frame in audio_frames:
                frame, type, eventpoint = audio_frame

                # 削波保护：确保音频数据在 [-1.0, 1.0] 范围内
                max_val = np.max(np.abs(frame))
                if max_val > 1.0:
                    logger.warning(
                        f"Audio clipping detected before conversion: max={max_val:.3f}, applying normalization")
                    frame = np.clip(frame, -1.0, 1.0)

                # 转换为 int16，使用安全的缩放
                frame = (frame * 32767).astype(np.int16)
                if not hasattr(self, '_audio_debug_count'):
                    self._audio_debug_count = 0
                self._audio_debug_count += 1
                if self._audio_debug_count <= 3 or (
                    isinstance(eventpoint, dict) and eventpoint.get('status') in ('start', 'end')
                ):
                    peak = int(np.max(np.abs(frame))) if frame.size > 0 else 0
                    rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) if frame.size > 0 else 0.0
                    logger.debug(
                        f"Audio frame queued for WebRTC: count={self._audio_debug_count}, "
                        f"peak={peak}, rms={rms:.1f}, status={eventpoint.get('status') if isinstance(eventpoint, dict) else None}"
                    )

                # webrtc
                new_frame = AudioFrame(format='s16', layout='mono', samples=frame.shape[0])
                new_frame.planes[0].update(frame.tobytes())
                new_frame.sample_rate = 16000

                # 记录音频帧生产
                self.audio_monitor.record_frame_produced()

                # 优化音频队列放入逻辑,避免阻塞导致掉帧
                # 如果队列满,尝试非阻塞放入,失败则记录警告但继续处理
                try:
                    # 使用put_nowait避免阻塞,如果队列满会抛出QueueFull异常
                    future = asyncio.run_coroutine_threadsafe(
                        audio_track._queue.put((new_frame, eventpoint)),
                        loop
                    )
                    # 设置较短的超时时间(50ms),避免长时间等待
                    future.result(timeout=0.05)
                except Exception as e:
                    # 队列满或超时,记录警告但不影响后续帧处理
                    self.audio_monitor.record_queue_full()
                    if not hasattr(self, '_audio_queue_warning_count'):
                        self._audio_queue_warning_count = 0
                    self._audio_queue_warning_count += 1
                    # 每100次只记录1次,避免日志刷屏
                    if self._audio_queue_warning_count % 100 == 1:
                        logger.warning(
                            f'Audio queue full or timeout, dropped frames: {self._audio_queue_warning_count}')

                self.record_audio_data(frame)
        logger.info('basereal process_frames thread stop')
