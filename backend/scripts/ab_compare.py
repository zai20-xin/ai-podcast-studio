#!/usr/bin/env python3
"""A/B 对比试听：旧版指令构造 vs 新版指令构造。

指令层的改动（维度职责分离、导演式情境描述）在代码层面可以验证「不再自相矛盾」，
但「是否更好听」只能靠耳朵判断。本工具让你用真实 Key 一次跑出两版音频对比。

用法：
    cd backend
    .venv/bin/python scripts/ab_compare.py                       # 默认场景与文案
    .venv/bin/python scripts/ab_compare.py "📖 讲故事" "你的文案"

需要 MIMO_API_KEY（环境变量或 backend/.env）。无 Key 时会明确提示。
两版使用**同一种音色**合成，唯一变量是指令文本，因此听到的差异全部来自指令。
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def load_dotenv() -> None:
    env = BACKEND / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


# ── 1.2.0 之前的实现快照（用于复现旧行为）──────────────────────────────
# 当时 14 条风格里有 12 条自带语速描述，与语速字典叠加后同一条指令
# 会对语速提出多种互斥要求。
LEGACY_STYLE_PRESETS = {
    "温柔": "用温柔舒缓的语气说话，语速偏慢，声音轻柔，像在哄孩子入睡",
    "兴奋": "用兴奋激动的语气说话，语速偏快，声音高亢，充满活力和热情",
    "悲伤": "用悲伤低沉的语气说话，语速缓慢，声音沙哑低沉，带着哽咽感",
    "愤怒": "用愤怒的语气说话，语速较快，声音尖锐有力，带着明显的怒气",
    "严肃": "用严肃正式的语气说话，语速适中，声音沉稳有力，带有权威感",
    "幽默": "用幽默诙谐的语气说话，语速轻快，声音带着笑意，轻松愉快",
    "紧张": "用紧张焦虑的语气说话，语速急促，声音颤抖，带着紧迫感",
    "平静": "用平静从容的语气说话，语速平稳，声音柔和，不带明显情绪波动",
    "叙述": "用讲故事的口吻叙述，语速适中，声音富有感染力，带有画面感",
    "新闻播报": "用新闻播报的专业语气，语速均匀，吐字清晰，语气客观正式",
    "撒娇": "用撒娇的语气说话，声音软糯，尾音拖长带着依赖感",
    "磁性低沉": "用低沉磁性的嗓音说话，声音浑厚有共鸣，像深夜电台主播",
    "活泼可爱": "用活泼可爱的语气说话，语速轻快，声音清脆明亮",
    "苍老": "用苍老沙哑的声音说话，语速缓慢，带着岁月沧桑感",
}

LEGACY_SPEED_PRESETS = {
    "很慢": "语速非常缓慢，每个字都拖长，像在读诗",
    "偏慢": "语速偏慢，从容不迫",
    "正常": "语速适中",
    "偏快": "语速偏快，节奏紧凑",
    "很快": "语速非常快，像连珠炮一样",
}

LEGACY_SCENES = {
    "📖 讲故事": {
        "voice_id": "冰糖", "style": "叙述", "speed": "正常", "emotion": "平静",
        "global_instruction": "讲故事场景，有代入感、有感染力，自然停顿，像在给朋友讲故事",
    },
    "📰 新闻播报": {
        "voice_id": "茉莉", "style": "新闻播报", "speed": "偏快", "emotion": "严肃",
        "global_instruction": "新闻播报场景，语速均匀，吐字清晰，语气客观正式，不带个人情绪",
    },
    "🌙 睡前陪伴": {
        "voice_id": "冰糖", "style": "温柔", "speed": "很慢", "emotion": "平静",
        "global_instruction": "睡前陪伴场景，语速非常慢，声音轻柔，像在耳边低语，让人放松入睡",
    },
    "😄 轻松闲聊": {
        "voice_id": "冰糖", "style": "幽默", "speed": "偏快", "emotion": "开心",
        "global_instruction": "轻松闲聊场景，口语化，带着笑意，像朋友之间聊天，轻松愉快",
    },
}


def legacy_build_instruction(style, speed, emotion, global_instruction) -> str:
    """1.2.0 之前的组装方式：三个来源用「。」直接拼接"""
    parts = []
    if global_instruction:
        parts.append(global_instruction)
    if style and style in LEGACY_STYLE_PRESETS:
        parts.append(LEGACY_STYLE_PRESETS[style])
    elif style:
        parts.append(style)
    if speed and speed in LEGACY_SPEED_PRESETS:
        parts.append(LEGACY_SPEED_PRESETS[speed])
    elif speed:
        parts.append(f"语速{speed}")
    if emotion:
        parts.append(f"情绪：{emotion}")
    return "。".join(parts)


def main() -> int:
    load_dotenv()
    if not os.environ.get("MIMO_API_KEY"):
        print("❌ 未配置 MIMO_API_KEY。请填入 backend/.env 或设置环境变量后重试。")
        return 1

    parser = argparse.ArgumentParser(description="新旧指令 A/B 对比试听")
    parser.add_argument("scene", nargs="?", default="🌙 睡前陪伴",
                        help=f"可选场景：{' / '.join(LEGACY_SCENES)}")
    parser.add_argument("text", nargs="?",
                        default="大家好，欢迎收听今天的节目。今天我们聊一个很有意思的话题，"
                               "相信听完之后，你会有一些新的想法。")
    args = parser.parse_args()

    if args.scene not in LEGACY_SCENES:
        print(f"❌ 未知场景「{args.scene}」。可用：{' / '.join(LEGACY_SCENES)}")
        return 1

    from app.services.tts_service import TTSService
    from app.services.presets import SCENE_PRESETS

    old_cfg = LEGACY_SCENES[args.scene]
    new_preset = SCENE_PRESETS[args.scene]
    # 两版统一用新版预设的音色：唯一变量是指令，听到的差异全部来自指令本身
    voice = new_preset["voice_id"]

    svc = TTSService()
    old_ins = legacy_build_instruction(
        old_cfg["style"], old_cfg["speed"], old_cfg["emotion"], old_cfg["global_instruction"]
    )
    new_ins = svc._build_instruction(
        new_preset["style"], new_preset["speed"], new_preset["emotion"],
        new_preset["global_instruction"],
    )

    print(f"场景：{args.scene}　音色：{voice}（两版一致）\n")
    print(f"【旧版指令】{old_ins}")
    print(f"   语速描述出现 {old_ins.count('语速')} 次\n")
    print(f"【新版指令】{new_ins}")
    print(f"   语速描述出现 {new_ins.count('语速')} 次\n")

    out_dir = BACKEND.parent / "data" / "audio" / "ab_compare"
    out_dir.mkdir(parents=True, exist_ok=True)

    async def run():
        ann_old, ann_new, p_old, p_new = await asyncio.gather(
            svc.synthesize("下面是旧版指令的效果", model_type="builtin",
                           voice_id=voice, dest_dir=out_dir),
            svc.synthesize("下面是新版指令的效果", model_type="builtin",
                           voice_id=voice, dest_dir=out_dir),
            svc.synthesize(args.text, model_type="builtin",
                           voice_id=voice, global_instruction=old_ins, dest_dir=out_dir),
            svc.synthesize(args.text, model_type="builtin",
                           voice_id=voice, global_instruction=new_ins, dest_dir=out_dir),
        )
        return ann_old, ann_new, p_old, p_new

    try:
        ann_old, ann_new, p_old, p_new = asyncio.run(run())
    except Exception as e:
        from app.services.llm import friendly_error
        print(f"❌ 合成失败：{friendly_error(e)}")
        return 1

    from pydub import AudioSegment

    combined = AudioSegment.empty()
    for p in (ann_old, p_old, ann_new, p_new):
        combined += AudioSegment.from_file(p)
        combined += AudioSegment.silent(duration=800)
    final = out_dir / "AB对比.mp3"
    combined.export(str(final), format="mp3", bitrate="192k")

    print("✅ 对比带已生成：")
    print(f"   {final}")
    print("   顺序：报幕（旧）→ 旧版效果 → 报幕（新）→ 新版效果")
    return 0


if __name__ == "__main__":
    sys.exit(main())
