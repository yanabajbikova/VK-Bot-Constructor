import sqlite3
import json
import os
import httpx
from fastapi import FastAPI, Request, HTTPException, Security, Depends
from fastapi.responses import PlainTextResponse, FileResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette import status
from pydantic import BaseModel
from . import database as db
from dotenv import load_dotenv

# МОДУЛЬ: ОСНОВНОЙ СЕРВЕР (FASTAPI)

# Подгрузка конфигурационных файлов из переменных окружения
from .nlp_engine import analyze_text, get_answer
from .database import (
    init_db, get_all_rules, add_rule_to_db, 
    delete_rule_from_db, update_rule_in_db, 
    log_message, get_analytics, create_user, get_user_by_username, get_user_by_login_or_email,
    get_chat_state, set_operator_mode, get_answer_from_db, get_popular_rules,
    get_all_categories, add_category_to_db, get_rules_by_category
)
load_dotenv()

# Идентификационные данные сообщества и ключи доступа
token = os.getenv("VK_TOKEN")
group_id = os.getenv("GROUP_ID")

app = FastAPI()
init_db() # Инициализация базы данных при запуске

templates = Jinja2Templates(directory="app/static")

static_path = os.path.join(os.path.dirname(__file__), "static")

# Возврат главного файла интерфейса пользователя
# Возврат главного файла интерфейса пользователя через шаблонизатор
@app.get("/")
async def read_index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "group_id": os.getenv("GROUP_ID")
    })

app.mount("/static", StaticFiles(directory=static_path), name="static")

# БЕЗОПАСНОСТЬ И КОНТРОЛЬ ДОСТУПА

API_KEY = os.getenv("INTERNAL_API_KEY")
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(header_value: str = Depends(api_key_header)):
    if header_value == API_KEY:
        return header_value
    raise HTTPException(status_code=403, detail="Доступ запрещен")

# Константые данные Callback API ВКонтакте
CONFIRMATION_STR = os.getenv("VK_CONFIRMATION_STR")
TOKEN = os.getenv("VK_TOKEN")

# МОДЕЛИ ДАННЫХ

# Структура запроса для создания/редактирования правила бота
class RuleRequest(BaseModel):
    owner_id: int
    keyword: str
    answer: str
    category: str = "Общее"
    buttons: str = ""

# Структура данных для аутентификации пользователя
class UserRequest(BaseModel):
    username: str
    password: str

# АВТОРИЗАЦИЯ

# Регистрация новой учетной записи
@app.post("/register")
async def register(req: UserRequest):
    user_id = create_user(req.username, req.password)
    return {"status": "success", "user_id": user_id} if user_id else {"status": "error"}

# Вход в систему
@app.post("/login")
async def login(req: UserRequest):
    # Используем поиск по логину ИЛИ почте
    user = get_user_by_login_or_email(req.username) 
    
    if user and user['password'] == req.password:
        return {
            "status": "success", 
            "user_id": user['id'], 
            "username": user['username']
        }
    return {"status": "error", "message": "Неверный логин/почта или пароль"}

# Выгрузка правил бота с подддержкой поиска
@app.get("/rules", dependencies=[Depends(get_api_key)])
async def get_rules(owner_id: int, search: str = None):
    rules = get_all_rules(owner_id, search)
    return {"rules": [dict(rule) for rule in rules]}

# Добавление нового триггера и соответствующего ему ответа
@app.post("/rules", dependencies=[Depends(get_api_key)])
async def create_rule(req: RuleRequest):
    if not req.keyword: return {"status": "error"}
    if add_rule_to_db(req.owner_id, req.keyword, req.answer, req.category, req.buttons):
        return {"status": "success"}
    return {"status": "error"}

# Удаление правила из базы данных
@app.delete("/rules/{rule_id}", dependencies=[Depends(get_api_key)])
async def delete_rule(rule_id: int, owner_id: int):
    if delete_rule_from_db(rule_id, owner_id):
        return {"status": "success"}
    return {"status": "error"}

# Обновление существующего правила
@app.put("/rules/{rule_id}")
async def update_rule(rule_id: int, req: RuleRequest):
    update_rule_in_db(rule_id, req.owner_id, req.keyword, req.answer, req.category)
    return {"status": "success"}

# АНАЛИТИКА И ЛОГИ

# Сбор статистики по количеству успешных ответов системы
@app.get("/analytics", dependencies=[Depends(get_api_key)])
async def admin_analytics():
    conn = sqlite3.connect(db.DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM message_logs WHERE bot_answer != 'ОЖИДАНИЕ ОПЕРАТОРА' AND bot_answer != ''")
    total = cursor.fetchone()[0]
    conn.close()
    return {"total_messages": total} 

# Получение списка популярных вопросов
@app.get("/stats/{owner_id}", dependencies=[Depends(get_api_key)])
async def get_stats(owner_id: int):
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM categories")
    categories = [row['name'].lower().strip() for row in cursor.fetchall()]
    
    ignore_list = [ # Список системных кнопок и технических фраз для игнорирования
        'начать', 'меню', '◀️ назад', 'старт', 'привет', 'hi','Начать',
        'здравствуйте', 'Нет нужного варианта', 'нет нужного варианта', '❓ Нет нужного варианта',
        'Да, спасибо', 'да, спасибо', 'Нет, другой вопрос', 
        'нет, другой вопрос', 'назад'
    ]

    full_stop_list = list(set(ignore_list + categories)) # Объединение категорий и списка систеных кнопок и технических фращ фразы
    
    placeholders = ', '.join(['?'] * len(full_stop_list))
    query = f"""
        SELECT user_text, COUNT(*) as use_count 
        FROM message_logs 
        WHERE LOWER(TRIM(user_text)) NOT IN ({placeholders})
        AND bot_answer NOT LIKE '%ОЖИДАНИЕ ОПЕРАТОРА%'
        AND user_text != ''
        GROUP BY user_text 
        ORDER BY use_count DESC 
        LIMIT 5
    """
    
    cursor.execute(query, full_stop_list)
    rows = cursor.fetchall()
    conn.close()
    
    return {"popular": [{"keyword": r['user_text'], "use_count": r['use_count']} for r in rows]}

# Вывод истории диалогов и текущих состояний чатов
@app.get("/logs", dependencies=[Depends(get_api_key)])
async def get_logs():
    
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = """
    SELECT 
        m.vk_user_id, 
        m.user_text, 
        m.bot_answer, 
        m.timestamp, 
        u.is_operator_mode,
        u.is_archived
    FROM message_logs m
    JOIN chat_states u ON m.vk_user_id = u.vk_user_id
    WHERE m.id IN (SELECT MAX(id) FROM message_logs GROUP BY vk_user_id)
    ORDER BY u.is_operator_mode DESC, m.timestamp DESC
    LIMIT 100
    """
    cursor.execute(query)
    logs = cursor.fetchall()
    conn.close()
    return {"logs": [dict(l) for l in logs]}

# УПРАВЛЕНИЕ КАТЕГОРИЯМИ

# Получение перечня категорий
@app.get("/categories")
async def get_cats():
    return {"categories": [dict(c) for c in get_all_categories()]}

# Создание новой категории
@app.post("/categories")
async def create_cat(name: str, desc: str = ""):
    add_category_to_db(name, desc)
    return {"status": "success"}

# Удаление категории из базы данных
@app.delete("/categories/{cat_name}", dependencies=[Depends(get_api_key)])
async def delete_category(cat_name: str):
    conn = sqlite3.connect(db.DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM categories WHERE name = ?", (cat_name,))
    conn.commit()
    conn.close()
    return {"status": "success"}

# РЕЖИМ ОПЕРАТОРА 

# Активация ручного управления (вызов оператора)
@app.post("/operator/start/{vk_id}", dependencies=[Depends(get_api_key)])
async def start_operator_mode(vk_id: int):
    set_operator_mode(vk_id, 1) 
    return {"status": "success"}

# Возврат чата под управление бота
@app.post("/operator/stop/{vk_id}", dependencies=[Depends(get_api_key)])
async def stop_operator(vk_id: int):
    set_operator_mode(vk_id, 0)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("UPDATE chat_states SET is_archived = 0 WHERE vk_user_id = ?", (vk_id,))
    conn.commit()
    conn.close()

    categories = get_all_categories()     # Генерация приветственного меню для уведомления пользователя о возвращении бота
    bot_response = "Оператор отключился. Я снова готов отвечать на Ваши вопросы! Напишите свой вопрос одним сообщением или выберите из предложенных:"
    
    btns = [[{"action": {"type": "text", "label": c['name']}, "color": "primary"}] for c in categories]
    btns.append([{"action": {"type": "text", "label": "Нет нужного варианта"}, "color": "secondary"}])
    keyboard = json.dumps({"one_time": False, "buttons": btns, "inline": False})
    
    async with httpx.AsyncClient() as client:  # Асинхронная отправка сервисного сообщения через API ВКонтакте
        await client.post("https://api.vk.com/method/messages.send", params={
            "peer_id": vk_id, "message": bot_response, "random_id": 0,
            "access_token": TOKEN, "v": "5.199", "keyboard": keyboard
        })

    return {"status": "success"}

# ОБРАБОТЧИК ВКОНТАКТЕ
@app.post("/vk")
async def vk_handler(request: Request): # Основная логика диспетчеризации входящих сообщений
    data = await request.json()
    
    if data.get("type") == "message_new":
        msg = data['object']['message']
        v_id, text = msg['from_id'], msg['text']
        text_l = text.lower().strip()

        if text_l == "да, спасибо":    # Обработка позитивного закрытия диалога пользователем
            set_operator_mode(v_id, 0)  # Автоматический возврат бота в режим ожидания
            
            async with httpx.AsyncClient() as client:
                await client.post("https://api.vk.com/method/messages.send", params={
                    "peer_id": v_id, "message": "Рад был помочь! Если появятся новые вопросы – я всегда здесь.",
                    "random_id": 0, "access_token": TOKEN, "v": "5.199"
                })
            return PlainTextResponse("ok")
        
        elif text_l == "нет, другой вопрос":
            set_operator_mode(v_id, 0) # Принудительная активация бота
            categories = get_all_categories()
            bot_response = "Давайте попробуем сначала. Выберите подходящий раздел:"
            
            btns = [[{"action": {"type": "text", "label": c['name']}, "color": "primary"}] for c in categories] # Подготовка кнопок навигации для главного меню
            btns.append([
                {"action": {"type": "text", "label": "Нет нужного варианта"}, "color": "secondary"},
                {"action": {"type": "text", "label": "◀️ назад"}, "color": "secondary"}
            ])
            
            keyboard = json.dumps({"buttons": btns, "inline": False})
            
            async with httpx.AsyncClient() as client:
                await client.post("https://api.vk.com/method/messages.send", params={
                    "peer_id": v_id, "message": bot_response,
                    "random_id": 0, "access_token": TOKEN, "v": "5.199", "keyboard": keyboard
                })
            return PlainTextResponse("ok")
        
        if get_chat_state(v_id) == 1:  # Блокировка бота при активном чате с оператором
            log_message(v_id, text, "ОЖИДАНИЕ ОПЕРАТОРА")
            return PlainTextResponse("ok")

        bot_response = ""
        keyboard = None
        
        if text_l in ["начать", "меню", "◀️ назад", " назад", "привет", "старт", "hi", "здравствуйте"]: # Логика навигации главного меню 
            db.set_operator_mode(v_id, 0)
            
            async with httpx.AsyncClient() as client:
                user_req = await client.get("https://api.vk.com/method/users.get", params={ # Получение имени пользователя
                    "user_ids": v_id,
                    "access_token": TOKEN,
                    "v": "5.199"
                })
                user_data = user_req.json()
                first_name = user_data['response'][0]['first_name'] if 'response' in user_data else "пользователь"

                group_req = await client.get("https://api.vk.com/method/groups.getById", params={ # Получение названия группы
                    "access_token": TOKEN,
                    "v": "5.199"
                })
                group_data = group_req.json()
                group_name = group_data['response']['groups'][0]['name'] if 'response' in group_data else 'нашей компании'

                bot_response = f"Привет, {first_name}! На связи Бот технической поддержки компании \"{group_name}\". Напишите свой вопрос одним сообщением или выберите из предложенных:"

            categories = get_all_categories()
            btns = [[{"action": {"type": "text", "label": c['name']}, "color": "primary"}] for c in categories]
            btns.append([{"action": {"type": "text", "label": "Нет нужного варианта"}, "color": "secondary"}])
            keyboard = json.dumps({"one_time": False, "buttons": btns, "inline": False})

        elif text_l in [c['name'].lower() for c in get_all_categories()]:     # Углубление в категорию, вывод списка уточняющих вопросов
            cat_name = next(c['name'] for c in get_all_categories() if c['name'].lower() == text_l)
            rules = get_rules_by_category(cat_name)

            conn = sqlite3.connect(db.DB_PATH)  # Фиксация текущего контекста (категории) пользователя в БД
            conn.execute("UPDATE chat_states SET current_category = ? WHERE vk_user_id = ?", (cat_name, v_id))
            conn.commit()
            conn.close()
            if rules:
                bot_response = f"Раздел '{cat_name}'. Выберите, что хотите узнать:"
                btns = []
                for r in rules:
                    label = r['keyword'].split(',')[0].strip()
                    btns.append([{"action": {"type": "text", "label": label}, "color": "primary"}])
                
                btns.append([{"action": {"type": "text", "label": "◀️ назад"}, "color": "secondary"}])
                keyboard = json.dumps({"one_time": False, "buttons": btns, "inline": False})
            else:
                bot_response = "В этой категории пока нет ответов. Попробуйте ввести вопрос текстом."
        
        elif text_l == "нет нужного варианта":         # Режим ожидания текстового ввода при отсутствии нужного варианта в кнопках
            bot_response = "Напишите вопрос одним сообщением, и я постараюсь найти ответ!"
            keyboard = json.dumps({"buttons": [], "one_time": True})
        
        else:  # Поиск ответа через NLP с учетом категории 
            conn = sqlite3.connect(db.DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT current_category FROM chat_states WHERE vk_user_id = ?", (v_id,))
            row = cursor.fetchone()
            current_cat = row[0] if row else None
            conn.close()

            bot_response = get_answer(text, current_cat)             # Вызов функции лингвистического анализа и поиска соответствий
            
            if bot_response:
                log_message(v_id, text, bot_response)
                bot_response = f"{bot_response}\n\nЯ ответил на Ваш вопрос?"
                keyboard = json.dumps({
                    "inline": True, 
                    "buttons": [
                        [{"action": {"type": "text", "label": "Да, спасибо"}, "color": "positive"}],
                        [{"action": {"type": "text", "label": "Нет, другой вопрос"}, "color": "negative"}]
                    ]
                })
            else:
                set_operator_mode(v_id, 1)
                bot_response = "Думаю, с этим вопросом лучше разберется оператор. Я уже позвал его."
                keyboard = json.dumps({"buttons": [], "one_time": True})

        log_message(v_id, text, bot_response) # Регистрация ответа и отправка сообщением
        async with httpx.AsyncClient() as client:
            params = {
                "peer_id": v_id, "message": bot_response, "random_id": 0,
                "access_token": TOKEN, "v": "5.199"
            }
            if keyboard: params["keyboard"] = keyboard
            await client.post("https://api.vk.com/method/messages.send", params=params)
            
    return PlainTextResponse("ok")
