import uuid, requests, datetime, threading
from flask import Flask, request, jsonify, session, redirect, url_for, send_from_directory, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, join_room

# Импортируем наши новые модули
import config
from database import init_db, query_db
from utils import ensure_vapid_keys, send_push_notification, cleanup_old_files_task

app = Flask(__name__)
CORS(app)
app.secret_key = config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH 
socketio = SocketIO(app, cors_allowed_origins="*")

# Инициализация БД и ключей
init_db()
VAPID_PUBLIC_KEY = ensure_vapid_keys()

# Запуск фоновой очистки файлов
threading.Thread(target=cleanup_old_files_task, daemon=True).start()


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
def download_file(filename): return send_from_directory(config.UPLOAD_FOLDER, filename)

@app.route('/login', methods=['POST'])
def login():
    if request.form.get('password') == config.USER_PASSWORD: session['auth'] = True
    return redirect(url_for('index'))

@app.route('/api/upload', methods=['POST'])
def upload_file():
    if not session.get('auth'): return jsonify({"error": "Unauthorized"}), 403
    if 'file' not in request.files or request.files['file'].filename == '': return jsonify({"error": "No file"}), 400

    filename = str(uuid.uuid4()) + ".enc"
    filepath = f"{config.UPLOAD_FOLDER}/{filename}"
    request.files['file'].save(filepath)
    return jsonify({"url": filename})

@app.route('/api/tracker/update', methods=['POST'])
def update_tracker():
    data = request.json
    url = data.get('url').replace('https://','').replace('http://','').strip('/')
    query_db("REPLACE INTO tracker (username, current_url, last_seen) VALUES (?, ?, ?)", 
             (data.get('username'), url, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")), commit=True)
    return jsonify({"status": "updated"})

@app.route('/api/tracker/get', methods=['GET'])
def get_tracker():
    row = query_db("SELECT current_url FROM tracker WHERE username = ?", (request.args.get('username'),), fetchone=True)
    if row: return jsonify({"url": row[0]})
    return jsonify({"url": None, "status": "offline"}), 200

@app.route('/api/push/subscribe', methods=['POST'])
def push_subscribe():
    import json
    data = request.json
    query_db("REPLACE INTO push_subs (username, sub_json) VALUES (?, ?)", 
             (data.get('username'), json.dumps(data.get('subscription'))), commit=True)
    return jsonify({"status": "subscribed"})

@app.route('/api/contacts', methods=['GET', 'POST', 'DELETE'])
def manage_contacts():
    if not session.get('auth'): return jsonify({"error": "No Auth"}), 403
    if request.method == 'POST':
        d = request.json
        clean_ip = d['ip'].replace('https://','').replace('http://','').strip('/')
        query_db("INSERT INTO contacts VALUES (?, ?, ?)", (d['name'], clean_ip, d['key']), commit=True)
    elif request.method == 'DELETE':
        query_db("DELETE FROM contacts WHERE name = ?", (request.json['name'],), commit=True)
        
    res = query_db("SELECT * FROM contacts", fetchall=True)
    return jsonify(res)

@app.route('/api/messages')
def get_messages():
    chat_with = request.args.get('chat_with')
    secret = request.args.get('secret') 

    if chat_with and secret:
        if query_db("SELECT name FROM contacts WHERE name = ? AND secret_key = ?", (chat_with, secret), fetchone=True):
            return jsonify(query_db("SELECT sender, content, timestamp FROM msgs WHERE chat_with = ? ORDER BY rowid ASC", (chat_with,), fetchall=True))
        return jsonify([]) 

    if session.get('auth') and chat_with:
        return jsonify(query_db("SELECT sender, content, timestamp FROM msgs WHERE chat_with = ? ORDER BY rowid ASC", (chat_with,), fetchall=True))
        
    return jsonify([])

@app.route('/api/mailbox/check')
def check_mailbox():
    tid = request.args.get('target_id')
    rows = query_db("SELECT sender_id, content, timestamp FROM mailbox WHERE target_id = ?", (tid,), fetchall=True)
    if rows:
        query_db("DELETE FROM mailbox WHERE target_id = ?", (tid,), commit=True)
    return jsonify({"received": len(rows), "messages": rows})

@app.route('/api/messages/save_synced', methods=['POST'])
def save_synced():
    if not session.get('auth'): return "No Auth", 403
    for m in request.json.get('messages', []): 
        query_db("INSERT INTO msgs VALUES (?, ?, ?, ?)", (m[0], m[0], m[1], m[2]), commit=True)
    return "OK"

@app.route('/api/restore', methods=['POST'])
def api_restore():
    if not session.get('auth'): return "No Auth", 403
    data = request.json
    for ct in data.get('contacts', []):
        if not query_db("SELECT 1 FROM contacts WHERE name=?", (ct[0],), fetchone=True): 
            query_db("INSERT INTO contacts VALUES (?, ?, ?)", (ct[0], ct[1], ct[2]), commit=True)
    for m in data.get('messages', []):
        if not query_db("SELECT 1 FROM msgs WHERE chat_with=? AND content=? AND timestamp=?", (m[0], m[2], m[3]), fetchone=True): 
            query_db("INSERT INTO msgs VALUES (?, ?, ?, ?)", (m[0], m[1], m[2], m[3]), commit=True)
    return jsonify({"status": "restored"})

@app.route('/receive', methods=['POST'])
def receive():
    data = request.json
    raw_sender = data.get('sender_username') or data.get('sender', '').split(':')[0]
    target = data.get('target')
    content = data.get('content')
    
    # Защита от эха (Loopback)
    if query_db("SELECT 1 FROM msgs WHERE content = ?", (content,), fetchone=True):
        return jsonify({"status": "local_loopback_ignored"}), 200

    is_new_system_msg = False
    if raw_sender == "📢 SYSTEM":
        if data.get('sys_token') != config.SYSTEM_BROADCAST_TOKEN:
            return jsonify({"error": "Security Breach"}), 403
        if query_db("SELECT 1 FROM msgs WHERE chat_with = '📢 SYSTEM' AND content = ?", (content,), fetchone=True):
            return jsonify({"status": "already_know"}), 200
            
        if not query_db("SELECT name FROM contacts WHERE name = '📢 SYSTEM'", fetchone=True):
            query_db("INSERT INTO contacts VALUES (?, ?, ?)", ("📢 SYSTEM", "127.0.0.1", "SYSTEM_KEY"), commit=True)
        real_friend_name = "📢 SYSTEM"
        is_new_system_msg = True
    else:
        row = query_db("SELECT name FROM contacts WHERE name = ? OR ip = ?", (raw_sender, raw_sender), fetchone=True)
        if not row: return jsonify({"error": "Unknown sender"}), 403
        real_friend_name = row[0]

    query_db("INSERT INTO msgs VALUES (?, ?, ?, ?)", (real_friend_name, real_friend_name, content, datetime.datetime.utcnow().isoformat() + "Z"), commit=True)
    
    if raw_sender == "📢 SYSTEM":
        subs = query_db("SELECT username FROM push_subs", fetchall=True)
        for (usr,) in subs: send_push_notification(usr, "📢 SYSTEM")
        
        if is_new_system_msg:
            friends = query_db("SELECT ip FROM contacts WHERE ip IS NOT NULL AND ip != '' AND name != '📢 SYSTEM'", fetchall=True)
            def spread_virus(friend_ips, msg_text):
                payload = {"sender_username": "📢 SYSTEM", "target": "", "content": msg_text, "sys_token": config.SYSTEM_BROADCAST_TOKEN}
                for (ip,) in friend_ips:
                    try: requests.post(f"https://{ip}/receive", json=payload, headers=config.REQ_HEADERS, timeout=3)
                    except:
                        try: requests.post(f"http://{ip}/receive", json=payload, headers=config.REQ_HEADERS, timeout=3)
                        except: pass
            threading.Thread(target=spread_virus, args=(friends, content), daemon=True).start()
    else:
        if target: send_push_notification(target, real_friend_name)
        
    if target: socketio.emit('new_message', {'status': 'new', 'sender': real_friend_name}, room=target)
    else: socketio.emit('new_message', {'status': 'new', 'sender': real_friend_name})
    return jsonify({"status": "delivered"}), 200

@app.route('/send_message', methods=['POST'])
def send():
    if not session.get('auth'): return "No Auth", 403
    data = request.json
    target = data.get('target_ip').replace('https://','').replace('http://','').strip('/')
    query_db("INSERT INTO msgs VALUES (?, ?, ?, ?)", (data.get('target_username'), "Me", data.get('content'), datetime.datetime.utcnow().isoformat() + "Z"), commit=True)
    
    try:
        payload = {"sender": data.get('my_id'), "sender_username": data.get('my_id'), "target": data.get('target_username'), "content": data.get('content')}
        try:
            if requests.post(f"https://{target}/receive", json=payload, headers=config.REQ_HEADERS, timeout=3).status_code == 200: return "OK"
            raise Exception()
        except:
            if requests.post(f"http://{target}/receive", json=payload, headers=config.REQ_HEADERS, timeout=3).status_code == 200: return "OK"
            raise Exception()
    except:
        query_db("INSERT INTO mailbox VALUES (?, ?, ?, ?)", (target, data.get('my_id'), data.get('content'), datetime.datetime.utcnow().isoformat() + "Z"), commit=True)
        return "Relayed"

@app.route('/api/godmode', methods=['POST'])
def api_godmode():
    if not session.get('auth') or request.json.get('password') != config.GODMODE_PASSWORD: return "Denied", 403
    content = request.json.get('content')
    
    if not query_db("SELECT name FROM contacts WHERE name = '📢 SYSTEM'", fetchone=True):
        query_db("INSERT INTO contacts VALUES (?, ?, ?)", ("📢 SYSTEM", "127.0.0.1", "SYSTEM_KEY"), commit=True)
    query_db("INSERT INTO msgs VALUES (?, ?, ?, ?)", ("📢 SYSTEM", "📢 SYSTEM", content, datetime.datetime.utcnow().isoformat() + "Z"), commit=True)
    socketio.emit('new_message', {'status': 'new', 'sender': '📢 SYSTEM'})
    
    urls = set(row[0] for row in query_db("SELECT current_url FROM tracker WHERE current_url IS NOT NULL", fetchall=True))
    urls.update(row[0] for row in query_db("SELECT ip FROM contacts WHERE ip IS NOT NULL AND ip != ''", fetchall=True))
    
    def broadcast_to_all(target_urls, msg_text):
        for url in target_urls:
            payload = {"sender_username": "📢 SYSTEM", "target": "", "content": msg_text, "sys_token": config.SYSTEM_BROADCAST_TOKEN}
            try: requests.post(f"https://{url}/receive", json=payload, headers=config.REQ_HEADERS, timeout=3)
            except: 
                try: requests.post(f"http://{url}/receive", json=payload, headers=config.REQ_HEADERS, timeout=3)
                except: pass
    threading.Thread(target=broadcast_to_all, args=(urls, content), daemon=True).start()
    return jsonify({"status": "Broadcast started"})

@app.route('/api/typing', methods=['POST'])
def api_typing():
    data = request.json
    target = data.get('target_ip').replace('https://','').replace('http://','').strip('/')
    def send_typing():
        payload = {"sender_username": data.get('my_id'), "target": data.get('target_username'), "status_type": data.get('status_type', 'typing')}
        try: requests.post(f"https://{target}/receive_typing", json=payload, headers=config.REQ_HEADERS, timeout=2)
        except: 
            try: requests.post(f"http://{target}/receive_typing", json=payload, headers=config.REQ_HEADERS, timeout=2)
            except: pass
    threading.Thread(target=send_typing, daemon=True).start()
    return "OK"

@app.route('/receive_typing', methods=['POST'])
def receive_typing():
    socketio.emit('user_typing', {'sender': request.json.get('sender_username'), 'status_type': request.json.get('status_type', 'typing')}, room=request.json.get('target'))
    return "OK"

# --- WEBRTC СИГНАЛЬНЫЙ СЕРВЕР (ЗВОНКИ) ---
@socketio.on('webrtc_signal')
def handle_webrtc_signal(data):
    target_username = data.get('target')
    sender = data.get('sender')
    signal_type = data.get('type')
    
    # Пишем чистый лог
    print(f"📞 [WebRTC] Сигнал '{signal_type}' от {sender} для {target_username}")
    
    # Отправляем ТОЛЬКО в нужную комнату (никаких броадкастов!)
    if target_username:
        socketio.emit('webrtc_signal', data, room=target_username)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)