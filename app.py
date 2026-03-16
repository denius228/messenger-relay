import sys, os, sqlite3, datetime, uuid, requests
from flask import Flask, request, jsonify, session, redirect, url_for, send_from_directory, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
app.secret_key = 'HIFI_STABLE_V10'
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 30 * 1024 * 1024 # Лимит 30MB

USER_PASSWORD = "123" 

def init_db():
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    # ИЗМЕНЕНИЕ 1: Добавили столбец chat_with для изоляции переписок
    c.execute('CREATE TABLE IF NOT EXISTS msgs (chat_with TEXT, sender TEXT, content TEXT, timestamp TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS contacts (name TEXT, ip TEXT, secret_key TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS mailbox (target_id TEXT, sender_id TEXT, content TEXT, timestamp TEXT)')
    conn.commit()
    conn.close()

@app.route('/')
def index():
    # ИЗМЕНЕНИЕ 2: Теперь мы грузим HTML из отдельного файла
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
    if request.form.get('password') == USER_PASSWORD: 
        session['auth'] = True
    return redirect(url_for('index'))

@app.route('/api/contacts', methods=['GET', 'POST'])
def manage_contacts():
    if not session.get('auth'): return jsonify([])
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    if request.method == 'POST':
        d = request.json
        # Очищаем IP от лишнего при сохранении
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
    chat_with = request.args.get('chat_with') # ИЗМЕНЕНИЕ 3: Запрашиваем сообщения только с конкретным контактом
    if not chat_with: return jsonify([])
    
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    c.execute("SELECT sender, content, timestamp FROM msgs WHERE chat_with = ? ORDER BY timestamp ASC", (chat_with,))
    messages = c.fetchall()
    conn.close()
    return jsonify(messages)

@app.route('/api/mailbox/check')
def check_mailbox():
    # ИЗМЕНЕНИЕ 4: Сервер друга теперь НЕ забирает твои письма себе в базу. Он просто их отдает и удаляет.
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
    # ИЗМЕНЕНИЕ 5: Новый эндпоинт. Твой клиент сохраняет письма, полученные из чужого ящика, в ТВОЮ базу.
    if not session.get('auth'): return "No Auth", 403
    data = request.json.get('messages', [])
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    for m in data: # m = [sender_id, content, timestamp]
        c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (m[0], m[0], m[1], m[2]))
    conn.commit()
    conn.close()
    return "OK"

@app.route('/receive', methods=['POST'])
def receive():
    data = request.json
    sender = data.get('sender')
    content = data.get('content')
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    # Записываем: с кем чат (sender), кто отправил (sender), контент, время
    c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (sender, sender, content, datetime.datetime.now().strftime("%H:%M")))
    conn.commit()
    conn.close()
    return jsonify({"status": "delivered"}), 200

@app.route('/send_message', methods=['POST'])
def send():
    if not session.get('auth'): return "No Auth", 403
    target = request.form.get('target_ip').replace('https://','').replace('http://','').strip('/')
    content = request.form.get('content')
    my_id = request.host
    
    conn = sqlite3.connect('messages.db')
    c = conn.cursor()
    # Записываем: с кем чат (target), кто отправил ("Me"), контент, время
    c.execute("INSERT INTO msgs VALUES (?, ?, ?, ?)", (target, "Me", content, datetime.datetime.now().strftime("%H:%M")))
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
    app.run(host='0.0.0.0', port=5000)