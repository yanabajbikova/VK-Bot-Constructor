# Модуль работы с базой данных
import os
from contextlib import contextmanager
from typing import Optional, Any

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Получение адреса базы данных
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/chatbot_platform"
)

# Открытие соединения с базой данных
@contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# Добавление недостающего столбца
def _add_column_if_not_exists(cur, table: str, column: str, definition: str) -> None:
    # Выполнение изменения таблицы
    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")

# Инициализация структуры базы данных
def init_db():
    """Создает и безопасно дополняет таблицы PostgreSQL при запуске приложения."""
    with get_conn() as conn:
        cur = conn.cursor()

        # Создание таблицы пользователей
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE,
                password_hash TEXT NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'operator'
                    CHECK (role IN ('admin', 'operator')),
                created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Создание таблицы ботов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bots (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name VARCHAR(150) NOT NULL,
                token TEXT NOT NULL,
                group_link TEXT,
                group_id VARCHAR(100),
                confirmation_string TEXT,
                is_active BOOLEAN DEFAULT TRUE,

                greeting_message TEXT DEFAULT 'Привет, {first_name}! На связи бот поддержки компании "{group_name}". Напишите вопрос одним сообщением или выберите раздел:',
                fallback_message TEXT DEFAULT 'Напишите вопрос одним сообщением, и я постараюсь найти ответ!',
                operator_called_message TEXT DEFAULT 'Думаю, с этим вопросом лучше разберется оператор. Я уже позвал его.',
                operator_finished_message TEXT DEFAULT 'Оператор отключился. Я снова готов отвечать на Ваши вопросы!',
                answer_confirmation_message TEXT DEFAULT 'Я ответил на Ваш вопрос?',
                positive_close_message TEXT DEFAULT 'Рад был помочь! Если появятся новые вопросы — я всегда здесь.',
                restart_message TEXT DEFAULT 'Давайте попробуем сначала. Выберите подходящий раздел:',

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Создание таблицы доступа к ботам
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_users (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role VARCHAR(20) NOT NULL DEFAULT 'operator'
                    CHECK (role IN ('admin', 'operator')),
                UNIQUE(bot_id, user_id)
            )
        """)

        # Создание таблицы категорий
        cur.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                name VARCHAR(150) NOT NULL,
                description TEXT,
                UNIQUE(bot_id, name)
            )
        """)

        # Создание таблицы интентов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_intents (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                name VARCHAR(150) NOT NULL,
                examples TEXT,
                normalized_examples TEXT,
                response TEXT,
                use_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(bot_id, name)
            )
        """)

        # Создание таблицы базы знаний
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_rules (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                intent_id INTEGER REFERENCES user_intents(id) ON DELETE SET NULL,
                keyword TEXT NOT NULL,
                normalized_keyword TEXT,
                answer TEXT NOT NULL,
                category VARCHAR(150) DEFAULT 'Общее',
                buttons TEXT DEFAULT '',
                use_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Создание таблицы кнопок ответов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rule_buttons (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                rule_id INTEGER NOT NULL REFERENCES bot_rules(id) ON DELETE CASCADE,
                label VARCHAR(120) NOT NULL,
                action VARCHAR(30) NOT NULL DEFAULT 'finish'
                    CHECK (action IN ('go_rule', 'go_category', 'operator', 'restart', 'finish')),
                target_rule_id INTEGER REFERENCES bot_rules(id) ON DELETE SET NULL,
                target_category VARCHAR(150),
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Создание таблицы состояний диалогов
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_states (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                messenger_user_id VARCHAR(100) NOT NULL,
                is_operator_mode BOOLEAN DEFAULT FALSE,
                is_archived BOOLEAN DEFAULT FALSE,
                current_category VARCHAR(150),
                current_rule_id INTEGER,
                current_scenario_node_id INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(bot_id, messenger_user_id)
            )
        """)

        # Создание таблицы истории сообщений
        cur.execute("""
            CREATE TABLE IF NOT EXISTS message_logs (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                messenger_user_id VARCHAR(100) NOT NULL,
                user_text TEXT,
                bot_answer TEXT,
                answer_source VARCHAR(50) DEFAULT 'bot',
                operator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                confidence NUMERIC(5, 2),
                is_success BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Создание таблицы интеграций
        cur.execute("""
            CREATE TABLE IF NOT EXISTS integrations (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                integration_type VARCHAR(50) NOT NULL,
                settings JSONB DEFAULT '{}',
                is_enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(bot_id, integration_type)
            )
        """)

        # Создание таблицы блоков сценариев
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scenario_nodes (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                title VARCHAR(150) NOT NULL,
                node_type VARCHAR(30) NOT NULL DEFAULT 'message'
                    CHECK (node_type IN ('message', 'question', 'operator', 'end')),
                message TEXT DEFAULT '',
                is_start BOOLEAN DEFAULT FALSE,
                position_x INTEGER DEFAULT 80,
                position_y INTEGER DEFAULT 80,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Создание таблицы переходов сценариев
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scenario_edges (
                id SERIAL PRIMARY KEY,
                bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                from_node_id INTEGER NOT NULL REFERENCES scenario_nodes(id) ON DELETE CASCADE,
                to_node_id INTEGER NOT NULL REFERENCES scenario_nodes(id) ON DELETE CASCADE,
                label VARCHAR(150) NOT NULL,
                condition_text TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Добавление связи ответа с интентом
        _add_column_if_not_exists(cur, "bot_rules", "intent_id", "INTEGER REFERENCES user_intents(id) ON DELETE SET NULL")
        # Добавление нормализованного текста ответа
        _add_column_if_not_exists(cur, "bot_rules", "normalized_keyword", "TEXT")
        # Добавление даты обновления ответа
        _add_column_if_not_exists(cur, "bot_rules", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        # Добавление нормализованных примеров интента
        _add_column_if_not_exists(cur, "user_intents", "normalized_examples", "TEXT")
        # Добавление счетчика использования интента
        _add_column_if_not_exists(cur, "user_intents", "use_count", "INTEGER DEFAULT 0")
        # Добавление даты обновления интента
        _add_column_if_not_exists(cur, "user_intents", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        # Добавление текущего ответа в состояние
        _add_column_if_not_exists(cur, "chat_states", "current_rule_id", "INTEGER")
        # Добавление текущего блока сценария
        _add_column_if_not_exists(cur, "chat_states", "current_scenario_node_id", "INTEGER")
        # Добавление источника ответа в историю
        _add_column_if_not_exists(cur, "message_logs", "answer_source", "VARCHAR(50) DEFAULT 'bot'")
        # Добавление оценки успешности ответа
        _add_column_if_not_exists(cur, "message_logs", "is_success", "BOOLEAN")
        # Добавление даты создания интеграции
        _add_column_if_not_exists(cur, "integrations", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        # Добавление даты обновления интеграции
        _add_column_if_not_exists(cur, "integrations", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

        # Индекс для поиска ответов бота
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rules_bot_id ON bot_rules(bot_id)")
        # Индекс для фильтрации ответов по категории
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rules_category ON bot_rules(bot_id, category)")
        # Индекс для поиска кнопок ответа
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rule_buttons_rule_id ON rule_buttons(bot_id, rule_id)")
        # Индекс для поиска интентов бота
        cur.execute("CREATE INDEX IF NOT EXISTS idx_intents_bot_id ON user_intents(bot_id)")
        # Индекс для истории сообщений бота
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_bot_id ON message_logs(bot_id)")
        # Индекс для сортировки сообщений по дате
        cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_created_at ON message_logs(bot_id, created_at)")
        # Индекс для состояний диалогов
        cur.execute("CREATE INDEX IF NOT EXISTS idx_states_bot_id ON chat_states(bot_id)")
        # Индекс для интеграций бота
        cur.execute("CREATE INDEX IF NOT EXISTS idx_integrations_bot_id ON integrations(bot_id)")
        # Индекс для блоков сценария
        cur.execute("CREATE INDEX IF NOT EXISTS idx_scenario_nodes_bot_id ON scenario_nodes(bot_id)")
        # Индекс для переходов сценария
        cur.execute("CREATE INDEX IF NOT EXISTS idx_scenario_edges_bot_id ON scenario_edges(bot_id)")

# Создание учетной записи пользователя
def create_user(username: str, password_hash: str, email: Optional[str] = None,
                role: str = "operator", created_by: Optional[int] = None):
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            # Сохранение пользователя
            cur.execute(
                """
                INSERT INTO users (username, email, password_hash, role, created_by)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (username, email or None, password_hash, role, created_by)
            )
            return cur.fetchone()["id"]
        except psycopg2.Error:
            return None

# Поиск пользователя по логину или почте
def get_user_by_login_or_email(login_data: str):
    with get_conn() as conn:
        cur = conn.cursor()
        # Поиск учетной записи
        cur.execute(
            "SELECT * FROM users WHERE username = %s OR email = %s",
            (login_data, login_data)
        )
        return cur.fetchone()

# Получение пользователя по идентификатору
def get_user_by_id(user_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение пользователя по номеру
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        return cur.fetchone()

# Получение списка операторов администратора
def get_operators_for_admin(admin_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Выборка операторов администратора
        cur.execute(
            """
            SELECT id, username, email, role, created_at
            FROM users
            WHERE created_by = %s AND role = 'operator'
            ORDER BY created_at DESC
            """,
            (admin_id,)
        )
        return cur.fetchall()

# Получение данных оператора администратора
def get_operator_for_admin(operator_id: int, admin_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Выборка операторов администратора
        cur.execute(
            """
            SELECT id, username, email, role, created_at
            FROM users
            WHERE id = %s AND created_by = %s AND role = 'operator'
            """,
            (operator_id, admin_id)
        )
        return cur.fetchone()

# Обновление данных оператора
def update_operator_for_admin(
    operator_id: int,
    admin_id: int,
    username: str,
    email: Optional[str] = None,
    password_hash: Optional[str] = None,
):
    clean_username = (username or "").strip()
    clean_email = (email or "").strip() or None
    if not clean_username:
        return None

    # Подготовка полей для обновления оператора
    fields = ["username = %s", "email = %s"]
    values: list[Any] = [clean_username, clean_email]
    if password_hash:
        fields.append("password_hash = %s")
        values.append(password_hash)
    values.extend([operator_id, admin_id])

    with get_conn() as conn:
        cur = conn.cursor()
        try:
            # Запись изменений оператора
            cur.execute(
                f"""
                UPDATE users
                SET {', '.join(fields)}
                WHERE id = %s AND created_by = %s AND role = 'operator'
                """,
                tuple(values)
            )
            return cur.rowcount > 0
        except psycopg2.Error:
            return None

# Удаление оператора администратора
def delete_operator_for_admin(operator_id: int, admin_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Удаление учетной записи оператора
        cur.execute(
            """
            DELETE FROM users
            WHERE id = %s AND created_by = %s AND role = 'operator'
            """,
            (operator_id, admin_id)
        )
        return cur.rowcount > 0

# Создание нового бота
def create_bot(owner_id: int, name: str, token: str, group_link: str = "",
               group_id: str = "", confirmation_string: str = ""):
    with get_conn() as conn:
        cur = conn.cursor()
        # Сохранение основных данных бота
        cur.execute(
            """
            INSERT INTO bots
            (owner_id, name, token, group_link, group_id, confirmation_string)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (owner_id, name, token, group_link, group_id, confirmation_string)
        )
        bot_id = cur.fetchone()["id"]

        # Назначение пользователя в боте
        cur.execute(
            """
            INSERT INTO bot_users (bot_id, user_id, role)
            VALUES (%s, %s, 'admin')
            ON CONFLICT (bot_id, user_id) DO UPDATE SET role = 'admin'
            """,
            (bot_id, owner_id)
        )

        # Сохранение категории
        cur.execute(
            """
            INSERT INTO categories (bot_id, name, description)
            VALUES (%s, 'Общее', 'Основная категория вопросов')
            ON CONFLICT (bot_id, name) DO NOTHING
            """,
            (bot_id,)
        )

        # Сохранение настроек интеграции ВКонтакте
        cur.execute(
            """
            INSERT INTO integrations (bot_id, integration_type, settings, is_enabled)
            VALUES (%s, 'vk', %s, TRUE)
            ON CONFLICT (bot_id, integration_type)
            DO UPDATE SET settings = EXCLUDED.settings, is_enabled = TRUE, updated_at = CURRENT_TIMESTAMP
            """,
            (bot_id, Json({"group_link": group_link, "group_id": group_id, "callback_url": f"/vk/{bot_id}"}))
        )

        # Сохранение настроек языковой обработки
        cur.execute(
            """
            INSERT INTO integrations (bot_id, integration_type, settings, is_enabled)
            VALUES (%s, 'natasha', %s, TRUE)
            ON CONFLICT (bot_id, integration_type) DO NOTHING
            """,
            (bot_id, Json({"threshold": 0.70, "engine": "natasha+rapidfuzz"}))
        )

        return bot_id

# Обновление настроек бота
def update_bot(bot_id: int, **fields):
    allowed = {
        "name", "token", "group_link", "group_id", "confirmation_string",
        "is_active", "greeting_message", "fallback_message",
        "operator_called_message", "operator_finished_message",
        "answer_confirmation_message", "positive_close_message", "restart_message"
    }
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return False
    # Формирование списка обновляемых полей
    set_clause = ", ".join([f"{k} = %s" for k in clean.keys()])
    values = list(clean.values()) + [bot_id]
    with get_conn() as conn:
        cur = conn.cursor()
        # Запись изменений бота
        cur.execute(f"UPDATE bots SET {set_clause} WHERE id = %s", values)
        return cur.rowcount > 0

# Удаление бота
def delete_bot(bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Удаление записи бота
        cur.execute("DELETE FROM bots WHERE id = %s", (bot_id,))
        return cur.rowcount > 0

# Получение данных бота
def get_bot(bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение записи бота
        cur.execute("SELECT * FROM bots WHERE id = %s", (bot_id,))
        return cur.fetchone()

# Получение ботов пользователя
def get_bots_for_user(user_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Выборка доступных ботов
        cur.execute(
            """
            SELECT DISTINCT b.*, bu.role AS user_role
            FROM bots b
            JOIN bot_users bu ON bu.bot_id = b.id
            WHERE bu.user_id = %s
            ORDER BY b.created_at DESC
            """,
            (user_id,)
        )
        return cur.fetchall()

# Добавление пользователя к боту
def add_user_to_bot(bot_id: int, user_id: int, role: str = "operator"):
    with get_conn() as conn:
        cur = conn.cursor()
        # Назначение пользователя в боте
        cur.execute(
            """
            INSERT INTO bot_users (bot_id, user_id, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (bot_id, user_id) DO UPDATE SET role = EXCLUDED.role
            """,
            (bot_id, user_id, role)
        )
        return True

# Получение роли пользователя в боте
def get_user_bot_role(user_id: int, bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение роли пользователя
        cur.execute(
            "SELECT role FROM bot_users WHERE user_id = %s AND bot_id = %s",
            (user_id, bot_id)
        )
        row = cur.fetchone()
        return row["role"] if row else None

# Получение списка категорий
def get_all_categories(bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Сохранение категории
        cur.execute("SELECT * FROM categories WHERE bot_id = %s ORDER BY name", (bot_id,))
        return cur.fetchall()

# Добавление категории
def add_category_to_db(bot_id: int, name: str, description: str = ""):
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            # Сохранение категории
            cur.execute(
                """
                INSERT INTO categories (bot_id, name, description)
                VALUES (%s, %s, %s)
                ON CONFLICT (bot_id, name) DO UPDATE SET description = EXCLUDED.description
                """,
                (bot_id, name, description)
            )
            return True
        except psycopg2.Error:
            return False

# Обновление категории
def update_category(bot_id: int, old_name: str, new_name: str, description: str = ""):
    with get_conn() as conn:
        cur = conn.cursor()
        # Переименование категории
        cur.execute(
            "UPDATE categories SET name = %s, description = %s WHERE bot_id = %s AND name = %s",
            (new_name, description, bot_id, old_name)
        )
        # Обновление категории в состояниях диалогов
        cur.execute(
            "UPDATE bot_rules SET category = %s WHERE bot_id = %s AND category = %s",
            (new_name, bot_id, old_name)
        )
        # Удаление категории
        cur.execute(
            "UPDATE chat_states SET current_category = %s WHERE bot_id = %s AND current_category = %s",
            (new_name, bot_id, old_name)
        )
        # Удаление категории
        cur.execute(
            "UPDATE rule_buttons SET target_category = %s WHERE bot_id = %s AND target_category = %s",
            (new_name, bot_id, old_name)
        )
        return True

# Удаление категории по названию
def delete_category_by_name(bot_id: int, name: str):
    with get_conn() as conn:
        cur = conn.cursor()
        # Удаление категории
        cur.execute("DELETE FROM categories WHERE bot_id = %s AND name = %s", (bot_id, name))
        # Обновление категории в кнопках ответов
        cur.execute(
            "UPDATE bot_rules SET category = 'Общее' WHERE bot_id = %s AND category = %s",
            (bot_id, name)
        )
        # Обновление категории в кнопках ответов
        cur.execute(
            "UPDATE rule_buttons SET target_category = 'Общее' WHERE bot_id = %s AND target_category = %s",
            (bot_id, name)
        )
        return True

# Добавление ответа в базу знаний
def add_rule_to_db(bot_id: int, keyword: str, answer: str, category: str = "Общее",
                   buttons: str = "", intent_id: Optional[int] = None,
                   normalized_keyword: str = ""):
    with get_conn() as conn:
        cur = conn.cursor()
        # Запись ответа базы знаний
        cur.execute(
            """
            INSERT INTO bot_rules (bot_id, intent_id, keyword, normalized_keyword, answer, category, buttons)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (bot_id, intent_id, keyword, normalized_keyword, answer, category, buttons)
        )
        return cur.fetchone()["id"]

# Получение ответов базы знаний
def get_all_rules(bot_id: int, search: Optional[str] = None, category: Optional[str] = None):
    with get_conn() as conn:
        cur = conn.cursor()
        # Формирование условий поиска ответов
        conditions = ["r.bot_id = %s"]
        params: list[Any] = [bot_id]

        if search:
            # Подготовка строки поиска
            like = f"%{search}%"
            conditions.append("(r.keyword ILIKE %s OR r.category ILIKE %s OR r.answer ILIKE %s OR i.name ILIKE %s)")
            params.extend([like, like, like, like])

        if category:
            conditions.append("r.category = %s")
            params.append(category)

        where_clause = " AND ".join(conditions)
        # Получение ответов с интентами
        cur.execute(
            f"""
            SELECT r.*, i.name AS intent_name
            FROM bot_rules r
            LEFT JOIN user_intents i ON i.id = r.intent_id
            WHERE {where_clause}
            ORDER BY r.id DESC
            """,
            tuple(params)
        )
        return cur.fetchall()

# Получение ответов по категории
def get_rules_by_category(bot_id: int, category_name: str):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение ответов базы знаний
        cur.execute(
            "SELECT * FROM bot_rules WHERE bot_id = %s AND category = %s ORDER BY id DESC",
            (bot_id, category_name)
        )
        return cur.fetchall()

# Обновление ответа базы знаний
def update_rule_in_db(rule_id: int, bot_id: int, keyword: str, answer: str, category: str,
                      buttons: str = "", intent_id: Optional[int] = None,
                      normalized_keyword: str = ""):
    with get_conn() as conn:
        cur = conn.cursor()
        # Сохранение нормализованного текста ответа
        cur.execute(
            """
            UPDATE bot_rules
            SET keyword = %s, normalized_keyword = %s, answer = %s, category = %s,
                buttons = %s, intent_id = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND bot_id = %s
            """,
            (keyword, normalized_keyword, answer, category, buttons, intent_id, rule_id, bot_id)
        )
        return cur.rowcount > 0

# Удаление ответа из базы знаний
def delete_rule_from_db(rule_id: int, bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение ответов базы знаний
        cur.execute("DELETE FROM bot_rules WHERE id = %s AND bot_id = %s", (rule_id, bot_id))
        return cur.rowcount > 0

# Увеличение счетчика использования ответа
def increment_rule_use(rule_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение ответов базы знаний
        cur.execute("UPDATE bot_rules SET use_count = COALESCE(use_count, 0) + 1 WHERE id = %s", (rule_id,))

# Получение ответа по идентификатору
def get_rule_by_id(bot_id: int, rule_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение ответов базы знаний
        cur.execute("SELECT * FROM bot_rules WHERE bot_id = %s AND id = %s", (bot_id, rule_id))
        return cur.fetchone()

# Получение кнопок ответа
def get_rule_buttons(bot_id: int, rule_id: Optional[int] = None):
    with get_conn() as conn:
        cur = conn.cursor()
        if rule_id is None:
            # Чтение кнопок ответа
            cur.execute(
                """
                SELECT rb.*, tr.keyword AS target_rule_keyword
                FROM rule_buttons rb
                LEFT JOIN bot_rules tr ON tr.id = rb.target_rule_id
                WHERE rb.bot_id = %s
                ORDER BY rb.rule_id, rb.sort_order, rb.id
                """,
                (bot_id,)
            )
        else:
            # Чтение кнопок ответа
            cur.execute(
                """
                SELECT rb.*, tr.keyword AS target_rule_keyword
                FROM rule_buttons rb
                LEFT JOIN bot_rules tr ON tr.id = rb.target_rule_id
                WHERE rb.bot_id = %s AND rb.rule_id = %s
                ORDER BY rb.sort_order, rb.id
                """,
                (bot_id, rule_id)
            )
        return cur.fetchall()

# Замена кнопок ответа
def replace_rule_buttons(bot_id: int, rule_id: int, buttons: list[dict]):
    with get_conn() as conn:
        cur = conn.cursor()
        # Удаление прежних кнопок ответа
        cur.execute("DELETE FROM rule_buttons WHERE bot_id = %s AND rule_id = %s", (bot_id, rule_id))
        for index, button in enumerate(buttons):
            # Проверка текста кнопки
            label = (button.get("label") or "").strip()
            if not label:
                continue
            # Определение действия кнопки
            action = button.get("action") or "finish"
            if action not in {"go_rule", "go_category", "operator", "restart", "finish"}:
                action = "finish"
            target_rule_id = button.get("target_rule_id") or None
            target_category = (button.get("target_category") or "").strip() or None
            sort_order = button.get("sort_order") if button.get("sort_order") is not None else index
            # Запись кнопки ответа
            cur.execute(
                """
                INSERT INTO rule_buttons
                (bot_id, rule_id, label, action, target_rule_id, target_category, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (bot_id, rule_id, label, action, target_rule_id, target_category, sort_order)
            )
        return True

# Поиск кнопки ответа по тексту
def find_rule_button_by_label(bot_id: int, rule_id: int, text: str):
    text_norm = (text or "").strip().lower()
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение кнопок ответа
        cur.execute(
            """
            SELECT rb.*
            FROM rule_buttons rb
            WHERE rb.bot_id = %s
              AND rb.rule_id = %s
              AND LOWER(TRIM(rb.label)) = %s
            ORDER BY rb.sort_order, rb.id
            LIMIT 1
            """,
            (bot_id, rule_id, text_norm)
        )
        return cur.fetchone()

# Сохранение текущего ответа диалога
def set_current_rule(bot_id: int, messenger_user_id: str, rule_id: Optional[int]):
    with get_conn() as conn:
        cur = conn.cursor()
        # Сохранение состояния диалога
        cur.execute(
            """
            INSERT INTO chat_states (bot_id, messenger_user_id, current_rule_id, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (bot_id, messenger_user_id)
            DO UPDATE SET current_rule_id = EXCLUDED.current_rule_id,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (bot_id, str(messenger_user_id), rule_id)
        )

# Получение списка интентов
def get_intents(bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение интентов
        cur.execute("SELECT * FROM user_intents WHERE bot_id = %s ORDER BY id DESC", (bot_id,))
        return cur.fetchall()

# Получение интента
def get_intent(intent_id: int, bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение интентов
        cur.execute("SELECT * FROM user_intents WHERE id = %s AND bot_id = %s", (intent_id, bot_id))
        return cur.fetchone()

# Добавление интента
def add_intent(bot_id: int, name: str, examples: str = "", response: str = "", normalized_examples: str = ""):
    with get_conn() as conn:
        cur = conn.cursor()
        # Запись интента
        cur.execute(
            """
            INSERT INTO user_intents (bot_id, name, examples, response, normalized_examples)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (bot_id, name, examples, response, normalized_examples)
        )
        return cur.fetchone()["id"]

# Обновление интента
def update_intent(intent_id: int, bot_id: int, name: str, examples: str = "", response: str = "",
                  normalized_examples: str = ""):
    with get_conn() as conn:
        cur = conn.cursor()
        # Обновление ответа базы знаний
        cur.execute(
            """
            UPDATE user_intents
            SET name = %s, examples = %s, response = %s, normalized_examples = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND bot_id = %s
            """,
            (name, examples, response, normalized_examples, intent_id, bot_id)
        )
        return cur.rowcount > 0

# Удаление интента
def delete_intent(intent_id: int, bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Запись счетчика использования ответа
        cur.execute("UPDATE bot_rules SET intent_id = NULL WHERE bot_id = %s AND intent_id = %s", (bot_id, intent_id))
        # Запись счетчика использования интента
        cur.execute("DELETE FROM user_intents WHERE id = %s AND bot_id = %s", (intent_id, bot_id))
        return cur.rowcount > 0

# Увеличение счетчика использования интента
def increment_intent_use(intent_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Запись счетчика использования интента
        cur.execute("UPDATE user_intents SET use_count = COALESCE(use_count, 0) + 1 WHERE id = %s", (intent_id,))

# Получение списка интеграций
def get_integrations(bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение интеграций
        cur.execute("SELECT * FROM integrations WHERE bot_id = %s ORDER BY integration_type", (bot_id,))
        return cur.fetchall()

# Сохранение интеграции
def upsert_integration(bot_id: int, integration_type: str, settings: dict[str, Any], is_enabled: bool = True):
    with get_conn() as conn:
        cur = conn.cursor()
        # Запись интеграции
        cur.execute(
            """
            INSERT INTO integrations (bot_id, integration_type, settings, is_enabled)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (bot_id, integration_type)
            DO UPDATE SET settings = EXCLUDED.settings,
                          is_enabled = EXCLUDED.is_enabled,
                          updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (bot_id, integration_type, Json(settings or {}), is_enabled)
        )
        return cur.fetchone()["id"]

# Удаление интеграции
def delete_integration(integration_id: int, bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Удаление записи интеграции
        cur.execute("DELETE FROM integrations WHERE id = %s AND bot_id = %s", (integration_id, bot_id))
        return cur.rowcount > 0

# Получение блоков сценария
def get_scenario_nodes(bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение блоков сценария
        cur.execute("SELECT * FROM scenario_nodes WHERE bot_id = %s ORDER BY is_start DESC, id DESC", (bot_id,))
        return cur.fetchall()

# Получение начальных блоков сценария
def get_start_scenario_nodes(bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение блоков сценария
        cur.execute("SELECT * FROM scenario_nodes WHERE bot_id = %s AND is_start = TRUE ORDER BY id", (bot_id,))
        return cur.fetchall()

# Получение блока сценария
def get_scenario_node(bot_id: int, node_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение блоков сценария
        cur.execute("SELECT * FROM scenario_nodes WHERE bot_id = %s AND id = %s", (bot_id, node_id))
        return cur.fetchone()

# Добавление блока сценария
def add_scenario_node(bot_id: int, title: str, node_type: str, message: str,
                      is_start: bool = False, position_x: int = 80, position_y: int = 80):
    with get_conn() as conn:
        cur = conn.cursor()
        # Запись блока сценария
        cur.execute(
            """
            INSERT INTO scenario_nodes (bot_id, title, node_type, message, is_start, position_x, position_y)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (bot_id, title, node_type, message, is_start, position_x, position_y)
        )
        return cur.fetchone()["id"]

# Обновление блока сценария
def update_scenario_node(node_id: int, bot_id: int, title: str, node_type: str, message: str,
                         is_start: bool = False, position_x: int = 80, position_y: int = 80):
    with get_conn() as conn:
        cur = conn.cursor()
        # Запись изменений блока сценария
        cur.execute(
            """
            UPDATE scenario_nodes
            SET title = %s, node_type = %s, message = %s, is_start = %s,
                position_x = %s, position_y = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND bot_id = %s
            """,
            (title, node_type, message, is_start, position_x, position_y, node_id, bot_id)
        )
        return cur.rowcount > 0

# Удаление блока сценария
def delete_scenario_node(node_id: int, bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Удаление блока сценария
        cur.execute("DELETE FROM scenario_nodes WHERE id = %s AND bot_id = %s", (node_id, bot_id))
        return cur.rowcount > 0

# Получение переходов сценария
def get_scenario_edges(bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение переходов сценария
        cur.execute(
            """
            SELECT e.*, f.title AS from_title, t.title AS to_title
            FROM scenario_edges e
            JOIN scenario_nodes f ON f.id = e.from_node_id AND f.bot_id = e.bot_id
            JOIN scenario_nodes t ON t.id = e.to_node_id AND t.bot_id = e.bot_id
            WHERE e.bot_id = %s
            ORDER BY e.from_node_id, e.sort_order, e.id
            """,
            (bot_id,)
        )
        return cur.fetchall()

# Получение переходов из блока сценария
def get_edges_from_node(bot_id: int, from_node_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение переходов из блока
        cur.execute(
            """
            SELECT * FROM scenario_edges
            WHERE bot_id = %s AND from_node_id = %s
            ORDER BY sort_order, id
            """,
            (bot_id, from_node_id)
        )
        return cur.fetchall()

# Поиск начального блока по тексту
def find_start_node_by_text(bot_id: int, text: str):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение блоков сценария
        cur.execute(
            """
            SELECT * FROM scenario_nodes
            WHERE bot_id = %s AND is_start = TRUE AND LOWER(TRIM(title)) = LOWER(TRIM(%s))
            LIMIT 1
            """,
            (bot_id, text)
        )
        return cur.fetchone()

# Добавление перехода сценария
def add_scenario_edge(bot_id: int, from_node_id: int, to_node_id: int, label: str,
                      condition_text: str = "", sort_order: int = 0):
    with get_conn() as conn:
        cur = conn.cursor()
        # Запись перехода сценария
        cur.execute(
            """
            INSERT INTO scenario_edges (bot_id, from_node_id, to_node_id, label, condition_text, sort_order)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (bot_id, from_node_id, to_node_id, label, condition_text, sort_order)
        )
        return cur.fetchone()["id"]

# Обновление перехода сценария
def update_scenario_edge(edge_id: int, bot_id: int, from_node_id: int, to_node_id: int,
                         label: str, condition_text: str = "", sort_order: int = 0):
    with get_conn() as conn:
        cur = conn.cursor()
        # Запись изменений перехода
        cur.execute(
            """
            UPDATE scenario_edges
            SET from_node_id = %s, to_node_id = %s, label = %s, condition_text = %s, sort_order = %s
            WHERE id = %s AND bot_id = %s
            """,
            (from_node_id, to_node_id, label, condition_text, sort_order, edge_id, bot_id)
        )
        return cur.rowcount > 0

# Удаление перехода сценария
def delete_scenario_edge(edge_id: int, bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Сохранение состояния диалога
        cur.execute("DELETE FROM scenario_edges WHERE id = %s AND bot_id = %s", (edge_id, bot_id))
        return cur.rowcount > 0

# Изменение режима оператора
def set_operator_mode(bot_id: int, messenger_user_id: str, mode: bool):
    with get_conn() as conn:
        cur = conn.cursor()
        # Сохранение состояния диалога
        cur.execute(
            """
            INSERT INTO chat_states (bot_id, messenger_user_id, is_operator_mode, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (bot_id, messenger_user_id)
            DO UPDATE SET is_operator_mode = EXCLUDED.is_operator_mode,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (bot_id, str(messenger_user_id), mode)
        )

# Сохранение текущей категории диалога
def set_current_category(bot_id: int, messenger_user_id: str, category: Optional[str]):
    with get_conn() as conn:
        cur = conn.cursor()
        # Сохранение состояния диалога
        cur.execute(
            """
            INSERT INTO chat_states (bot_id, messenger_user_id, current_category, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (bot_id, messenger_user_id)
            DO UPDATE SET current_category = EXCLUDED.current_category,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (bot_id, str(messenger_user_id), category)
        )

# Сохранение текущего блока сценария
def set_current_scenario_node(bot_id: int, messenger_user_id: str, node_id: Optional[int]):
    with get_conn() as conn:
        cur = conn.cursor()
        # Сохранение состояния диалога
        cur.execute(
            """
            INSERT INTO chat_states (bot_id, messenger_user_id, current_scenario_node_id, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (bot_id, messenger_user_id)
            DO UPDATE SET current_scenario_node_id = EXCLUDED.current_scenario_node_id,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (bot_id, str(messenger_user_id), node_id)
        )

# Получение состояния диалога
def get_chat_state(bot_id: int, messenger_user_id: str):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение состояния диалога
        cur.execute(
            "SELECT * FROM chat_states WHERE bot_id = %s AND messenger_user_id = %s",
            (bot_id, str(messenger_user_id))
        )
        return cur.fetchone()

# Запись сообщения в историю
def log_message(bot_id: int, messenger_user_id: str, user_text: str, bot_answer: str,
                confidence: Optional[float] = None, operator_id: Optional[int] = None,
                answer_source: str = "bot", is_success: Optional[bool] = None):
    with get_conn() as conn:
        cur = conn.cursor()
        # Сохранение записи истории
        cur.execute(
            """
            INSERT INTO message_logs
            (bot_id, messenger_user_id, user_text, bot_answer, confidence, operator_id, answer_source, is_success)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (bot_id, str(messenger_user_id), user_text, bot_answer, confidence, operator_id, answer_source, is_success)
        )

# Оценка последнего ответа бота
def mark_last_answer_success(bot_id: int, messenger_user_id: str, is_success: bool):
    with get_conn() as conn:
        cur = conn.cursor()
        # Обновление последней оценки
        cur.execute(
            """
            UPDATE message_logs
            SET is_success = %s
            WHERE id = (
                SELECT id FROM message_logs
                WHERE bot_id = %s AND messenger_user_id = %s AND answer_source IN ('bot', 'rule', 'intent', 'scenario', 'rule_button')
                ORDER BY created_at DESC
                LIMIT 1
            )
            """,
            (is_success, bot_id, str(messenger_user_id))
        )

# Получение последних диалогов
def get_logs(bot_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        # Получение последних сообщений диалогов
        cur.execute(
            """
            WITH last_messages AS (
                SELECT DISTINCT ON (messenger_user_id) *
                FROM message_logs
                WHERE bot_id = %s
                ORDER BY messenger_user_id, created_at DESC
            )
            SELECT
                lm.messenger_user_id,
                lm.user_text,
                lm.bot_answer,
                lm.confidence,
                lm.answer_source,
                lm.created_at,
                COALESCE(s.is_operator_mode, FALSE) AS is_operator_mode,
                COALESCE(s.is_archived, FALSE) AS is_archived
            FROM last_messages lm
            LEFT JOIN chat_states s
                ON s.bot_id = lm.bot_id AND s.messenger_user_id = lm.messenger_user_id
            ORDER BY COALESCE(s.is_operator_mode, FALSE) DESC, lm.created_at DESC
            LIMIT 100
            """,
            (bot_id,)
        )
        return cur.fetchall()

# Получение истории диалога
def get_dialog_messages(bot_id: int, messenger_user_id: str, limit: int = 50):
    with get_conn() as conn:
        cur = conn.cursor()
        # Чтение истории сообщений
        cur.execute(
            """
            SELECT * FROM message_logs
            WHERE bot_id = %s AND messenger_user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (bot_id, str(messenger_user_id), limit)
        )
        return list(reversed(cur.fetchall()))

# Расчет статистики бота
def get_analytics(bot_id: int):
    # Список служебных фраз для исключения из статистики
    ignore_list = [
        'начать', 'меню', '◀️ назад', 'старт', 'привет', 'hi', 'здравствуйте',
        'нет нужного варианта', '❓ нет нужного варианта', 'да, спасибо',
        'нет, другой вопрос', 'назад', 'ответ оператора', 'ожидание оператора', '◀️ в главное меню', 'в главное меню'
    ]
    with get_conn() as conn:
        cur = conn.cursor()
        # Подсчет общего количества сообщений
        cur.execute("SELECT COUNT(*) AS total FROM message_logs WHERE bot_id = %s", (bot_id,))
        total = cur.fetchone()["total"]

        # Подсчет диалогов в режиме оператора
        cur.execute(
            "SELECT COUNT(*) AS need_operator FROM chat_states WHERE bot_id = %s AND is_operator_mode = TRUE",
            (bot_id,)
        )
        need_operator = cur.fetchone()["need_operator"]

        # Подсчет ответов бота
        cur.execute(
            """
            SELECT COUNT(*) AS bot_answers
            FROM message_logs
            WHERE bot_id = %s AND answer_source IN ('bot', 'rule', 'intent', 'scenario', 'rule_button')
              AND COALESCE(bot_answer, '') <> ''
              AND bot_answer NOT ILIKE '%%ОЖИДАНИЕ ОПЕРАТОРА%%'
            """,
            (bot_id,)
        )
        bot_answers = cur.fetchone()["bot_answers"]

        # Подсчет ответов оператора
        cur.execute(
            "SELECT COUNT(*) AS operator_answers FROM message_logs WHERE bot_id = %s AND answer_source = 'operator'",
            (bot_id,)
        )
        operator_answers = cur.fetchone()["operator_answers"]

        # Подсчет обращений без найденного ответа
        cur.execute(
            """
            SELECT COUNT(*) AS fallback_count
            FROM message_logs
            WHERE bot_id = %s
              AND answer_source IN ('fallback', 'operator_request')
            """,
            (bot_id,)
        )
        fallback_count = cur.fetchone()["fallback_count"]

        # Расчет средней уверенности ответа
        cur.execute(
            """
            SELECT ROUND(AVG(confidence)::numeric, 2) AS avg_confidence
            FROM message_logs
            WHERE bot_id = %s AND confidence IS NOT NULL
            """,
            (bot_id,)
        )
        avg_confidence = cur.fetchone()["avg_confidence"] or 0

        # Подсчет успешных ответов
        cur.execute(
            """
            SELECT COUNT(*) AS successful
            FROM message_logs
            WHERE bot_id = %s
              AND is_success = TRUE
              AND answer_source IN ('bot', 'rule', 'intent', 'scenario', 'rule_button', 'scenario_end')
            """,
            (bot_id,)
        )
        successful = cur.fetchone()["successful"]

        # Расчет процента успешных ответов
        success_rate = round((successful / bot_answers) * 100, 1) if bot_answers else 0

        # Получение популярных вопросов
        cur.execute(
            """
            SELECT LOWER(TRIM(user_text)) AS keyword, COUNT(*) AS use_count
            FROM message_logs
            WHERE bot_id = %s
              AND user_text IS NOT NULL
              AND TRIM(user_text) <> ''
              AND LOWER(TRIM(user_text)) <> ALL(%s)
              AND answer_source IN ('rule', 'intent')
              AND user_text NOT ILIKE '%%оператор%%завершил%%'
              AND user_text NOT ILIKE '%%ожидание%%оператора%%'
            GROUP BY LOWER(TRIM(user_text))
            ORDER BY use_count DESC
            LIMIT 5
            """,
            (bot_id, ignore_list)
        )
        popular = cur.fetchall()

        # Получение сообщений по дням
        cur.execute(
            """
            SELECT DATE(created_at) AS day, COUNT(*) AS messages
            FROM message_logs
            WHERE bot_id = %s AND created_at >= CURRENT_DATE - INTERVAL '6 days'
            GROUP BY DATE(created_at)
            ORDER BY day
            """,
            (bot_id,)
        )
        by_day = cur.fetchall()

        return {
            "total_messages": total,
            "need_operator": need_operator,
            "bot_answers": bot_answers,
            "operator_answers": operator_answers,
            "fallback_count": fallback_count,
            "avg_confidence": float(avg_confidence or 0),
            "success_rate": success_rate,
            "popular": popular,
            "by_day": by_day,
        }

# Получение популярных вопросов
def get_popular_questions(bot_id: int):
    return get_analytics(bot_id)["popular"]

# Обновление нормализованного текста ответа
def update_rule_normalized(rule_id: int, normalized_keyword: str):
    with get_conn() as conn:
        cur = conn.cursor()
        # Сохранение нормализованного текста ответа
        cur.execute(
            "UPDATE bot_rules SET normalized_keyword = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (normalized_keyword, rule_id)
        )

# Обновление нормализованных примеров интента
def update_intent_normalized(intent_id: int, normalized_examples: str):
    with get_conn() as conn:
        cur = conn.cursor()
        # Сохранение нормализованных примеров интента
        cur.execute(
            "UPDATE user_intents SET normalized_examples = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (normalized_examples, intent_id)
        )

# Выполнение запроса с несколькими результатами
def fetch_all(query, params=None):
    with get_conn() as conn:
        cur = conn.cursor()
        # Выполнение переданного запроса
        cur.execute(query, params or ())
        return cur.fetchall()

# Выполнение запроса с одним результатом
def fetch_one(query, params=None):
    with get_conn() as conn:
        cur = conn.cursor()
        # Выполнение переданного запроса
        cur.execute(query, params or ())
        return cur.fetchone()

# Выполнение запроса без возврата данных
def execute_query(query, params=None):
    with get_conn() as conn:
        cur = conn.cursor()
        # Выполнение переданного запроса
        cur.execute(query, params or ())
        return True
