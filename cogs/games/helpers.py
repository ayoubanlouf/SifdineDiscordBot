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

def get_typeracer_text() -> str:
    count = 5
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
