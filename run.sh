#!/bin/bash

# AI Avatar 启动脚本（uv）
# 用法: ./run.sh [avatar_id] [port]
# avatar_id: wav2lip_avatar_female_model(默认) | wav2lip_avatar_glass_man | wav2lip_avatar_long_hair_girl
# port: 端口号(默认8010)

set -e

# 默认参数
AVATAR_ID=${1:-"wav2lip_avatar_female_model"}
PORT=${2:-8010}

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════╗"
echo "║           AI Avatar 数字人             ║"
echo "║        实时交互流式数字人系统           ║"
echo "║                                       ║"
echo "║  🤖 支持wav2lip数字人模型                 ║"
echo "║  🎤 支持声音克隆                       ║"
echo "║  💬 支持实时对话                       ║"
echo "║  📹 支持WebRTC视频输出                 ║"
echo "║                                       ║"
echo "║  首次运行会自动下载必要文件             ║"
echo "╚═══════════════════════════════════════╝"
echo -e "${NC}"

# 检查 uv 环境
if ! command -v uv &> /dev/null; then
    echo -e "${RED}错误: 未找到 uv${NC}"
    echo -e "${YELLOW}请先安装 uv: https://docs.astral.sh/uv/getting-started/installation/${NC}"
    exit 1
fi

echo -e "${GREEN}✓ 使用 uv 管理 Python 环境${NC}"

echo -e "${GREEN}启动配置:${NC}"
echo -e "  数字人形象: ${AVATAR_ID}"
echo -e "  Web端口: ${PORT}"
echo -e "  访问地址: http://127.0.0.1:${PORT}/index.html"
echo ""

echo -e "${BLUE}正在启动服务...${NC}"
echo "按 Ctrl+C 停止服务"
echo ""

# 启动应用
uv run python backend/main.py --avatar_id "$AVATAR_ID" --port "$PORT"
