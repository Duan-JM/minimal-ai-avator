# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## Project Overview

Minimal AI Avator is a real-time interactive digital human service powered by Wav2Lip and WebRTC. It provides:

- Wav2Lip-based lip-sync avatar rendering
- WebRTC audio/video streaming from the backend to browser clients
- LLM streaming responses with reasoning-content filtering before speech playback
- TTS integrations for Doubao, Doubao 3.0, Azure TTS, Tencent TTS, and self-hosted vLLM-Omni
- Optional idle/custom avatar video actions
- First-run download of model and avatar assets
- Local GPU inference, or split deployment with a remote GPU inference service
- Optional split deployment: frontend can be hosted by nginx/CDN while the backend runs in API-only mode

## Commands

```bash
# Install/sync dependencies
uv sync

# Production-style dependency sync
uv sync --frozen --no-dev

# Optional extras
uv sync --extra azure
uv sync --extra local-audio

# Run the main WebRTC/API service
./run.sh
./run.sh wav2lip_avatar_female_model 8010
uv run python backend/main.py --avatar_id wav2lip_avatar_glass_man --port 8010

# Run with a specific TTS backend/voice
uv run python backend/main.py \
  --avatar_id wav2lip_avatar_long_hair_girl \
  --tts doubao \
  --REF_FILE zh_female_roumeinvyou_emo_v2_mars_bigtts

# Start the remote GPU Wav2Lip inference service
uv run python backend/src/gpu_wav2lip_service.py --host 0.0.0.0 --port 8080 --batch_size 32 --fp16

# Run backend in API-only mode (frontend is hosted separately)
uv run python backend/main.py --port 8010 --no-static

# Serve the static frontend separately during development
./frontend/serve.sh 5173

# Connect the web/API service to a remote GPU service
uv run python backend/main.py \
  --gpu_server_url http://GPU_SERVER_IP:8080 \
  --avatar_id wav2lip_avatar_female_model \
  --port 8010

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_llm_output_filtering.py

# Run a subset of tests
uv run pytest tests/test_webrtc_tts_events.py -k start_end

# Manual GPU service smoke test after starting gpu_wav2lip_service.py
uv run python backend/src/gpu_server_test.py --url http://127.0.0.1:8080

# Docker
docker compose --profile integrated up --build  # integrated
docker compose --profile split up --build       # split (backend + nginx frontend)

# Generate a custom avatar from a video
uv run python backend/src/wav2lip/genavatar.py \
  --video_path your_video.mp4 \
  --img_size 256 \
  --avatar_id wav2lip_avatar_custom
```

## Architecture

### Source layout

**Backend entry point**: `backend/main.py` - starts the aiohttp service, serves static frontend files, handles WebRTC offers, routes chat/audio/control API calls, loads avatars/models, and switches between local and remote GPU inference.

**Main backend modules under `backend/src/`:**

1. **Runtime paths and configuration**
   - `paths.py` - repository-aware paths for `backend/`, `frontend/static/`, `data/`, `models/`, and `backend/config.yml`.
   - `config.py` - YAML config loader and accessors for `LLM`, `TTS`, `DOWNLOAD`, and `AVATARS`.
   - `log.py` - centralized Loguru logger, controlled by `AI_AVATAR_LOG_LEVEL`.

2. **Conversation, TTS, and streaming**
   - `llm.py` - OpenAI-compatible LLM client, conversation history, and filtering of reasoning text before playback.
   - `ttsreal.py` - TTS backends and audio chunk streaming into avatar playback.
   - `webrtc.py` - `HumanPlayer` media tracks and data-channel events such as `llm`, `tts_start`, and `tts_end`.
   - `basereal.py`, `baseasr.py`, `lipasr.py`, `protocols.py` - shared avatar/audio abstractions and protocols.

3. **Wav2Lip inference**
   - `lipreal.py` - local Wav2Lip model/avatar loading and inference.
   - `lipreal_remote.py` - client-side adapter for remote GPU inference.
   - `gpu_wav2lip_service.py` - standalone GPU inference service.
   - `wav2lip/` - Wav2Lip model utilities and avatar-generation scripts.

**Frontend layout**: `frontend/static/` contains the static browser app served by `backend/main.py` (or by an independent nginx/CDN in split deployment).

- `config.js`, `api.js` - runtime configuration (`window.APP_CONFIG`) and URL helpers (`window.apiUrl`, `window.mediaUrl`, `window.apiFetch`). Editing `config.js` repoints API/media requests to a separately-deployed backend without rebuilding the client.
- `index.html` - avatar selection and the primary entry point.
- `talk.html`, `client_talk.js` - conversational UI with avatar selection, speech recognition, subtitles, and media diagnostics.

**Split-deployment artifacts** (under `frontend/`):

- `serve.sh` - run a standalone static server (`python -m http.server`) for local frontend-only development.
- `Dockerfile`, `nginx.conf`, `config.template.js`, `docker-entrypoint.sh` - build an nginx image that ships the static assets and renders `config.js` from `BACKEND_API_URL` / `BACKEND_MEDIA_URL` / `FRONTEND_ICE_SERVERS_JSON` env vars at container start.

`docker-compose.yml` exposes two Compose profiles: `integrated` (single-port `aiavatar`) and `split` (`aiavatar-backend` with `--no-static` plus the nginx `aiavatar-frontend`). Pick one with `docker compose --profile <name> up`.

**Tests**: `tests/` contains pytest/unittest tests for path resolution, LLM output filtering, TTS streaming, WebRTC TTS events, ASR buffering, and frontend audio behavior.

### Key patterns

- **Configuration**: Runtime configuration lives in `backend/config.yml` and is loaded via `src.config`. Never commit real LLM/TTS keys or tokens. Document new required config keys in `README.md`.
- **Path handling**: Use `src.paths` instead of assuming the current working directory. Tests assert the split `backend/` + `frontend/` layout.
- **Runtime assets**: Models and avatar data belong under top-level `models/` and `data/`. The main service can download configured assets on first run.
- **Service architecture**: Browser clients create a WebRTC offer to `/offer`; the backend creates a `HumanPlayer`, binds audio/video tracks, and uses the data channel for LLM/TTS events. Frontend URLs go through `window.apiUrl()` / `window.mediaUrl()` (see `frontend/static/api.js`) so the same client works whether the backend is same-origin or hosted on a separate domain.
- **Inference modes**: Local mode loads Wav2Lip in the web/API process. Remote mode uses `--gpu_server_url` and `lipreal_remote.py` so CPU/web nodes can share a GPU service.
- **Deployment modes**: Integrated mode (default) serves `frontend/static/` and `/data` from the API process. Split mode uses `--no-static` (and optionally `--no-data-static`) plus an independent static host (e.g. `frontend/Dockerfile` nginx image, or `frontend/serve.sh`). CORS is permissive (`allow_credentials=False`) so the browser can reach the backend cross-origin.
- **State management**: Active sessions are keyed by `sessionid` in `backend/main.py`. Clean up peer connections and session state on closed/failed WebRTC connections.
- **Logging**: Use `from src.log import logger` in backend code. Prefer structured, actionable log messages; control verbosity with `AI_AVATAR_LOG_LEVEL`.
- **Error handling**: Surface configuration, download, network, and inference errors explicitly. Do not silently ignore failures that affect media playback or model availability.
- **No legacy code**: When replacing a module or flow, delete the obsolete code and update imports, tests, and documentation. Do not leave dead code "for reference".

### Infrastructure

- **Dockerfile**: Builds from a GPU-capable PyTorch/Transformers image, installs system audio/video dependencies, syncs uv dependencies, and runs `python backend/main.py`.
- **Docker Compose** (`docker-compose.yml`): Exposes two profiles. `integrated` runs a single `aiavatar` service on port 8010. `split` runs `aiavatar-backend` (port 8010, `--no-static`) plus `aiavatar-frontend` (nginx, port 8011). Both profiles mount `./models`, `./data`, and `./backend/config.yml`, and reserve one NVIDIA GPU for the backend container(s).
- **Startup script** (`run.sh`): Convenience wrapper around `uv run python backend/main.py --avatar_id ... --port ...`.
- **Docs/assets** (`docs/`): Images and troubleshooting notes used by project documentation.

## Code Style

- Target Python `>=3.10,<3.11`; do not introduce Python 3.11+ syntax.
- Dependency management is uv-based. Do not add `requirements.txt` as the source of truth.
- Follow the existing module style: small functions and focused classes are both acceptable; avoid broad rewrites just to change style.
- Use 4-space indentation, descriptive names, and type hints for new public functions or methods when practical.
- Keep comments scarce and useful. Comments may be in English or Chinese if that matches the surrounding file.
- Backend code should use `src.log.logger` rather than `print` for operational messages.
- Use `src.paths.resolve_project_path` and related constants for repository-relative files.
- Frontend code is plain static HTML/CSS/JavaScript; there is no Node build step in this repository.
- Keep user-facing Chinese UI copy consistent with the existing frontend and README.
- This repository currently has no configured Black, Ruff, or mypy command. Do not add new tooling unless the task explicitly asks for tooling changes.

## Testing

- Run `uv run pytest` for the full test suite.
- Tests import `src` via pytest `pythonpath = ["backend"]` from `pyproject.toml`; keep backend imports compatible with this.
- Unit tests should mock or stub heavyweight/external dependencies such as OpenAI clients, Loguru, aiortc, av, GPU inference, network downloads, and real TTS services.
- Do not require real API keys, model downloads, GPU hardware, browser media devices, or external network access in automated tests.
- Add or update targeted tests when changing LLM filtering, TTS chunking, WebRTC data-channel events, path resolution, or frontend media behavior.
- There is no configured coverage gate. Do not invent one for unrelated changes.

## Dependencies

- **Package manager**: uv. Use `uv add`, `uv add --dev`, `uv lock`, and `uv sync` so both `pyproject.toml` and `uv.lock` stay consistent.
- **Python**: `>=3.10,<3.11`.
- **Core runtime dependencies**: aiohttp, aiohttp-cors, aiortc, av, Flask, OpenAI-compatible client, PyYAML, Loguru, NumPy/SciPy/librosa/resampy/soundfile, OpenCV headless, PyTorch/TorchAudio/TorchVision, ffmpeg-python, websockets.
- **Optional extras**:
  - `azure` for Azure Cognitive Services Speech.
  - `local-audio` for PyAudio/local playback support.
- **System dependencies**: ffmpeg is required for media processing. Linux/Docker deployments also need audio/video libraries such as `libsndfile1`, `libgl1`, and `libglib2.0-0`.

## Iteration Workflow (MANDATORY for AI agents)

Every code change - feature, fix, refactor, docs, even one-line typos - must go through this loop. Direct pushes to `main` are forbidden, no exceptions. The loop ensures CI is the single source of truth for "is this change safe to merge".

### The 6-step loop

1. **Branch from latest `main`**

   ```bash
   git checkout main && git pull --ff-only origin main
   git checkout -b <type>/<slug>
   ```

   `<type>` is one of `feat`, `fix`, `docs`, `refactor`, `test`, or `chore`, matching Conventional Commits. `<slug>` is 2-5 words in kebab-case, for example `fix/tts-end-event` or `docs/update-agent-guide`.

2. **Implement and verify locally** before pushing:

   ```bash
   uv sync
   uv run pytest
   ```

   Use targeted commands such as `uv run pytest tests/test_llm_output_filtering.py` while iterating, but run the full pytest suite before creating a PR. If Docker files changed, also run `docker compose config` and consider `docker compose up --build` when feasible. If docs-only files changed, pytest is not required unless tests cover the changed behavior.

3. **Commit** with Conventional Commits format. Every commit message must include the trailer:

   ```text
   Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
   ```

4. **Push the branch and open a PR**:

   ```bash
   git push -u origin HEAD
   gh pr create --fill --base main
   ```

   The PR body must include a `## Verification` section listing exactly what was run locally and the outcomes.

5. **Watch CI and self-heal until green**:

   ```bash
   gh run watch --exit-status
   # if it fails:
   gh run view <run-id> --log-failed
   ```

   Push fix commits to the same branch and repeat. Hard limit: 3 fix attempts. If CI is still red after the third push, stop, summarize what was tried, and surface the failure to the human. Suspected flaky failures count toward this budget; if you believe a failure is flaky, say so explicitly in the PR and stop.

6. **Stop after the PR is green. Do NOT auto-merge.** Report the PR URL and the final green CI run ID. Merging is the human's call.

### Why no direct pushes to `main`

Changes that look clean locally can still fail in CI's cold environment. The PR + CI loop catches those failures before they land on `main`, and gives reviewers a single artifact - the PR diff - to inspect rather than a moving `main`.
