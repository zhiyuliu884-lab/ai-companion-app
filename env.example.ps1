# 复制成 env.ps1，改成你自己的，然后在同一个窗口里跑 python server/app.py
$env:AC_PORT="6191"
$env:AC_NAME="你的角色"
$env:AC_PERSONA="你是她，一个住在电脑里的温柔女孩子。说话自然、口语化。"

# LLM（必填）
$env:AC_PROVIDER_BASE="https://api.deepseek.com/v1"
$env:AC_PROVIDER_MODEL="deepseek-chat"
$env:AC_PROVIDER_KEY="sk-你的密钥"

# 可选：接你自己的服务
$env:AC_TTS="http://127.0.0.1:6194/tts"
$env:AC_IMG="http://127.0.0.1:6196/generate"

# 可选：立绘（720x989 透明 PNG）
# $env:AC_APPEARANCE="C:\path\to\your_art.png"

# 可选：有 AstrBot 时指向它，能复用记忆/人格
# $env:AC_INSTANCE="C:\AstrBot\data"

# 可选：你的 QQ 号（QQ 掉线提醒用）
# $env:AC_QQ_USER="123456789"
