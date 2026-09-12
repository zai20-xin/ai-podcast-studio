# Contributing

感谢关注 AI Podcast Studio。

## 开发环境

```bash
# 后端
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # 填入本地密钥，勿提交 .env
python run.py

# 前端
cd frontend
npm install
npm run dev
```

系统依赖：FFmpeg（音频合并 / loudnorm）。

## 代码约定

- Python：类型注解、`logging`（不要在业务代码里 `print` 调试残留）
- Vue3：保持组件职责单一；样式优先使用 `src/styles/theme.css` 中的 design token
- 新增 API：在对应 `app/api/*` 中注册，并更新 README 中的接口表（如有）

## 提交前

```bash
cd backend && python -m unittest tests.test_core -v
cd frontend && npm run build
```

## Pull Request

- 说明动机与改动范围
- 涉及 UI 时简述交互变化
- 不要提交 `backend/.env`、`data/`、`node_modules/`、构建产物
