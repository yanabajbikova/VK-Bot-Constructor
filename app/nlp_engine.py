# Модуль обработки естественного языка
from __future__ import annotations

from rapidfuzz import fuzz

from . import database as db

# Обработка ошибок
try:
    from natasha import Segmenter, MorphVocab, NewsEmbedding, NewsMorphTagger, Doc

    # Инициализация сегментатора
    segmenter = Segmenter()
    # Создание словаря
    morph_vocab = MorphVocab()
    emb = NewsEmbedding()
    morph_tagger = NewsMorphTagger(emb)
    NATASHA_READY = True
except Exception:
    NATASHA_READY = False

def normalize_text(text: str) -> str:
    # Проверка условия
    if not text:
        return ""

    text = text.lower().strip()

    if not NATASHA_READY:
        cleaned = "".join(ch if ch.isalpha() or ch.isspace() else " " for ch in text)
        return " ".join(cleaned.split())

    # Разбор текста
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_morph(morph_tagger)

    lemmas = []
    for token in doc.tokens:
        token.lemmatize(morph_vocab)
        if token.lemma and token.lemma.isalpha():
            lemmas.append(token.lemma)

    # Возврат результата
    return " ".join(lemmas)

def analyze_text(text: str):
    normalized = normalize_text(text)
    return normalized.split() if normalized else []

def _split_examples(value: str) -> list[str]:
    if not value:
        # Возврат результата
        return []
    parts = []
    for raw in value.replace("\n", ",").split(","):
        item = raw.strip()
        if item:
            parts.append(item)
    return parts

def _score_texts(user_norm: str, examples: list[str], normalized_blob: str = "") -> int:
    # Начальный оценка совпадения
    max_score = 0
    normalized_examples = _split_examples(normalized_blob) if normalized_blob else []
    candidates = normalized_examples or [normalize_text(e) for e in examples]
    for candidate in candidates:
        if not candidate:
            continue
        score = max(
            fuzz.token_set_ratio(candidate, user_norm),
            fuzz.partial_ratio(candidate, user_norm),
        )
        # Обновление оценки
        max_score = max(max_score, score)
    return int(max_score)

def get_answer(user_text: str, bot_id: int, category: str | None = None, threshold: float = 0.70):
    user_norm = normalize_text(user_text)

    if not user_norm:
        return {"answer": None, "confidence": 0, "source": "none"}

    # Проверка условия
    if category:
        rules = db.fetch_all(
            """
            SELECT id, keyword, normalized_keyword, answer, intent_id
            FROM bot_rules
            WHERE bot_id = %s AND category = %s
            """,
            (bot_id, category)
        )
    else:
        # Загрузка правил
        rules = db.fetch_all(
            """
            SELECT id, keyword, normalized_keyword, answer, intent_id
            FROM bot_rules
            WHERE bot_id = %s
            """,
            (bot_id,)
        )

    # Хранение лучшего совпадения
    best = {
        "answer": None,
        "confidence": 0,
        "source": "none",
        "rule_id": None,
        "intent_id": None,
    }

    # Обход данных
    for rule in rules:
        examples = _split_examples(rule.get("keyword", ""))
        score = _score_texts(user_norm, examples, rule.get("normalized_keyword") or "")
        if score > best["confidence"] * 100:
            best = {
                "answer": rule["answer"],
                "confidence": round(score / 100, 2),
                "source": "rule",
                "rule_id": rule["id"],
                "intent_id": rule.get("intent_id"),
            }

    # Загрузка интентов
    intents = db.fetch_all(
        """
        SELECT id, name, examples, normalized_examples, response
        FROM user_intents
        WHERE bot_id = %s
        """,
        (bot_id,)
    )

    # Обход данных
    for intent in intents:
        examples = _split_examples(intent.get("examples", "")) + [intent.get("name", "")]
        score = _score_texts(user_norm, examples, intent.get("normalized_examples") or "")
        if score > best["confidence"] * 100:
            linked_rules_query = """
                SELECT id, answer
                FROM bot_rules
                WHERE bot_id = %s AND intent_id = %s
            """
            # Подготовка параметров
            params = [bot_id, intent["id"]]
            if category:
                linked_rules_query += " AND category = %s"
                params.append(category)
            linked_rules_query += " ORDER BY use_count DESC, id DESC LIMIT 1"
            # Получение связанного правила
            linked_rule = db.fetch_one(linked_rules_query, tuple(params))

            answer = (intent.get("response") or "").strip()
            best = {
                "answer": answer or (linked_rule["answer"] if linked_rule else None),
                "confidence": round(score / 100, 2),
                "source": "intent",
                "rule_id": linked_rule["id"] if linked_rule else None,
                "intent_id": intent["id"],
            }

    # Проверка условия
    if best["answer"] and best["confidence"] >= threshold:
        if best.get("rule_id"):
            db.increment_rule_use(best["rule_id"])
        if best.get("intent_id"):
            db.increment_intent_use(best["intent_id"])
        return best

    # Возврат результата
    return {
        "answer": None,
        "confidence": best["confidence"],
        "source": "none",
        "rule_id": best.get("rule_id"),
        "intent_id": best.get("intent_id"),
    }

def train_bot_model(bot_id: int):
    # Загрузка обучающих правил
    rules = db.fetch_all("SELECT id, keyword FROM bot_rules WHERE bot_id = %s", (bot_id,))
    intents = db.fetch_all("SELECT id, name, examples FROM user_intents WHERE bot_id = %s", (bot_id,))

    for rule in rules:
        normalized_items = [normalize_text(x) for x in _split_examples(rule.get("keyword") or "")]
        db.update_rule_normalized(rule["id"], ", ".join(x for x in normalized_items if x))

    for intent in intents:
        examples = _split_examples(intent.get("examples") or "") + [intent.get("name") or ""]
        # Нормализация примеров
        normalized_items = [normalize_text(x) for x in examples]
        db.update_intent_normalized(intent["id"], ", ".join(x for x in normalized_items if x))

    return {
        "status": "success",
        "rules_trained": len(rules),
        "intents_trained": len(intents),
        "engine": "natasha+rapidfuzz" if NATASHA_READY else "simple-normalizer+rapidfuzz",
    }