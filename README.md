# Minimal AI Avator

Minimal AI Avator 是一个实时交互式数字人项目：前端通过 WebRTC 播放音视频，后端使用 Wav2Lip 做唇形同步，并接入 LLM 与 TTS 生成对话内容。

本项目采用前后端分离目录：`backend/` 存放 Python 服务与配置，`frontend/` 存放静态前端资源；根目录保留 uv、Docker、文档、测试和运行资产管理。依赖以 `pyproject.toml` 和 `uv.lock` 为准，不再使用 `requirements.txt`。

## 功能概览

- Wav2Lip 唇形同步数字人
- WebRTC 实时音视频输出
- 豆包 TTS / 豆包 3.0 TTS / Azure TTS / 腾讯 TTS / vLLM-Omni TTS 接入
- LLM 流式回复，并过滤推理内容后再播报
- 支持播放自定义待机视频动作
- 支持模型与数字人素材首次运行自动下载
- 支持本地 GPU 推理，也支持 GPU 服务与 Web 前端服务分离部署
- 支持前后端分离部署：前端可由 nginx/CDN 单独托管，后端以 API-only 模式运行

## 环境要求

- Python 3.10
- [uv](https://docs.astral.sh/uv/)
- ffmpeg
- 本地 Wav2Lip 实时推理建议使用 NVIDIA GPU；CPU 机器建议连接远程 GPU 服务

macOS 可用于开发、测试和前端联调；实时推理性能主要取决于 GPU。

## 快速开始

### 1. 安装 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

如果已经安装 uv，确认版本即可：

```bash
uv --version
```

### 2. 同步依赖

```bash
uv sync
```

`uv sync` 会根据 `uv.lock` 创建 `.venv` 并安装运行依赖与开发依赖。生产环境可使用：

```bash
uv sync --frozen --no-dev
```

### 3. 配置密钥

编辑 `backend/config.yml`，替换为自己的 LLM 与 TTS 配置：

- `LLM.LLM_API_KEY`
- `LLM.LLM_BASE_URL`
- `LLM.LLM_MODEL_NAME`
- `TTS.DOUBAO_APPID`
- `TTS.DOUBAO_TOKEN`

不要把真实密钥提交到公开仓库。首次运行会按 `backend/config.yml` 中的 `DOWNLOAD` 配置自动下载模型和数字人素材。

### 4. 启动服务

推荐使用启动脚本：

```bash
./run.sh
```

也可以直接使用 uv：

```bash
uv run python backend/main.py --avatar_id wav2lip_avatar_female_model --port 8010
```

启动后访问：

```text
http://127.0.0.1:8010/index.html
```

首次运行会自动检查并下载：

- `models/wav2lip.pth`
- `data/wav2lip_avatar_female_model`
- `data/wav2lip_avatar_glass_man`
- `data/wav2lip_avatar_long_hair_girl`

如 Hugging Face 访问较慢，可把 `backend/config.yml` 中的 `DOWNLOAD.BASE_URL` 改为可访问的镜像地址，例如：

```yaml
DOWNLOAD:
  BASE_URL: "https://hf-mirror.com/shibing624/ai-avatar-wav2lip/resolve/main"
```

## 常用运行方式

### 使用不同数字人

```bash
./run.sh wav2lip_avatar_female_model 8010
./run.sh wav2lip_avatar_glass_man 8010
./run.sh wav2lip_avatar_long_hair_girl 8010
```

等价的 uv 命令：

```bash
uv run python backend/main.py --avatar_id wav2lip_avatar_glass_man --port 8010
```

### 指定 TTS

```bash
uv run python backend/main.py \
  --avatar_id wav2lip_avatar_long_hair_girl \
  --tts doubao \
  --REF_FILE zh_female_roumeinvyou_emo_v2_mars_bigtts
```

可选值见代码中的 `--tts` 参数说明：`tencent`、`doubao`、`doubao3`、`azuretts`、`vllm_omni`。

Azure TTS 和本地音频播放依赖默认不安装，需要时可额外同步：

```bash
uv sync --extra azure
uv sync --extra local-audio
```

#### vLLM-Omni TTS

自部署 [vLLM-Omni](https://github.com/vllm-project/vllm-omni) 的 OpenAI 兼容
`POST /v1/audio/speech` 接口可作为 TTS 后端。先在 GPU 机器上启动服务：

```bash
vllm serve Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice \
  --deploy-config vllm_omni/deploy/qwen3_tts.yaml \
  --omni --port 8091 --trust-remote-code --enforce-eager
```

然后在 `backend/main.py` 启动时指定：

```bash
uv run python backend/main.py \
  --avatar_id wav2lip_avatar_female_model \
  --tts vllm_omni \
  --TTS_SERVER http://GPU_SERVER:8091 \
  --REF_FILE vivian
```

或在 `backend/config.yml` 的 `TTS:` 段中配置默认服务地址、API Key、模型、音色、
语言、任务类型与采样率（参见 `config.yml` 中的 `VLLM_OMNI_*` 示例键）。
命令行 `--TTS_SERVER` 与 `--REF_FILE` 优先级高于配置文件。
不同 vLLM-Omni 模型返回的 PCM 采样率不同（Qwen3-TTS / Voxtral / CosyVoice3 为
24 kHz，Fish Speech S2 Pro 为 44.1 kHz），需通过 `VLLM_OMNI_SAMPLE_RATE` 设置。

### 本地 GPU 推理

默认模式会在当前进程加载 Wav2Lip 模型：

```bash
uv run python backend/main.py --port 8010
```

性能指标可关注后端日志中的：

- `inferfps`：GPU 推理帧率
- `finalfps`：最终推流帧率

两者都稳定高于 25 时更接近实时体验。

### 远程 GPU 服务

在 GPU 服务器启动推理服务：

```bash
uv run python backend/src/gpu_wav2lip_service.py --host 0.0.0.0 --port 8080 --batch_size 32 --fp16
```

在前端/CPU 服务器连接远程 GPU：

```bash
uv run python backend/main.py \
  --gpu_server_url http://GPU_SERVER_IP:8080 \
  --avatar_id wav2lip_avatar_female_model \
  --port 8010
```

这种方式适合生产部署：GPU 资源集中在推理服务，多个前端服务可以复用同一 GPU 服务。

## Docker 运行

仓库提供单一 `docker-compose.yml`，通过 [Compose profiles](https://docs.docker.com/compose/profiles/) 切换部署模式。

**一体化部署**（backend 同时托管前端）：

```bash
docker compose --profile integrated up --build
```

容器会使用 `pyproject.toml` 和 `uv.lock` 同步依赖，并挂载：

- `./models:/app/models`
- `./data:/app/data`
- `./backend/config.yml:/app/backend/config.yml:ro`

浏览器访问：

```text
http://127.0.0.1:8010/index.html
```

**前后端分离部署**（参见下文章节）：

```bash
docker compose --profile split up --build
# 浏览器访问 http://127.0.0.1:8011/index.html
```

两个 profile 都监听 8010，二者互斥。不传 `--profile` 不会启动任何服务，也可以用
`COMPOSE_PROFILES=integrated docker compose up --build` 通过环境变量指定。

## 前后端分离部署

默认 `backend/main.py` 同时提供 API 与 `frontend/static/` 静态资源，单端口直接可用。
如果想把前端单独部署到 nginx / CDN / 静态托管，可以按下面的方式拆开。

### 1. 调整前端的运行时配置

`frontend/static/config.js` 是浏览器侧的运行时配置，缺省值为同源（空字符串），保持一体化部署行为：

```js
window.APP_CONFIG = {
    apiBaseUrl: '',         // 后端 API 与 WebRTC 信令的基础 URL
    mediaBaseUrl: '',       // 后端静态媒体 URL，留空回落到 apiBaseUrl
    iceServers: [],         // 传给浏览器 RTCPeerConnection 的 iceServers
};
```

分离部署时把 `apiBaseUrl` 改成可被浏览器访问的后端地址，例如：

```js
window.APP_CONFIG = {
    apiBaseUrl: 'https://api.your-domain.com',
    mediaBaseUrl: 'https://api.your-domain.com',
    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
};
```

所有 `fetch('/offer' | '/human' | '/api/avatars' | ...)` 和头像图片路径都会经过
`window.apiUrl()` / `window.mediaUrl()` 自动前缀，无需改 HTML。

### 2. 后端以 API-only 模式启动

```bash
uv run python backend/main.py --port 8010 --no-static
```

- `--no-static`：关闭对 `frontend/static/` 的托管（前端由其他服务器提供）。
- `--no-data-static`：可选，如果头像图片由 CDN/nginx 提供，关闭后端 `/data` 静态资源。
  默认保持开启，避免破坏头像加载。

CORS 默认放开所有来源（`allow_credentials=False`），可直接被任意域名的前端访问。

### 3. 单独运行前端（开发模式）

```bash
./frontend/serve.sh 5173
# 浏览器访问 http://127.0.0.1:5173/index.html
```

`serve.sh` 实际上调用 `python3 -m http.server`，仅用于本地联调。
生产环境推荐 nginx/Caddy 或托管到 CDN。

### 4. 用 Docker Compose 一键拉起分离部署

```bash
docker compose --profile split up --build
```

该 profile 会启动两个服务：

- `aiavatar-backend`：监听 `8010`，传入 `--no-static`，仅暴露 API 与 `/data`。
- `aiavatar-frontend`：基于 `frontend/Dockerfile` 的 nginx 镜像，监听 `8011`。
  容器启动时通过 `envsubst` 把 `BACKEND_API_URL`、`BACKEND_MEDIA_URL`、
  `FRONTEND_ICE_SERVERS_JSON` 注入 `config.js`，同一份镜像可以服务于不同环境。

浏览器访问 `http://localhost:8011/index.html`。
部署到公网时把 `BACKEND_API_URL` 改成浏览器实际可访问的后端域名/端口，
建议前后端共用同一 HTTPS 入口（例如反向代理后同源），避免浏览器 mixed-content 拦截。

### 5. 注意事项

- **HTTPS 与 Mixed Content**：前端走 HTTPS 时 `apiBaseUrl` 也必须是 HTTPS，否则
  浏览器会拦截跨协议请求；麦克风权限亦需要 secure context（localhost 除外）。
- **WebRTC 可达性**：`/offer` 只完成信令，后续音视频直接走 WebRTC。
  在 NAT/容器/云主机环境下，请通过 `iceServers` 配置 STUN/TURN，
  否则 ICE candidate 可能无法穿透。
- **跨域凭据**：后端默认 `allow_credentials=False`，前端的 `fetch` 不要传
  `credentials: 'include'`，否则浏览器会因为通配 Origin 而拒绝响应。

## 创建自己的数字人

准备一个人物闭嘴、不说话、脸部清晰的视频，建议 5-30 秒、25-30 fps。

生成数字人素材：

```bash
uv run python backend/src/wav2lip/genavatar.py \
  --video_path your_video.mp4 \
  --img_size 256 \
  --avatar_id wav2lip_avatar_custom
```

复制到项目数据目录：

```bash
cp -r results/avatars/wav2lip_avatar_custom data/
```

启动时指定新数字人：

```bash
uv run python backend/main.py --avatar_id wav2lip_avatar_custom --port 8010
```

生成目录通常包含：

- `full_imgs/`：完整视频帧
- `face_imgs/`：裁剪后的人脸图像
- `coords.pkl`：人脸坐标信息

## uv 工作流

安装或同步依赖：

```bash
uv sync
```

添加运行依赖：

```bash
uv add package-name
```

添加开发依赖：

```bash
uv add --dev package-name
```

更新锁文件：

```bash
uv lock
```

运行测试：

```bash
uv run pytest
```

导出兼容 pip 的依赖文件：

```bash
uv export --no-hashes -o requirements.txt
```

## 测试与调试

运行单元测试：

```bash
uv run pytest
```

GPU 服务启动后，可运行手动冒烟测试：

```bash
uv run python backend/src/gpu_server_test.py --url http://127.0.0.1:8080
```

调整日志级别：

```bash
AI_AVATAR_LOG_LEVEL=INFO uv run python backend/main.py
```

### 健康检查与会话容量

主服务提供两个无需模型推理的健康检查接口：

- `GET /health/live`：进程存活检查。
- `GET /health/ready`：服务就绪检查，同时返回当前会话数和容量。

`--max_session` 控制同时存在的 WebRTC 会话数，默认值为 `1`。容量用尽时
`POST /offer` 返回 HTTP `429`，不会继续创建 GPU、媒体线程或 TTS 资源：

```bash
uv run python backend/main.py --max_session 2
```

业务接口对无效 JSON、参数、会话和过大请求分别使用标准的 4xx/5xx HTTP
状态码，并返回 `code`、`error`、`msg` 字段。音频上传及其他请求体上限为
10 MiB。WebRTC 断开或服务退出时会清理媒体资源、LLM 任务引用和对话历史。

## 项目结构

```text
.
├── backend/
│   ├── main.py                     # WebRTC/API 服务入口
│   ├── config.yml                  # LLM、TTS、模型下载与数字人配置
│   └── src/
│       ├── gpu_wav2lip_service.py  # 远程 GPU 推理服务
│       ├── lipreal.py              # 本地 Wav2Lip 推理
│       ├── lipreal_remote.py       # 远程 GPU 推理客户端
│       ├── ttsreal.py              # TTS 实现
│       ├── llm.py                  # LLM 对话逻辑
│       └── wav2lip/                # Wav2Lip 模型与工具
├── frontend/
│   ├── static/                     # Web 前端（含 config.js / api.js）
│   ├── serve.sh                    # 本地独立托管前端的开发脚本
│   ├── Dockerfile                  # 前端独立部署的 nginx 镜像
│   ├── nginx.conf                  # 前端镜像的 nginx 配置
│   ├── config.template.js          # 容器启动时由 envsubst 渲染成 config.js
│   └── docker-entrypoint.sh        # 渲染 config.js 的入口脚本
├── docker-compose.yml              # 一体化 / 分离两个 profile（integrated、split）
├── pyproject.toml                  # uv 项目与依赖声明
├── uv.lock                         # uv 锁文件
├── run.sh                          # uv 启动脚本
├── tests/                          # 单元测试
├── models/                         # 自动下载或手动放置的模型文件
└── data/                           # 自动下载或自定义数字人素材
```

## 常见问题

### 依赖安装很慢

首次 `uv sync` 会安装 PyTorch、OpenCV、音视频相关依赖，耗时较长是正常的。建议保留 `uv.lock`，后续同步会更稳定。

### 缺少 ffmpeg

录制、推流和部分音视频处理依赖系统 ffmpeg。macOS 可使用：

```bash
brew install ffmpeg
```

Ubuntu/Debian 可使用：

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg libsndfile1 libgl1 libglib2.0-0
```

### 模型或数字人下载失败

检查网络后重试，或手动从模型仓库下载：

```text
https://huggingface.co/shibing624/ai-avatar-wav2lip
```

将模型放入 `models/`，将数字人目录放入 `data/`。

## License

本项目使用 [Apache License 2.0](LICENSE)。

## Acknowledgements

- [AIAvatar](https://github.com/shibing624/AIAvatar)
- [LiveTalking](https://github.com/lipku/LiveTalking)
- [MuseTalk](https://github.com/TMElyralab/MuseTalk)
