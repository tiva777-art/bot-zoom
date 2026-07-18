

import asyncio
import json
import logging
import os
import random
import sqlite3
import string
import threading
import base64
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict

import aiohttp
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# ================== الإعدادات العامة ==================
BOT_TOKEN = "7591229217:AAFshbXAwbaY8bP7EC6l2zmkikvux-l2eN4"
ADMIN_ID = 1444139300
REQUIRED_CHANNEL = "@market_tiva"
DB_PATH = Path("family_bot.db")
LOG_FILE = Path("bot_actions.log")
MAINTENANCE_MODE = False
TOKEN_REFRESH_THRESHOLD = 300
SEND_METHOD = 1  # 1 = aiohttp, 2 = threading

SYSTEM_TRANSLATIONS = {
    '14pt_Raya7Balak': 'ريح بالك كله 14 قرش',
    'Flex_40': 'فليكس 40', 'Flex_60': 'فليكس 60', 'Flex_90': 'فليكس 90',
    'Flex_130': 'فليكس 130', 'Flex_260': 'فليكس 260',
    'Flex_45': 'فليكس 45', 'Flex_70': 'فليكس 70', 'Flex_100': 'فليكس 100',
    'Flex_150': 'فليكس 150', 'Flex_300': 'فليكس 300',
    'Flex_Family_Member': 'فليكس فاميلي', 'Flex_Family': 'فليكس فاميلي', 'Family_Flex': 'فليكس فاميلي',
}
SUPPORTED_SYSTEMS = ["فليكس 260", "فليكس 130", "فليكس 90"]

# ================== إعداد التسجيل ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger("family_bot")

def log_action(user_id: int, action: str, details: str = "") -> None:
    logger.info(f"User {user_id}: {action} - {details}")

def admin_log(action: str, details: str = "") -> None:
    logger.info(f"ADMIN: {action} - {details}")

# ================== دوال مساعدة عامة ==================
def generate_digital_id() -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=13))

def generate_device_id() -> str:
    return ''.join(random.choices('0123456789abcdef', k=16))

def get_random_device_info() -> Tuple[str, str]:
    return random.choice(["Samsung SM-A515F", "OPPO CPH2473"]), random.choice(["13", "14"])

def convert_to_local(phone: str) -> str:
    if phone.startswith("20") and len(phone) == 12:
        return "0" + phone[2:]
    return phone

def translate_system(system_name: Optional[str]) -> str:
    if not system_name:
        return "غير محدد"
    for key, value in SYSTEM_TRANSLATIONS.items():
        if key in system_name:
            return value
    return system_name

def validate_phone(number: str) -> Tuple[bool, str]:
    if not number.isdigit():
        return False, "❌ الرقم يجب أن يحتوي على أرقام فقط"
    if len(number) != 11:
        return False, "❌ الرقم يجب أن يكون 11 رقماً"
    if not number.startswith('01'):
        return False, "❌ الرقم يجب أن يبدأ بـ 01"
    return True, "✅"

# ================== تشفير وإدارة كلمات المرور ==================
def encrypt_password(password: str) -> str:
    return base64.b64encode(password.encode()).decode()

def decrypt_password(encrypted: str) -> str:
    return base64.b64decode(encrypted.encode()).decode()

def save_password(user_id: int, number: str, password: str, expiry_hours: int = 24):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS saved_passwords 
                 (telegram_id INTEGER, number TEXT, password_encrypted TEXT, expiry TIMESTAMP,
                 PRIMARY KEY (telegram_id, number))''')
    expiry = datetime.now() + timedelta(hours=expiry_hours)
    encrypted = encrypt_password(password)
    c.execute("INSERT OR REPLACE INTO saved_passwords (telegram_id, number, password_encrypted, expiry) VALUES (?, ?, ?, ?)",
              (user_id, number, encrypted, expiry.isoformat()))
    conn.commit()
    conn.close()

def get_saved_password(user_id: int, number: str) -> Optional[str]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT password_encrypted, expiry FROM saved_passwords WHERE telegram_id = ? AND number = ?", (user_id, number))
    row = c.fetchone()
    conn.close()
    if row:
        encrypted, expiry_str = row
        try:
            expiry = datetime.fromisoformat(expiry_str)
            if expiry > datetime.now():
                return decrypt_password(encrypted)
        except:
            # إذا فشل تحويل التاريخ، نحذف المدخل لأنه قد يكون فاسداً
            delete_saved_password(user_id, number)
            return None
    return None

def delete_saved_password(user_id: int, number: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM saved_passwords WHERE telegram_id = ? AND number = ?", (user_id, number))
    conn.commit()
    conn.close()

# ================== قاعدة البيانات (مع جداول الأدمن والاشتراكات) ==================
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # الجداول الأصلية
    c.execute('''CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, phone TEXT, access_token TEXT, token_expiry TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS saved_numbers (telegram_id INTEGER, number TEXT, PRIMARY KEY (telegram_id, number))''')
    c.execute('''CREATE TABLE IF NOT EXISTS banned_users (telegram_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_activity (telegram_id INTEGER PRIMARY KEY, first_seen TIMESTAMP, last_seen TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS saved_tokens (user_id INTEGER, number TEXT, token TEXT, expiry TIMESTAMP, PRIMARY KEY (user_id, number))''')
    c.execute('''CREATE TABLE IF NOT EXISTS saved_passwords (telegram_id INTEGER, number TEXT, password_encrypted TEXT, expiry TIMESTAMP, PRIMARY KEY (telegram_id, number))''')
    
    # جداول جديدة للأدمن والاشتراكات
    c.execute('''CREATE TABLE IF NOT EXISTS admins (telegram_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (telegram_id INTEGER PRIMARY KEY, expiry TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS bot_config (key TEXT PRIMARY KEY, value TEXT)''')
    conn.commit()
    
    # إضافة الأدمن الرئيسي إذا لم يكن موجوداً
    c.execute("INSERT OR IGNORE INTO admins (telegram_id) VALUES (?)", (ADMIN_ID,))
    # إضافة الوضع المجاني افتراضياً (1 = مجاني، 0 = اشتراك)
    c.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('free_mode', '1')")
    conn.commit()
    conn.close()

init_db()

# ================== دوال الأدمن الجديدة ==================
def is_admin(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM admins WHERE telegram_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

def add_admin(user_id: int) -> bool:
    if is_admin(user_id):
        return False
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO admins (telegram_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()
    return True

def remove_admin(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return False  # لا يمكن إزالة الأدمن الرئيسي
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE telegram_id = ?", (user_id,))
    conn.commit()
    affected = c.rowcount
    conn.close()
    return affected > 0

def list_admins() -> List[int]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT telegram_id FROM admins")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def is_free_mode() -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM bot_config WHERE key = 'free_mode'")
    row = c.fetchone()
    conn.close()
    return row is not None and row[0] == '1'

def set_free_mode(enabled: bool):
    value = '1' if enabled else '0'
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('free_mode', ?)", (value,))
    conn.commit()
    conn.close()

# ================== دوال الاشتراكات ==================
def get_subscription_expiry(user_id: int) -> Optional[datetime]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT expiry FROM subscriptions WHERE telegram_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return datetime.fromisoformat(row[0])
    return None

def is_subscription_active(user_id: int) -> bool:
    expiry = get_subscription_expiry(user_id)
    if expiry is None:
        return False
    return expiry > datetime.now()

def add_subscription(user_id: int, days: int) -> datetime:
    new_expiry = datetime.now() + timedelta(days=days)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO subscriptions (telegram_id, expiry) VALUES (?, ?)", (user_id, new_expiry.isoformat()))
    conn.commit()
    conn.close()
    return new_expiry

def remove_subscription(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM subscriptions WHERE telegram_id = ?", (user_id,))
    conn.commit()
    affected = c.rowcount
    conn.close()
    return affected > 0

def list_active_subscriptions() -> List[Tuple[int, datetime]]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("SELECT telegram_id, expiry FROM subscriptions WHERE expiry > ?", (now,))
    rows = c.fetchall()
    conn.close()
    return [(row[0], datetime.fromisoformat(row[1])) for row in rows]


async def check_channel_subscription(bot, user_id:int):
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status not in ("left","kicked")
    except Exception:
        return False

# ================== دالة التحقق من الاشتراك (تستثني الأدمن) ==================
async def require_subscription_or_free(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback: bool = False, query=None) -> bool:
    # تحديد user_id بشكل صحيح
    if is_callback and query is not None:
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id
    
    # الأدمن يستخدم البوت دائماً
    if is_admin(user_id):
        return True

    bot = query.bot if (is_callback and query is not None) else update.get_bot()
    if not await check_channel_subscription(bot, user_id):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 اشترك في القناة", url="https://t.me/market_tiva")],
            [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_channel")]
        ])
        msg = "📢 يجب الاشتراك في القناة أولاً."
        if is_callback and query:
            await query.edit_message_text(msg, reply_markup=keyboard)
        else:
            await update.message.reply_text(msg, reply_markup=keyboard)
        return False
    if is_free_mode():
        return True
    if is_subscription_active(user_id):
        return True
    
    text = "🔒 **هذا البوت يعمل بنظام الاشتراك.**\n\nلم يعد لديك اشتراك نشط.\nيرجى التواصل مع الأدمن للحصول على اشتراك.\n📞 تواصل مع المالك: @Mahmoud_tiva"
    if is_callback and query:
        await query.edit_message_text(text, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, parse_mode='Markdown')
    return False

# ================== دوال المستخدم الأساسية (بدون تغيير) ==================
def is_user_banned(telegram_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM banned_users WHERE telegram_id = ?", (telegram_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

def ban_user(telegram_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO banned_users (telegram_id) VALUES (?)", (telegram_id,))
    conn.commit()
    conn.close()

def unban_user(telegram_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM banned_users WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()

def record_user_activity(telegram_id: int) -> None:
    now = datetime.now()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO user_activity (telegram_id, first_seen, last_seen) VALUES (?, ?, ?)", (telegram_id, now, now))
    c.execute("UPDATE user_activity SET last_seen = ? WHERE telegram_id = ?", (now, telegram_id))
    conn.commit()
    conn.close()

def get_all_users() -> List[Tuple[int, datetime, datetime]]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT telegram_id, first_seen, last_seen FROM user_activity ORDER BY last_seen DESC")
    rows = c.fetchall()
    conn.close()
    user_list = []
    for uid, first, last in rows:
        first_dt = datetime.fromisoformat(first) if isinstance(first, str) else first
        last_dt = datetime.fromisoformat(last) if isinstance(last, str) else last
        user_list.append((uid, first_dt, last_dt))
    return user_list

def get_stats() -> Dict[str, int]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM user_activity")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users")
    active_sessions = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM saved_numbers")
    saved_numbers = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM banned_users")
    banned_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM subscriptions WHERE expiry > datetime('now')")
    active_subs = c.fetchone()[0]
    conn.close()
    return {'total_users': total_users, 'active_sessions': active_sessions,
            'saved_numbers': saved_numbers, 'banned_count': banned_count, 'active_subs': active_subs}

def save_number(telegram_id: int, number: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO saved_numbers (telegram_id, number) VALUES (?, ?)", (telegram_id, number))
    conn.commit()
    conn.close()

def get_saved_numbers(telegram_id: int) -> List[str]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT number FROM saved_numbers WHERE telegram_id = ?", (telegram_id,))
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def delete_saved_number(telegram_id: int, number: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM saved_numbers WHERE telegram_id = ? AND number = ?", (telegram_id, number))
    conn.commit()
    conn.close()
    delete_saved_password(telegram_id, number)

def save_token(user_id: int, number: str, token: str, expiry: datetime) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO saved_tokens (user_id, number, token, expiry) VALUES (?, ?, ?, ?)",
              (user_id, number, token, expiry.isoformat()))
    conn.commit()
    conn.close()

def get_valid_token(user_id: int, number: str) -> Optional[str]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT token, expiry FROM saved_tokens WHERE user_id = ? AND number = ?", (user_id, number))
    row = c.fetchone()
    conn.close()
    if row:
        token, expiry_str = row
        try:
            expiry = datetime.fromisoformat(expiry_str)
            if expiry > datetime.now():
                return token
        except:
            pass
    return None

def save_user(telegram_id: int, phone: str, token: str, expiry: datetime) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO users (telegram_id, phone, access_token, token_expiry) VALUES (?, ?, ?, ?)',
              (telegram_id, phone, token, expiry.isoformat()))
    conn.commit()
    conn.close()
    save_token(telegram_id, phone, token, expiry)

def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT phone, access_token, token_expiry FROM users WHERE telegram_id = ?', (telegram_id,))
    row = c.fetchone()
    conn.close()
    if row:
        phone, token, expiry_str = row
        expiry = datetime.fromisoformat(expiry_str)
        return {'phone': phone, 'access_token': token, 'token_expiry': expiry}
    return None

def delete_user(telegram_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM users WHERE telegram_id = ?', (telegram_id,))
    conn.commit()
    conn.close()

# ================== API فودافون (وظائف البوت الأساسية) ==================
def build_base_headers(additional: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    digital_id = generate_digital_id()
    device_id = generate_device_id()
    device_model, os_version = get_random_device_info()
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'device-id': device_id,
        'x-agent-operatingsystem': os_version,
        'clientId': 'AnaVodafoneAndroid',
        'x-agent-device': device_model,
        'x-agent-version': '2026.4.1',
        'x-agent-build': '1139',
        'Accept-Language': 'ar',
        'Content-Type': 'application/json; charset=UTF-8',
        'digitalId': digital_id,
    }
    if additional:
        headers.update(additional)
    return headers

async def login_vodafone(phone: str, password: str) -> Tuple[str, datetime]:
    headers = build_base_headers({
        'Content-Type': 'application/x-www-form-urlencoded',
        'silentLogin': 'true',
        'msisdn': phone,
    })
    data = {
        'grant_type': 'password',
        'username': phone,
        'password': password,
        'client_secret': 'dca0pbLUWXVhXR266Gw1iT5rqwvvJQoN',
        'client_id': 'AnaVF',
    }
    url = 'https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token'
    def sync_login():
        resp = requests.post(url, headers=headers, data=data, timeout=30)
        if resp.status_code == 200:
            token = resp.json().get('access_token')
            expires_in = resp.json().get('expires_in', 3600)
            return token, expires_in
        else:
            raise Exception(f"فشل تسجيل الدخول: {resp.status_code}")
    token, exp_in = await asyncio.to_thread(sync_login)
    expiry = datetime.now() + timedelta(seconds=exp_in)
    return token, expiry

async def get_current_system(token: str, phone: str) -> str:
    url = f"https://web.vodafone.com.eg/services/dxl/sam/serviceAccountManagement/v1/serviceAccount?@type=userProfile&$.resources%5B?(@resourceType%3D%3D%27MSISDN%27)%5D.IDs%5B0%5D.value={phone}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 13; SM-A515F) AppleWebKit/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'ar-EG,ar;q=0.9,en;q=0.8',
        'Authorization': f'Bearer {token}',
        'api-version': 'v3',
        'clientId': 'AnaVodafoneAndroid',
        'device-id': '7be546fe335911d2',
        'x-agent-build': '1063',
        'x-agent-device': 'Samsung SM-A515F',
        'x-agent-operatingsystem': '13',
        'x-agent-version': '2025.11.1',
        'msisdn': phone,
        'Content-Type': 'application/json',
        'Connection': 'keep-alive',
        'Referer': 'https://web.vodafone.com.eg/',
        'Origin': 'https://web.vodafone.com.eg'
    }
    def sync_get():
        resp = requests.get(url, headers=headers, timeout=15)
        return resp.status_code, resp.text
    status, text = await asyncio.to_thread(sync_get)
    if status != 200:
        return f"خطأ في جلب الباقة (كود {status})"
    try: