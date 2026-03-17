import sys, os, sqlite3, datetime, uuid, requests, time, threading
from flask import Flask, request, jsonify, session, redirect, url_for, send_from_directory, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
app.secret_key = 'HIFI_STABLE_V10'
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 30 * 1024 * 1024

USER_PASSWORD = "123"

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
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS msgs (chat_with TEXT, sender TEXT, content TEXT, timestamp TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS contacts (name TEXT, ip TEXT, secret_key TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS mailbox (target_id TEXT, sender_id TEXT, content TEXT, timestamp TEXT)')
    
    # --- НОВАЯ ТАБЛИЦА: ТРЕКЕР (Маршрутизатор) ---
    c.execute('CREATE TABLE IF NOT EXISTS tracker (username TEXT PRIMARY KEY, current_url TEXT, last_seen TEXT)')
    # ---------------------------------------------
    conn.commit()
    conn.close()

@app.route('/')
def index():
    return render_template('index.html', logged_in=session.get('auth'))

@app.route('/uploads/<path:filename>')
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/api/upload', methods=['POST'])
def upload_file():
    data = request.json['data']
    filename = str(uuid.uuid4()) + ".enc"
    with open(os.path.join(UPLOAD_FOLDER, filename), 'w') as f:
        f.write(data)
    return jsonify({"url": f"https://{request.host}/uploads/{filename}"})

@app.route('/login', methods=['POST'])
def login():
    if request.form.get('password') == USER_PASSWORD: session['auth'] = True
    return redirect(url_for('index'))

# ==========================================
# НОВЫЕ ЭНДПОИНТЫ ДЛЯ МАРШРУТИЗАТОРА (TRACKER)
# ==========================================

@app.route('/api/tracker/update', methods=['POST'])
def update_tracker():
    """ Устройство сообщает трекеру свой новый Cloudflare URL """
    data = request.json
    username = data.get('username')
    url = data.get('url').replace('https://','').replace('http://','').strip('/')
    
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    c.execute("REPLACE INTO tracker (username, current_url, last_seen) VALUES (?, ?, ?)", 
              (username, url, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return jsonify({"status": "updated"})

@app.route('/api/tracker/get', methods=['GET'])
def get_tracker():
    """ Устройство спрашивает трекер: "Где сейчас этот username?" """
    username = request.args.get('username')
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    c.execute("SELECT current_url FROM tracker WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    if row: return jsonify({"url": row[0]})
    return jsonify({"error": "not found"}), 404

# ==========================================

@app.route('/api/contacts', methods=['GET', 'POST'])
def manage_contacts():
    if not session.get('auth'): return jsonify([])
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    if request.method == 'POST':
        d = request.json
        clean_ip = d['ip'].replace('https://','').replace('http://','').strip('/')
        c.execute("INSERT INTO contacts VALUES (?, ?, ?)", (d['name'], clean_ip, d['key']))
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
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    c.execute("SELECT sender, content, timestamp FROM msgs WHERE chat_with = ? ORDER BY timestamp ASC", (chat_with,))
    messages = c.fetchall()
    conn.close()
    return jsonify(messages)

@app.route('/api/mailbox/check')
def check_mailbox():
    tid = request.args.get('target_id')
    conn = sqlite3.connect('messages.db')
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
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    for m in data:
        c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (m[0], m[0], m[1], m[2]))
    conn.commit()
    conn.close()
    return "OK"

@app.route('/receive', methods=['POST'])
def receive():
    data = request.json
    sender = data.get('sender', '').split(':')[0]
    content = data.get('content')
    
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    c.execute("SELECT ip FROM contacts WHERE ip = ?", (sender,))
    if not c.fetchone():
        conn.close()
        print(f"❌ СПАМ БЛОК: {sender} попытался написать, но его нет в контактах!")
        return jsonify({"error": "Who are you?"}), 403

    print(f"✅ ПОЛУЧЕНО СООБЩЕНИЕ ОТ {sender}!")
    c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (sender, sender, content, datetime.datetime.now().strftime("%H:%M")))
    conn.commit()
    conn.close()
    return jsonify({"status": "delivered"}), 200

@app.route('/send_message', methods=['POST'])
def send():
    if not session.get('auth'): return "No Auth", 403
    target = request.form.get('target_ip').replace('https://','').replace('http://','').strip('/')
    target_username = request.form.get('target_username') # НОВОЕ: Берем никнейм друга
    content = request.form.get('content')
    
    my_id = request.form.get('my_id')
    if not my_id: my_id = request.host
    my_id = my_id.replace('https://','').replace('http://','').split(':')[0]
    
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    # НОВОЕ: Сохраняем переписку под никнеймом друга, а не под ссылкой
    c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (target_username, "Me", content, datetime.datetime.now().strftime("%H:%M")))
    conn.commit()
    conn.close()
    
    try:
        url = f"https://{target}/receive"
        resp = requests.post(url, json={"sender": my_id, "content": content}, timeout=10)
        if resp.status_code == 200: return "OK"
        raise Exception()
    except:
        conn = sqlite3.connect('messages.db')
        c = conn.cursor()
        c.execute("INSERT INTO mailbox VALUES (?, ?, ?, ?)", (target, my_id, content, datetime.datetime.now().strftime("%H:%M")))
        conn.commit()
        conn.close()
        return "Relayed"

if __name__ == '__main__':
    init_db()
    # Запускаем на всех интерфейсах (чтобы VPS был доступен по IP)
    app.run(host='0.0.0.0', port=5000)