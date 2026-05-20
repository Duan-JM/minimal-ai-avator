# Minimal AI Avator

Minimal AI Avator 是一个实时交互式数字人项目：前端通过 WebRTC 播放音视频，后端使用 Wav2Lip 做唇形同步，并接入 LLM 与 TTS 生成对话内容。

本项目采用前后端分离目录：`backend/` 存放 Python 服务与配置，`frontend/` 存放静态前端资源；根目录保留 uv、Docker、文档、测试和运行资产管理。依赖以 `pyproject.toml` 和 `uv.lock` 为准，不再使用 `requirements.txt`。

## 功能概览

- Wav2Lip 唇形同步数字人
- WebRTC 实时音视频输出
- 豆包 TTS / 豆包 3.0 TTS / Azure TTS / 腾讯 TTS 接入
- LLM 流式回复，并过滤推理内容后再播报
- 支持播放自定义待机视频动作
- 支持模型与数字人素材首次运行自动下载
- 支持本地 GPU 推理，也支持 GPU 服务与 Web 前端服务分离部署

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

可选值见代码中的 `--tts` 参数说明：`tencent`、`doubao`、`doubao3`、`azuretts`。

Azure TTS 和本地音频播放依赖默认不安装，需要时可额外同步：

```bash
uv sync --extra azure
uv sync --extra local-audio
```

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

构建并启动：

```bash
docker compose up --build
```

容器会使用 `pyproject.toml` 和 `uv.lock` 同步依赖，并挂载：

- `./models:/app/models`
- `./data:/app/data`
- `./backend/config.yml:/app/backend/config.yml:ro`

默认访问地址：

```text
http://127.0.0.1:8010/index.html
```

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
│   └── static/                     # Web 前端
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
