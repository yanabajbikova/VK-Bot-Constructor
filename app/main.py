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

# МОДУЛЬ: ОСНОВНОЙ СЕРВЕР FASTAPI

load_dotenv()

app = FastAPI(title="Платформа-конструктор чат-ботов")
db.init_db()

BASE_DIR = os.path.dirname(__file__)
static_path = os.path.join(BASE_DIR, "static")
templates = Jinja2Templates(directory=static_path)
app.mount("/static", StaticFiles(directory=static_path), name="static")

SECRET_KEY = os.getenv("SECRET_KEY", "change_me_please_2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

# ===== МОДЕЛИ =====

class RegisterRequest(BaseModel):
    username: str
    password: str
    email: Optional[EmailStr] = None

class LoginRequest(BaseModel):
    username: str
    password: str

class BotRequest(BaseModel):
    name: str
    token: str
    group_link: Optional[str] = ""
    group_id: Optional[str] = ""
    confirmation_string: Optional[str] = ""

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

class RuleRequest(BaseModel):
    bot_id: int
    keyword: str
    answer: str
    category: str = "Общее"
    buttons: str = ""
    intent_id: Optional[int] = None

class RuleButtonItem(BaseModel):
    label: str
    action: str = "finish"
    target_rule_id: Optional[int] = None
    target_category: Optional[str] = None
    sort_order: int = 0

class RuleButtonsRequest(BaseModel):
    bot_id: int
    buttons: List[RuleButtonItem] = []

class CategoryRequest(BaseModel):
    bot_id: int
    name: str
    description: str = ""
    old_name: Optional[str] = None

class IntentRequest(BaseModel):
    bot_id: int
    name: str
    examples: str = ""
    response: str = ""

class IntegrationRequest(BaseModel):
    bot_id: int
    integration_type: str = Field(..., description="Например: vk, natasha, telegram, web")
    settings: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True

class ScenarioNodeRequest(BaseModel):
    bot_id: int
    title: str
    node_type: str = "message"
    message: str = ""
    is_start: bool = False
    position_x: int = 80
    position_y: int = 80

class ScenarioEdgeRequest(BaseModel):
    bot_id: int
    from_node_id: int
    to_node_id: int
    label: str
    condition_text: str = ""
    sort_order: int = 0

class OperatorCreateRequest(BaseModel):
    username: str
    password: str
    email: Optional[EmailStr] = None
    bot_ids: List[int] = []

class OperatorUpdateRequest(BaseModel):
    username: str
    email: Optional[EmailStr] = None
    password: Optional[str] = None

class ManualReplyRequest(BaseModel):
    bot_id: int
    messenger_user_id: str
    message: str

class TrainRequest(BaseModel):
    bot_id: int

# ===== БЕЗОПАСНОСТЬ =====

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)

def create_access_token(data: dict):
    payload = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload.update({"exp": expire})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def _model_to_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True)
    return model.dict(exclude_unset=True)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Необходима авторизация")

    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Некорректный токен")

    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user

def require_admin(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Недостаточно прав администратора")
    return user

def ensure_bot_access(bot_id: int, user: dict, admin_only: bool = False):
    role = db.get_user_bot_role(user["id"], bot_id)
    if not role:
        raise HTTPException(status_code=403, detail="Нет доступа к этому боту")
    if admin_only and role != "admin":
        raise HTTPException(status_code=403, detail="Действие доступно только администратору бота")
    return role

# ===== ИНТЕРФЕЙС =====

@app.get("/")
async def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# ===== АВТОРИЗАЦИЯ =====

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

@app.get("/me")
async def me(user=Depends(get_current_user)):
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"]
    }

# ===== БОТЫ =====

@app.get("/bots")
async def get_bots(user=Depends(get_current_user)):
    return {"bots": db.get_bots_for_user(user["id"])}

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

@app.put("/bots/{bot_id}")
async def update_bot(bot_id: int, req: BotUpdateRequest, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    fields = _model_to_dict(req)
    ok = db.update_bot(bot_id, **fields)
    return {"status": "success" if ok else "error"}

@app.delete("/bots/{bot_id}")
async def delete_bot(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_bot(bot_id)
    return {"status": "success" if ok else "error"}

# ===== ОПЕРАТОРЫ =====

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

@app.get("/operators")
async def get_operators(user=Depends(require_admin)):
    return {"operators": db.get_operators_for_admin(user["id"])}

@app.put("/operators/{operator_id}")
async def update_operator(operator_id: int, req: OperatorUpdateRequest, user=Depends(require_admin)):
    if not db.get_operator_for_admin(operator_id, user["id"]):
        raise HTTPException(status_code=404, detail="Оператор не найден")

    username = (req.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Введите логин оператора")

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

@app.delete("/operators/{operator_id}")
async def delete_operator(operator_id: int, user=Depends(require_admin)):
    ok = db.delete_operator_for_admin(operator_id, user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Оператор не найден")
    return {"status": "success"}

# ===== БАЗА ЗНАНИЙ =====

@app.get("/rules")
async def get_rules(
    bot_id: int,
    search: Optional[str] = None,
    category: Optional[str] = None,
    user=Depends(get_current_user)
):
    ensure_bot_access(bot_id, user)

    # В обновлённой database.py get_all_rules принимает category.
    # Блок try оставлен для совместимости со старой database.py, где было только 2 аргумента.
    try:
        rules = db.get_all_rules(bot_id, search, category)
    except TypeError:
        rules = db.get_all_rules(bot_id, search)
        if category:
            rules = [r for r in rules if (r.get("category") or "") == category]

    return {"rules": rules}

@app.post("/rules")
async def create_rule(req: RuleRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    keyword = (req.keyword or "").strip()
    answer = (req.answer or "").strip()
    category = (req.category or "Общее").strip()
    if not keyword or not answer:
        raise HTTPException(status_code=400, detail="Заполните фразы пользователя и ответ")
    normalized_keyword = ", ".join(normalize_text(x.strip()) for x in keyword.split(",") if x.strip())
    rule_id = db.add_rule_to_db(req.bot_id, keyword, answer, category, "", req.intent_id, normalized_keyword)
    return {"status": "success", "rule_id": rule_id}

@app.put("/rules/{rule_id}")
async def update_rule(rule_id: int, req: RuleRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    keyword = (req.keyword or "").strip()
    answer = (req.answer or "").strip()
    category = (req.category or "Общее").strip()
    if not keyword or not answer:
        raise HTTPException(status_code=400, detail="Заполните фразы пользователя и ответ")
    normalized_keyword = ", ".join(normalize_text(x.strip()) for x in keyword.split(",") if x.strip())
    ok = db.update_rule_in_db(
        rule_id, req.bot_id, keyword, answer, category, "", req.intent_id, normalized_keyword
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Ответ не найден")
    return {"status": "success"}

@app.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_rule_from_db(rule_id, bot_id)
    return {"status": "success" if ok else "error"}

@app.get("/rules/{rule_id}/buttons")
async def get_rule_buttons(rule_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"buttons": db.get_rule_buttons(bot_id, rule_id)}

@app.put("/rules/{rule_id}/buttons")
async def save_rule_buttons(rule_id: int, req: RuleButtonsRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    rule = db.get_rule_by_id(req.bot_id, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    rows = []
    for index, button in enumerate(req.buttons):
        item = button.model_dump() if hasattr(button, "model_dump") else button.dict()
        item["sort_order"] = item.get("sort_order", index)
        rows.append(item)
    db.replace_rule_buttons(req.bot_id, rule_id, rows)
    return {"status": "success"}

# ===== КАТЕГОРИИ =====

@app.get("/categories")
async def get_categories(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"categories": db.get_all_categories(bot_id)}

@app.post("/categories")
async def save_category(req: CategoryRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    if req.old_name:
        db.update_category(req.bot_id, req.old_name, req.name, req.description)
    else:
        db.add_category_to_db(req.bot_id, req.name, req.description)
    return {"status": "success"}

@app.delete("/categories/{cat_name}")
async def delete_category(cat_name: str, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    db.delete_category_by_name(bot_id, cat_name)
    return {"status": "success"}

# ===== ИНТЕНТЫ =====

@app.get("/intents")
async def get_intents(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"intents": db.get_intents(bot_id)}

@app.post("/intents")
async def create_intent(req: IntentRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    normalized = ", ".join(normalize_text(x.strip()) for x in (req.examples + "," + req.name).split(",") if x.strip())
    intent_id = db.add_intent(req.bot_id, req.name, req.examples, req.response, normalized)
    return {"status": "success", "intent_id": intent_id}

@app.put("/intents/{intent_id}")
async def update_intent(intent_id: int, req: IntentRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    normalized = ", ".join(normalize_text(x.strip()) for x in (req.examples + "," + req.name).split(",") if x.strip())
    ok = db.update_intent(intent_id, req.bot_id, req.name, req.examples, req.response, normalized)
    return {"status": "success" if ok else "error"}

@app.delete("/intents/{intent_id}")
async def delete_intent(intent_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_intent(intent_id, bot_id)
    return {"status": "success" if ok else "error"}

@app.post("/train")
async def train(req: TrainRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    result = train_bot_model(req.bot_id)
    return result

# ===== ИНТЕГРАЦИИ =====

@app.get("/integrations")
async def get_integrations(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"integrations": db.get_integrations(bot_id)}

@app.post("/integrations")
async def save_integration(req: IntegrationRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    integration_id = db.upsert_integration(req.bot_id, req.integration_type, req.settings, req.is_enabled)
    return {"status": "success", "integration_id": integration_id}

@app.delete("/integrations/{integration_id}")
async def delete_integration(integration_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_integration(integration_id, bot_id)
    return {"status": "success" if ok else "error"}

# ===== ВИЗУАЛЬНЫЙ РЕДАКТОР СЦЕНАРИЕВ =====

def _validate_scenario_edge(bot_id: int, from_node_id: int, to_node_id: int, label: str) -> None:
    if not (label or "").strip():
        raise HTTPException(status_code=400, detail="Введите текст перехода")
    if from_node_id == to_node_id:
        raise HTTPException(status_code=400, detail="Переход не должен вести в тот же самый шаг")
    from_node = db.get_scenario_node(bot_id, from_node_id)
    to_node = db.get_scenario_node(bot_id, to_node_id)
    if not from_node or not to_node:
        raise HTTPException(status_code=400, detail="Оба шага перехода должны принадлежать выбранному боту")

@app.get("/scenarios/nodes")
async def get_scenario_nodes(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"nodes": db.get_scenario_nodes(bot_id)}

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

@app.delete("/scenarios/nodes/{node_id}")
async def delete_scenario_node(node_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_scenario_node(node_id, bot_id)
    return {"status": "success" if ok else "error"}

@app.get("/scenarios/edges")
async def get_scenario_edges(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"edges": db.get_scenario_edges(bot_id)}

@app.post("/scenarios/edges")
async def create_scenario_edge(req: ScenarioEdgeRequest, user=Depends(get_current_user)):
    ensure_bot_access(req.bot_id, user, admin_only=True)
    _validate_scenario_edge(req.bot_id, req.from_node_id, req.to_node_id, req.label)
    edge_id = db.add_scenario_edge(
        req.bot_id, req.from_node_id, req.to_node_id, req.label.strip(), req.condition_text or "", req.sort_order
    )
    return {"status": "success", "edge_id": edge_id}

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

@app.delete("/scenarios/edges/{edge_id}")
async def delete_scenario_edge(edge_id: int, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user, admin_only=True)
    ok = db.delete_scenario_edge(edge_id, bot_id)
    return {"status": "success" if ok else "error"}

# ===== АНАЛИТИКА И ДИАЛОГИ =====

@app.get("/analytics")
async def analytics(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return db.get_analytics(bot_id)

@app.get("/stats/{bot_id}")
async def stats(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"popular": db.get_popular_questions(bot_id)}

@app.get("/logs")
async def logs(bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"logs": db.get_logs(bot_id)}

@app.get("/dialogs/{messenger_user_id}")
async def dialog_history(messenger_user_id: str, bot_id: int, user=Depends(get_current_user)):
    ensure_bot_access(bot_id, user)
    return {"messages": db.get_dialog_messages(bot_id, messenger_user_id)}

# ===== ОТПРАВКА В VK И ОПЕРАТОР =====

async def send_vk_message(bot: dict, peer_id: str, message: str, keyboard: Optional[str] = None):
    params = {
        "peer_id": peer_id,
        "message": message,
        "random_id": 0,
        "access_token": bot["token"],
        "v": "5.199"
    }
    if keyboard:
        params["keyboard"] = keyboard

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://api.vk.com/method/messages.send", params=params)
            data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"VK недоступен: {exc}")

    if data.get("error"):
        err = data["error"]
        raise HTTPException(status_code=502, detail=f"VK error {err.get('error_code')}: {err.get('error_msg')}")
    return data

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

# ===== VK CALLBACK ДЛЯ НЕСКОЛЬКИХ БОТОВ =====

def _button(label: str, color: str = "primary") -> dict:
    return {"action": {"type": "text", "label": label}, "color": color}

def build_main_keyboard(categories, start_nodes=None):
    btns = []
    for node in (start_nodes or []):
        btns.append([_button(node["title"], "primary")])
    for c in categories:
        btns.append([_button(c["name"], "secondary")])
    btns.append([_button("Нет нужного варианта", "secondary")])
    return json.dumps({"one_time": False, "buttons": btns, "inline": False}, ensure_ascii=False)

def build_edges_keyboard(edges):
    btns = [[_button(e["label"])] for e in edges]
    btns.append([_button("◀️ в главное меню", "secondary")])
    return json.dumps({"one_time": False, "buttons": btns, "inline": False}, ensure_ascii=False)

def build_confirmation_keyboard():
    return json.dumps({
        "inline": True,
        "buttons": [
            [_button("Да, спасибо", "positive")],
            [_button("Нет, другой вопрос", "negative")]
        ]
    }, ensure_ascii=False)

def _rule_answer_and_keyboard(bot: dict, bot_id: int, rule: dict):
    # База знаний теперь хранит только простой ответ. Ветки и кнопки живут в сценариях.
    return f"{rule['answer']}\n\n{bot['answer_confirmation_message']}", build_confirmation_keyboard(), []

async def send_rule_answer(bot: dict, bot_id: int, vk_id: str, user_text: str, rule: dict, source: str = "rule"):
    response, keyboard, _ = _rule_answer_and_keyboard(bot, bot_id, rule)
    db.set_current_rule(bot_id, vk_id, None)
    db.set_current_category(bot_id, vk_id, rule.get("category"))
    db.set_current_scenario_node(bot_id, vk_id, None)
    await send_vk_message(bot, vk_id, response, keyboard)
    db.log_message(bot_id, vk_id, user_text, response, confidence=1.0, answer_source=source)
    return True

def _category_keyboard_from_rules(rules):
    btns = []
    for r in rules:
        label = r["keyword"].split(",")[0].strip()
        btns.append([_button(label)])
    btns.append([_button("◀️ в главное меню", "secondary")])
    return json.dumps({"one_time": False, "buttons": btns, "inline": False}, ensure_ascii=False)

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

async def process_rule_button(bot: dict, bot_id: int, vk_id: str, text: str, state: Optional[dict], categories):
    # Оставлено для совместимости со старыми данными. Новые кнопки настраиваются только в сценариях.
    return False

async def get_vk_user_name(bot: dict, vk_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            user_req = await client.get("https://api.vk.com/method/users.get", params={
                "user_ids": vk_id,
                "access_token": bot["token"],
                "v": "5.199"
            })
            data = user_req.json()
            return data.get("response", [{}])[0].get("first_name", "пользователь")
    except Exception:
        return "пользователь"

async def get_vk_group_name(bot: dict) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            group_req = await client.get("https://api.vk.com/method/groups.getById", params={
                "access_token": bot["token"],
                "v": "5.199"
            })
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

def _edge_matches(edge: dict, text_l: str) -> bool:
    # Сценарий должен быть предсказуемым: переход срабатывает на точный текст кнопки
    # или на одну из дополнительных фраз. Сравниваем и обычный текст, и нормализованный.
    label = (edge.get("label") or "").lower().strip()
    condition = (edge.get("condition_text") or "").lower().strip()
    variants = [label] + [x.strip().lower() for x in condition.replace("\n", ",").split(",") if x.strip()]

    normalized_text = normalize_text(text_l)
    normalized_variants = [normalize_text(v) for v in variants]

    return text_l in variants or normalized_text in normalized_variants

def _format_node_message(node: dict, bot: dict) -> str:
    if node["node_type"] == "operator":
        return node.get("message") or bot.get("operator_called_message") or "Я позвал оператора."
    if node["node_type"] == "end":
        return node.get("message") or bot.get("positive_close_message") or "Готово."
    return node.get("message") or node.get("title") or "Продолжим."

async def _activate_scenario_node(bot: dict, bot_id: int, vk_id: str, node: dict, user_text: str):
    """
    Активирует выбранный блок сценария.
    Важно: финальный блок теперь отправляет своё сообщение и сразу возвращает главное меню.
    """
    edges = db.get_edges_from_node(bot_id, node["id"])
    response = _format_node_message(node, bot)
    node_type = node["node_type"]

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

    if node_type == "end":
        db.set_operator_mode(bot_id, vk_id, False)
        db.set_current_scenario_node(bot_id, vk_id, None)
        db.set_current_category(bot_id, vk_id, None)
        db.set_current_rule(bot_id, vk_id, None)

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

    # Обычный блок сценария.
    db.set_operator_mode(bot_id, vk_id, False)
    db.set_current_scenario_node(bot_id, vk_id, node["id"])

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

async def _try_process_scenario(bot: dict, bot_id: int, vk_id: str, text: str, state: Optional[dict]):
    """
    Обработка сценариев.
    Эта функция должна быть ОТДЕЛЬНОЙ, а не внутри _activate_scenario_node.
    Именно её отсутствие вызывало ошибку:
    NameError: name '_try_process_scenario' is not defined.
    """
    text_l = (text or "").lower().strip()

    # 1. Если пользователь уже находится внутри сценария — проверяем переходы из текущего блока.
    if state and state.get("current_scenario_node_id"):
        current_node_id = state["current_scenario_node_id"]
        edges = db.get_edges_from_node(bot_id, current_node_id)

        for edge in edges:
            if _edge_matches(edge, text_l):
                next_node = db.get_scenario_node(bot_id, edge["to_node_id"])
                if next_node:
                    return await _activate_scenario_node(bot, bot_id, vk_id, next_node, text)

        # Если пользователь написал что-то не из кнопок — повторяем доступные варианты.
        if edges:
            response = "Выберите один из вариантов ниже или нажмите «В главное меню»."
            await send_vk_message(bot, vk_id, response, build_edges_keyboard(edges))
            db.log_message(bot_id, vk_id, text, response, answer_source="scenario_help")
            return True

        # Если у текущего блока нет переходов, сценарий можно отпустить в обычную базу знаний.
        return False

    # 2. Если пользователь нажал стартовый блок сценария из главного меню.
    start_node = db.find_start_node_by_text(bot_id, text)
    if start_node:
        return await _activate_scenario_node(bot, bot_id, vk_id, start_node, text)

    return False

@app.post("/vk/{bot_id}")
async def vk_handler(bot_id: int, request: Request):
    bot = db.get_bot(bot_id)
    if not bot or not bot["is_active"]:
        return PlainTextResponse("bot_not_found", status_code=404)

    data = await request.json()

    if data.get("type") == "confirmation":
        return PlainTextResponse(bot.get("confirmation_string") or "")

    if data.get("type") != "message_new":
        return PlainTextResponse("ok")

    msg = data["object"]["message"]
    vk_id = str(msg["from_id"])
    text = msg.get("text", "").strip()
    text_l = text.lower().strip()

    categories = db.get_all_categories(bot_id)
    start_nodes = db.get_start_scenario_nodes(bot_id)

    if text_l == "да, спасибо":
        db.set_operator_mode(bot_id, vk_id, False)
        db.set_current_rule(bot_id, vk_id, None)
        db.set_current_scenario_node(bot_id, vk_id, None)
        db.mark_last_answer_success(bot_id, vk_id, True)
        await send_vk_message(bot, vk_id, bot["positive_close_message"])
        db.log_message(bot_id, vk_id, text, bot["positive_close_message"], answer_source="system", is_success=True)
        return PlainTextResponse("ok")

    if text_l == "нет, другой вопрос":
        db.set_operator_mode(bot_id, vk_id, False)
        db.set_current_category(bot_id, vk_id, None)
        db.set_current_rule(bot_id, vk_id, None)
        db.set_current_scenario_node(bot_id, vk_id, None)
        db.mark_last_answer_success(bot_id, vk_id, False)
        keyboard = build_main_keyboard(categories, start_nodes)
        await send_vk_message(bot, vk_id, bot["restart_message"], keyboard)
        db.log_message(bot_id, vk_id, text, bot["restart_message"], answer_source="system", is_success=False)
        return PlainTextResponse("ok")

    state = db.get_chat_state(bot_id, vk_id)

    if state and state.get("is_operator_mode"):
        db.log_message(bot_id, vk_id, text, "ОЖИДАНИЕ ОПЕРАТОРА", answer_source="operator_wait")
        return PlainTextResponse("ok")

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
        db.log_message(bot_id, vk_id, text, bot_response, answer_source="system")
        return PlainTextResponse("ok")

    # Сценарии проверяем раньше категорий и базы знаний.
    # Так пользователь внутри сценария не "выпадает" случайно в раздел базы знаний.
    state = db.get_chat_state(bot_id, vk_id)
    if await _try_process_scenario(bot, bot_id, vk_id, text, state):
        return PlainTextResponse("ok")

    if text_l in [c["name"].lower() for c in categories]:
        cat_name = next(c["name"] for c in categories if c["name"].lower() == text_l)
        await show_category(bot, bot_id, vk_id, cat_name, text, categories, start_nodes)
        return PlainTextResponse("ok")

    if text_l == "нет нужного варианта":
        db.set_current_rule(bot_id, vk_id, None)
        bot_response = bot["fallback_message"]
        await send_vk_message(bot, vk_id, bot_response)
        db.log_message(bot_id, vk_id, text, bot_response, answer_source="fallback")
        return PlainTextResponse("ok")

    state = db.get_chat_state(bot_id, vk_id)
    current_category = state.get("current_category") if state else None

    result = get_answer(user_text=text, bot_id=bot_id, category=current_category)

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

    vk_send_result = await send_vk_message(bot, vk_id, bot_response, keyboard)
    print("VK SEND RESULT:", vk_send_result)

    return PlainTextResponse("ok")