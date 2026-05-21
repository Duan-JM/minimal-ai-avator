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
from typing import Dict

backend_dir = os.path.abspath(os.path.dirname(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from src.paths import DATA_DIR, MODELS_DIR, STATIC_DIR, resolve_project_path
from src.webrtc import HumanPlayer
from src.basereal import BaseReal
from src.llm import llm_response
from src.log import logger
from src.get_file import http_get
from src.config import get_model_download_config, get_avatar_download_config, get_avatars_config, get_avatar_config

nerfreals: Dict[int, BaseReal] = {}  # sessionid:BaseReal
opt = None
model = None
avatar = None

# webrtc
pcs = set()

default_model_path = str(MODELS_DIR / 'wav2lip.pth')


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


def build_nerfreal(sessionid: int) -> BaseReal:
    opt.sessionid = sessionid
    # 检查是否使用远程GPU服务
    if opt.gpu_server_url:
        from src.lipreal_remote import LipReal
        logger.info(f"Using remote GPU service: {opt.gpu_server_url}")
    else:
        from src.lipreal import LipReal
        logger.info("Using local device")
    nerfreal = LipReal(opt, model, avatar)
    return nerfreal


async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    
    # 获取avatar_id参数，如果没有则使用默认
    avatar_id = params.get("avatar_id", opt.avatar_id)
    logger.info(f'Requested avatar_id: {avatar_id}')
    
    # 根据avatar_id加载对应的配置
    avatar_config = get_avatar_config(avatar_id)
    if avatar_config:
        logger.info(f'Using avatar: {avatar_config["name"]} ({avatar_config["description"]})')
        # 临时修改opt以使用指定的avatar配置
        temp_opt = argparse.Namespace(**vars(opt))
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
        temp_opt = opt
        logger.warning(f'Avatar config not found for {avatar_id}, using default')

    sessionid = uuid.uuid4().int % 1000000
    nerfreals[sessionid] = None
    logger.debug(f"sessionid={sessionid}, session num={len(nerfreals)}")
    
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
        # 需要加载不同的avatar
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
    nerfreals[sessionid] = nerfreal
    pc = RTCPeerConnection(configuration=RTCConfiguration(
        iceServers=[],
    ))
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"Connection state is {pc.connectionState}  sessionid={sessionid}")
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)
            if sessionid in nerfreals:
                del nerfreals[sessionid]
        if pc.connectionState == "closed":
            pcs.discard(pc)
            if sessionid in nerfreals:
                del nerfreals[sessionid]
    
    @pc.on("datachannel")
    def on_datachannel(channel):
        logger.info(f"Data channel established: {channel.label}")
        # 将数据通道传递给player，以便发送LLM回答
        player.set_data_channel(channel)
        
        @channel.on("message")
        def on_message(message):
            logger.debug(f"Received message from client: {message}")

    player = HumanPlayer(nerfreals[sessionid])
    audio_sender = pc.addTrack(player.audio)
    video_sender = pc.addTrack(player.video)
    capabilities = RTCRtpSender.getCapabilities("video")
    preferences = list(filter(lambda x: x.name == "H264", capabilities.codecs))
    preferences += list(filter(lambda x: x.name == "VP8", capabilities.codecs))
    preferences += list(filter(lambda x: x.name == "rtx", capabilities.codecs))
    transceiver = pc.getTransceivers()[1]
    transceiver.setCodecPreferences(preferences)

    await pc.setRemoteDescription(offer)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "sessionid": sessionid}
        ),
    )


async def human(request):
    try:
        params = await request.json()

        sessionid = params.get('sessionid', 0)
        if params.get('interrupt'):
            nerfreals[sessionid].flush_talk()

        if params['type'] == 'echo':
            nerfreals[sessionid].put_msg_txt(params['text'])
        elif params['type'] == 'chat':
            asyncio.get_event_loop().run_in_executor(None, llm_response, params['text'], nerfreals[sessionid])

        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": 0, "msg": "ok"}
            ),
        )
    except Exception as e:
        logger.exception('exception:')
        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": -1, "msg": str(e)}
            ),
        )


async def interrupt_talk(request):
    try:
        params = await request.json()

        sessionid = params.get('sessionid', 0)
        nerfreals[sessionid].flush_talk()

        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": 0, "msg": "ok"}
            ),
        )
    except Exception as e:
        logger.exception('exception:')
        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": -1, "msg": str(e)}
            ),
        )


async def humanaudio(request):
    try:
        form = await request.post()
        sessionid = int(form.get('sessionid', 0))
        fileobj = form["file"]
        filename = fileobj.filename
        filebytes = fileobj.file.read()
        nerfreals[sessionid].put_audio_file(filebytes)

        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": 0, "msg": "ok"}
            ),
        )
    except Exception as e:
        logger.exception('exception:')
        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": -1, "msg": str(e)}
            ),
        )


async def set_audiotype(request):
    try:
        params = await request.json()

        sessionid = params.get('sessionid', 0)
        nerfreals[sessionid].set_custom_state(params['audiotype'], params['reinit'])

        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": 0, "msg": "ok"}
            ),
        )
    except Exception as e:
        logger.exception('exception:')
        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": -1, "msg": str(e)}
            ),
        )


async def record(request):
    try:
        params = await request.json()

        sessionid = params.get('sessionid', 0)
        if params['type'] == 'start_record':
            # nerfreals[sessionid].put_msg_txt(params['text'])
            nerfreals[sessionid].start_recording()
        elif params['type'] == 'end_record':
            nerfreals[sessionid].stop_recording()
        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": 0, "msg": "ok"}
            ),
        )
    except Exception as e:
        logger.exception('exception:')
        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": -1, "msg": str(e)}
            ),
        )


async def is_speaking(request):
    params = await request.json()

    sessionid = params.get('sessionid', 0)
    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {"code": 0, "data": nerfreals[sessionid].is_speaking()}
        ),
    )


async def get_avatars(request):
    """获取所有可用的数字人列表"""
    try:
        config = get_avatars_config()
        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": 0, "data": config["avatars"]}
            ),
        )
    except Exception as e:
        logger.exception('get_avatars exception:')
        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"code": -1, "msg": str(e)}
            ),
        )


async def on_shutdown(app):
    # close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()


def build_app(*, serve_static: bool = True, serve_data_static: bool = True) -> web.Application:
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
    app = web.Application(client_max_size=1024 ** 2 * 100)
    app.on_shutdown.append(on_shutdown)

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
    parser.add_argument('--batch_size', type=int, default=64, help="infer batch")
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
    )

    if opt.serve_static:
        logger.info('一体化模式：前端访问 http://%s:%s/index.html', opt.host, opt.port)
    else:
        logger.info('API-only 模式：前端请单独部署并把 apiBaseUrl 指向 http://%s:%s', opt.host, opt.port)

    runner = web.AppRunner(appasync)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, opt.host, opt.port)
    loop.run_until_complete(site.start())
    loop.run_forever()
