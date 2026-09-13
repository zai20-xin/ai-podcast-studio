# AI Podcast Studio

本地优先的 AI 播客制作台：写稿 → 试听 → 合成 → 导出。支持单人朗读与双人对谈，基于 MiMo TTS。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 功能

- **三种声音来源**：内置音色 / 文字描述声音设计 / 参考音频克隆
- **单人朗读 · 双人对谈**：角色识别与主播映射
- **AI 写稿**：大纲/文章 → 口播脚本；口语化 / 压短 / 润色
- **制作台**：项目与版本（Takes）、草稿自动保存、单句试听、分句失败续跑
- **合成前预估**：句数、字数、大致时长确认
- **片头片尾** + **响度归一（约 -16 LUFS）**
- **导出**：MP3 / SRT / 脚本 Markdown
- **本地 LLM（可选）**：任意 OpenAI 兼容接口（如 freellmapi、Ollama、vLLM）

## 快速开始

### 系统依赖

- Python 3.10+
- Node.js 18+
- [FFmpeg](https://ffmpeg.org/)（合并与 loudnorm）

```bash
# macOS
brew install ffmpeg
# Debian/Ubuntu
sudo apt install ffmpeg
```

### 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env：填入 MIMO_API_KEY；可选 LLM_* 用于写稿

python run.py
```

- API：http://127.0.0.1:8000  
- Swagger：http://127.0.0.1:8000/docs  

### 前端

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 http://127.0.0.1:3000 （Vite 默认端口以终端为准）。

### 获取密钥

| 用途 | 获取方式 |
|------|----------|
| 语音合成 | [MiMo 控制台](https://platform.xiaomimimo.com/console/api-keys) |
| 写稿 LLM | 任意 OpenAI 兼容服务；在「设置」中配置 Base URL / Key / 模型 |

也可在应用内「设置」页直接填写（会写入 `backend/.env`，重启后端后仍然有效；不写入前端存储）。

## 环境变量

见 `backend/.env.example`：

| 变量 | 说明 |
|------|------|
| `MIMO_API_KEY` | MiMo TTS API Key（必填） |
| `MIMO_BASE_URL` | TTS 接口地址 |
| `LLM_API_KEY` | 写稿 LLM Key（可选） |
| `LLM_BASE_URL` | OpenAI 兼容 Base URL，默认 `http://localhost:3001/v1` |
| `LLM_MODEL` | 模型 ID，默认 `auto` |
| `TTS_CONCURRENCY` | 同时在飞的 TTS 请求上限，默认 `4`（调高需自行确认服务端限流阈值） |
| `TTS_RATE_LIMIT_RETRIES` | 触发限流后的退避重试次数，默认 `4` |
| `INTERMEDIATE_KEEP_HOURS` | 试听缓存保留时长（小时），默认 `24` |
| `CLEANUP_INTERVAL_HOURS` | 清理扫描间隔（小时），默认 `6` |

**请勿将 `.env` 或真实 Key 提交到 Git。** 详见 [SECURITY.md](SECURITY.md)。

## 使用摘要

1. 新建项目（选定单人/双人，制作台内固定该模式）
2. 手写脚本，或「AI 写稿」；可用「口语化 / 压短 / 润色」
3. 配置主播音色，用「试听」确认
4. 可选填写片头片尾 → 「合成新版本」→ 确认预估
5. 合成中可随时「停止」；失败句可「仅重试」。**已成功的句子会按配置指纹复用，不会重做**
6. 完成后导出 MP3 / SRT / MD

脚本格式示例：

```
A: 你好，欢迎收听。
B: 今天我们聊聊人工智能。
```

支持中文/英文冒号与角色名；`【章节】` 会跳过；可嵌入 `(轻笑)(停顿)` 等标签。

## 架构

```
frontend/   Vue3 + Element Plus + Pinia（录音棚风格 UI）
backend/    FastAPI + SQLAlchemy(SQLite) + OpenAI SDK
data/       运行时音频与数据库（默认不入库）
```

## 测试

三类测试均不依赖真实 TTS/LLM 密钥（契约与校验路径）：

```bash
# 后端单元 + API + 契约（backend/tests）
cd backend
.venv/bin/python -m unittest discover -s tests -v

# 前端单元 + 契约（frontend/tests，Vitest）
cd frontend
npm test
```

契约单一来源：`contracts/api-contract.json`（后端 `test_contract.py` 与前端 `contract.spec.js` 共同校验）。

## 安全与限制

- 默认面向**本机开发/受信环境**，API **无用户鉴权**
- 默认监听 `127.0.0.1`；请勿在未加固情况下暴露公网
- 合成依赖第三方 TTS/LLM，请遵守相应服务条款与内容规范

## License

[MIT](LICENSE)
