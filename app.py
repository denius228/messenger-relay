import sys, os, sqlite3, datetime, uuid, requests, time, threading, json, base64
from flask import Flask, request, jsonify, session, redirect, url_for, send_from_directory, render_template
from flask_cors import CORS
from pywebpush import webpush, WebPushException
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
CORS(app)

# 🔒 Безопасное хранение паролей (можно задавать через переменные окружения)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'HIFI_STABLE_V10')
USER_PASSWORD = os.getenv('CHAT_PASSWORD', '123')

# 🛡 Токен защиты от подделки системных сообщений хакерами
SYSTEM_BROADCAST_TOKEN = "SUPER_SECRET_GOD_TOKEN_999"

socketio = SocketIO(app, cors_allowed_origins="*")

# 🛡 ЗАЩИТА ОТ БЛОКИРОВОК CLOUDFLARE И NGROK
REQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "ngrok-skip-browser-warning": "true",
    "Bypass-Tunnel-Reminder": "true"
}

UPLOAD_FOLDER = 'uploads'
DB_DIR = 'db_data'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)

DB_PATH = os.path.join(DB_DIR, 'messages.db')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 

VAPID_PRIVATE_PEM = os.path.join(DB_DIR, "vapid_private.pem")
VAPID_PUBLIC_TXT = os.path.join(DB_DIR, "vapid_public.txt")
VAPID_PUBLIC_KEY = ""

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
    VAPID_PUBLIC_KEY = f.read().strip()

def cleanup_old_files():
    while True:
        now = time.time()
        for filename in os.listdir(UPLOAD_FOLDER):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(filepath) and os.stat(filepath).st_mtime < now - 604800:
                try: os.remove(filepath)
                except Exception: pass
        time.sleep(86400)

threading.Thread(target=cleanup_old_files, daemon=True).start()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS msgs (chat_with TEXT, sender TEXT, content TEXT, timestamp TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS contacts (name TEXT, ip TEXT, secret_key TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS mailbox (target_id TEXT, sender_id TEXT, content TEXT, timestamp TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS tracker (username TEXT PRIMARY KEY, current_url TEXT, last_seen TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS push_subs (username TEXT PRIMARY KEY, sub_json TEXT)')
    conn.commit()
    conn.close()

@socketio.on('join')
def on_join(data):
    username = data.get('username')
    if username: join_room(username)

@app.route('/sw.js')
def serve_sw(): return app.send_static_file('sw.js')

@app.route('/')
def index(): 
    return render_template('index.html', logged_in=session.get('auth'), vapid_public_key=VAPID_PUBLIC_KEY)

@app.route('/uploads/<path:filename>')
def download_file(filename): return send_from_directory(UPLOAD_FOLDER, filename)

# 🔄 ЭТАП 1: Оптимизированная и защищенная загрузка медиа (Бинарный поток FormData)
@app.route('/api/upload', methods=['POST'])
def upload_file():
    if not session.get('auth'): 
        return jsonify({"error": "Unauthorized"}), 403
    
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    filename = str(uuid.uuid4()) + ".enc"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    return jsonify({"url": filename})

@app.route('/login', methods=['POST'])
def login():
    if request.form.get('password') == USER_PASSWORD: session['auth'] = True
    return redirect(url_for('index'))

@app.route('/api/tracker/update', methods=['POST'])
def update_tracker():
    data = request.json
    username = data.get('username')
    url = data.get('url').replace('https://','').replace('http://','').strip('/')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO tracker (username, current_url, last_seen) VALUES (?, ?, ?)", (username, url, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return jsonify({"status": "updated"})

@app.route('/api/tracker/get', methods=['GET'])
def get_tracker():
    username = request.args.get('username')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT current_url FROM tracker WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    if row: return jsonify({"url": row[0]})
    return jsonify({"url": None, "status": "offline"}), 200

@app.route('/api/push/subscribe', methods=['POST'])
def push_subscribe():
    data = request.json
    username = data.get('username')
    sub_json = json.dumps(data.get('subscription'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO push_subs (username, sub_json) VALUES (?, ?)", (username, sub_json))
    conn.commit()
    conn.close()
    return jsonify({"status": "subscribed"})

def send_push_notification(target_username, sender_username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT sub_json FROM push_subs WHERE username = ?", (target_username,))
    row = c.fetchone()
    conn.close()
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
        except Exception: pass

# 🔄 ЭТАП 2: Асинхронный и защищенный Режим Бога (Gossip Protocol)
@app.route('/api/godmode', methods=['POST'])
def api_godmode():
    if not session.get('auth'): return "No Auth", 403
    data = request.json
    if data.get('password') != '777': return "Bad Password", 403
    content = data.get('content')
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    urls = set()
    c.execute("SELECT current_url FROM tracker WHERE current_url IS NOT NULL")
    for row in c.fetchall(): urls.add(row[0])
    c.execute("SELECT ip FROM contacts WHERE ip IS NOT NULL AND ip != ''")
    for row in c.fetchall(): urls.add(row[0])
    conn.close()
    
    def broadcast_to_all(target_urls, msg_text):
        for target_url in target_urls:
            try:
                payload = {"sender_username": "📢 SYSTEM", "target": "", "content": msg_text, "sys_token": SYSTEM_BROADCAST_TOKEN}
                try: requests.post(f"https://{target_url}/receive", json=payload, headers=REQ_HEADERS, timeout=3)
                except: requests.post(f"http://{target_url}/receive", json=payload, headers=REQ_HEADERS, timeout=3)
            except: pass

    threading.Thread(target=broadcast_to_all, args=(urls, content), daemon=True).start()
    return jsonify({"status": "Broadcast started in background"})

@app.route('/receive', methods=['POST'])
def receive():
    data = request.json
    raw_sender = data.get('sender_username') or data.get('sender', '').split(':')[0]
    target = data.get('target')
    content = data.get('content')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    is_new_system_msg = False
    
    if raw_sender == "📢 SYSTEM":
        # Проверяем токен! Защита от подделок
        if data.get('sys_token') != SYSTEM_BROADCAST_TOKEN:
            conn.close()
            return jsonify({"error": "Security Breach: Invalid System Token"}), 403

        # 🦠 АНТИ-ШТОРМ: Проверка на дубликат вирусного сообщения
        c.execute("SELECT 1 FROM msgs WHERE chat_with = '📢 SYSTEM' AND content = ?", (content,))
        if c.fetchone():
            conn.close()
            return jsonify({"status": "already_know"}), 200
            
        c.execute("SELECT name FROM contacts WHERE name = '📢 SYSTEM'")
        if not c.fetchone():
            c.execute("INSERT INTO contacts VALUES (?, ?, ?)", ("📢 SYSTEM", "127.0.0.1", "SYSTEM_KEY"))
        real_friend_name = "📢 SYSTEM"
        is_new_system_msg = True
    else:
        c.execute("SELECT name FROM contacts WHERE name = ? OR ip = ?", (raw_sender, raw_sender))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": f"Anti-Spam: Unknown sender '{raw_sender}'"}), 403
        real_friend_name = row[0]

    c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (real_friend_name, real_friend_name, content, datetime.datetime.now().strftime("%H:%M")))
    conn.commit()
    
    if raw_sender == "📢 SYSTEM":
        c.execute("SELECT username FROM push_subs")
        for (usr,) in c.fetchall(): send_push_notification(usr, "📢 SYSTEM")
        
        # 🦠 GOSSIP PROTOCOL: Пересылаем дальше друзьям асинхронно
        if is_new_system_msg:
            c.execute("SELECT ip FROM contacts WHERE ip IS NOT NULL AND ip != '' AND name != '📢 SYSTEM'")
            friends = c.fetchall()
            def spread_virus(friend_ips, msg_text):
                payload = {"sender_username": "📢 SYSTEM", "target": "", "content": msg_text, "sys_token": SYSTEM_BROADCAST_TOKEN}
                for (ip,) in friend_ips:
                    try: requests.post(f"https://{ip}/receive", json=payload, headers=REQ_HEADERS, timeout=3)
                    except:
                        try: requests.post(f"http://{ip}/receive", json=payload, headers=REQ_HEADERS, timeout=3)
                        except: pass
            threading.Thread(target=spread_virus, args=(friends, content), daemon=True).start()
    else:
        if target: send_push_notification(target, real_friend_name)
        
    conn.close()
    
    if target: socketio.emit('new_message', {'status': 'new'}, room=target)
    else: socketio.emit('new_message', {'status': 'new'})
    return jsonify({"status": "delivered"}), 200

# 🔄 ЭТАП 2: Ускоренная отправка + Фолбэк на HTTP для локального тестирования
@app.route('/send_message', methods=['POST'])
def send():
    if not session.get('auth'): return "No Auth", 403
    data = request.json
    target = data.get('target_ip').replace('https://','').replace('http://','').strip('/')
    target_username = data.get('target_username')
    content = data.get('content')
    my_username = data.get('my_id') 
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (target_username, "Me", content, datetime.datetime.now().strftime("%H:%M")))
    conn.commit()
    conn.close()
    
    try:
        # Пробуем HTTPS
        url_https = f"https://{target}/receive"
        payload = {"sender": my_username, "sender_username": my_username, "target": target_username, "content": content}
        try:
            resp = requests.post(url_https, json=payload, headers=REQ_HEADERS, timeout=3)
            if resp.status_code == 200: return "OK"
            raise Exception("HTTPS Failed")
        except:
            # Фолбэк на HTTP (важно для localhost тестирования!)
            url_http = f"http://{target}/receive"
            resp = requests.post(url_http, json=payload, headers=REQ_HEADERS, timeout=3)
            if resp.status_code == 200: return "OK"
            raise Exception("HTTP Failed")
    except:
        # Сохраняем в Mailbox только если оба протокола не ответили
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO mailbox VALUES (?, ?, ?, ?)", (target, my_username, content, datetime.datetime.now().strftime("%H:%M")))
        conn.commit()
        conn.close()
        return "Relayed"

@app.route('/api/contacts', methods=['GET', 'POST', 'DELETE'])
def manage_contacts():
    if not session.get('auth'): return jsonify({"error": "No Auth"}), 403
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if request.method == 'POST':
        d = request.json
        clean_ip = d['ip'].replace('https://','').replace('http://','').strip('/')
        c.execute("INSERT INTO contacts VALUES (?, ?, ?)", (d['name'], clean_ip, d['key']))
        conn.commit()
    elif request.method == 'DELETE':
        d = request.json
        c.execute("DELETE FROM contacts WHERE name = ?", (d['name'],))
        conn.commit()
    c.execute("SELECT * FROM contacts")
    res = c.fetchall()
    conn.close()
    return jsonify(res)

@app.route('/api/messages')
def get_messages():
    chat_with = request.args.get('chat_with')
    secret = request.args.get('secret') 
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if chat_with and secret:
        c.execute("SELECT name FROM contacts WHERE name = ? AND secret_key = ?", (chat_with, secret))
        if c.fetchone():
            c.execute("SELECT sender, content, timestamp FROM msgs WHERE chat_with = ? ORDER BY timestamp ASC", (chat_with,))
            messages = c.fetchall()
            conn.close()
            return jsonify(messages)
        else:
            conn.close()
            return jsonify([]) 

    if session.get('auth'):
        if not chat_with: 
            conn.close()
            return jsonify([])
        c.execute("SELECT sender, content, timestamp FROM msgs WHERE chat_with = ? ORDER BY timestamp ASC", (chat_with,))
        messages = c.fetchall()
        conn.close()
        return jsonify(messages)
        
    conn.close()
    return jsonify([])

@app.route('/api/mailbox/check')
def check_mailbox():
    tid = request.args.get('target_id')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT sender_id, content, timestamp FROM mailbox WHERE target_id = ?", (tid,))
    rows = c.fetchall()
    if rows:
        c.execute("DELETE FROM mailbox WHERE target_id = ?", (tid,))
        conn.commit()
    conn.close()
    return jsonify({"received": len(rows), "messages": rows})

@app.route('/api/messages/save_synced', methods=['POST'])
def save_synced():
    if not session.get('auth'): return "No Auth", 403
    data = request.json.get('messages', [])
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for m in data: c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (m[0], m[0], m[1], m[2]))
    conn.commit()
    conn.close()
    return "OK"

@app.route('/api/restore', methods=['POST'])
def api_restore():
    if not session.get('auth'): return "No Auth", 403
    data = request.json
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for ct in data.get('contacts', []):
        c.execute("SELECT 1 FROM contacts WHERE name=?", (ct[0],))
        if not c.fetchone(): c.execute("INSERT INTO contacts VALUES (?, ?, ?)", (ct[0], ct[1], ct[2]))
    for m in data.get('messages', []):
        c.execute("SELECT 1 FROM msgs WHERE chat_with=? AND content=? AND timestamp=?", (m[0], m[2], m[3]))
        if not c.fetchone(): c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (m[0], m[1], m[2], m[3]))
    conn.commit()
    conn.close()
    return jsonify({"status": "restored"})

# 🔄 ЭТАП 2: Асинхронный Тайпинг (Не тормозит UI)
@app.route('/api/typing', methods=['POST'])
def api_typing():
    data = request.json
    target = data.get('target_ip').replace('https://','').replace('http://','').strip('/')
    
    def send_typing():
        try:
            payload = {"sender_username": data.get('my_id'), "target": data.get('target_username'), "status_type": data.get('status_type', 'typing')}
            try: requests.post(f"https://{target}/receive_typing", json=payload, headers=REQ_HEADERS, timeout=2)
            except: requests.post(f"http://{target}/receive_typing", json=payload, headers=REQ_HEADERS, timeout=2)
        except: pass

    threading.Thread(target=send_typing, daemon=True).start()
    return "OK"

@app.route('/receive_typing', methods=['POST'])
def receive_typing():
    data = request.json
    sender = data.get('sender_username')
    target = data.get('target')
    status_type = data.get('status_type', 'typing')
    socketio.emit('user_typing', {'sender': sender, 'status_type': status_type}, room=target)
    return "OK"

if __name__ == '__main__':
    init_db()
    socketio.run(app, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)