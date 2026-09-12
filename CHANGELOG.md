# 变更日志

## [1.1.0] - 2026-09

### Added
- 分句合成与失败续跑、单句试听、草稿自动保存
- 合成前预估确认；片头片尾；SRT / 脚本 Markdown 导出
- AI 写稿与口语化 / 压短 / 润色（OpenAI 兼容 LLM，可在设置中配置）
- 声音设计模型、克隆音色管理与试听
- 项目列表统计；错误信息人话化

### Security
- 默认监听 127.0.0.1；CORS 收紧为本地前端源
- 密钥仅经环境变量 / `.env`（已 ignore），示例文件不含真实 Key

### Docs
- 补充 LICENSE / SECURITY / CONTRIBUTING 与完整 README
