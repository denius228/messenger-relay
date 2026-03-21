import sqlite3
from config import DB_PATH

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

def query_db(query, args=(), fetchone=False, fetchall=False, commit=False):
    """Универсальная функция для выполнения SQL-запросов (сокращает код в 3 раза)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(query, args)
    
    res = None
    if fetchone:
        res = c.fetchone()
    elif fetchall:
        res = c.fetchall()
        
    if commit:
        conn.commit()
        
    conn.close()
    return res