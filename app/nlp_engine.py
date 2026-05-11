import pymorphy2
from fuzzywuzzy import fuzz
import sqlite3
import os

# Ммодуль: Движок обработки естественного языка (NLP)

# Инициализация анализатора и определение пути к базе данных
morph = pymorphy2.MorphAnalyzer()
DB_PATH = os.path.join(os.path.dirname(__file__), "bot_database.db")

# Очистка входного текста, приведение слов к начальной форме (лемме)
def normalize_text(text):
    if not text:
        return ""
    
    for char in "?!,.:;()":
        text = text.replace(char, "")

    words = text.lower().split()
    lemmas = [morph.parse(word)[0].normal_form for word in words]

    return " ".join(lemmas)

# Возвращение списка лемм для совместимости с внешними модулями системы
def analyze_text(text):
    """Возвращает список лемм для совместимости."""
    norm = normalize_text(text)
    return norm.split() if norm else []

# Поиск наиболее подходящего ответа
def get_answer(user_text, category_name=None):
    user_norm = normalize_text(user_text)
    if not user_norm:
        return None
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try: # Фильтрация правил по категории, если она указана
        if category_name:
            cursor.execute("SELECT * FROM bot_rules WHERE category = ?", (category_name,))
        else:
            cursor.execute("SELECT * FROM bot_rules")
        rules = cursor.fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()

    best_match = None
    max_score = 0


# Перебор всех загруженных вопросов
    for rule in rules:
        triggers = [t.strip() for t in rule['keyword'].split(',')]

        for trigger in triggers:
            trigger_norm = normalize_text(trigger)

            # Рассчет коэффициента сходства с использованием алгоритма Левенштейна
            score = fuzz.token_set_ratio(trigger_norm, user_norm)
           
            # Порог срабатывания
            if score > 70 and score > max_score:
                max_score = score
                best_match = rule
    
# Возвращение найденного ответа или пустого значения при отсутсвии совпадения
    if best_match:
        return best_match['answer']
    
    return None