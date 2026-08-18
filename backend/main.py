# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Server
"""
import json
import torch.multiprocessing as mp

from aiohttp import web
import aiohttp
import aiohttp_cors
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration
from aiortc.rtcrtpsender import RTCRtpSender

import argparse
import asyncio
import uuid
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

backend_dir = os.path.abspath(os.path.dirname(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from src.paths import DATA_DIR, MODELS_DIR, STATIC_DIR, resolve_project_path
from src.webrtc import HumanPlayer
from src.basereal import BaseReal
from src.llm import llm_response
from src.llm import clear_conversation_history
from src.log import logger
from src.get_file import http_get
from src.config import get_model_download_config, get_avatar_download_config, get_avatars_config, get_avatar_config


@dataclass
class SessionState:
    sessionid: int
    nerfreal: BaseReal
    player: HumanPlayer
    pc: RTCPeerConnection
    llm_tasks: Set[asyncio.Future] = field(default_factory=set)


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message


sessions: Dict[int, Optional[SessionState]] = {}
llm_tasks: Set[asyncio.Future] = set()
opt = None
model = None
avatar = None

# webrtc
pcs = set()

default_model_path = str(MODELS_DIR / 'wav2lip.pth')
DEFAULT_INFERENCE_BATCH_SIZE = 16
DEFAULT_MAX_UPLOAD_SIZE = 10 * 1024 ** 2
MAX_SESSIONS_KEY = web.AppKey("max_sessions", int)
READY_KEY = web.AppKey("ready", bool)


def ensure_models_and_avatars():
    """确保模型和形象文件存在，如果不存在则自动下载"""
    # 创建必要的目录
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(MODELS_DIR, exist_ok=True)
    
    logger.info("=== 检查模型和形象文件 ===")
    
    # 获取下载配置
    model_config = get_model_download_config()
    avatar_config = get_avatar_download_config()
    
    # 检查并下载模型文件
    for model_name, config in model_config.items():
        model_path = resolve_project_path(config["path"])
        if not model_path.exists():
            logger.info(f"模型文件 {model_name} ({config['size']}) 不存在，开始下载...")
            logger.info(f"描述: {config['description']}")
            try:
                http_get(config["url"], str(model_path), extract=False)
                logger.info(f"✓ 模型文件 {model_name} 下载完成")
            except Exception as e:
                logger.error(f"✗ 下载模型文件 {model_name} 失败: {e}")
                logger.error("请尝试手动下载或检查网络连接")
                raise
        else:
            logger.info(f"✓ 模型文件 {model_name} 已存在")
    
    # 检查并下载形象文件
    for avatar_name, config in avatar_config.items():
        avatar_dir = DATA_DIR / avatar_name
        archive_path = resolve_project_path(config["path"])
        if not avatar_dir.exists():
            logger.info(f"形象文件 {avatar_name} ({config['size']}) 不存在，开始下载...")
            logger.info(f"描述: {config['description']}")
            try:
                # 下载并自动解压
                http_get(config["url"], str(archive_path), extract=True)
                logger.info(f"✓ 形象文件 {avatar_name} 下载并解压完成")
                
                # 清理zip文件
                if archive_path.exists():
                    archive_path.unlink()
                    logger.info(f"已清理临时文件 {archive_path}")
                    
            except Exception as e:
                logger.error(f"✗ 下载形象文件 {avatar_name} 失败: {e}")
                logger.error("请尝试手动下载或检查网络连接")
                raise
        else:
            logger.info(f"✓ 形象文件 {avatar_name} 已存在")
    
    logger.info("=== 所有文件检查完成 ===")


def json_response(payload: dict, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


async def parse_json_object(request: web.Request) -> dict:
    try:
        params = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ApiError(400, "invalid_json", "Request body must be valid JSON") from exc
    if not isinstance(params, dict):
        raise ApiError(400, "invalid_json", "Request body must be a JSON object")
    return params


def get_session_id(params: dict) -> int:
    raw_sessionid = params.get("sessionid")
    if isinstance(raw_sessionid, bool):
        raise ApiError(400, "invalid_session", "sessionid must be an integer")
    if isinstance(raw_sessionid, int):
        sessionid = raw_sessionid
    elif isinstance(raw_sessionid, str) and raw_sessionid.isdigit():
        sessionid = int(raw_sessionid)
    else:
        raise ApiError(400, "invalid_session", "sessionid must be an integer")
    if sessionid <= 0:
        raise ApiError(400, "invalid_session", "sessionid must be a positive integer")
    return sessionid


def get_session(sessionid: int) -> SessionState:
    state = sessions.get(sessionid)
    if state is None:
        if sessionid in sessions:
            raise ApiError(409, "session_initializing", "Session is still initializing")
        raise ApiError(404, "session_not_found", "Session does not exist or has ended")
    return state


def reserve_session(max_sessions: int) -> int:
    if len(sessions) >= max_sessions:
        raise ApiError(429, "session_limit_reached", "The server has reached its session limit")

    for _ in range(10):
        sessionid = uuid.uuid4().int % 1000000
        if sessionid > 0 and sessionid not in sessions:
            sessions[sessionid] = None
            return sessionid
    raise ApiError(503, "session_id_unavailable", "Unable to allocate a session")


async def cleanup_session(sessionid: int, *, close_peer: bool = True) -> None:
    state = sessions.pop(sessionid, None)
    clear_conversation_history(sessionid)
    if state is None:
        return

    state.llm_tasks.clear()
    try:
        state.nerfreal.flush_talk()
    except Exception:
        logger.exception(f"Session {sessionid} failed to flush during cleanup")

    if close_peer and state.pc.connectionState != "closed":
        try:
            await state.pc.close()
        except Exception:
            logger.exception(f"Session {sessionid} peer connection failed to close")
    pcs.discard(state.pc)

    for track in (state.player.audio, state.player.video):
        if track.readyState != "ended":
            try:
                track.stop()
            except Exception:
                logger.exception(f"Session {sessionid} media track failed to stop")

    logger.info(f"Session {sessionid} cleaned up; active sessions={len(sessions)}")


def on_llm_task_done(sessionid: int, task: asyncio.Future) -> None:
    llm_tasks.discard(task)
    state = sessions.get(sessionid)
    if state is not None:
        state.llm_tasks.discard(task)
    if task.cancelled():
        if state is None:
            clear_conversation_history(sessionid)
        return
    try:
        task.result()
    except Exception:
        logger.exception(f"Session {sessionid} LLM task failed")
        if state is not None:
            state.player.send_error("LLM service is unavailable. Please try again.")
    finally:
        if state is None:
            clear_conversation_history(sessionid)


@web.middleware
async def api_error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except ApiError as exc:
        return json_response(
            {"code": -1, "error": exc.error, "msg": exc.message},
            status=exc.status,
        )
    except web.HTTPRequestEntityTooLarge:
        return json_response(
            {"code": -1, "error": "payload_too_large", "msg": "Request body is too large"},
            status=413,
        )
    except web.HTTPException:
        raise
    except Exception:
        logger.exception(f"Unhandled API error: {request.method} {request.path}")
        return json_response(
            {"code": -1, "error": "internal_error", "msg": "Internal server error"},
            status=500,
        )


async def offer(request):
    params = await parse_json_object(request)
    sdp = params.get("sdp")
    offer_type = params.get("type")
    if not isinstance(sdp, str) or not sdp:
        raise ApiError(400, "invalid_offer", "sdp must be a non-empty string")
    if offer_type != "offer":
        raise ApiError(400, "invalid_offer", "type must be 'offer'")

    sessionid = reserve_session(request.app[MAX_SESSIONS_KEY])
    pc = None
    player = None

    try:
        rtc_offer = RTCSessionDescription(sdp=sdp, type=offer_type)
    
        # 获取avatar_id参数，如果没有则使用默认
        avatar_id = params.get("avatar_id", opt.avatar_id)
        if not isinstance(avatar_id, str) or not avatar_id:
            raise ApiError(400, "invalid_avatar", "avatar_id must be a non-empty string")
        logger.info(f'Requested avatar_id: {avatar_id}')
    
        # 根据avatar_id加载对应的配置
        avatar_config = get_avatar_config(avatar_id)
        temp_opt = argparse.Namespace(**vars(opt))
        if avatar_config:
            logger.info(f'Using avatar: {avatar_config["name"]} ({avatar_config["description"]})')
            temp_opt.avatar_id = avatar_config["avatar_dir"]
            temp_opt.REF_FILE = avatar_config["tts_config"]["voice"]
            temp_opt.tts = avatar_config["tts_config"]["type"]
            if temp_opt.tts != opt.tts or temp_opt.REF_FILE != opt.REF_FILE:
                logger.info(
                    f"Avatar config overrides CLI TTS settings: "
                    f"cli_tts={opt.tts}, cli_voice={opt.REF_FILE} -> "
                    f"avatar_tts={temp_opt.tts}, avatar_voice={temp_opt.REF_FILE}"
                )
        else:
            logger.warning(f'Avatar config not found for {avatar_id}, using default')

        logger.debug(f"sessionid={sessionid}, session num={len(sessions)}")
    
        # 使用temp_opt构建nerfreal
        temp_opt.sessionid = sessionid
        if temp_opt.gpu_server_url:
            from src.lipreal_remote import LipReal
            logger.info(f"Using remote GPU service: {temp_opt.gpu_server_url}")
        else:
            from src.lipreal import LipReal
            logger.info("Using local device")
    
        # 为这个会话加载对应的avatar
        if temp_opt.avatar_id != opt.avatar_id:
            if temp_opt.gpu_server_url:
                from src.lipreal_remote import load_avatar as load_avatar_remote
                session_avatar = load_avatar_remote(temp_opt.avatar_id)
            else:
                from src.lipreal import load_avatar as load_avatar_local
                session_avatar = load_avatar_local(temp_opt.avatar_id)
        else:
            session_avatar = avatar
    
        nerfreal = LipReal(temp_opt, model, session_avatar)
        logger.info(
            f"Session {sessionid} effective TTS config: type={temp_opt.tts}, voice={temp_opt.REF_FILE}"
        )
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
        pcs.add(pc)
        player = HumanPlayer(nerfreal)
        state = SessionState(sessionid, nerfreal, player, pc)
        sessions[sessionid] = state

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info(f"Connection state is {pc.connectionState}  sessionid={sessionid}")
            if pc.connectionState in {"failed", "closed"}:
                if pc.connectionState == "failed":
                    await pc.close()
                await cleanup_session(sessionid, close_peer=False)

        @pc.on("datachannel")
        def on_datachannel(channel):
            logger.info(f"Data channel established: {channel.label}")
            player.set_data_channel(channel)
        
            @channel.on("message")
            def on_message(message):
                logger.debug(f"Received message from client: {message}")

        pc.addTrack(player.audio)
        pc.addTrack(player.video)
        capabilities = RTCRtpSender.getCapabilities("video")
        preferences = list(filter(lambda x: x.name == "H264", capabilities.codecs))
        preferences += list(filter(lambda x: x.name == "VP8", capabilities.codecs))
        preferences += list(filter(lambda x: x.name == "rtx", capabilities.codecs))
        transceiver = pc.getTransceivers()[1]
        transceiver.setCodecPreferences(preferences)

        await pc.setRemoteDescription(rtc_offer)

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return json_response(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "sessionid": sessionid}
        )
    except (ApiError, asyncio.CancelledError):
        if sessionid in sessions:
            await cleanup_session(sessionid, close_peer=pc is not None)
        elif pc is not None and pc.connectionState != "closed":
            await pc.close()
        raise
    except Exception:
        if sessionid in sessions:
            await cleanup_session(sessionid, close_peer=pc is not None)
        elif pc is not None and pc.connectionState != "closed":
            await pc.close()
        raise


async def human(request):
    params = await parse_json_object(request)
    state = get_session(get_session_id(params))
    request_type = params.get("type")
    text = params.get("text")
    if request_type not in {"echo", "chat"}:
        raise ApiError(400, "invalid_type", "type must be 'echo' or 'chat'")
    if not isinstance(text, str) or not text.strip():
        raise ApiError(400, "invalid_text", "text must be a non-empty string")
    if len(text) > 10000:
        raise ApiError(400, "invalid_text", "text is too long")

    interrupt = params.get("interrupt", False)
    if not isinstance(interrupt, bool):
        raise ApiError(400, "invalid_interrupt", "interrupt must be a boolean")
    if interrupt:
        state.nerfreal.flush_talk()

    if request_type == "echo":
        state.nerfreal.put_msg_txt(text)
    else:
        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(None, llm_response, text, state.nerfreal)
        state.llm_tasks.add(task)
        llm_tasks.add(task)
        task.add_done_callback(
            lambda completed, sessionid=state.sessionid: on_llm_task_done(sessionid, completed)
        )

    return json_response({"code": 0, "msg": "ok"})


async def interrupt_talk(request):
    params = await parse_json_object(request)
    state = get_session(get_session_id(params))
    state.nerfreal.flush_talk()
    return json_response({"code": 0, "msg": "ok"})


async def humanaudio(request):
    form = await request.post()
    state = get_session(get_session_id(form))
    fileobj = form.get("file")
    if fileobj is None or not hasattr(fileobj, "file"):
        raise ApiError(400, "missing_file", "file is required")
    filebytes = fileobj.file.read()
    if not filebytes:
        raise ApiError(400, "empty_file", "file must not be empty")
    state.nerfreal.put_audio_file(filebytes)
    return json_response({"code": 0, "msg": "ok"})


async def set_audiotype(request):
    params = await parse_json_object(request)
    state = get_session(get_session_id(params))
    if "audiotype" not in params:
        raise ApiError(400, "invalid_audiotype", "audiotype is required")
    reinit = params.get("reinit", True)
    if not isinstance(reinit, bool):
        raise ApiError(400, "invalid_reinit", "reinit must be a boolean")
    state.nerfreal.set_custom_state(params["audiotype"], reinit)
    return json_response({"code": 0, "msg": "ok"})


async def record(request):
    params = await parse_json_object(request)
    state = get_session(get_session_id(params))
    record_type = params.get("type")
    if record_type == "start_record":
        state.nerfreal.start_recording()
    elif record_type == "end_record":
        state.nerfreal.stop_recording()
    else:
        raise ApiError(400, "invalid_record_type", "type must be 'start_record' or 'end_record'")
    return json_response({"code": 0, "msg": "ok"})


async def is_speaking(request):
    params = await parse_json_object(request)
    state = get_session(get_session_id(params))
    return json_response({"code": 0, "data": state.nerfreal.is_speaking()})


async def get_avatars(request):
    """获取所有可用的数字人列表"""
    config = get_avatars_config()
    return json_response({"code": 0, "data": config["avatars"]})


async def health_live(request):
    return json_response({"status": "ok"})


async def health_ready(request):
    ready = request.app[READY_KEY]
    return json_response(
        {
            "status": "ready" if ready else "not_ready",
            "active_sessions": len(sessions),
            "max_sessions": request.app[MAX_SESSIONS_KEY],
        },
        status=200 if ready else 503,
    )


async def on_shutdown(app):
    await asyncio.gather(
        *(cleanup_session(sessionid) for sessionid in list(sessions)),
        return_exceptions=True,
    )
    for task in list(llm_tasks):
        task.cancel()
    llm_tasks.clear()
    pcs.clear()


def build_app(
        *,
        serve_static: bool = True,
        serve_data_static: bool = True,
        max_sessions: int = 1,
        ready: bool = True,
) -> web.Application:
    """构建 aiohttp 应用，注册 API 与（可选的）静态资源路由。

    Args:
        serve_static: 是否托管 ``frontend/static`` 作为根路径下的静态文件。
            前后端分离部署时（前端单独由 nginx/CDN 托管），传 ``False`` 关闭。
        serve_data_static: 是否托管 ``data/`` 目录用于头像图片等媒体。
            分离部署时如果由 nginx/CDN 提供这些资源，可传 ``False`` 关闭。
            默认开启以避免破坏现有头像加载行为。

    Returns:
        已注册路由、已挂上 CORS 与 shutdown 回调的 ``web.Application`` 实例。
    """
    if max_sessions < 1:
        raise ValueError("max_sessions must be at least 1")

    app = web.Application(
        client_max_size=DEFAULT_MAX_UPLOAD_SIZE,
        middlewares=[api_error_middleware],
    )
    app[MAX_SESSIONS_KEY] = max_sessions
    app[READY_KEY] = ready
    app.on_shutdown.append(on_shutdown)

    app.router.add_get("/health/live", health_live)
    app.router.add_get("/health/ready", health_ready)
    app.router.add_post("/offer", offer)
    app.router.add_post("/human", human)
    app.router.add_post("/humanaudio", humanaudio)
    app.router.add_post("/set_audiotype", set_audiotype)
    app.router.add_post("/record", record)
    app.router.add_post("/interrupt_talk", interrupt_talk)
    app.router.add_post("/is_speaking", is_speaking)
    app.router.add_get("/api/avatars", get_avatars)

    if serve_data_static:
        app.router.add_static('/data', path=str(DATA_DIR))
    else:
        logger.info("Static data serving disabled (--no-data-static)")

    if serve_static:
        app.router.add_static('/', path=str(STATIC_DIR))
    else:
        logger.info("Static frontend serving disabled (--no-static); API-only mode")

    # CORS：默认放开所有来源，方便前端独立部署。
    # 注意：allow_credentials 保持 False，避免与通配 Origin 组合时被浏览器拒绝；
    # 当前前端的 fetch 也未发送 credentials，因此切换为 False 不会改变行为。
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=False,
            expose_headers="*",
            allow_headers="*",
        )
    })
    for route in list(app.router.routes()):
        cors.add(route)

    return app


async def post(url, data):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data) as response:
                return await response.text()
    except aiohttp.ClientError as e:
        logger.info(f'Error: {e}')


if __name__ == '__main__':
    mp.set_start_method('spawn')
    parser = argparse.ArgumentParser()

    # audio FPS
    parser.add_argument('--fps', type=int, default=50, help="audio fps,must be 50")
    # sliding window left-middle-right length (unit: 20ms)
    parser.add_argument('-l', type=int, default=10)
    parser.add_argument('-m', type=int, default=8)
    parser.add_argument('-r', type=int, default=10)

    parser.add_argument('--W', type=int, default=450, help="GUI width")
    parser.add_argument('--H', type=int, default=450, help="GUI height")

    # musetalk opt
    parser.add_argument('--avatar_id', type=str, default='wav2lip_avatar_long_hair_girl', help="define which avatar in data directory")
    parser.add_argument(
        '--batch_size',
        type=int,
        default=DEFAULT_INFERENCE_BATCH_SIZE,
        help="infer batch (smaller values reduce realtime latency)",
    )
    parser.add_argument('--customvideo_config', type=str, default='', help="custom action json")

    parser.add_argument('--tts', type=str, default='doubao3',
                        help="tts service type")  # tencent doubao azuretts doubao3 vllm_omni
    parser.add_argument('--REF_FILE', type=str, default="zh_female_santongyongns_saturn_bigtts",
                        help="参考音频文件名或语音模型ID，默认值为 edgetts的语音模型ID zh-CN-YunxiaNeural, 若--tts指定为azuretts, 可以使用Azure语音模型ID, 如zh-CN-XiaoxiaoMultilingualNeural,"
                             "doubao的音色列表：https://www.volcengine.com/docs/6561/1257544 选择语音合成模型1.0音色列表, doubao3选择2.0音色列表, "
                             "vllm_omni 传入服务端音色名（如 vivian、ryan 等），可通过 GET {VLLM_OMNI_URL}/v1/audio/voices 查询")
    parser.add_argument('--REF_TEXT', type=str, default=None)
    parser.add_argument('--TTS_SERVER', type=str, default='')  # http://localhost:9000, also used as base URL for vllm_omni

    # GPU服务器配置（用于wav2lip远程推理）
    parser.add_argument('--gpu_server_url', type=str, default='',
                        help='Remote GPU server URL for wav2lip, e.g., http://29.245.58.12:8080')

    parser.add_argument('--max_session', type=int, default=1)  # multi session count
    parser.add_argument('--port', type=int, default=8010, help="web listen port")
    parser.add_argument('--host', type=str, default='0.0.0.0', help="web bind host")

    # 前后端分离部署相关开关；默认与一体化部署一致，传入参数才会关闭对应的静态服务。
    parser.add_argument('--no-static', dest='serve_static', action='store_false', default=True,
                        help='不托管 frontend/static，作为 API-only 服务运行（前端单独部署时使用）')
    parser.add_argument('--no-data-static', dest='serve_data_static', action='store_false', default=True,
                        help='不托管 /data 目录（如果由 nginx/CDN 提供头像图片等媒体资源）')

    opt = parser.parse_args()
    opt.customopt = []
    if opt.customvideo_config != '':
        with open(opt.customvideo_config, 'r') as file:
            opt.customopt = json.load(file)
    
    # 确保模型和形象文件存在
    logger.info("检查并下载必要的模型和形象文件...")
    ensure_models_and_avatars()
    
    if opt.gpu_server_url:
        # 远程GPU模式：只加载avatar，不加载模型
        from src.lipreal_remote import load_avatar, preload_avatars

        logger.info(f"Using remote GPU service: {opt.gpu_server_url}")
        model = None  # 不需要本地模型
        avatar = load_avatar(opt.avatar_id)
    else:
        # 本地GPU模式
        from src.lipreal import load_model, load_avatar, warm_up, preload_avatars

        logger.info(f"Using local device, model_path: {default_model_path}, avatar_id: {opt.avatar_id}")
        model = load_model(default_model_path)
        avatar = load_avatar(opt.avatar_id)
        warm_up(opt.batch_size, model, 256)
    
    # 预加载所有配置的avatars
    logger.info("预加载所有数字人形象到内存...")
    avatars_config = get_avatars_config()
    avatar_ids_to_preload = [a['avatar_dir'] for a in avatars_config['avatars']]
    preload_avatars(avatar_ids_to_preload)

    # app async
    appasync = build_app(
        serve_static=opt.serve_static,
        serve_data_static=opt.serve_data_static,
        max_sessions=opt.max_session,
        ready=True,
    )

    if opt.serve_static:
        logger.info('一体化模式：前端访问 http://%s:%s/index.html', opt.host, opt.port)
    else:
        logger.info('API-only 模式：前端请单独部署并把 apiBaseUrl 指向 http://%s:%s', opt.host, opt.port)

    web.run_app(
        appasync,
        host=opt.host,
        port=opt.port,
        handler_cancellation=True,
    )
