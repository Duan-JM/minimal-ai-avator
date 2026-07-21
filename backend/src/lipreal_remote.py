###############################################################################
#  Copyright (C) 2024 LiveTalking@lipku https://github.com/lipku/LiveTalking
#  Remote GPU版本 - 通过HTTP调用远程GPU服务
###############################################################################

import torch
import numpy as np

import os
import time
import cv2
import glob
import pickle  # nosec B403 - used only to load trusted local avatar coords files
import copy
import base64
import requests

import queue
from queue import Queue
from threading import Thread, Event
import torch.multiprocessing as mp
from tqdm import tqdm

from src.basereal import (
    BaseReal,
    _get_pipeline_queue_item,
    _put_pipeline_queue_item,
)
from src.log import logger
from src.lipasr import LipASR
from src.paths import DATA_DIR

device = "cpu"  # 客户端使用CPU

# 全局avatar缓存
_avatar_cache = {}
BINARY_INFERENCE_MEDIA_TYPE = 'application/octet-stream'


def _decode_inference_response(response):
    content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
    if content_type == BINARY_INFERENCE_MEDIA_TYPE:
        shape_header = response.headers.get('X-Batch-Shape', '')
        batch_shape = tuple(int(value) for value in shape_header.split(',') if value)
        if len(batch_shape) != 4 or any(value <= 0 for value in batch_shape):
            raise ValueError(f'Invalid binary batch shape: {shape_header!r}')
        if response.headers.get('X-Batch-Dtype', 'uint8') != 'uint8':
            raise ValueError('Unsupported binary batch dtype')

        expected_size = int(np.prod(batch_shape, dtype=np.int64))
        if len(response.content) != expected_size:
            raise ValueError(
                f'Binary batch size mismatch: expected {expected_size}, '
                f'got {len(response.content)}'
            )
        frames = np.frombuffer(response.content, dtype=np.uint8).reshape(batch_shape)
        fps = float(response.headers.get('X-Inference-FPS', 0))
    else:
        result = response.json()
        batch_bytes = base64.b64decode(result['batch_data'])
        batch_shape = tuple(result['batch_shape'])
        frames = np.frombuffer(batch_bytes, dtype=np.uint8).reshape(batch_shape)
        fps = float(result.get('fps', 0))

    return frames.astype(np.float32), fps


def load_avatar(avatar_id):
    """加载avatar数据，带缓存机制"""
    global _avatar_cache
    
    # 检查缓存
    if avatar_id in _avatar_cache:
        logger.info(f'✓ Avatar "{avatar_id}" loaded from cache')
        return _avatar_cache[avatar_id]
    
    logger.info(f'Loading avatar "{avatar_id}" from disk...')
    avatar_path = os.path.join(str(DATA_DIR), avatar_id)
    full_imgs_path = f"{avatar_path}/full_imgs" 
    face_imgs_path = f"{avatar_path}/face_imgs" 
    coords_path = f"{avatar_path}/coords.pkl"
    
    logger.info(f'Avatar path: {avatar_path}')
    logger.info(f'Full images path: {full_imgs_path}')
    logger.info(f'Face images path: {face_imgs_path}')

    if os.path.exists(coords_path):
        with open(coords_path, 'rb') as f:
            coord_list_cycle = pickle.load(f)  # nosec B301 - trusted, local avatar data
        logger.info(f'Loaded coords from {coords_path}, count: {len(coord_list_cycle)}')
    else:
        coord_list_cycle = None
        logger.warning(f'coords.pkl not found at {coords_path}')
    
    full_glob_pattern = os.path.join(full_imgs_path, '*.[jpJP][pnPN]*[gG]')
    input_img_list = glob.glob(full_glob_pattern)
    logger.info(f'Found {len(input_img_list)} full images')
    input_img_list = sorted(input_img_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    frame_list_cycle = read_imgs(input_img_list)
    
    face_glob_pattern = os.path.join(face_imgs_path, '*.[jpJP][pnPN]*[gG]')
    input_face_list = glob.glob(face_glob_pattern)
    logger.info(f'Found {len(input_face_list)} face images')
    input_face_list = sorted(input_face_list, key=lambda x: int(os.path.splitext(os.path.basename(x))[0]))
    face_list_cycle = read_imgs(input_face_list)
    
    if len(face_list_cycle) == 0:
        raise ValueError(f"No face images loaded from {face_imgs_path}. Please check if the avatar was generated correctly.")
    if len(frame_list_cycle) == 0:
        raise ValueError(f"No full images loaded from {full_imgs_path}. Please check if the avatar was generated correctly.")
    if coord_list_cycle is None or len(coord_list_cycle) == 0:
        raise ValueError(f"No coordinates loaded from {coords_path}. Please check if the avatar was generated correctly.")

    # 缓存结果
    _avatar_cache[avatar_id] = (frame_list_cycle, face_list_cycle, coord_list_cycle)
    logger.info(f'✓ Avatar "{avatar_id}" loaded and cached (full: {len(frame_list_cycle)}, face: {len(face_list_cycle)})')

    return frame_list_cycle, face_list_cycle, coord_list_cycle


def preload_avatars(avatar_ids):
    """预加载多个avatar到缓存"""
    logger.info(f'Preloading {len(avatar_ids)} avatars...')
    for avatar_id in avatar_ids:
        if avatar_id not in _avatar_cache:
            try:
                load_avatar(avatar_id)
            except Exception as e:
                logger.error(f'Failed to preload avatar "{avatar_id}": {e}')
    logger.info(f'Preloading completed. Cached avatars: {list(_avatar_cache.keys())}')


def clear_avatar_cache(avatar_id=None):
    """清除avatar缓存
    
    Args:
        avatar_id: 指定要清除的avatar_id，如果为None则清除所有
    """
    global _avatar_cache
    if avatar_id is None:
        _avatar_cache.clear()
        logger.info('All avatar cache cleared')
    elif avatar_id in _avatar_cache:
        del _avatar_cache[avatar_id]
        logger.info(f'Avatar "{avatar_id}" cache cleared')


def get_cached_avatars():
    """获取已缓存的avatar列表"""
    return list(_avatar_cache.keys())


def read_imgs(img_list):
    frames = []
    logger.info('reading images...')
    for img_path in tqdm(img_list):
        frame = cv2.imread(img_path)
        frames.append(frame)
    return frames


def __mirror_index(size, index):
    if size == 0:
        logger.error(f"__mirror_index called with size=0, index={index}")
        raise ValueError("Avatar size is 0. Please check if the avatar was loaded correctly.")
    turn = index // size
    res = index % size
    if turn % 2 == 0:
        return res
    else:
        return size - res - 1


class RemoteGPUClient:
    """远程GPU服务客户端"""
    def __init__(self, gpu_server_url, session_id, face_list_cycle):
        self.gpu_server_url = gpu_server_url.rstrip('/')
        self.session_id = session_id
        self.face_list_cycle = face_list_cycle
        self.session_initialized = False
        
    def init_session(self):
        """初始化远程session，上传face图片"""
        if self.session_initialized:
            return True
            
        try:
            logger.info(f"Initializing remote GPU session {self.session_id}...")
            
            # 编码face图片为base64
            face_imgs_b64 = []
            for face in self.face_list_cycle:
                _, buffer = cv2.imencode('.png', face)
                face_b64 = base64.b64encode(buffer).decode('utf-8')
                face_imgs_b64.append(face_b64)
            
            # 发送初始化请求
            resp = requests.post(
                f"{self.gpu_server_url}/session/init",
                json={
                    'session_id': self.session_id,
                    'face_imgs': face_imgs_b64
                },
                timeout=60
            )
            
            if resp.status_code == 200:
                result = resp.json()
                logger.info(f"Remote session initialized: {result}")
                self.session_initialized = True
                return True
            else:
                logger.error(f"Failed to init session: {resp.status_code} {resp.text}")
                return False
                
        except Exception as e:
            logger.exception("Error initializing remote session")
            return False
    
    def inference_batch(self, mel_batch, face_indices):
        """调用远程推理（优化版：批量传输）"""
        try:
            if not self.session_initialized:
                if not self.init_session():
                    logger.error("Failed to initialize remote GPU session")
                    return None
            
            # 编码mel_batch（使用更高效的float16减少传输量）
            mel_float16 = mel_batch.astype(np.float16)  # 减半数据量
            mel_bytes = mel_float16.tobytes()
            mel_b64 = base64.b64encode(mel_bytes).decode('utf-8')
            
            # 发送推理请求
            resp = requests.post(
                f"{self.gpu_server_url}/inference/batch",
                json={
                    'session_id': self.session_id,
                    'mel_batch': mel_b64,
                    'mel_shape': list(mel_batch.shape),
                    'mel_dtype': 'float16',  # 标记数据类型
                    'face_indices': face_indices
                },
                headers={'Accept': BINARY_INFERENCE_MEDIA_TYPE},
                timeout=10
            )
            
            if resp.status_code == 200:
                frames, fps = _decode_inference_response(resp)
                logger.debug(f"Remote inference OK: batch_shape={frames.shape}, fps={fps:.1f}")
                return frames
            else:
                logger.error(f"Remote inference failed: {resp.status_code} {resp.text}")
                return None
        except (requests.RequestException, KeyError, TypeError, ValueError):
            logger.exception("Error in remote inference")
            return None
    
    def close_session(self):
        """关闭远程session"""
        if not self.session_initialized:
            return
            
        try:
            resp = requests.post(
                f"{self.gpu_server_url}/session/close",
                json={'session_id': self.session_id},
                timeout=5
            )
            logger.info(f"Remote session closed: {resp.status_code}")
        except Exception as e:
            logger.warning(f"Error closing remote session: {e}")


def inference(quit_event, batch_size, face_list_cycle, audio_feat_queue, audio_out_queue, 
              res_frame_queue, gpu_client):
    """
    推理线程 - 调用远程GPU服务（优化版）
    """
    length = len(face_list_cycle)
    index = 0
    count = 0
    counttime = 0
    logger.debug('start remote inference')
    
    while not quit_event.is_set():
        mel_batch = []
        try:
            mel_batch = audio_feat_queue.get(block=True, timeout=1)
        except queue.Empty:
            continue
            
        is_all_silence = True
        audio_frames = []
        for _ in range(batch_size*2):
            received, audio_item = _get_pipeline_queue_item(quit_event, audio_out_queue)
            if not received:
                return
            frame, type, eventpoint = audio_item
            audio_frames.append((frame, type, eventpoint))
            if type == 0:
                is_all_silence = False

        if is_all_silence:
            for i in range(batch_size):
                queued = _put_pipeline_queue_item(
                    quit_event,
                    res_frame_queue,
                    (None, __mirror_index(length, index), audio_frames[i*2:i*2+2]),
                )
                if not queued:
                    return
                index = index + 1
        else:
            # logger.debug(f"🎤 Speech detected, calling remote inference (batch_size={batch_size})")
            t = time.perf_counter()
            
            # 【优化】预计算face索引，避免重复计算
            face_indices = [__mirror_index(length, index + i) for i in range(batch_size)]
            
            # 【优化】直接使用mel_batch（已经是numpy数组），避免二次转换
            # mel_batch本身已经是list of numpy，直接转为numpy数组
            if isinstance(mel_batch, list):
                mel_batch_np = np.stack(mel_batch, axis=0) if len(mel_batch) > 0 else np.array(mel_batch)
            else:
                mel_batch_np = mel_batch
            
            # 调用远程GPU推理
            pred = gpu_client.inference_batch(mel_batch_np, face_indices)
            
            if pred is None:
                logger.error("Remote inference failed, using silence frames")
                for i in range(batch_size):
                    queued = _put_pipeline_queue_item(
                        quit_event,
                        res_frame_queue,
                        (None, __mirror_index(length, index), audio_frames[i*2:i*2+2]),
                    )
                    if not queued:
                        return
                    index = index + 1
                continue
            
            infer_time = time.perf_counter() - t
            counttime += infer_time
            count += batch_size
            
            if count >= 100:
                avg_fps = count / counttime
                logger.debug(f"actual avg final fps:{avg_fps:.4f}")
                count = 0
                counttime = 0
                
            # 【优化】直接遍历，避免enumerate开销
            # logger.debug(f"📹 Putting {len(pred)} frames to queue, pred shape={pred.shape}, dtype={pred.dtype}, value_range=[{pred.min():.1f}, {pred.max():.1f}]")
            for i in range(len(pred)):
                queued = _put_pipeline_queue_item(
                    quit_event,
                    res_frame_queue,
                    (pred[i], __mirror_index(length, index), audio_frames[i*2:i*2+2]),
                )
                if not queued:
                    return
                index = index + 1
                
    logger.debug('lipreal remote inference processor stop')


class LipReal(BaseReal):
    @torch.no_grad()
    def __init__(self, opt, model, avatar):
        super().__init__(opt)
        
        self.fps = opt.fps
        self.batch_size = opt.batch_size
        self.idx = 0
        self.res_frame_queue = Queue(self.batch_size*2)
        
        # avatar数据
        self.frame_list_cycle, self.face_list_cycle, self.coord_list_cycle = avatar
        
        # 创建远程GPU客户端
        self.gpu_client = RemoteGPUClient(
            gpu_server_url=opt.gpu_server_url,
            session_id=opt.sessionid,
            face_list_cycle=self.face_list_cycle
        )
        
        self.asr = LipASR(opt, self)
        self.asr.warm_up()
        
        self.render_event = mp.Event()

    def paste_back_frame(self, pred_frame, idx: int):
        bbox = self.coord_list_cycle[idx]
        combine_frame = copy.deepcopy(self.frame_list_cycle[idx])
        y1, y2, x1, x2 = bbox
        res_frame = cv2.resize(pred_frame.astype(np.uint8), (x2-x1, y2-y1))
        combine_frame[y1:y2, x1:x2] = res_frame
        return combine_frame
            
    def render(self, quit_event, loop=None, audio_track=None, video_track=None):
        self.init_customindex()
        self.tts.render(quit_event)
        
        # 启动推理线程
        infer_quit_event = Event()
        infer_thread = Thread(
            target=inference, 
            args=(infer_quit_event, self.batch_size, self.face_list_cycle,
                  self.asr.feat_queue, self.asr.output_queue, self.res_frame_queue,
                  self.gpu_client,)
        )
        infer_thread.start()
        
        # 启动帧处理线程
        process_quit_event = Event()
        process_thread = Thread(target=self.process_frames, args=(process_quit_event, loop, audio_track, video_track))
        process_thread.start()

        _starttime = time.perf_counter()
        
        while not quit_event.is_set(): 
            t = time.perf_counter()
            self.asr.run_step(quit_event)

            # 流量控制：当视频帧队列积压过多时，暂停生产以避免延迟累积
            if video_track and video_track._queue.qsize() >= 30:
                time.sleep(0.04)

        logger.info('lipreal thread stop')

        infer_quit_event.set()
        process_quit_event.set()
        infer_thread.join()
        process_thread.join()
        
        # 关闭远程session
        self.gpu_client.close_session()
