# Основной модуль приложения
import json
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field

from . import database as db
from .nlp_engine import get_answer, normalize_text, train_bot_model

# Загрузка переменных окружения
load_dotenv()

# Создание приложения
app = FastAPI(title="Платформа-конструктор чат-ботов")
# Подготовка базы данных
db.init_db()

# Настройка папки статических файлов
BASE_DIR = os.path.dirname(__file__)
static_path = os.path.join(BASE_DIR, "static")
templates = Jinja2Templates(directory=static_path)
app.mount("/static", StaticFiles(directory=static_path), name="static")

# Настройка параметров токена доступа
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# Настройка проверки паролей
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

# Данные для регистрации пользователя
class RegisterRequest(BaseModel):
    username: str
    password: str
    email: Optional[EmailStr] = None

# Данные для входа пользователя
class LoginRequest(BaseModel):
    username: str
    password: str

# Данные для создания бота
class BotRequest(BaseModel):
    name: str
    token: str
    group_link: Optional[str] = ""
    group_id: Optional[str] = ""
    confirmation_string: Optional[str] = ""

# Данные для обновления бота
class BotUpdateRequest(BaseModel):
    name: Optional[str] = None
    token: Optional[str] = None
    group_link: Optional[str] = None
    group_id: Optional[str] = None
    confirmation_string: Optional[str] = None
    is_active: Optional[bool] = None
    greeting_message: Optional[str] = None
    fallback_message: Optional[str] = None
    operator_called_message: Optional[str] = None
    operator_finished_message: Optional[str] = None
    answer_confirmation_message: Optional[str] = None
    positive_close_message: Optional[str] = None
    restart_message: Optional[str] = None

# Данные ответа базы знаний
class RuleRequest(BaseModel):
    bot_id: int
    keyword: str
    answer: str
    category: str = "Общее"
    buttons: str = ""
    intent_id: Optional[int] = None

# Данные кнопки ответа
class RuleButtonItem(BaseModel):
    label: str
    action: str = "finish"
    target_rule_id: Optional[int] = None
    target_category: Optional[str] = None
    sort_order: int = 0

# Список кнопок ответа
class RuleButtonsRequest(BaseModel):
    bot_id: int
    buttons: List[RuleButtonItem] = []

# Данные категории базы знаний
class CategoryRequest(BaseModel):
    bot_id: int
    name: str
    description: str = ""
    old_name: Optional[str] = None

# Данные интента пользователя
class IntentRequest(BaseModel):
    bot_id: int
    name: str
    examples: str = ""
    response: str = ""

# Данные интеграции бота
class IntegrationRequest(BaseModel):
    bot_id: int
    integration_type: str = Field(..., description="Например: vk, natasha, telegram, web")
    settings: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True

# Данные блока сценария
class ScenarioNodeRequest(BaseModel):
    bot_id: int
    title: str
    node_type: str = "message"
    message: str = ""
    is_start: bool = False
    position_x: int = 80
    position_y: int = 80

# Данные перехода сценария
class ScenarioEdgeRequest(BaseModel):
    bot_id: int
    from_node_id: int
    to_node_id: int
    label: str
    condition_text: str = ""
    sort_order: int = 0

# Данные для создания оператора
class OperatorCreateRequest(BaseModel):
    username: str
    password: str
    email: Optional[EmailStr] = None
    bot_ids: List[int] = []

# Данные для обновления оператора
class OperatorUpdateRequest(BaseModel):
    username: str
    email: Optional[EmailStr] = None
    password: Optional[str] = None

# Данные ручного ответа оператора
class ManualReplyRequest(BaseModel):
    bot_id: int
    messenger_user_id: str
    message: str

# Данные для запуска обучения бота
class TrainRequest(BaseModel):
    bot_id: int

# Хеширование пароля
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

# Проверка пароля
def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)

# Создание токена доступа
def create_access_token(data: dict):
    # Подготовка данных токена
    payload = data.copy()
    # Установка времени действия токена
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload.update({"exp": expire})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

# Преобразование модели в словарь
def _model_to_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True)
    return model.dict(exclude_unset=True)

# Получение текущего пользователя
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Необходима авторизация")

    try:
        # Расшифровка токена доступа
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Некорректный токен")

    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user

# Проверка прав администратора
def require_admin(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Недостаточно прав администратора")
    return user

# Проверка доступа к боту
def ensure_bot_access(bot_id: int, user: dict, admin_only: bool = False):
    # Получение роли пользователя в боте
    role = db.get_user_bot_role(user["id"], bot_id)
    if not role:
        raise HTTPException(status_code=403, detail="Нет доступа к этому боту")
    if admin_only and role != "admin":
        raise HTTPException(status_code=403, detail="Действие доступно только администратору бота")
    return role

# Открытие главной страницы
@app.get("/")
async def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# Регистрация администратора
@app.post("/register")
async def register(req: RegisterRequest):
    """Публичная регистрация создает администратора платформы."""
    user_id = db.create_user(
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
        role="admin"
    )
    if not user_id:
        return {"status": "error", "message": "Такой логин или email уже существует"}
    return {"status": "success", "user_id": user_id}

# Авторизация пользователя
@app.post("/login")
async def login(req: LoginRequest):
    user = db.get_user_by_login_or_email(req.username)
    if not user or not verify_password(req.password, user["password_hash"]):
        return {"status": "error", "message": "Неверный логин/почта или пароль"}

    token = create_access_token({"sub": str(user["id"]), "role": user["role"]})
    return {
        "status": "success",
        "access_token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"]
        }
    }

# Получение профиля пользователя
@app.get("/me")
async def me(user=Depends(get_current_user)):
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"]
    }

# Получение списка ботов
@app.get("/bots")
async def get_bots(user=Depends(get_current_user)):
    return {"bots": db.get_bots_for_user(user["id"])}

# Создание бота
@app.post("/bots")
async def create_bot(req: BotRequest, user=Depends(require_admin)):
    bot_id = db.create_bot(
        owner_id=user["id"],
        name=req.name,
        token=req.token,
        group_link=req.group_link or "",
        group_id=req.group_id or "",
        confirmation_string=req.confirmation_string or "",
    )
    return {"status": "success", "bot_id": bot_id}

# Обновление бота
@app.put("/bots/{bot_id}")
async def update_bot(bot_id: int, req: BotUpdateRequest, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    # Подготовка полей для обновления
    fields = _model_to_dict(req)
    ok = db.update_bot(bot_id, **fields)
    return {"status": "success" if ok else "error"}

# Удаление бота
@app.delete("/bots/{bot_id}")
async def delete_bot(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_bot(bot_id)
    return {"status": "success" if ok else "error"}

# Создание оператора
@app.post("/operators")
async def create_operator(req: OperatorCreateRequest, user=Depends(require_admin)):
    operator_id = db.create_user(
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
        role="operator",
        created_by=user["id"]
    )
    if not operator_id:
        return {"status": "error", "message": "Такой оператор уже существует"}

    for bot_id in req.bot_ids:
        ensure_bot_access(bot_id, user, admin_only=True)
        db.add_user_to_bot(bot_id, operator_id, "operator")

    return {"status": "success", "operator_id": operator_id}

# Получение списка операторов
@app.get("/operators")
async def get_operators(user=Depends(require_admin)):
    return {"operators": db.get_operators_for_admin(user["id"])}

# Обновление оператора
@app.put("/operators/{operator_id}")
async def update_operator(operator_id: int, req: OperatorUpdateRequest, user=Depends(require_admin)):
    if not db.get_operator_for_admin(operator_id, user["id"]):
        raise HTTPException(status_code=404, detail="Оператор не найден")

    username = (req.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Введите логин оператора")

    # Подготовка нового пароля оператора
    password_hash = hash_password(req.password.strip()) if req.password and req.password.strip() else None
    ok = db.update_operator_for_admin(
        operator_id=operator_id,
        admin_id=user["id"],
        username=username,
        email=req.email,
        password_hash=password_hash,
    )
    if ok is None:
        raise HTTPException(status_code=400, detail="Логин или email уже используется")
    if not ok:
        raise HTTPException(status_code=404, detail="Оператор не найден")
    return {"status": "success"}

# Удаление оператора
@app.delete("/operators/{operator_id}")
async def delete_operator(operator_id: int, user=Depends(require_admin)):
    ok = db.delete_operator_for_admin(operator_id, user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Оператор не найден")
    return {"status": "success"}

# Получение ответов базы знаний
@app.get("/rules")
async def get_rules(
    bot_id: int,
    search: Optional[str] = None,
    category: Optional[str] = None,
    user=Depends(get_current_user)
):
    ensure_bot_access(bot_id, user)

    try:
        rules = db.get_all_rules(bot_id, search, category)
    except TypeError:
        rules = db.get_all_rules(bot_id, search)
        if category:
            rules = [r for r in rules if (r.get("category") or "") == category]

    return {"rules": rules}

# Создание ответа базы знаний
@app.post("/rules")
async def create_rule(req: RuleRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    keyword = (req.keyword or "").strip()
    answer = (req.answer or "").strip()
    category = (req.category or "Общее").strip()
    if not keyword or not answer:
        raise HTTPException(status_code=400, detail="Заполните фразы пользователя и ответ")
    # Нормализация фраз для поиска
    normalized_keyword = ", ".join(normalize_text(x.strip()) for x in keyword.split(",") if x.strip())
    rule_id = db.add_rule_to_db(req.bot_id, keyword, answer, category, "", req.intent_id, normalized_keyword)
    return {"status": "success", "rule_id": rule_id}

# Обновление ответа базы знаний
@app.put("/rules/{rule_id}")
async def update_rule(rule_id: int, req: RuleRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    keyword = (req.keyword or "").strip()
    answer = (req.answer or "").strip()
    category = (req.category or "Общее").strip()
    if not keyword or not answer:
        raise HTTPException(status_code=400, detail="Заполните фразы пользователя и ответ")
    # Нормализация фраз для поиска
    normalized_keyword = ", ".join(normalize_text(x.strip()) for x in keyword.split(",") if x.strip())
    ok = db.update_rule_in_db(
        rule_id, req.bot_id, keyword, answer, category, "", req.intent_id, normalized_keyword
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Ответ не найден")
    return {"status": "success"}

# Удаление ответа базы знаний
@app.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_rule_from_db(rule_id, bot_id)
    return {"status": "success" if ok else "error"}

# Получение кнопок ответа
@app.get("/rules/{rule_id}/buttons")
async def get_rule_buttons(rule_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"buttons": db.get_rule_buttons(bot_id, rule_id)}

# Сохранение кнопок ответа
@app.put("/rules/{rule_id}/buttons")
async def save_rule_buttons(rule_id: int, req: RuleButtonsRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    rule = db.get_rule_by_id(req.bot_id, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    # Подготовка списка кнопок
    rows = []
    for index, button in enumerate(req.buttons):
        # Преобразование кнопки в словарь
        item = button.model_dump() if hasattr(button, "model_dump") else button.dict()
        item["sort_order"] = item.get("sort_order", index)
        rows.append(item)
    db.replace_rule_buttons(req.bot_id, rule_id, rows)
    return {"status": "success"}

# Получение категорий
@app.get("/categories")
async def get_categories(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"categories": db.get_all_categories(bot_id)}

# Сохранение категории
@app.post("/categories")
async def save_category(req: CategoryRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    if req.old_name:
        db.update_category(req.bot_id, req.old_name, req.name, req.description)
    else:
        db.add_category_to_db(req.bot_id, req.name, req.description)
    return {"status": "success"}

# Удаление категории
@app.delete("/categories/{cat_name}")
async def delete_category(cat_name: str, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    db.delete_category_by_name(bot_id, cat_name)
    return {"status": "success"}

# Получение интентов
@app.get("/intents")
async def get_intents(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"intents": db.get_intents(bot_id)}

# Создание интента
@app.post("/intents")
async def create_intent(req: IntentRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    # Нормализация примеров интента
    normalized = ", ".join(normalize_text(x.strip()) for x in (req.examples + "," + req.name).split(",") if x.strip())
    intent_id = db.add_intent(req.bot_id, req.name, req.examples, req.response, normalized)
    return {"status": "success", "intent_id": intent_id}

# Обновление интента
@app.put("/intents/{intent_id}")
async def update_intent(intent_id: int, req: IntentRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    # Нормализация примеров интента
    normalized = ", ".join(normalize_text(x.strip()) for x in (req.examples + "," + req.name).split(",") if x.strip())
    ok = db.update_intent(intent_id, req.bot_id, req.name, req.examples, req.response, normalized)
    return {"status": "success" if ok else "error"}

# Удаление интента
@app.delete("/intents/{intent_id}")
async def delete_intent(intent_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_intent(intent_id, bot_id)
    return {"status": "success" if ok else "error"}

# Обновление обучения бота
@app.post("/train")
async def train(req: TrainRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    result = train_bot_model(req.bot_id)
    return result

# Получение интеграций
@app.get("/integrations")
async def get_integrations(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"integrations": db.get_integrations(bot_id)}

# Сохранение интеграции
@app.post("/integrations")
async def save_integration(req: IntegrationRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    integration_id = db.upsert_integration(req.bot_id, req.integration_type, req.settings, req.is_enabled)
    return {"status": "success", "integration_id": integration_id}

# Удаление интеграции
@app.delete("/integrations/{integration_id}")
async def delete_integration(integration_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_integration(integration_id, bot_id)
    return {"status": "success" if ok else "error"}

# Проверка перехода сценария
def _validate_scenario_edge(bot_id: int, from_node_id: int, to_node_id: int, label: str) -> None:
    if not (label or "").strip():
        raise HTTPException(status_code=400, detail="Введите текст перехода")
    if from_node_id == to_node_id:
        raise HTTPException(status_code=400, detail="Переход не должен вести в тот же самый шаг")
    # Проверка начального блока перехода
    from_node = db.get_scenario_node(bot_id, from_node_id)
    # Проверка конечного блока перехода
    to_node = db.get_scenario_node(bot_id, to_node_id)
    if not from_node or not to_node:
        raise HTTPException(status_code=400, detail="Оба шага перехода должны принадлежать выбранному боту")

# Получение блоков сценария
@app.get("/scenarios/nodes")
async def get_scenario_nodes(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"nodes": db.get_scenario_nodes(bot_id)}

# Создание блока сценария
@app.post("/scenarios/nodes")
async def create_scenario_node(req: ScenarioNodeRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Введите название шага")
    if req.node_type not in {"message", "question", "operator", "end"}:
        raise HTTPException(status_code=400, detail="Некорректный тип шага")
    node_id = db.add_scenario_node(
        req.bot_id, title, req.node_type, req.message or "", req.is_start, req.position_x, req.position_y
    )
    return {"status": "success", "node_id": node_id}

# Обновление блока сценария
@app.put("/scenarios/nodes/{node_id}")
async def update_scenario_node(node_id: int, req: ScenarioNodeRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Введите название шага")
    if req.node_type not in {"message", "question", "operator", "end"}:
        raise HTTPException(status_code=400, detail="Некорректный тип шага")
    ok = db.update_scenario_node(
        node_id, req.bot_id, title, req.node_type, req.message or "", req.is_start, req.position_x, req.position_y
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Шаг не найден")
    return {"status": "success"}

# Удаление блока сценария
@app.delete("/scenarios/nodes/{node_id}")
async def delete_scenario_node(node_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_scenario_node(node_id, bot_id)
    return {"status": "success" if ok else "error"}

# Получение переходов сценария
@app.get("/scenarios/edges")
async def get_scenario_edges(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"edges": db.get_scenario_edges(bot_id)}

# Создание перехода сценария
@app.post("/scenarios/edges")
async def create_scenario_edge(req: ScenarioEdgeRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    _validate_scenario_edge(req.bot_id, req.from_node_id, req.to_node_id, req.label)
    edge_id = db.add_scenario_edge(
        req.bot_id, req.from_node_id, req.to_node_id, req.label.strip(), req.condition_text or "", req.sort_order
    )
    return {"status": "success", "edge_id": edge_id}

# Обновление перехода сценария
@app.put("/scenarios/edges/{edge_id}")
async def update_scenario_edge(edge_id: int, req: ScenarioEdgeRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    _validate_scenario_edge(req.bot_id, req.from_node_id, req.to_node_id, req.label)
    ok = db.update_scenario_edge(
        edge_id, req.bot_id, req.from_node_id, req.to_node_id, req.label.strip(), req.condition_text or "", req.sort_order
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Переход не найден")
    return {"status": "success"}

# Удаление перехода сценария
@app.delete("/scenarios/edges/{edge_id}")
async def delete_scenario_edge(edge_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_scenario_edge(edge_id, bot_id)
    return {"status": "success" if ok else "error"}

# Получение статистики бота
@app.get("/analytics")
async def analytics(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return db.get_analytics(bot_id)

# Получение популярных вопросов
@app.get("/stats/{bot_id}")
async def stats(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"popular": db.get_popular_questions(bot_id)}

# Получение последних диалогов
@app.get("/logs")
async def logs(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"logs": db.get_logs(bot_id)}

# Получение истории диалога
@app.get("/dialogs/{messenger_user_id}")
async def dialog_history(messenger_user_id: str, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"messages": db.get_dialog_messages(bot_id, messenger_user_id)}

# Отправка сообщения во ВКонтакте
async def send_vk_message(bot: dict, peer_id: str, message: str, keyboard: Optional[str] = None):
    # Подготовка параметров сообщения
    params = {
        "peer_id": peer_id,
        "message": message,
        "random_id": 0,
        "access_token": bot["token"],
        "v": "5.199"
    }
    # Добавление клавиатуры к сообщению
    if keyboard:
        params["keyboard"] = keyboard

    try:
        # Отправка запроса во ВКонтакте
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://api.vk.com/method/messages.send", params=params)
            data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"VK недоступен: {exc}")

    # Проверка ошибки отправки
    if data.get("error"):
        err = data["error"]
        raise HTTPException(status_code=502, detail=f"VK error {err.get('error_code')}: {err.get('error_msg')}")
    return data

# Отправка ответа оператора
@app.post("/operator/reply")
async def operator_reply(req: ManualReplyRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user)
    bot = db.get_bot(req.bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Бот не найден")

    await send_vk_message(bot, req.messenger_user_id, req.message)
    db.set_operator_mode(req.bot_id, req.messenger_user_id, True)
    db.log_message(
        req.bot_id,
        req.messenger_user_id,
        "ОТВЕТ ОПЕРАТОРА",
        req.message,
        operator_id=user["id"],
        answer_source="operator",
    )
    return {"status": "success"}

# Завершение работы оператора
@app.post("/operator/stop/{messenger_user_id}")
async def stop_operator(messenger_user_id: str, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    bot = db.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Бот не найден")

    db.set_operator_mode(bot_id, messenger_user_id, False)
    await send_vk_message(bot, messenger_user_id, bot["operator_finished_message"])
    db.log_message(
        bot_id,
        messenger_user_id,
        "ОПЕРАТОР ЗАВЕРШИЛ ДИАЛОГ",
        bot["operator_finished_message"],
        answer_source="system"
    )
    return {"status": "success"}

# Создание кнопки клавиатуры
def _button(label: str, color: str = "primary") -> dict:
    return {"action": {"type": "text", "label": label}, "color": color}

# Формирование главной клавиатуры
def build_main_keyboard(categories, start_nodes=None):
    # Подготовка списка кнопок
    btns = []
    # Добавление кнопок начальных сценариев
    for node in (start_nodes or []):
        btns.append([_button(node["title"], "primary")])
    # Добавление кнопок категорий
    for c in categories:
        btns.append([_button(c["name"], "secondary")])
    btns.append([_button("Нет нужного варианта", "secondary")])
    return json.dumps({"one_time": False, "buttons": btns, "inline": False}, ensure_ascii=False)

# Формирование клавиатуры переходов
def build_edges_keyboard(edges):
    btns = [[_button(e["label"])] for e in edges]
    btns.append([_button("◀️ В главное меню", "secondary")])
    return json.dumps({"one_time": False, "buttons": btns, "inline": False}, ensure_ascii=False)

# Формирование клавиатуры подтверждения
def build_confirmation_keyboard():
    return json.dumps({
        "inline": True,
        "buttons": [
            [_button("Да, спасибо", "positive")],
            [_button("Нет, другой вопрос", "negative")]
        ]
    }, ensure_ascii=False)

# Подготовка ответа из базы знаний
def _rule_answer_and_keyboard(bot: dict, bot_id: int, rule: dict):
    return f"{rule['answer']}\n\n{bot['answer_confirmation_message']}", build_confirmation_keyboard(), []

# Отправка ответа из базы знаний
async def send_rule_answer(bot: dict, bot_id: int, vk_id: str, user_text: str, rule: dict, source: str = "rule"):
    response, keyboard, _ = _rule_answer_and_keyboard(bot, bot_id, rule)
    db.set_current_rule(bot_id, vk_id, None)
    db.set_current_category(bot_id, vk_id, rule.get("category"))
    db.set_current_scenario_node(bot_id, vk_id, None)
    await send_vk_message(bot, vk_id, response, keyboard)
    db.log_message(bot_id, vk_id, user_text, response, confidence=1.0, answer_source=source)
    return True

# Формирование клавиатуры категории
def _category_keyboard_from_rules(rules):
    # Подготовка списка кнопок
    btns = []
    for r in rules:
        # Получение короткого текста кнопки
        label = r["keyword"].split(",")[0].strip()
        btns.append([_button(label)])
    btns.append([_button("◀️ В главное меню", "secondary")])
    return json.dumps({"one_time": False, "buttons": btns, "inline": False}, ensure_ascii=False)

# Показ выбранной категории
async def show_category(
    bot: dict,
    bot_id: int,
    vk_id: str,
    category_name: str,
    user_text: str,
    categories,
    start_nodes=None,
):
    db.set_current_category(bot_id, vk_id, category_name)
    db.set_current_rule(bot_id, vk_id, None)
    db.set_current_scenario_node(bot_id, vk_id, None)
    # Получение ответов выбранной категории
    rules = db.get_rules_by_category(bot_id, category_name)
    if rules:
        response = f"Раздел '{category_name}'. Выберите, что хотите узнать:"
        keyboard = _category_keyboard_from_rules(rules)
    else:
        response = "В этой категории пока нет ответов. Попробуйте ввести вопрос текстом."
        keyboard = build_main_keyboard(categories, start_nodes or db.get_start_scenario_nodes(bot_id))
    await send_vk_message(bot, vk_id, response, keyboard)
    db.log_message(bot_id, vk_id, user_text, response, answer_source="system")
    return True

# Обработка кнопки ответа
async def process_rule_button(bot: dict, bot_id: int, vk_id: str, text: str, state: Optional[dict], categories):
    return False

# Получение имени пользователя ВКонтакте
async def get_vk_user_name(bot: dict, vk_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            user_req = await client.get("https://api.vk.com/method/users.get", params={
                "user_ids": vk_id,
                "access_token": bot["token"],
                "v": "5.199"
            })
            # Чтение ответа с данными пользователя
            data = user_req.json()
            return data.get("response", [{}])[0].get("first_name", "пользователь")
    except Exception:
        return "пользователь"

# Получение названия сообщества ВКонтакте
async def get_vk_group_name(bot: dict) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            group_req = await client.get("https://api.vk.com/method/groups.getById", params={
                "access_token": bot["token"],
                "v": "5.199"
            })
            # Чтение ответа с данными сообщества
            data = group_req.json()
            if isinstance(data.get("response"), dict):
                groups = data["response"].get("groups", [])
                if groups:
                    return groups[0].get("name", "нашей компании")
            if isinstance(data.get("response"), list) and data["response"]:
                return data["response"][0].get("name", "нашей компании")
    except Exception:
        pass
    return "нашей компании"

# Проверка совпадения перехода
def _edge_matches(edge: dict, text_l: str) -> bool:
    label = (edge.get("label") or "").lower().strip()
    condition = (edge.get("condition_text") or "").lower().strip()
    # Формирование вариантов текста перехода
    variants = [label] + [x.strip().lower() for x in condition.replace("\n", ",").split(",") if x.strip()]

    # Нормализация сообщения пользователя
    normalized_text = normalize_text(text_l)
    normalized_variants = [normalize_text(v) for v in variants]

    return text_l in variants or normalized_text in normalized_variants

# Формирование текста блока сценария
def _format_node_message(node: dict, bot: dict) -> str:
    if node["node_type"] == "operator":
        return node.get("message") or bot.get("operator_called_message") or "Я позвал оператора."
    if node["node_type"] == "end":
        return node.get("message") or bot.get("positive_close_message") or "Готово."
    return node.get("message") or node.get("title") or "Продолжим."

# Запуск блока сценария
async def _activate_scenario_node(bot: dict, bot_id: int, vk_id: str, node: dict, user_text: str):
    """
    Активирует выбранный блок сценария.
    Важно: финальный блок теперь отправляет своё сообщение и сразу возвращает главное меню.
    """
    # Получение переходов текущего блока
    edges = db.get_edges_from_node(bot_id, node["id"])
    response = _format_node_message(node, bot)
    node_type = node["node_type"]

    # Обработка блока вызова оператора
    if node_type == "operator":
        db.set_operator_mode(bot_id, vk_id, True)
        db.set_current_scenario_node(bot_id, vk_id, None)

        await send_vk_message(bot, vk_id, response)
        db.log_message(
            bot_id,
            vk_id,
            user_text,
            response,
            confidence=1.0,
            answer_source="operator_request"
        )
        return True

    # Обработка завершающего блока
    if node_type == "end":
        db.set_operator_mode(bot_id, vk_id, False)
        db.set_current_scenario_node(bot_id, vk_id, None)
        db.set_current_category(bot_id, vk_id, None)
        db.set_current_rule(bot_id, vk_id, None)

        # Получение данных для главного меню
        categories = db.get_all_categories(bot_id)
        start_nodes = db.get_start_scenario_nodes(bot_id)
        main_keyboard = build_main_keyboard(categories, start_nodes)

        restart_text = bot.get("restart_message") or "Выберите, чем помочь дальше:"
        final_text = response + "\n\n" + restart_text

        await send_vk_message(bot, vk_id, final_text, main_keyboard)
        db.log_message(
            bot_id,
            vk_id,
            user_text,
            final_text,
            confidence=1.0,
            answer_source="scenario_end",
            is_success=True
        )
        return True

    db.set_operator_mode(bot_id, vk_id, False)
    db.set_current_scenario_node(bot_id, vk_id, node["id"])

    # Подготовка клавиатуры переходов
    keyboard = build_edges_keyboard(edges) if edges else None
    await send_vk_message(bot, vk_id, response, keyboard)
    db.log_message(
        bot_id,
        vk_id,
        user_text,
        response,
        confidence=1.0,
        answer_source="scenario"
    )
    return True

# Обработка текущего сценария
async def _try_process_scenario(bot: dict, bot_id: int, vk_id: str, text: str, state: Optional[dict]):
    """
    Обработка сценариев.
    Эта функция должна быть ОТДЕЛЬНОЙ, а не внутри _activate_scenario_node.
    Именно её отсутствие вызывало ошибку:
    NameError: name '_try_process_scenario' is not defined.
    """
    text_l = (text or "").lower().strip()

    # Проверка активного сценария
    if state and state.get("current_scenario_node_id"):
        current_node_id = state["current_scenario_node_id"]
        # Получение переходов текущего блока
        edges = db.get_edges_from_node(bot_id, current_node_id)

        # Поиск подходящего перехода
        for edge in edges:
            if _edge_matches(edge, text_l):
                next_node = db.get_scenario_node(bot_id, edge["to_node_id"])
                if next_node:
                    return await _activate_scenario_node(bot, bot_id, vk_id, next_node, text)

        if edges:
            response = "Выберите один из вариантов ниже или нажмите «В главное меню»."
            await send_vk_message(bot, vk_id, response, build_edges_keyboard(edges))
            db.log_message(bot_id, vk_id, text, response, answer_source="scenario_help")
            return True

        return False

    # Поиск сценария по сообщению пользователя
    start_node = db.find_start_node_by_text(bot_id, text)
    if start_node:
        return await _activate_scenario_node(bot, bot_id, vk_id, start_node, text)

    return False

# Обработка входящего события ВКонтакте
@app.post("/vk/{bot_id}")
async def vk_handler(bot_id: int, request: Request):
    # Получение бота по адресу события
    bot = db.get_bot(bot_id)
    if not bot or not bot["is_active"]:
        return PlainTextResponse("bot_not_found", status_code=404)

    # Чтение события от ВКонтакте
    data = await request.json()

    # Подтверждение адреса сервера
    if data.get("type") == "confirmation":
        return PlainTextResponse(bot.get("confirmation_string") or "")

    # Игнорирование неподдерживаемых событий
    if data.get("type") != "message_new":
        return PlainTextResponse("ok")

    # Получение текста входящего сообщения
    msg = data["object"]["message"]
    vk_id = str(msg["from_id"])
    text = msg.get("text", "").strip()
    text_l = text.lower().strip()

    # Получение данных для главного меню
    categories = db.get_all_categories(bot_id)
    start_nodes = db.get_start_scenario_nodes(bot_id)

    # Обработка положительной оценки ответа
    if text_l == "да, спасибо":
        db.set_operator_mode(bot_id, vk_id, False)
        db.set_current_rule(bot_id, vk_id, None)
        db.set_current_scenario_node(bot_id, vk_id, None)
        db.mark_last_answer_success(bot_id, vk_id, True)
        await send_vk_message(bot, vk_id, bot["positive_close_message"])
        # Сохранение результата обработки сообщения
        db.log_message(bot_id, vk_id, text, bot["positive_close_message"], answer_source="system", is_success=True)
        return PlainTextResponse("ok")

    # Обработка отрицательной оценки ответа
    if text_l == "нет, другой вопрос":
        db.set_operator_mode(bot_id, vk_id, False)
        db.set_current_category(bot_id, vk_id, None)
        db.set_current_rule(bot_id, vk_id, None)
        db.set_current_scenario_node(bot_id, vk_id, None)
        db.mark_last_answer_success(bot_id, vk_id, False)
        keyboard = build_main_keyboard(categories, start_nodes)
        await send_vk_message(bot, vk_id, bot["restart_message"], keyboard)
        # Сохранение результата обработки сообщения
        db.log_message(bot_id, vk_id, text, bot["restart_message"], answer_source="system", is_success=False)
        return PlainTextResponse("ok")

    state = db.get_chat_state(bot_id, vk_id)

    # Проверка режима ожидания оператора
    if state and state.get("is_operator_mode"):
        # Сохранение результата обработки сообщения
        db.log_message(bot_id, vk_id, text, "ОЖИДАНИЕ ОПЕРАТОРА", answer_source="operator_wait")
        return PlainTextResponse("ok")

    # Обработка команды главного меню
    if text_l in ["начать", "меню", "◀️ в главное меню", "в главное меню", "назад", "привет", "старт", "hi", "здравствуйте"]:
        db.set_operator_mode(bot_id, vk_id, False)
        db.set_current_category(bot_id, vk_id, None)
        db.set_current_rule(bot_id, vk_id, None)
        db.set_current_scenario_node(bot_id, vk_id, None)
        first_name = await get_vk_user_name(bot, vk_id)
        group_name = await get_vk_group_name(bot)
        bot_response = bot["greeting_message"].format(first_name=first_name, group_name=group_name)
        keyboard = build_main_keyboard(categories, start_nodes)
        await send_vk_message(bot, vk_id, bot_response, keyboard)
        # Сохранение результата обработки сообщения
        db.log_message(bot_id, vk_id, text, bot_response, answer_source="system")
        return PlainTextResponse("ok")

    state = db.get_chat_state(bot_id, vk_id)
    # Попытка продолжить сценарий
    if await _try_process_scenario(bot, bot_id, vk_id, text, state):
        return PlainTextResponse("ok")

    # Проверка выбора категории
    if text_l in [c["name"].lower() for c in categories]:
        cat_name = next(c["name"] for c in categories if c["name"].lower() == text_l)
        await show_category(bot, bot_id, vk_id, cat_name, text, categories, start_nodes)
        return PlainTextResponse("ok")

    # Обработка запроса без подходящего варианта
    if text_l == "нет нужного варианта":
        db.set_current_rule(bot_id, vk_id, None)
        bot_response = bot["fallback_message"]
        await send_vk_message(bot, vk_id, bot_response)
        # Сохранение результата обработки сообщения
        db.log_message(bot_id, vk_id, text, bot_response, answer_source="fallback")
        return PlainTextResponse("ok")

    state = db.get_chat_state(bot_id, vk_id)
    current_category = state.get("current_category") if state else None

    # Поиск ответа в базе знаний
    result = get_answer(user_text=text, bot_id=bot_id, category=current_category)

    # Обработка найденного ответа
    if result and result.get("answer"):
        answer_source = result.get("source") or "bot"
        rule = db.get_rule_by_id(bot_id, result["rule_id"]) if result.get("rule_id") else None
        if rule:
            bot_response, keyboard, custom_buttons = _rule_answer_and_keyboard(bot, bot_id, rule)
            db.set_current_rule(bot_id, vk_id, rule["id"] if custom_buttons else None)
        else:
            bot_response = f"{result['answer']}\n\n{bot['answer_confirmation_message']}"
            keyboard = build_confirmation_keyboard()
            db.set_current_rule(bot_id, vk_id, None)
    else:
        db.set_current_rule(bot_id, vk_id, None)
        db.set_operator_mode(bot_id, vk_id, True)
        bot_response = bot.get("operator_called_message") or "Думаю, с этим вопросом лучше разберется оператор. Я уже позвал его."
        keyboard = json.dumps({"buttons": [], "one_time": True}, ensure_ascii=False)
        answer_source = "operator_request"

    db.log_message(
        bot_id,
        vk_id,
        text,
        bot_response,
        confidence=result.get("confidence") if result else None,
        answer_source=answer_source,
    )

    # Отправка ответа пользователю
    vk_send_result = await send_vk_message(bot, vk_id, bot_response, keyboard)
    print("VK SEND RESULT:", vk_send_result)

    return PlainTextResponse("ok")
