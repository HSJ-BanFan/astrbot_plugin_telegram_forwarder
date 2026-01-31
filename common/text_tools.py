import re

def clean_telegram_text(text: str) -> str:
    if not text: return ""
    
    # 1. Remove Telegram Headers/Signatures
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        # Skip signature lines often seen in channels
        if "频道" in line and "@" in line: continue
        if line.strip().startswith("@") and len(line) < 20: continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    
    # 2. Strip Markdown Bold/Italic
    text = text.replace("**", "").replace("__", "")
    
    # 3. Optimize Markdown Links: [Text](URL) -> Text: URL
    # Regex for [text](url)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1: \2", text)
    
    return text.strip()
