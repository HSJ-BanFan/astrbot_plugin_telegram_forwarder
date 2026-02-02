"""
文本处理工具模块

提供 Telegram 消息文本的清洗和格式化功能，用于优化转发到其他平台（如 QQ）时的阅读体验。
"""

import re


def clean_telegram_text(text: str) -> str:
    """
    清洗 Telegram 消息文本

    Args:
        text (str): 原始 Telegram 消息文本

    Returns:
        str: 清洗和格式化后的文本

    主要处理：
    1. 移除特定的频道签名和头部信息
    2. 去除 Markdown 格式标记（加粗、斜体）
    3. 优化链接显示格式
    """
    if not text:
        return ""

    # 1. 移除 Telegram 头部/签名
    # 许多频道会在消息末尾添加类似 "频道 @channelname" 的签名
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        # 跳过包含 "频道" 和 "@" 的签名行
        if "频道" in line and "@" in line:
            continue
        # 跳过纯频道引用行（如 @channelname）
        if line.strip().startswith("@") and len(line) < 20:
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # 2. 正则主要内容清洗 (新增)
    # 去除特定的元数据文本
    patterns = [
        r"\* 此原图经过处理",  # 指定去除的提示
        r"投稿 by .*",      # 指定去除的投稿信息
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    # 3. 去除 Markdown 加粗/斜体标记
    # QQ 等目标平台可能不支持或只需纯文本
    text = text.replace("**", "").replace("__", "")

    # 3. 优化 Markdown 链接显示
    # 将 [Text](URL) 转换为 Text: URL 格式，更易读
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1: \2", text)

    return text.strip()
