import os, time, base64, json
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from pywebpush import webpush

from config import VAPID_PRIVATE_PEM, VAPID_PUBLIC_TXT, UPLOAD_FOLDER
from database import query_db

def ensure_vapid_keys():
    """Генерирует ключи для Push-уведомлений, если их нет"""
    if not os.path.exists(VAPID_PRIVATE_PEM):
        private_key = ec.generate_private_key(ec.SECP256R1())
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )
        with open(VAPID_PRIVATE_PEM, "wb") as f:
            f.write(pem)
            
        public_numbers = private_key.public_key().public_numbers()
        public_bytes = b'\x04' + public_numbers.x.to_bytes(32, 'big') + public_numbers.y.to_bytes(32, 'big')
        pub_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b'=').decode('utf-8')
        with open(VAPID_PUBLIC_TXT, "w") as f:
            f.write(pub_b64)

    with open(VAPID_PUBLIC_TXT, "r") as f:
        return f.read().strip()

def send_push_notification(target_username, sender_username):
    """Отправляет WebPush уведомление спящему телефону"""
    row = query_db("SELECT sub_json FROM push_subs WHERE username = ?", (target_username,), fetchone=True)
    if row:
        try:
            subscription_info = json.loads(row[0])
            webpush(
                subscription_info=subscription_info,
                data=json.dumps({"title": "Secure Chat", "body": f"Новое сообщение от {sender_username} 🔒"}),
                vapid_private_key=VAPID_PRIVATE_PEM,
                vapid_claims={"sub": "mailto:admin@eprobot.ru"},
                ttl=86400,
                headers={"Urgency": "high", "Topic": "new-message"}
            )
        except Exception: 
            pass

def cleanup_old_files_task():
    """Удаляет медиафайлы старше 7 дней для экономии места на сервере"""
    while True:
        now = time.time()
        for filename in os.listdir(UPLOAD_FOLDER):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(filepath) and os.stat(filepath).st_mtime < now - 604800:
                try: os.remove(filepath)
                except Exception: pass
        time.sleep(86400)