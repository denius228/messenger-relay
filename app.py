import sys, os, sqlite3, datetime, uuid, requests, time, threading, json, base64
from flask import Flask, request, jsonify, session, redirect, url_for, send_from_directory, render_template
from flask_cors import CORS
from pywebpush import webpush, WebPushException
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

app = Flask(__name__)
CORS(app)
app.secret_key = 'HIFI_STABLE_V10'

# ==========================================
# НАСТРОЙКИ ПАПОК И ВЕЧНОГО ХРАНИЛИЩА DOCKER
# ==========================================
UPLOAD_FOLDER = 'uploads'
DB_DIR = 'db_data'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)

DB_PATH = os.path.join(DB_DIR, 'messages.db')
USER_PASSWORD = "123"
app.config['MAX_CONTENT_LENGTH'] = 30 * 1024 * 1024

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

@app.route('/sw.js')
def serve_sw(): return app.send_static_file('sw.js')

@app.route('/')
def index(): 
    return render_template('index.html', logged_in=session.get('auth'), vapid_public_key=VAPID_PUBLIC_KEY)

@app.route('/uploads/<path:filename>')
def download_file(filename): return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/api/upload', methods=['POST'])
def upload_file():
    data = request.json['data']
    filename = str(uuid.uuid4()) + ".enc"
    with open(os.path.join(UPLOAD_FOLDER, filename), 'w') as f: f.write(data)
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
    # ФИКС КРАСНЫХ ОШИБОК В КОНСОЛИ
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
            print(f"🔔 Push отправлен пользователю {target_username}!")
        except Exception as e:
            print("Push error:", e)
    else:
        # ДОБАВЬТЕ ЭТУ СТРОКУ, ЧТОБЫ ВИДЕТЬ ОШИБКУ В ТЕРМИНАЛЕ!
        print(f"⚠️ ПУШ ОТМЕНЕН: Юзер {target_username} не подписался (не нажал 🔔) или нет в БД!")

@app.route('/receive', methods=['POST'])
def receive():
    data = request.json
    sender = data.get('sender', '').split(':')[0]
    target = data.get('target')
    content = data.get('content')
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT ip FROM contacts WHERE name = ? OR ip = ?", (sender, sender))
    if not c.fetchone():
        conn.close()
        return jsonify({"error": f"Anti-Spam: Unknown sender {sender}"}), 403

    c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (sender, sender, content, datetime.datetime.now().strftime("%H:%M")))
    conn.commit()
    conn.close()
    
    if target: send_push_notification(target, sender)
        
    return jsonify({"status": "delivered"}), 200

@app.route('/send_message', methods=['POST'])
def send():
    if not session.get('auth'): return "No Auth", 403
    
    # ПЕРЕВЕЛИ НА ЧИСТЫЙ JSON
    data = request.json
    target = data.get('target_ip').replace('https://','').replace('http://','').strip('/')
    target_username = data.get('target_username')
    content = data.get('content')
    
    my_id = data.get('my_id')
    if not my_id: my_id = request.host
    my_id = my_id.replace('https://','').replace('http://','').split(':')[0]
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (target_username, "Me", content, datetime.datetime.now().strftime("%H:%M")))
    conn.commit()
    conn.close()
    
    try:
        url = f"https://{target}/receive"
        resp = requests.post(url, json={"sender": my_id, "target": target_username, "content": content}, timeout=10)
        if resp.status_code == 200: return "OK"
        raise Exception()
    except:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO mailbox VALUES (?, ?, ?, ?)", (target, my_id, content, datetime.datetime.now().strftime("%H:%M")))
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
    if not session.get('auth'): return jsonify([])
    chat_with = request.args.get('chat_with')
    if not chat_with: return jsonify([])
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT sender, content, timestamp FROM msgs WHERE chat_with = ? ORDER BY timestamp ASC", (chat_with,))
    messages = c.fetchall()
    conn.close()
    return jsonify(messages)

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
    for m in data:
        c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (m[0], m[0], m[1], m[2]))
    conn.commit()
    conn.close()
    return "OK"

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)