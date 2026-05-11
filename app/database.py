import sqlite3
import os
# МОДУЛЬ: СИСТЕМА УПРАВЛЕНИЯ БАЗОЙ ДАННЫХ

# Путь к базе данных определяется динамически относительно расположения файла
DB_PATH = os.path.join(os.path.dirname(__file__), "bot_database.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Регистрация пользователей системы (администраторов, операторов)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')

    # Формирование справочника категорий
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT
        )
    ''')
    
    # Основной реестр правил ответов бота
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            keyword TEXT NOT NULL,
            answer TEXT NOT NULL,
            category TEXT DEFAULT 'Общее',
            buttons TEXT DEFAULT '',
            use_count INTEGER DEFAULT 0,
            FOREIGN KEY (owner_id) REFERENCES users (id)
        )
    ''')
    
    # Мониторинг состояний активных чатов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_states (
            vk_user_id INTEGER PRIMARY KEY,
            is_operator_mode INTEGER DEFAULT 0
        )
    ''')
    
    # Истории сообщений
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS message_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vk_user_id INTEGER,
            user_text TEXT,
            bot_answer TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    try:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        conn.commit()
        print("Колонка email успешно добавлена в таблицу users")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE chat_states ADD COLUMN is_archived INTEGER DEFAULT 0")
        conn.commit()
        print("Колонка is_archived успешно добавлена")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE chat_states ADD COLUMN current_category TEXT")
        conn.commit()
        print("Колонка current_category добавлена")
    except: pass
    
    conn.close()

# УПРАВЛЕНИЕ УЧЕТНЫМИ ЗАПИСЯМИ ПОЛЬЗОВАТЕЛЕЙ

# Создание новой учетной записи
def create_user(username, password):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
        conn.commit()
        return cursor.lastrowid
    except: return None
    finally: conn.close()

# Поиск данных профиля по логину и почте
def get_user_by_login_or_email(login_data):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? OR email = ?", (login_data, login_data))
    user = cursor.fetchone()
    conn.close()
    return user

def get_user_by_username(username):
    return get_user_by_login_or_email(username)

# УПРАВЛЕНИЕ ПРАВИЛАМИ (ВОПРОС + ОТВЕТ + КАТЕГОРИЯ ВОПРОСА)

# Создание нового правила
def add_rule_to_db(owner_id, keyword, answer, category="Общее", buttons=""):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO bot_rules (owner_id, keyword, answer, category, buttons) VALUES (?, ?, ?, ?, ?)",
            (owner_id, keyword, answer, category, buttons)
        )
        conn.commit()
        return True
    except: return False
    finally: conn.close()

# Выгрузка полного списка правил
def get_all_rules(owner_id, search=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if search: # Поиск по ключевому слову или фильтрафция по категории
        cursor.execute(
            "SELECT * FROM bot_rules WHERE owner_id = ? AND (keyword LIKE ? OR category LIKE ?)", 
            (owner_id, f'%{search}%', f'%{search}%')
        )
    else:
        cursor.execute("SELECT * FROM bot_rules WHERE owner_id = ?", (owner_id,))
    rules = cursor.fetchall()
    conn.close()
    return rules

# Редактирование существующего правила
def update_rule_in_db(rule_id, owner_id, keyword, answer, category):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE bot_rules SET keyword = ?, answer = ?, category = ? WHERE id = ? AND owner_id = ?",
        (keyword, answer, category, rule_id, owner_id)
    )
    conn.commit()
    conn.close()

# Удаление правила из базы данных
def delete_rule_from_db(rule_id, owner_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bot_rules WHERE id = ? AND owner_id = ?", (rule_id, owner_id))
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success

# УПРАВЛЕНИЕ КАТЕГОРИЯМИ

# Создание новой категории
def add_category_to_db(name, description=""):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO categories (name, description) VALUES (?, ?)", (name, description))
        conn.commit()
        return True
    except: return False
    finally: conn.close()

# Выгрузка полного списка категорий
def get_all_categories():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM categories")
    res = cursor.fetchall()
    conn.close()
    return res

# Выборка всех вопросов, относящихся к выбранной категории
def get_rules_by_category(category_name):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bot_rules WHERE category = ?", (category_name,))
    rules = cursor.fetchall()
    conn.close()
    return rules

# Удаление категории (правила из категории перенусутся в "Общее")
def delete_category_by_name(name):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM categories WHERE name = ?", (name,))
        cursor.execute("UPDATE bot_rules SET category = 'Общее' WHERE category = ?", (name,))
        conn.commit()
    finally:
        conn.close()

# РАБОТА БОТА И ОПЕРАТОРА

# Поиск ответа в базе данных
def get_answer_from_db(keyword):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, answer, buttons FROM bot_rules WHERE keyword = ?", (keyword,))
    res = cursor.fetchone()
    if res:
        cursor.execute("UPDATE bot_rules SET use_count = use_count + 1 WHERE id = ?", (res['id'],))
        conn.commit()
    conn.close()
    return res

# Переключение режима (бот/оператор)
def set_operator_mode(vk_user_id, mode):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO chat_states (vk_user_id, is_operator_mode) VALUES (?, ?)", (vk_user_id, mode))
    conn.commit()
    conn.close()

# Получение статуса активности оператора
def get_chat_state(vk_user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_operator_mode FROM chat_states WHERE vk_user_id = ?", (vk_user_id,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else 0

# АНАЛИТИКА, ЛОГИ И АРХИВАЦИЯ

# Сохранение истории сообщений в журнал
def log_message(vk_id, text, answer):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO message_logs (vk_user_id, user_text, bot_answer) VALUES (?, ?, ?)", (vk_id, text, answer))
    conn.commit()
    conn.close()

# Получение статистики по количеству сообщений
def get_analytics():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM message_logs")
    total = cursor.fetchone()[0]
    conn.close()
    return {"total_messages": total}

# Выборка топ-5 частых вопросов
def get_popular_rules(owner_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT keyword, use_count FROM bot_rules WHERE owner_id = ? ORDER BY use_count DESC LIMIT 5", 
        (owner_id,)
    )
    res = cursor.fetchall()
    conn.close()
    return res

# Архивация старых диалогов при достижении лимита
def archive_old_dialogs(limit=100):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM chat_states WHERE is_archived = 0")
    count = cursor.fetchone()[0]
    
    if count > limit:   # Архивация самого старого диалога, если превышен лимит
        query = """
            UPDATE chat_states 
            SET is_archived = 1 
            WHERE vk_user_id = (
                SELECT s.vk_user_id 
                FROM chat_states s
                JOIN (SELECT vk_user_id, MAX(timestamp) as last_msg FROM message_logs GROUP BY vk_user_id) m 
                ON s.vk_user_id = m.vk_user_id
                WHERE s.is_operator_mode = 0 AND s.is_archived = 0
                ORDER BY m.last_msg ASC
                LIMIT 1
            )
        """
        cursor.execute(query)
        conn.commit()
    conn.close()