import telebot
from telebot import types
import sqlite3
import threading
import time
import logging
from datetime import datetime, timedelta
import os
import requests
import json
import re
import base64
import asyncio
import aiohttp
import random
import string
from typing import Dict, List, Optional, Tuple
import uuid

# ==================== الإعدادات الأساسية ====================
USER_BOT_TOKEN = "8817782484:AAGZXVyhCfGyWoS_evaYtzNGaZyKgCbLZHU"
ADMIN_BOT_TOKEN = "8746156510:AAHvZoeIuPA9ddaynEqNlK6NYew8pO-MEnM"
DEV_ID = 8606855463
ASSISTANT_ADMIN_ID = 8606855463
ADMINS = {DEV_ID, ASSISTANT_ADMIN_ID}
DEV_USERNAME = "@jdkfdh"

# ==================== إعدادات نظام الاشتراك المدفوع ====================
SUBSCRIPTION_PRICE = 250          # سعر الاشتراك بالجنيه شهرياً
VODAFONE_CASH_NUMBER = "01010805694"   # رقم فودافون كاش للاستقبال
SUBSCRIPTION_ENABLED = False       # تفعيل/إيقاف نظام الاشتراك (False = مجاني للجميع)

# القنوات المطلوب الاشتراك فيها
CHANNELS = [
    
]

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.ERROR
)
logger = logging.getLogger(__name__)

# إنشاء البوتين
user_bot = telebot.TeleBot(USER_BOT_TOKEN)
admin_bot = telebot.TeleBot(ADMIN_BOT_TOKEN)

# ==================== إدارة الجلسات وتجديد التوكن ====================
# تخزين الجلسات مع وقت الانتهاء
user_sessions = {}  # {user_id: {"phone": str, "password": str, "token": str, "bearer_token": str, "login_time": str, "expires_at": datetime}}
# تخزين بيانات التوكن لكل رقم للتجديد التلقائي
phone_tokens = {}  # {phone: {"token": str, "bearer_token": str, "last_refresh": datetime, "expires_at": datetime}}

# ==================== نظام حفظ الباسورد لـ 24 ساعة (تسجيل دخول تلقائي) ====================
# {user_id: {"phone": str, "password": str, "saved_at": datetime, "expires_at": datetime}}
saved_passwords = {}

def save_password_for_user(user_id: int, phone: str, password: str):
    """حفظ بيانات تسجيل الدخول للمستخدم لمدة 24 ساعة"""
    now = datetime.now()
    saved_passwords[user_id] = {
        "phone": phone,
        "password": password,
        "saved_at": now,
        "expires_at": now + timedelta(hours=24)
    }

def get_saved_password(user_id: int) -> Optional[Dict]:
    """جلب البيانات المحفوظة للمستخدم إذا لم تنتهِ صلاحيتها"""
    if user_id not in saved_passwords:
        return None
    data = saved_passwords[user_id]
    if datetime.now() > data.get("expires_at", datetime.min):
        del saved_passwords[user_id]
        return None
    return data

def clear_saved_password(user_id: int):
    """حذف الباسورد المحفوظ عند تسجيل الخروج"""
    if user_id in saved_passwords:
        del saved_passwords[user_id]

def refresh_token_using_old_token(phone: str, old_token: str) -> Optional[Dict]:
    """تجديد التوكن باستخدام التوكن القديم فقط (بدون كلمة المرور)"""
    try:
        # محاولة تجديد التوكن عبر API مختلف
        url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
        
        # طريقة 1: محاولة refresh_token
        headers = {
            'User-Agent': "okhttp/4.12.0",
            'Accept': "application/json, text/plain, */*",
            'Accept-Encoding': "gzip",
            'Content-Type': "application/x-www-form-urlencoded",
            'Authorization': f"Bearer {old_token}"
        }
        
        data = {
            'grant_type': "refresh_token",
            'client_id': "ana-vodafone-app",
            'client_secret': "95fd95fb-7489-4958-8ae6-d31a525cd20a"
        }
        
        response = requests.post(url, data=data, headers=headers, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            new_token = result.get('access_token')
            if new_token:
                return {
                    "token": new_token,
                    "bearer_token": f"Bearer {new_token}"
                }
        
        # طريقة 2: إعادة استخدام نفس التوكن مع تحديث الطلب (لأن فودافون تقبل التوكن القديم لفترة)
        # نتحقق من صلاحية التوكن أولاً
        test_url = "https://mobile.vodafone.com.eg/services/dxl/usage/usageConsumptionReport"
        test_params = {'@type': "aggregated", 'bucket.product.publicIdentifier': phone}
        test_headers = {
            'Authorization': f"Bearer {old_token}",
            'msisdn': phone,
            'Accept': 'application/json'
        }
        
        test_response = requests.get(test_url, params=test_params, headers=test_headers, timeout=10)
        
        if test_response.status_code == 200:
            # التوكن لا يزال صالحاً
            return {
                "token": old_token,
                "bearer_token": f"Bearer {old_token}"
            }
        
        return None
        
    except Exception:
        return None

def refresh_phone_token_if_needed(phone: str) -> Tuple[bool, Optional[str]]:
    """التحقق من صلاحية توكن الرقم وتجديده إذا لزم الأمر"""
    if phone not in phone_tokens:
        return False, None
    
    token_data = phone_tokens[phone]
    now = datetime.now()
    
    # إذا كان التوكن لا يزال صالحاً (أكثر من 5 دقائق متبقية)
    if token_data.get('expires_at') and token_data['expires_at'] > now + timedelta(minutes=5):
        return True, token_data.get('bearer_token')
    
    # محاولة تجديد التوكن
    old_token = token_data.get('token')
    if not old_token:
        return False, None
    
    new_token_data = refresh_token_using_old_token(phone, old_token)
    
    if new_token_data:
        phone_tokens[phone] = {
            "token": new_token_data['token'],
            "bearer_token": new_token_data['bearer_token'],
            "last_refresh": now,
            "expires_at": now + timedelta(minutes=30)
        }
        return True, new_token_data['bearer_token']
    
    return False, None

def update_session_token(user_id: int, new_bearer_token: str, new_token: str):
    """تحديث التوكن في جلسة المستخدم"""
    if user_id in user_sessions:
        user_sessions[user_id]['token'] = new_token
        user_sessions[user_id]['bearer_token'] = new_bearer_token
        user_sessions[user_id]['expires_at'] = datetime.now() + timedelta(hours=12)
        
        # تحديث في phone_tokens أيضاً
        phone = user_sessions[user_id]['phone']
        if phone in phone_tokens:
            phone_tokens[phone]['token'] = new_token
            phone_tokens[phone]['bearer_token'] = new_bearer_token
            phone_tokens[phone]['last_refresh'] = datetime.now()
            phone_tokens[phone]['expires_at'] = datetime.now() + timedelta(minutes=30)

def check_and_refresh_user_session(user_id: int) -> bool:
    """التحقق من صلاحية جلسة المستخدم وتجديدها إذا لزم الأمر"""
    if user_id not in user_sessions:
        return False
    
    session = user_sessions[user_id]
    
    # التحقق من صلاحية الجلسة (12 ساعة)
    if session.get('expires_at') and session['expires_at'] < datetime.now():
        # انتهت صلاحية الجلسة
        del user_sessions[user_id]
        return False
    
    phone = session['phone']
    success, new_bearer = refresh_phone_token_if_needed(phone)
    
    if success and new_bearer:
        # استخراج التوكن بدون Bearer
        new_token = new_bearer.replace("Bearer ", "")
        update_session_token(user_id, new_bearer, new_token)
        return True
    
    # إذا فشل التجديد، نتحقق من أن التوكن الحالي لا يزال صالحاً
    # عن طريق اختبار بسيط
    try:
        test_url = "https://mobile.vodafone.com.eg/services/dxl/usage/usageConsumptionReport"
        test_params = {'@type': "aggregated", 'bucket.product.publicIdentifier': phone}
        test_headers = {
            'Authorization': session.get('bearer_token', ''),
            'msisdn': phone,
            'Accept': 'application/json'
        }
        test_response = requests.get(test_url, params=test_params, headers=test_headers, timeout=10)
        if test_response.status_code == 200:
            # التوكن لا يزال صالحاً، نحدث وقت الانتهاء
            session['expires_at'] = datetime.now() + timedelta(minutes=25)
            return True
    except:
        pass
    
    return False

def create_session(user_id: int, phone: str, password: str, token: str, bearer_token: str):
    """إنشاء جلسة جديدة للمستخدم"""
    now = datetime.now()
    user_sessions[user_id] = {
        'phone': phone,
        'password': password,
        'token': token,
        'bearer_token': bearer_token,
        'login_time': now.strftime("%Y-%m-%d %H:%M:%S"),
        'expires_at': now + timedelta(hours=12)
    }
    
    # تخزين التوكن للرقم للتجديد التلقائي
    phone_tokens[phone] = {
        "token": token,
        "bearer_token": bearer_token,
        "last_refresh": now,
        "expires_at": now + timedelta(minutes=30)
    }

def get_session_by_phone(phone: str) -> Optional[Dict]:
    """البحث عن جلسة نشطة بواسطة رقم الهاتف"""
    for user_id, session in user_sessions.items():
        if session.get('phone') == phone:
            # التحقق من صلاحية الجلسة
            if session.get('expires_at') and session['expires_at'] > datetime.now():
                return session
            else:
                # حذف الجلسة منتهية الصلاحية
                del user_sessions[user_id]
                return None
    return None

def auto_refresh_all_tokens():
    """دالة تعمل في الخلفية لتجديد التوكنات لكل الأرقام المسجلة"""
    while True:
        try:
            time.sleep(60)  # كل دقيقة نتحقق
            
            now = datetime.now()
            phones_to_refresh = []
            
            # البحث عن الأرقام التي تحتاج تجديد
            for phone, token_data in phone_tokens.items():
                expires_at = token_data.get('expires_at')
                if expires_at and expires_at < now + timedelta(minutes=5):
                    phones_to_refresh.append(phone)
            
            # تجديد التوكنات
            for phone in phones_to_refresh:
                success, new_bearer = refresh_phone_token_if_needed(phone)
                if success and new_bearer:
                    # تحديث الجلسات المرتبطة بهذا الرقم
                    new_token = new_bearer.replace("Bearer ", "")
                    for user_id, session in user_sessions.items():
                        if session.get('phone') == phone:
                            update_session_token(user_id, new_bearer, new_token)
                            
        except Exception as e:
            print(f"خطأ في تجديد التوكنات التلقائي: {e}")

# تشغيل خيط تجديد التوكنات التلقائي
refresh_thread = threading.Thread(target=auto_refresh_all_tokens, daemon=True)
refresh_thread.start()

# ==================== وظائف فودافون الأساسية (المستخدمة في الفاميلي) ====================
def get_authorization(number, password):
    """الحصول على رمز التفويض"""
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    data = {
        "grant_type": "password",
        "username": number,
        "password": password,
        "client_secret": "95fd95fb-7489-4958-8ae6-d31a525cd20a",
        "client_id": "ana-vodafone-app"
    }
    
    headers = {
        'User-Agent': "okhttp/4.11.0",
        'Accept': "application/json, text/plain, */*",
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    try:
        response = requests.post(url, data=data, headers=headers, timeout=30)
        
        if response.status_code == 200:
            tokens = response.json()
            access_token = tokens.get("access_token")
            return {"success": True, "token": access_token, "bearer_token": "Bearer " + access_token}
        else:
            return {"success": False, "message": "الرقم أو كلمة السر غير صحيحة", "status_code": response.status_code}
            
    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "خطأ في الاتصال بالإنترنت"}
    except Exception as e:
        return {"success": False, "message": f"حدث خطأ: {str(e)}"}

def change_password_api(phone, current_password, new_password, token):
    """تغيير كلمة المرور عبر API فودافون"""
    url = "https://mobile.vodafone.com.eg/services/dxl/sam/serviceAccountManagement/v1/serviceAccount"
    
    payload = {
        "customerAccount": {
            "authentication": {
                "newPassword": new_password,
                "password": current_password
            }
        },
        "resources": [
            {
                "IDs": [
                    {
                        "value": phone
                    }
                ],
                "resourceType": "MSISDN"
            }
        ],
        "@type": "userPrefsUpdate"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/json",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "70d3004b2bd92694",
        'x-agent-operatingsystem': "11",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "OPPO oppo6779",
        'x-agent-version': "2025.12.1",
        'x-agent-build': "1075",
        'msisdn': phone,
        'Accept-Language': "ar",
        'Content-Type': "application/json; charset=UTF-8"
    }
    
    try:
        response = requests.patch(url, data=json.dumps(payload), headers=headers, timeout=30)
        
        if response.status_code == 200:
            return {"success": True, "message": "✅ تم تغيير كلمة المرور بنجاح!"}
        else:
            return {"success": False, "message": f"❌ فشل تغيير كلمة المرور: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"❌ حدث خطأ: {str(e)}"}

def addMember(on, mn, token, value=10):
    """إرسال دعوة"""
    headers = {
        "User-Agent": "okhttp/4.12.0",
    "Connection": "Keep-Alive",
    "Accept": "application/json",
    # "Accept-Encoding": "gzip",
    "Authorization": token,
    "api-version": "v2",
    "device-id": "7be546fe335911d2",
    "x-agent-operatingsystem": "13",
    "clientId": "AnaVodafoneAndroid",
    "x-agent-device": "Samsung SM-A515F",
    "x-agent-version": "2026.2.3",
    "x-agent-build": "1117",
    "msisdn": on,
    "Accept-Language": "ar",
    "Content-Type": "application/json; charset=UTF-8",
    }

    json_data = {
        'name': 'FlexFamily',
        'type': 'SendInvitation',
        'category': [
            {'value': '523', 'listHierarchyId': 'PackageID'},
            {'value': '47', 'listHierarchyId': 'TemplateID'},
            {'value': '523', 'listHierarchyId': 'TierID'},
            {'value': 'percentage', 'listHierarchyId': 'familybehavior'},
        ],
        'parts': {
            'member': [
                {'id': [{'value': on, 'schemeName': 'MSISDN'}], 'type': 'Owner'},
                {'id': [{'value': mn, 'schemeName': 'MSISDN'}], 'type': 'Member'},
            ],
            'characteristicsValue': {
                'characteristicsValue': [
                    {'characteristicName': 'quotaDist1', 'value': value, 'type': 'percentage'},
                ],
            },
        },
    }

    try:
        response = requests.post(
            'https://mobile.vodafone.com.eg/services/dxl/cg/customerGroupAPI/customerGroup',
            headers=headers,
            json=json_data,
            timeout=30
        )
        if str(response.status_code) in ['200', '201', '{}', '555']:
            return {"success": True, "message": f"تم إرسال الدعوة بنجاح - النسبة: {value}%"}
        else:
            return {"success": False, "message": f"فشل إرسال الدعوة: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"حدث خطأ: {str(e)}"}

def accept_invitation(owner_num, member_num, token):
    """قبول دعوة"""
    url = "https://mobile.vodafone.com.eg/services/dxl/cg/customerGroupAPI/customerGroup"
    headers = {
        "User-Agent": "okhttp/4.12.0",
    "Connection": "Keep-Alive",
    "Accept": "application/json",
    # "Accept-Encoding": "gzip",
    "api_id": "APP",
    "Authorization": token,
    "api-version": "v2",
    "device-id": "7be546fe335911d2",
    "x-agent-operatingsystem": "13",
    "clientId": "AnaVodafoneAndroid",
    "x-agent-device": "Samsung SM-A515F",
    "x-agent-version": "2026.2.3",
    "x-agent-build": "1117",
    "msisdn": member_num,
    "Accept-Language": "ar",
    "Content-Type": "application/json; charset=UTF-8",
    }
    data = {
        "category": [{"listHierarchyId": "TemplateID", "value": "47"}],
        "name": "FlexFamily",
        "parts": {
            "member": [
                {"id": [{"schemeName": "MSISDN", "value": owner_num}], "type": "Owner"},
                {"id": [{"schemeName": "MSISDN", "value": member_num}], "type": "Member"}
            ]
        },
        "type": "AcceptInvitation"
    }
    try:
        aa = requests.patch(url, headers=headers, json=data, timeout=30)
        Accept = aa.text
        if str(Accept) in ['{}', '201', '200']:
            return {"success": True, "message": "تم قبول الدعوة بنجاح"}
        elif "Customer not eligible-Family member" in str(Accept):
            return {"success": True, "message": "الرقم موجود بالفعل في عائلة"}
        else:
            return {"success": False, "message": f"خطأ في قبول الدعوة: {Accept[:100]}"}
    except Exception as e:
        return {"success": False, "message": f"حدث خطأ: {str(e)}"}

def remove_member(owner_num, token, member_num):
    """حذف عضو من العائلة"""
    try:
        headers = {
            "User-Agent": "okhttp/4.12.0",
    "Connection": "Keep-Alive",
    "Accept": "application/json",
    # "Accept-Encoding": "gzip",
    "Authorization": token,
    "api-version": "v2",
    "device-id": "7be546fe335911d2",
    "x-agent-operatingsystem": "13",
    "clientId": "AnaVodafoneAndroid",
    "x-agent-device": "Samsung SM-A515F",
    "x-agent-version": "2026.2.3",
    "x-agent-build": "1117",
    "msisdn": owner_num,
    "Accept-Language": "ar",
    "Content-Type": "application/json; charset=UTF-8",
        }

        payload = {
            "name": "FlexFamily",
            "type": "FamilyRemoveMember",
            "category": [{"value": "47", "listHierarchyId": "TemplateID"}],
            "parts": {
                "member": [
                    {"id": [{"value": owner_num, "schemeName": "MSISDN"}], "type": "Owner"},
                    {"id": [{"value": member_num, "schemeName": "MSISDN"}], "type": "Member"}
                ],
                "characteristicsValue": {
                    "characteristicsValue": [
                        {"characteristicName": "Disconnect", "value": "0"},
                        {"characteristicName": "LastMemberDeletion", "value": "1"}
                    ]
                }
            }
        }
        
        response = requests.patch(
            'https://mobile.vodafone.com.eg/services/dxl/cg/customerGroupAPI/customerGroup',
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code in [200, 201]:
            return {"success": True, "message": "✅ تم حذف العضو بنجاح"}
        else:
            return {"success": False, "message": f"❌ فشل في الحذف: {response.text[:100]}"}
            
    except Exception as e:
        return {"success": False, "message": f"❌ حدث خطأ: {str(e)}"}

def change_quota(owner_num, token, member_num, quota):
    """تغيير نسبة الفرد"""
    try:
        headers = {
           "User-Agent": "okhttp/4.12.0",
    "Connection": "Keep-Alive",
    "Accept": "application/json",
    # "Accept-Encoding": "gzip",
    "Authorization": token,
    "api-version": "v2",
    "device-id": "7be546fe335911d2",
    "x-agent-operatingsystem": "13",
    "clientId": "AnaVodafoneAndroid",
    "x-agent-device": "Samsung SM-A515F",
    "x-agent-version": "2026.2.3",
    "x-agent-build": "1117",
    "msisdn": owner_num,
    "Accept-Language": "ar",
    "Content-Type": "application/json; ch
