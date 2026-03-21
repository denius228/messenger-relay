import os

# Секреты и пароли
SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'HIFI_STABLE_V10')
USER_PASSWORD = os.getenv('CHAT_PASSWORD', '123')
SYSTEM_BROADCAST_TOKEN = os.getenv('SYSTEM_BROADCAST_TOKEN', 'SUPER_SECRET_GOD_TOKEN_999')
GODMODE_PASSWORD = os.getenv('GODMODE_PASSWORD', '777_SUPER_SECRET_ADMIN_PASS')

# Директории
UPLOAD_FOLDER = 'uploads'
DB_DIR = 'db_data'
DB_PATH = os.path.join(DB_DIR, 'messages.db')
VAPID_PRIVATE_PEM = os.path.join(DB_DIR, "vapid_private.pem")
VAPID_PUBLIC_TXT = os.path.join(DB_DIR, "vapid_public.txt")

# Лимиты и заголовки сети (Анти-Cloudflare)
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB
REQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "ngrok-skip-browser-warning": "true",
    "Bypass-Tunnel-Reminder": "true"
}

# Создаем папки при старте
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)