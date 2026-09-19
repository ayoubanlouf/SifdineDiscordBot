import os
import sqlite3
import time
import random
from typing import Optional

WORDS_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "words.db"))
_dict_conn = None

def get_dictionary_cursor():
    global _dict_conn
    if _dict_conn is None:
        _dict_conn = sqlite3.connect(WORDS_DB_PATH, check_same_thread=False)
        try:
            _dict_conn.execute("PRAGMA cache_size = -2000")
        except Exception:
            pass
    try:
        return _dict_conn.cursor()
    except Exception:
        _dict_conn = sqlite3.connect(WORDS_DB_PATH, check_same_thread=False)
        return _dict_conn.cursor()

def is_english_word(word: str) -> bool:
    if not word or not isinstance(word, str):
        return False
    clean_word = word.strip().lower()
    if not clean_word.isalpha() or len(clean_word) < 3:
        return False
    for attempt in range(2):
        try:
            cur = get_dictionary_cursor()
            cur.execute("SELECT 1 FROM dictionary_words WHERE word = ? LIMIT 1", (clean_word,))
            return cur.fetchone() is not None
        except Exception:
            global _dict_conn
            _dict_conn = None
    return False

def get_combo(difficulty: str = "medium") -> str:
    diff = difficulty.lower() if difficulty in ("easy", "medium", "hard") else "medium"
    for attempt in range(2):
        try:
            cur = get_dictionary_cursor()
            cur.execute("SELECT combo FROM word_combos WHERE difficulty = ? ORDER BY RANDOM() LIMIT 1", (diff,))
            row = cur.fetchone()
            if row and row[0]:
                return row[0]
            break
        except Exception:
            global _dict_conn
            _dict_conn = None
    fallbacks = {
        "easy": ["ing", "ter", "con", "sta", "ent", "pro", "all", "ver"],
        "medium": ["blo", "clo", "dra", "fre", "gla", "qui", "sco", "tra"],
        "hard": ["zyl", "phy", "kno", "rhy", "psy", "sph", "lyn", "hyp"]
    }
    return random.choice(fallbacks.get(diff, fallbacks["medium"]))

_classified_words_cache = {}

def normalize_difficulty(difficulty: str) -> str:
    if not difficulty or not isinstance(difficulty, str):
        return "medium"
    d = difficulty.strip().lower()
    if d in ("easy", "e", "sahel", "shl"):
        return "easy"
    if d in ("hard", "h", "s3ib", "wa3er"):
        return "hard"
    return "medium"

def _load_classified_words_cache() -> dict[str, list[str]]:
    global _classified_words_cache
    if _classified_words_cache:
        return _classified_words_cache

    pools = {"easy": [], "medium": [], "hard": []}
    for attempt in range(2):
        try:
            cur = get_dictionary_cursor()
            cur.execute("SELECT word, difficulty FROM classified_words")
            rows = cur.fetchall()
            if rows:
                for w, diff in rows:
                    if w and diff in pools:
                        pools[diff].append(w.lower())
                break
        except Exception as e:
            global _dict_conn
            _dict_conn = None

    # Fallback lists in case table or DB is unavailable
    if not pools["easy"]:
        pools["easy"] = [
            "apple", "water", "tiger", "bread", "house", "smile", "plant", "beach",
            "silver", "cloud", "music", "planet", "chair", "light", "river", "dream", "stone"
        ]
    if not pools["medium"]:
        pools["medium"] = [
            "castle", "danger", "blanket", "whisper", "journey", "diamond", "monster",
            "stadium", "horizon", "shadow", "future", "beacon", "mirror", "temple", "garden", "forest"
        ]
    if not pools["hard"]:
        pools["hard"] = [
            "rhythm", "awkward", "blizzard", "symphony", "dungeon", "mystery",
            "phantom", "labyrinth", "sphinx", "knights", "quench", "puzzle", "wizard", "galaxy", "oxygen"
        ]

    _classified_words_cache = pools
    return _classified_words_cache

def get_word(difficulty: str = "medium", min_length: int = 4, max_length: int = 10) -> str:
    diff = normalize_difficulty(difficulty)
    cache = _load_classified_words_cache()
    pool = cache.get(diff) or cache.get("medium", [])
    filtered = [w for w in pool if min_length <= len(w) <= max_length]
    if filtered:
        return random.choice(filtered)
    if pool:
        return random.choice(pool)
    return "planet"

def get_words_batch(difficulty: str = "medium", count: int = 5, min_length: int = 4, max_length: int = 10) -> list[str]:
    diff = normalize_difficulty(difficulty)
    cache = _load_classified_words_cache()
    pool = cache.get(diff) or cache.get("medium", [])
    filtered = [w for w in pool if min_length <= len(w) <= max_length]
    chosen_pool = filtered if filtered else pool
    if not chosen_pool:
        return ["planet"] * count
    if len(chosen_pool) >= count:
        return random.sample(chosen_pool, count)
    return [random.choice(chosen_pool) for _ in range(count)]

def get_unscramble_word(difficulty: str = "medium") -> str:
    return get_word(difficulty=difficulty, min_length=4, max_length=9)

def get_hangman_secret(difficulty: str = "medium") -> str:
    return get_word(difficulty=difficulty, min_length=5, max_length=8)


def get_typeracer_text() -> str:
    count = 8
    words = []
    for attempt in range(2):
        try:
            cur = get_dictionary_cursor()
            cur.execute("SELECT word FROM dictionary_words WHERE word GLOB '[a-z]*' ORDER BY RANDOM() LIMIT ?", (count,))
            rows = cur.fetchall()
            if rows:
                words = [r[0].lower() for r in rows if r[0] and r[0].isalpha()]
            break
        except Exception:
            global _dict_conn
            _dict_conn = None
    fallback_pool = [
        "guitar", "bridge", "summer", "yellow", "orange", "bottle", "window", "forest",
        "dragon", "castle", "planet", "silver", "garden", "market", "shadow", "future",
        "river", "ocean", "valley", "breeze", "storm", "beacon", "mirror", "temple"
    ]
    if len(words) < count:
        needed = count - len(words)
        words.extend(random.sample(fallback_pool, min(needed, len(fallback_pool))))
    return " ".join(words)

async def record_minigame_win(bot, guild_id: Optional[int], user_id: int, game: str, earnings: int = 0):
    if not guild_id or not hasattr(bot, "db") or not bot.db:
        return
    try:
        now_ts = int(time.time())
        g_name = game.lower()
        clean_earnings = max(0, earnings)
        await bot.db.execute("""
            INSERT INTO minigame_leaderboard (guild_id, user_id, game, wins, earnings, losses, loss_amount)
            VALUES (?, ?, ?, 1, ?, 0, 0)
            ON CONFLICT(guild_id, user_id, game) DO UPDATE SET wins = COALESCE(wins, 0) + 1, earnings = COALESCE(earnings, 0) + ?
        """, (guild_id, user_id, g_name, clean_earnings, clean_earnings))
        await bot.db.execute("""
            INSERT INTO minigame_win_logs (guild_id, user_id, game, earnings, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (guild_id, user_id, g_name, clean_earnings, now_ts))
        await bot.db.commit()
    except Exception as e:
        print(f"[record_minigame_win error]: {e}")

async def record_minigame_loss(bot, guild_id: Optional[int], user_id: int, game: str, loss_amount: int = 0):
    if not guild_id or not hasattr(bot, "db") or not bot.db:
        return
    try:
        now_ts = int(time.time())
        g_name = game.lower()
        clean_loss = max(0, loss_amount)
        await bot.db.execute("""
            INSERT INTO minigame_leaderboard (guild_id, user_id, game, wins, earnings, losses, loss_amount)
            VALUES (?, ?, ?, 0, 0, 1, ?)
            ON CONFLICT(guild_id, user_id, game) DO UPDATE SET losses = COALESCE(losses, 0) + 1, loss_amount = COALESCE(loss_amount, 0) + ?
        """, (guild_id, user_id, g_name, clean_loss, clean_loss))
        await bot.db.execute("""
            INSERT INTO minigame_loss_logs (guild_id, user_id, game, loss_amount, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (guild_id, user_id, g_name, clean_loss, now_ts))
        await bot.db.commit()
    except Exception as e:
        print(f"[record_minigame_loss error]: {e}")
