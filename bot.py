#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OK Virtuals Betting Prediction Bot - PAYSTACK VERSION
All features fully implemented with Paystack payment integration
"""
import os
import logging
import sqlite3
import json
import threading
import signal
import sys
import time
import requests
import uuid
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import Conflict, NetworkError, TimedOut, TelegramError, Forbidden, BadRequest
from telegram.constants import ParseMode, ChatMemberStatus
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

shutdown_flag = False
bot_application = None

@dataclass
class Config:
    BOT_TOKEN: str
    PAYSTACK_SECRET_KEY: str
    PAYSTACK_PUBLIC_KEY: str
    PREMIUM_CHANNEL_ID: str
    PREMIUM_CHANNEL_USERNAME: str
    DATABASE_PATH: str = "./okvirtuals_bot.db"
    PORT: int = 10000
    WEBHOOK_URL: str = ""
    ADMIN_USER_IDS: str = ""
    SUBSCRIPTION_AMOUNT: int = 10000
    SUBSCRIPTION_DAYS: int = 30
    REMINDER_DAYS: str = "7,3,1"

def load_config() -> Config:
    config = Config(
        BOT_TOKEN=os.getenv("BOT_TOKEN", ""),
        PAYSTACK_SECRET_KEY=os.getenv("PAYSTACK_SECRET_KEY", ""),
        PAYSTACK_PUBLIC_KEY=os.getenv("PAYSTACK_PUBLIC_KEY", ""),
        PREMIUM_CHANNEL_ID=os.getenv("PREMIUM_CHANNEL_ID", ""),
        PREMIUM_CHANNEL_USERNAME=os.getenv("PREMIUM_CHANNEL_USERNAME", ""),
        DATABASE_PATH=os.getenv("DATABASE_PATH", "./okvirtuals_bot.db"),
        PORT=int(os.getenv("PORT", 10000)),
        WEBHOOK_URL=os.getenv("WEBHOOK_URL", ""),
        ADMIN_USER_IDS=os.getenv("ADMIN_USER_IDS", ""),
        SUBSCRIPTION_AMOUNT=int(os.getenv("SUBSCRIPTION_AMOUNT", 10000)),
        SUBSCRIPTION_DAYS=int(os.getenv("SUBSCRIPTION_DAYS", 30)),
        REMINDER_DAYS=os.getenv("REMINDER_DAYS", "7,3,1")
    )
    
    if not config.BOT_TOKEN:
        raise ValueError("BOT_TOKEN is required")
    if not config.PAYSTACK_SECRET_KEY:
        raise ValueError("PAYSTACK_SECRET_KEY is required")
    
    return config

try:
    CONFIG = load_config()
    logger.info("Configuration loaded successfully")
except ValueError as e:
    logger.error(f"Configuration error: {e}")
    sys.exit(1)

BOT_COMMANDS = [
    BotCommand("start", "Start the bot and see welcome menu"),
    BotCommand("subscribe", "Subscribe to premium predictions"),
    BotCommand("status", "Check your subscription status"),
    BotCommand("predictions", "View today's betting predictions"),
    BotCommand("stats", "View your betting statistics"),
    BotCommand("support", "Get customer support"),
    BotCommand("help", "Get help and bot information"),
    BotCommand("premium", "Access premium channel"),
    BotCommand("admin", "Admin panel (admin only)"),
]

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.init_database()
    
    @contextmanager
    def get_connection(self):
        conn = None
        try:
            with self.lock:
                conn = sqlite3.connect(self.db_path, timeout=30.0)
                conn.row_factory = sqlite3.Row
                yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            raise e
        finally:
            if conn:
                conn.close()
    
    def init_database(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        subscription_start TEXT,
                        subscription_end TEXT,
                        is_premium INTEGER DEFAULT 0,
                        total_predictions_viewed INTEGER DEFAULT 0,
                        successful_bets INTEGER DEFAULT 0,
                        total_bets INTEGER DEFAULT 0,
                        last_active TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        invite_link TEXT,
                        last_reminder_sent TEXT
                    )
                ''')
                
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        transaction_ref TEXT UNIQUE,
                        amount REAL,
                        status TEXT DEFAULT 'pending',
                        payment_method TEXT,
                        paystack_id TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        completed_at TEXT,
                        FOREIGN KEY (user_id) REFERENCES users (user_id)
                    )
                ''')
                
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS subscription_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        start_date TEXT,
                        end_date TEXT,
                        amount REAL,
                        status TEXT,
                        is_renewal INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (user_id)
                    )
                ''')
                
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS notifications (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        type TEXT,
                        message TEXT,
                        sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (user_id)
                    )
                ''')
                
                conn.commit()
                logger.info("Database initialized successfully")
                
        except Exception as e:
            logger.error(f"Database initialization error: {str(e)}")
            raise
    
    def add_user(self, user_id: int, username: str = None, first_name: str = None):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO users (user_id, username, first_name, last_active, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, username or "", first_name or "", 
                     datetime.now(timezone.utc).isoformat(),
                     datetime.now(timezone.utc).isoformat()))
                
                cursor.execute('''
                    UPDATE users 
                    SET username = ?, first_name = ?, last_active = ?, updated_at = ?
                    WHERE user_id = ?
                ''', (username or "", first_name or "",
                     datetime.now(timezone.utc).isoformat(),
                     datetime.now(timezone.utc).isoformat(), user_id))
                
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error adding user {user_id}: {str(e)}")
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
                
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {str(e)}")
            return None
    
    def update_subscription(self, user_id: int, start_date: datetime, end_date: datetime, 
                          is_renewal: bool = False):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users 
                    SET subscription_start = ?, subscription_end = ?, 
                        is_premium = 1, updated_at = ?, last_reminder_sent = NULL
                    WHERE user_id = ?
                ''', (start_date.isoformat(), end_date.isoformat(), 
                     datetime.now(timezone.utc).isoformat(), user_id))
                
                cursor.execute('''
                    INSERT INTO subscription_history 
                    (user_id, start_date, end_date, amount, status, is_renewal)
                    VALUES (?, ?, ?, ?, 'active', ?)
                ''', (user_id, start_date.isoformat(), end_date.isoformat(), 
                     CONFIG.SUBSCRIPTION_AMOUNT / 100, 1 if is_renewal else 0))
                
                conn.commit()
                logger.info(f"Subscription updated for user {user_id}")
                
        except Exception as e:
            logger.error(f"Error updating subscription: {str(e)}")
            raise
    
    def revoke_subscription(self, user_id: int):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users 
                    SET is_premium = 0, updated_at = ?, invite_link = NULL
                    WHERE user_id = ?
                ''', (datetime.now(timezone.utc).isoformat(), user_id))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error revoking subscription: {str(e)}")
    
    def get_expired_subscriptions(self) -> List[Dict]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                current_time = datetime.now(timezone.utc).isoformat()
                cursor.execute('''
                    SELECT user_id, username, first_name, subscription_end
                    FROM users 
                    WHERE is_premium = 1 AND subscription_end < ?
                ''', (current_time,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"Error getting expired subscriptions: {str(e)}")
            return []
    
    def get_users_needing_reminder(self) -> List[Dict]:
        try:
            reminder_days = [int(d) for d in CONFIG.REMINDER_DAYS.split(',')]
            users_to_remind = []
            
            with self.get_connection() as conn:
                cursor = conn.cursor()
                current_time = datetime.now(timezone.utc)
                
                for days in reminder_days:
                    target_date = (current_time + timedelta(days=days)).isoformat()
                    next_day = (current_time + timedelta(days=days+1)).isoformat()
                    
                    cursor.execute('''
                        SELECT user_id, username, first_name, subscription_end, last_reminder_sent
                        FROM users 
                        WHERE is_premium = 1 
                        AND subscription_end >= ?
                        AND subscription_end < ?
                        AND (last_reminder_sent IS NULL OR last_reminder_sent < ?)
                    ''', (target_date, next_day, target_date))
                    
                    rows = cursor.fetchall()
                    for row in rows:
                        user_dict = dict(row)
                        user_dict['days_remaining'] = days
                        users_to_remind.append(user_dict)
                
                return users_to_remind
                
        except Exception as e:
            logger.error(f"Error getting reminder users: {str(e)}")
            return []
    
    def mark_reminder_sent(self, user_id: int):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users 
                    SET last_reminder_sent = ?
                    WHERE user_id = ?
                ''', (datetime.now(timezone.utc).isoformat(), user_id))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error marking reminder sent: {str(e)}")
    
    def add_payment_record(self, user_id: int, transaction_ref: str, amount: float):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO payments (user_id, transaction_ref, amount, status, created_at)
                    VALUES (?, ?, ?, 'pending', ?)
                ''', (user_id, transaction_ref, amount, datetime.now(timezone.utc).isoformat()))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error adding payment record: {str(e)}")
            raise
    
    def update_payment_status(self, transaction_ref: str, status: str, paystack_id: str = None):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE payments 
                    SET status = ?, completed_at = ?, paystack_id = ?
                    WHERE transaction_ref = ?
                ''', (status, datetime.now(timezone.utc).isoformat(), paystack_id, transaction_ref))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error updating payment status: {str(e)}")
            raise
    
    def get_payment_record(self, transaction_ref: str) -> Optional[Dict]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM payments WHERE transaction_ref = ?', (transaction_ref,))
                row = cursor.fetchone()
                return dict(row) if row else None
                
        except Exception as e:
            logger.error(f"Error getting payment record: {str(e)}")
            return None
    
    def update_user_stats(self, user_id: int, predictions_viewed: int = 0, bets_placed: int = 0):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users 
                    SET total_predictions_viewed = total_predictions_viewed + ?,
                        total_bets = total_bets + ?,
                        last_active = ?,
                        updated_at = ?
                    WHERE user_id = ?
                ''', (predictions_viewed, bets_placed,
                     datetime.now(timezone.utc).isoformat(),
                     datetime.now(timezone.utc).isoformat(), user_id))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error updating user stats: {str(e)}")
    
    def save_invite_link(self, user_id: int, invite_link: str):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users 
                    SET invite_link = ?
                    WHERE user_id = ?
                ''', (invite_link, user_id))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error saving invite link: {str(e)}")
    
    def log_notification(self, user_id: int, notification_type: str, message: str):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO notifications (user_id, type, message)
                    VALUES (?, ?, ?)
                ''', (user_id, notification_type, message))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error logging notification: {str(e)}")
    
    def get_admin_stats(self) -> Dict:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('SELECT COUNT(*) as count FROM users')
                total_users = cursor.fetchone()['count']
                
                current_time = datetime.now(timezone.utc).isoformat()
                cursor.execute('''
                    SELECT COUNT(*) as count FROM users 
                    WHERE is_premium = 1 AND subscription_end > ?
                ''', (current_time,))
                active_subs = cursor.fetchone()['count']
                
                cursor.execute('''
                    SELECT SUM(amount) as total FROM payments 
                    WHERE status = 'completed'
                ''')
                total_revenue = cursor.fetchone()['total'] or 0
                
                today = datetime.now(timezone.utc).date().isoformat()
                cursor.execute('''
                    SELECT COUNT(*) as count FROM payments 
                    WHERE status = 'completed' AND DATE(completed_at) = ?
                ''', (today,))
                today_subs = cursor.fetchone()['count']
                
                future_date = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
                cursor.execute('''
                    SELECT COUNT(*) as count FROM users 
                    WHERE is_premium = 1 AND subscription_end < ? AND subscription_end > ?
                ''', (future_date, current_time))
                expiring_soon = cursor.fetchone()['count']
                
                return {
                    'total_users': total_users,
                    'active_subscriptions': active_subs,
                    'total_revenue': total_revenue / 100,
                    'today_subscriptions': today_subs,
                    'expiring_soon': expiring_soon
                }
                
        except Exception as e:
            logger.error(f"Error getting admin stats: {str(e)}")
            return {}

class PaystackPayment:
    def __init__(self, secret_key: str, public_key: str):
        self.base_url = "https://api.paystack.co"
        self.secret_key = secret_key
        self.public_key = public_key
        self.headers = {
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json"
        }
    
    def create_payment_link(self, user_id: int, amount: float) -> Dict[str, Any]:
        try:
            tx_ref = f"okvirtuals_{user_id}_{uuid.uuid4().hex[:8]}_{int(time.time())}"
            
            amount_in_kobo = int(amount)
            
            payload = {
                "reference": tx_ref,
                "amount": amount_in_kobo,
                "email": f"user{user_id}@okvirtuals.com",
                "currency": "NGN",
                "callback_url": f"{CONFIG.WEBHOOK_URL}/payment/callback",
                "metadata": {
                    "user_id": str(user_id),
                    "plan": "monthly",
                    "custom_fields": [
                        {
                            "display_name": "User ID",
                            "variable_name": "user_id",
                            "value": str(user_id)
                        }
                    ]
                },
                "channels": ["card", "bank", "ussd", "qr", "mobile_money", "bank_transfer"]
            }
            
            response = requests.post(
                f"{self.base_url}/transaction/initialize",
                json=payload,
                headers=self.headers,
                timeout=30
            )
            
            response.raise_for_status()
            data = response.json()
            
            if data.get('status') and data.get('data'):
                return {
                    "status": "success",
                    "tx_ref": tx_ref,
                    "link": data["data"]["authorization_url"],
                    "access_code": data["data"]["access_code"]
                }
            else:
                logger.error(f"Paystack API error: {data}")
                return {
                    "status": "error", 
                    "message": data.get('message', 'Payment link creation failed')
                }
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Payment link creation error: {str(e)}")
            return {
                "status": "error", 
                "message": "Payment service temporarily unavailable"
            }
        except Exception as e:
            logger.error(f"Unexpected error in payment link creation: {str(e)}")
            return {
                "status": "error", 
                "message": "An error occurred while creating payment link"
            }
    
    def verify_payment(self, tx_ref: str) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self.base_url}/transaction/verify/{tx_ref}",
                headers=self.headers,
                timeout=30
            )
            
            response.raise_for_status()
            data = response.json()
            
            if data.get('status') and data.get('data'):
                transaction_data = data['data']
                
                if transaction_data.get('status') == 'success':
                    return {
                        "status": "success",
                        "data": {
                            "status": "successful",
                            "id": transaction_data.get('id'),
                            "reference": transaction_data.get('reference'),
                            "amount": transaction_data.get('amount'),
                            "currency": transaction_data.get('currency'),
                            "customer": transaction_data.get('customer'),
                            "paid_at": transaction_data.get('paid_at')
                        }
                    }
                else:
                    return {
                        "status": "pending",
                        "data": {
                            "status": transaction_data.get('status'),
                            "reference": transaction_data.get('reference')
                        }
                    }
            else:
                logger.error(f"Paystack verification error: {data}")
                return {
                    "status": "error",
                    "message": data.get('message', 'Verification failed')
                }
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Payment verification error: {str(e)}")
            return {
                "status": "error",
                "message": "Verification service temporarily unavailable"
            }
        except Exception as e:
            logger.error(f"Unexpected verification error: {str(e)}")
            return {
                "status": "error",
                "message": "An error occurred during verification"
            }
    
    def verify_webhook_signature(self, request_signature: str, payload: str) -> bool:
        try:
            computed_signature = hmac.new(
                self.secret_key.encode('utf-8'),
                payload.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
            
            return hmac.compare_digest(computed_signature, request_signature)
            
        except Exception as e:
            logger.error(f"Signature verification error: {str(e)}")
            return False

class GroupManager:
    def __init__(self, application: Application):
        self.application = application
        self.channel_id = CONFIG.PREMIUM_CHANNEL_ID
    
    async def create_invite_link(self, user_id: int) -> Optional[str]:
        try:
            invite = await self.application.bot.create_chat_invite_link(
                chat_id=self.channel_id,
                member_limit=1,
                expire_date=datetime.now(timezone.utc) + timedelta(hours=24),
                name=f"User {user_id}"
            )
            
            logger.info(f"Created invite link for user {user_id}")
            return invite.invite_link
            
        except Exception as e:
            logger.error(f"Error creating invite link: {str(e)}")
            return None
    
    async def check_membership(self, user_id: int) -> bool:
        try:
            member = await self.application.bot.get_chat_member(
                chat_id=self.channel_id,
                user_id=user_id
            )
            
            return member.status in [
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER
            ]
            
        except Exception as e:
            logger.error(f"Error checking membership: {str(e)}")
            return False
    
    async def remove_user_from_group(self, user_id: int) -> bool:
        try:
            await self.application.bot.ban_chat_member(
                chat_id=self.channel_id,
                user_id=user_id,
                revoke_messages=False
            )
            
            logger.info(f"Removed user {user_id} from premium group")
            return True
            
        except Exception as e:
            logger.error(f"Error removing user: {str(e)}")
            return False

class SubscriptionMonitor:
    def __init__(self, db: DatabaseManager, group_manager: GroupManager):
        self.db = db
        self.group_manager = group_manager
        self.running = False
        self.thread = None
    
    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.thread.start()
            logger.info("Subscription monitor started")
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
    
    def _monitor_loop(self):
        while self.running:
            try:
                self._check_expired_subscriptions()
                self._send_expiry_reminders()
                time.sleep(1800)
            except Exception as e:
                logger.error(f"Error in subscription monitor: {str(e)}")
                time.sleep(300)
    
    def _check_expired_subscriptions(self):
        try:
            expired_users = self.db.get_expired_subscriptions()
            
            if expired_users:
                logger.info(f"Found {len(expired_users)} expired subscriptions")
                
                for user in expired_users:
                    user_id = user['user_id']
                    self.db.revoke_subscription(user_id)
                    
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            self.group_manager.remove_user_from_group(user_id)
                        )
                        loop.run_until_complete(
                            self._send_expiry_notification(user_id)
                        )
                        loop.close()
                    except Exception as e:
                        logger.error(f"Failed to process expiry: {str(e)}")
                    
        except Exception as e:
            logger.error(f"Error checking expired subscriptions: {str(e)}")
    
    def _send_expiry_reminders(self):
        try:
            users_to_remind = self.db.get_users_needing_reminder()
            
            if users_to_remind:
                for user in users_to_remind:
                    user_id = user['user_id']
                    days_remaining = user['days_remaining']
                    
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            self._send_reminder_notification(user_id, days_remaining)
                        )
                        loop.close()
                        self.db.mark_reminder_sent(user_id)
                    except Exception as e:
                        logger.error(f"Failed to send reminder: {str(e)}")
                        
        except Exception as e:
            logger.error(f"Error sending reminders: {str(e)}")
    async def handle_email_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting_email'):
        email = update.message.text.strip()
        
        # Validate email format
        if '@' in email and '.' in email:
            user_id = update.effective_user.id
            
            # Save email to database
            self.db.update_user_email(user_id, email)
            
            # Now create payment with REAL email
            payment_result = self.payment.create_payment_link(
                user_id, 
                email,  # Use the real email here
                self.config.SUBSCRIPTION_AMOUNT
            )
            
            # Rest of payment code...
        else:
            await update.message.reply_text("❌ Invalid email. Please enter a valid email address.")
    async def _send_expiry_notification(self, user_id: int):
        try:
            if bot_application:
                message = f"""⚠️ *Subscription Expired*

Your premium subscription has expired.

💎 *Renew Now:*
Only ₦{CONFIG.SUBSCRIPTION_AMOUNT / 100:.0f} for 30 more days!

Use /subscribe to renew."""

                await bot_application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN
                )
                
        except Exception as e:
            logger.error(f"Error sending expiry notification: {str(e)}")
    
    async def _send_reminder_notification(self, user_id: int, days_remaining: int):
        try:
            if bot_application:
                message = f"""🔔 *Subscription Expiring Soon*

Your premium subscription expires in *{days_remaining} day{"s" if days_remaining > 1 else ""}*!

💎 *Renew Now:*
Only ₦{CONFIG.SUBSCRIPTION_AMOUNT / 100:.0f} for 30 more days!

Use /subscribe to renew."""

                await bot_application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN
                )
                
        except Exception as e:
            logger.error(f"Error sending reminder: {str(e)}")

class RateLimiter:
    def __init__(self):
        self.requests = {}
        self.max_requests_per_minute = 10
    
    def is_allowed(self, user_id: int) -> bool:
        current_time = time.time()
        minute_ago = current_time - 60
        
        if user_id not in self.requests:
            self.requests[user_id] = []
        
        self.requests[user_id] = [req_time for req_time in self.requests[user_id] if req_time > minute_ago]
        
        if len(self.requests[user_id]) < self.max_requests_per_minute:
            self.requests[user_id].append(current_time)
            return True
        
        return False

class OKVirtualsBot:
    def __init__(self, config: Config):
        self.config = config
        self.db = DatabaseManager(config.DATABASE_PATH)
        self.payment = PaystackPayment(config.PAYSTACK_SECRET_KEY, config.PAYSTACK_PUBLIC_KEY)
        self.rate_limiter = RateLimiter()
        self.application = None
        self.group_manager = None
        self.subscription_monitor = None
        self.admin_ids = [int(id.strip()) for id in config.ADMIN_USER_IDS.split(',') if id.strip()]
    
    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids
    
    async def setup_bot_commands(self):
        try:
            await self.application.bot.set_my_commands(BOT_COMMANDS)
            logger.info("Bot commands set successfully")
        except Exception as e:
            logger.error(f"Error setting bot commands: {str(e)}")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        welcome_text = f"""🎯 *Welcome to OK Virtuals Betting!*

Hello {user.first_name}! 👋

🔥 *What We Offer:*

✅100% ACCURACY 
✅VIRTUAL EXPERT
✅REAL TIME TIP
✅COMMUNITY FOR EXPERTS
💰 *Subscribe:* ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f}/month"""
        
        keyboard = [
            [InlineKeyboardButton("💎 Subscribe", callback_data="subscribe")],
            [InlineKeyboardButton("📊 Status", callback_data="status"),
             InlineKeyboardButton("🎯 Tips", callback_data="predictions")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_text, 
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def subscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        price_naira = self.config.SUBSCRIPTION_AMOUNT / 100
        subscribe_text = f"""💎 *Premium Subscription*

💰 *Price:* ₦{price_naira:.0f}
⏰ *Duration:* 30 Days
📊 *Success Rate:* 90%+

✨ *What You Get:*
✅ Daily Predictions
✅ VIP Group Access
✅ Expert Analysis
✅ Real-time Tips

Click below to subscribe!"""
        
        keyboard = [
            [InlineKeyboardButton("💳 Pay ₦100 Now", callback_data="process_payment")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            subscribe_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        user_data = self.db.get_user(user.id)
        
        if user_data and user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                current_time = datetime.now(timezone.utc)
                
                if end_date > current_time:
                    days_remaining = (end_date - current_time).days
                    
                    status_text = f"""✅ *Premium Active*

👤 User: {user.first_name}
📅 Expires: {end_date.strftime('%B %d, %Y')}
⏰ Days Left: {days_remaining} days"""
                    
                    keyboard = [
                        [InlineKeyboardButton("🔗 Access Channel", callback_data="premium")],
                        [InlineKeyboardButton("🎯 Predictions", callback_data="predictions")]
                    ]
                else:
                    status_text = "⚠️ *Subscription Expired*\n\nRenew to regain access!"
                    keyboard = [[InlineKeyboardButton("💎 Renew", callback_data="subscribe")]]
            except:
                status_text = "❌ Error retrieving status"
                keyboard = []
        else:
            status_text = f"""📊 *Subscription Status*

👤 User: {user.first_name}
❌ Status: Free User

💰 Subscribe: ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f}/month"""
            
            keyboard = [[InlineKeyboardButton("💎 Subscribe", callback_data="subscribe")]]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            status_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def predictions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        user_data = self.db.get_user(user.id)
        is_premium = user_data and user_data['is_premium']
        
        if is_premium:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                if end_date > datetime.now(timezone.utc):
                    self.db.update_user_stats(user.id, predictions_viewed=1)
                    
                    predictions_text = f"""🎯 *TODAY'S PREDICTIONS*

📅 {datetime.now().strftime('%B %d, %Y')}

⚽ *VIRTUAL FOOTBALL*
📊 Prediction: Over 2.5 Goals
💰 Odds: 1.85
✅ Confidence: 92%

🏀 *VIRTUAL BASKETBALL*
📊 Prediction: Over 215.5 Points
💰 Odds: 1.90
✅ Confidence: 88%

Use /premium to join channel!"""
                else:
                    is_premium = False
            except:
                is_premium = False
        
        if not is_premium:
            predictions_text = f"""🎯 *SAMPLE PREDICTIONS*

📅 {datetime.now().strftime('%B %d, %Y')}

⚽ *VIRTUAL FOOTBALL*
📊 [Premium Content]
💰 [Premium Content]

🔒 Subscribe to unlock!"""
        
        keyboard = []
        if not is_premium:
            keyboard.append([InlineKeyboardButton("💎 Subscribe", callback_data="subscribe")])
        else:
            keyboard.append([InlineKeyboardButton("🔗 Join Channel", callback_data="premium")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            predictions_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        user_data = self.db.get_user(user.id)
        
        predictions_viewed = user_data.get('total_predictions_viewed', 0) if user_data else 0
        total_bets = user_data.get('total_bets', 0) if user_data else 0
        
        stats_text = f"""📈 *YOUR STATISTICS*

👤 User: {user.first_name}
🎯 Predictions Viewed: {predictions_viewed}
🎲 Total Bets: {total_bets}"""
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            stats_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def support_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        support_text = f"""💬 *Customer Support*

✈️ Telegram: @okvirtual001
⏰ Response: Within 30 minutes

Your User ID: `{user.id}`"""
        
        keyboard = [[InlineKeyboardButton("✈️ Contact Support", url="https://t.me/okvirtual001")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            support_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """ℹ️ *Help & Commands*

📋 *Commands:*
/start - Start the bot
/subscribe - Subscribe to premium
/status - Check subscription status
/predictions - View predictions
/stats - View statistics
/support - Get support
/premium - Get invite link

💰 *Subscription:* ₦100 for 30 Days"""
        
        keyboard = [[InlineKeyboardButton("💎 Subscribe", callback_data="subscribe")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            help_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        if not self.is_admin(user.id):
            await update.message.reply_text("❌ Unauthorized")
            return
        
        stats = self.db.get_admin_stats()
        
        admin_text = f"""👑 *Admin Dashboard*

👥 Total Users: {stats.get('total_users', 0)}
💎 Active Subs: {stats.get('active_subscriptions', 0)}
💰 Revenue: ₦{stats.get('total_revenue', 0):.2f}
📅 Today: {stats.get('today_subscriptions', 0)}"""
        
        await update.message.reply_text(admin_text, parse_mode=ParseMode.MARKDOWN)
    
    async def premium_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        user_data = self.db.get_user(user.id)
        
        if user_data and user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                if end_date > datetime.now(timezone.utc):
                    invite_link = await self.group_manager.create_invite_link(user.id)
                    
                    if invite_link:
                        self.db.save_invite_link(user.id, invite_link)
                        
                        premium_text = f"""💎 *Premium Channel Access*

🔗 *Your Invite Link:*
{invite_link}

⚠️ Link expires in 24 hours
📅 Valid until: {end_date.strftime('%B %d, %Y')}"""
                        
                        keyboard = [[InlineKeyboardButton("🔗 Join Now", url=invite_link)]]
                    else:
                        premium_text = f"""💎 *Premium Access Active*

⚠️ Unable to create link
Join via: {self.config.PREMIUM_CHANNEL_USERNAME}"""
                        keyboard = []
                else:
                    premium_text = "⚠️ Subscription expired!"
                    keyboard = [[InlineKeyboardButton("💎 Renew", callback_data="subscribe")]]
            except:
                premium_text = "❌ Error checking subscription"
                keyboard = []
        else:
            premium_text = """🔒 *Premium Access Required*

Subscribe to get access!

💰 Only ₦100 for 30 days"""
            keyboard = [[InlineKeyboardButton("💎 Subscribe", callback_data="subscribe")]]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(premium_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    async def process_payment_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        if not self.rate_limiter.is_allowed(user_id):
            await query.edit_message_text("⚠️ Please wait before making another request.")
            return
        
        await query.edit_message_text("⏳ Creating payment link...")
        
        try:
            # First, ask for email
await query.edit_message_text(
    "📧 *Enter Your Email Address*\n\n"
    "Please enter your valid email address to proceed with payment:",
    parse_mode=ParseMode.MARKDOWN
)
# Store that we're waiting for email input
context.user_data['awaiting_email'] = True
            if payment_result['status'] == 'success':
                self.db.add_payment_record(user_id, payment_result['tx_ref'], self.config.SUBSCRIPTION_AMOUNT)
                
                payment_text = f"""💳 *Payment Details*

💰 Amount: ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f}
⏰ Duration: 30 Days

📝 *Instructions:*
1️⃣ Click "Pay Now"
2️⃣ Complete payment
3️⃣ Click "I have Paid"
4️⃣ Get instant access!

Transaction: `{payment_result['tx_ref']}`"""
                
                keyboard = [
                    [InlineKeyboardButton("💳 Pay Now", url=payment_result['link'])],
                    [InlineKeyboardButton("✅ I have Paid", callback_data=f"verify_{payment_result['tx_ref']}")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="subscribe")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    payment_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await query.edit_message_text(
                    f"❌ Error: {payment_result.get('message', 'Failed')}\n\nContact: @okvirtual001"
                )
        except Exception as e:
            logger.error(f"Payment error: {str(e)}")
            await query.edit_message_text("❌ Error. Contact support: @okvirtual001")
    
    async def verify_payment_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        tx_ref = query.data.split('_', 1)[1]
        user_id = query.from_user.id
        
        payment_record = self.db.get_payment_record(tx_ref)
        if not payment_record or payment_record['user_id'] != user_id:
            await query.edit_message_text("❌ Payment not found. Contact: @okvirtual001")
            return
        
        if payment_record['status'] == 'completed':
            await query.edit_message_text("✅ Already processed! Use /premium for link.")
            return
        
        await query.edit_message_text("⏳ Verifying payment...")
        
        try:
            verification_result = self.payment.verify_payment(tx_ref)
            
            if (verification_result.get('status') == 'success' and 
                verification_result.get('data', {}).get('status') == 'successful'):
                
                user_data = self.db.get_user(user_id)
                is_renewal = False
                
                if user_data and user_data['is_premium']:
                    try:
                        end_date = datetime.fromisoformat(user_data['subscription_end'])
                        if end_date > datetime.now(timezone.utc):
                            is_renewal = True
                            start_date = end_date
                            end_date = start_date + timedelta(days=self.config.SUBSCRIPTION_DAYS)
                        else:
                            start_date = datetime.now(timezone.utc)
                            end_date = start_date + timedelta(days=self.config.SUBSCRIPTION_DAYS)
                    except:
                        start_date = datetime.now(timezone.utc)
                        end_date = start_date + timedelta(days=self.config.SUBSCRIPTION_DAYS)
                else:
                    start_date = datetime.now(timezone.utc)
                    end_date = start_date + timedelta(days=self.config.SUBSCRIPTION_DAYS)
                
                self.db.update_subscription(user_id, start_date, end_date, is_renewal)
                self.db.update_payment_status(tx_ref, 'completed', verification_result.get('data', {}).get('id'))
                
                invite_link = await self.group_manager.create_invite_link(user_id)
                
                if invite_link:
                    self.db.save_invite_link(user_id, invite_link)
                    
                    success_text = f"""🎉 *PAYMENT SUCCESSFUL!*

Welcome to Premium! 💎

📅 Valid Until: {end_date.strftime('%B %d, %Y')}
💰 Paid: ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f}

🔗 *Your Invite Link:*
{invite_link}

⚠️ Link expires in 24 hours!

Use /predictions to see tips! 🎯"""
                    
                    keyboard = [
                        [InlineKeyboardButton("🔗 Join Channel NOW", url=invite_link)],
                        [InlineKeyboardButton("🎯 Predictions", callback_data="predictions")]
                    ]
                else:
                    success_text = f"""🎉 *PAYMENT SUCCESSFUL!*

Welcome to Premium! 💎

📅 Valid Until: {end_date.strftime('%B %d, %Y')}

Use /premium to get invite link!"""
                    
                    keyboard = [[InlineKeyboardButton("🔗 Get Link", callback_data="premium")]]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    success_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
                
                self.db.log_notification(user_id, "payment_success", f"Payment: {tx_ref}")
                logger.info(f"Payment successful for user {user_id}")
                
            else:
                await query.edit_message_text(
                    "⚠️ Payment not confirmed yet.\n\nWait and try again.\nContact: @okvirtual001",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Try Again", callback_data=f"verify_{tx_ref}")]
                    ])
                )
        except Exception as e:
            logger.error(f"Verification error: {str(e)}")
            await query.edit_message_text(
                "❌ Verification error. Contact: @okvirtual001",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data=f"verify_{tx_ref}")]
                ])
            )
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        
        try:
            if query.data == "subscribe":
                await self.subscribe_button(query, context)
            elif query.data == "process_payment":
                await self.process_payment_callback(update, context)
            elif query.data.startswith("verify_"):
                await self.verify_payment_callback(update, context)
            elif query.data == "status":
                await self.status_button(query, context)
            elif query.data == "predictions":
                await self.predictions_button(query, context)
            elif query.data == "stats":
                await self.stats_button(query, context)
            elif query.data == "support":
                await self.support_button(query, context)
            elif query.data == "premium":
                await self.premium_button(query, context)
            elif query.data == "back_to_menu":
                await self.back_to_menu(query, context)
            else:
                await query.answer("Unknown action")
        except Exception as e:
            logger.error(f"Button error: {str(e)}")
            await query.answer("Error occurred")
    
    async def subscribe_button(self, query, context):
        await query.answer()
        
        subscribe_text = f"""💎 *Premium Subscription*

💰 Price: ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f}
⏰ Duration: 30 Days

Click below to subscribe!"""
        
        keyboard = [
            [InlineKeyboardButton("💳 Pay Now", callback_data="process_payment")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
        ]
        
        await query.edit_message_text(
            subscribe_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def status_button(self, query, context):
        await query.answer()
        mock_update = type('obj', (object,), {
            'message': query.message,
            'effective_user': query.from_user
        })()
        await self.status_command(mock_update, context)
    
    async def predictions_button(self, query, context):
        await query.answer()
        mock_update = type('obj', (object,), {
            'message': query.message,
            'effective_user': query.from_user
        })()
        await self.predictions_command(mock_update, context)
    
    async def stats_button(self, query, context):
        await query.answer()
        mock_update = type('obj', (object,), {
            'message': query.message,
            'effective_user': query.from_user
        })()
        await self.stats_command(mock_update, context)
    
    async def support_button(self, query, context):
        await query.answer()
        mock_update = type('obj', (object,), {
            'message': query.message,
            'effective_user': query.from_user
        })()
        await self.support_command(mock_update, context)
    
    async def premium_button(self, query, context):
        await query.answer()
        mock_update = type('obj', (object,), {
            'message': query.message,
            'effective_user': query.from_user
        })()
        await self.premium_command(mock_update, context)
    
    async def back_to_menu(self, query, context):
        await query.answer()
        user = query.from_user
        
        welcome_text = f"""🎯 *OK Virtuals Betting*

Hello {user.first_name}! 👋

Use buttons below to navigate."""
        
        keyboard = [
            [InlineKeyboardButton("💎 Subscribe", callback_data="subscribe")],
            [InlineKeyboardButton("📊 Status", callback_data="status"),
             InlineKeyboardButton("🎯 Predictions", callback_data="predictions")]
        ]
        
        await query.edit_message_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == '/' or self.path == '':
                # Landing page
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                
                # Get bot username (remove @ if present)
                bot_username = CONFIG.PREMIUM_CHANNEL_USERNAME.replace('@', '') if CONFIG.PREMIUM_CHANNEL_USERNAME else 'your_bot'
                
                landing_html = f"""
                <!DOCTYPE html>
                <html lang="en">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>OK Virtuals Betting Bot - Premium Predictions</title>
                    <style>
                        * {{
                            margin: 0;
                            padding: 0;
                            box-sizing: border-box;
                        }}
                        body {{
                            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            min-height: 100vh;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            padding: 20px;
                        }}
                        .container {{
                            background: white;
                            border-radius: 20px;
                            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                            max-width: 600px;
                            width: 100%;
                            padding: 40px;
                            text-align: center;
                            animation: slideIn 0.5s ease-out;
                        }}
                        @keyframes slideIn {{
                            from {{
                                opacity: 0;
                                transform: translateY(-30px);
                            }}
                            to {{
                                opacity: 1;
                                transform: translateY(0);
                            }}
                        }}
                        .logo {{
                            font-size: 80px;
                            margin-bottom: 20px;
                            animation: bounce 2s infinite;
                        }}
                        @keyframes bounce {{
                            0%, 100% {{ transform: translateY(0); }}
                            50% {{ transform: translateY(-10px); }}
                        }}
                        h1 {{
                            color: #333;
                            font-size: 32px;
                            margin-bottom: 10px;
                        }}
                        .subtitle {{
                            color: #666;
                            font-size: 18px;
                            margin-bottom: 30px;
                        }}
                        .features {{
                            background: #f8f9fa;
                            border-radius: 10px;
                            padding: 25px;
                            margin: 30px 0;
                            text-align: left;
                        }}
                        .feature {{
                            display: flex;
                            align-items: center;
                            margin: 15px 0;
                            font-size: 16px;
                            color: #333;
                        }}
                        .feature-icon {{
                            font-size: 24px;
                            margin-right: 15px;
                            min-width: 30px;
                        }}
                        .btn {{
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            color: white;
                            text-decoration: none;
                            padding: 18px 40px;
                            border-radius: 50px;
                            font-size: 18px;
                            font-weight: bold;
                            display: inline-block;
                            margin: 20px 0;
                            transition: transform 0.3s, box-shadow 0.3s;
                            box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
                        }}
                        .btn:hover {{
                            transform: translateY(-3px);
                            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.6);
                        }}
                        .price {{
                            background: #28a745;
                            color: white;
                            padding: 15px 25px;
                            border-radius: 10px;
                            font-size: 24px;
                            font-weight: bold;
                            margin: 20px 0;
                            display: inline-block;
                        }}
                        .status {{
                            margin-top: 30px;
                            padding: 15px;
                            background: #e7f4ff;
                            border-radius: 10px;
                            color: #0066cc;
                            font-size: 14px;
                        }}
                        .footer {{
                            margin-top: 30px;
                            color: #999;
                            font-size: 14px;
                        }}
                        @media (max-width: 600px) {{
                            .container {{
                                padding: 30px 20px;
                            }}
                            h1 {{
                                font-size: 24px;
                            }}
                            .logo {{
                                font-size: 60px;
                            }}
                        }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="logo">🎯</div>
                        <h1>OK Virtuals Betting Bot</h1>
                        <p class="subtitle">Your Gateway to Premium Betting Predictions</p>
                        
                        <div class="features">
                            <div class="feature">
                                <span class="feature-icon">✅</span>
                                <span>Daily Expert Predictions</span>
                            </div>
                            <div class="feature">
                                <span class="feature-icon">📊</span>
                                <span>90%+ Accuracy Rate</span>
                            </div>
                            <div class="feature">
                                <span class="feature-icon">💎</span>
                                <span>Exclusive VIP Community</span>
                            </div>
                            <div class="feature">
                                <span class="feature-icon">⚡</span>
                                <span>Real-time Betting Tips</span>
                            </div>
                            <div class="feature">
                                <span class="feature-icon">🏆</span>
                                <span>Professional Analysis</span>
                            </div>
                        </div>
                        
                        <div class="price">₦{CONFIG.SUBSCRIPTION_AMOUNT / 10000:.0f} / Month</div>
                        
                        <a href="https://t.me/okvirtualbot" class="btn">
                            🚀 Start Winning Now
                        </a>
                        
                        <div class="status">
                            ✓ Bot is Online and Ready<br>
                            ✓ Secure Payment via Paystack<br>
                            ✓ Instant Access After Payment
                        </div>
                        
                        <div class="footer">
                            <p>Need help? Contact <a href="https://t.me/okvirtual001" style="color: #667eea;">@okvirtual001</a></p>
                            <p style="margin-top: 10px;">© 2025 OK Virtuals. All rights reserved.</p>
                        </div>
                    </div>
                </body>
                </html>
                """
                self.wfile.write(landing_html.encode('utf-8'))
                
            elif self.path.startswith('/health'):
                health_status = {
                    "status": "healthy",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "service": "OK Virtuals Bot (Paystack)"
                }
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(health_status).encode())
                
            elif self.path.startswith('/payment/callback'):
                # Payment callback - redirect to success page
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                
                # Get bot username
                bot_username = CONFIG.PREMIUM_CHANNEL_USERNAME.replace('@', '') if CONFIG.PREMIUM_CHANNEL_USERNAME else 'your_bot'
                
                success_html = f"""
                <!DOCTYPE html>
                <html lang="en">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Payment Successful - OK Virtuals</title>
                    <style>
                        * {{
                            margin: 0;
                            padding: 0;
                            box-sizing: border-box;
                        }}
                        body {{
                            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            min-height: 100vh;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            padding: 20px;
                        }}
                        .container {{
                            background: white;
                            border-radius: 20px;
                            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                            max-width: 500px;
                            width: 100%;
                            padding: 40px;
                            text-align: center;
                            animation: slideIn 0.5s ease-out;
                        }}
                        @keyframes slideIn {{
                            from {{
                                opacity: 0;
                                transform: scale(0.9);
                            }}
                            to {{
                                opacity: 1;
                                transform: scale(1);
                            }}
                        }}
                        .success-icon {{
                            font-size: 80px;
                            margin-bottom: 20px;
                            animation: bounce 1s ease-out;
                        }}
                        @keyframes bounce {{
                            0%, 100% {{ transform: scale(1); }}
                            50% {{ transform: scale(1.1); }}
                        }}
                        h1 {{
                            color: #28a745;
                            font-size: 32px;
                            margin-bottom: 15px;
                        }}
                        p {{
                            color: #666;
                            font-size: 16px;
                            line-height: 1.6;
                            margin-bottom: 20px;
                        }}
                        .steps {{
                            background: #f8f9fa;
                            border-radius: 10px;
                            padding: 20px;
                            margin: 25px 0;
                            text-align: left;
                        }}
                        .steps h3 {{
                            color: #333;
                            font-size: 18px;
                            margin-bottom: 15px;
                            text-align: center;
                        }}
                        .step {{
                            display: flex;
                            align-items: center;
                            margin: 12px 0;
                            padding: 10px;
                            background: white;
                            border-radius: 8px;
                        }}
                        .step-number {{
                            background: #667eea;
                            color: white;
                            width: 30px;
                            height: 30px;
                            border-radius: 50%;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            font-weight: bold;
                            margin-right: 15px;
                            flex-shrink: 0;
                        }}
                        .btn {{
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            color: white;
                            text-decoration: none;
                            padding: 16px 35px;
                            border-radius: 50px;
                            font-size: 18px;
                            font-weight: bold;
                            display: inline-block;
                            margin: 20px 0;
                            transition: transform 0.3s, box-shadow 0.3s;
                            box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
                        }}
                        .btn:hover {{
                            transform: translateY(-3px);
                            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.6);
                        }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="success-icon">🎉</div>
                        <h1>Payment Successful!</h1>
                        <p>Your payment has been received successfully. Complete the verification to unlock premium access.</p>
                        
                        <div class="steps">
                            <h3>Next Steps:</h3>
                            <div class="step">
                                <div class="step-number">1</div>
                                <span>Return to the Telegram bot</span>
                            </div>
                            <div class="step">
                                <div class="step-number">2</div>
                                <span>Click "✅ I have Paid" button</span>
                            </div>
                            <div class="step">
                                <div class="step-number">3</div>
                                <span>Get instant VIP access!</span>
                            </div>
                        </div>
                        
                        <a href="https://t.me/okvirtualbot" class="btn">
                            ↩️ Return to Bot
                        </a>
                        
                        <p style="margin-top: 20px; font-size: 14px; color: #999;">
                            Need help? Contact <a href="https://t.me/okvirtual001" style="color: #667eea;">@okvirtual001</a>
                        </p>
                    </div>
                </body>
                </html>
                """
                self.wfile.write(success_html.encode('utf-8'))
                
            else:
                self.send_response(404)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                
                not_found_html = """
                <!DOCTYPE html>
                <html>
                <head>
                    <title>404 - Page Not Found</title>
                    <style>
                        body {
                            font-family: Arial, sans-serif;
                            text-align: center;
                            padding: 50px;
                            background: #f5f5f5;
                        }
                        h1 { color: #ff6b6b; font-size: 48px; }
                        p { color: #666; font-size: 18px; }
                        a { color: #667eea; text-decoration: none; font-weight: bold; }
                    </style>
                </head>
                <body>
                    <h1>404</h1>
                    <p>Page not found</p>
                    <a href="/">← Go back home</a>
                </body>
                </html>
                """
                self.wfile.write(not_found_html.encode('utf-8'))
                
        except Exception as e:
            logger.error(f"GET request error: {str(e)}")
            self.send_response(500)
            self.end_headers()
    
    def do_POST(self):
        try:
            if self.path.startswith('/webhook/paystack'):
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                
                signature = self.headers.get('x-paystack-signature', '')
                
                payment = PaystackPayment(CONFIG.PAYSTACK_SECRET_KEY, CONFIG.PAYSTACK_PUBLIC_KEY)
                if payment.verify_webhook_signature(signature, post_data.decode('utf-8')):
                    webhook_data = json.loads(post_data.decode('utf-8'))
                    event = webhook_data.get('event')
                    
                    logger.info(f"Paystack webhook received: {event}")
                    
                    if event == 'charge.success':
                        data = webhook_data.get('data', {})
                        reference = data.get('reference')
                        status = data.get('status')
                        
                        if reference and status == 'success':
                            logger.info(f"Payment successful via webhook: {reference}")
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success"}).encode())
                else:
                    logger.warning("Invalid Paystack webhook signature")
                    self.send_response(401)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
                
        except Exception as e:
            logger.error(f"Webhook error: {str(e)}")
            self.send_response(500)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

def run_webhook_server():
    try:
        server = HTTPServer(('0.0.0.0', CONFIG.PORT), WebhookHandler)
        logger.info(f"Webhook server started on port {CONFIG.PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Webhook server error: {str(e)}")

def signal_handler(signum, frame):
    global shutdown_flag
    logger.info("Initiating graceful shutdown...")
    shutdown_flag = True

def main():
    global shutdown_flag, bot_application
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info("Starting OK Virtuals Betting Bot (Paystack)...")
    
    bot = OKVirtualsBot(CONFIG)
    logger.info("Bot initialized")
    
    application = Application.builder().token(CONFIG.BOT_TOKEN).build()
    bot.application = application
    bot_application = application
    bot.group_manager = GroupManager(application)
    
    bot.subscription_monitor = SubscriptionMonitor(bot.db, bot.group_manager)
    bot.subscription_monitor.start()
    
    application.add_handler(CommandHandler("start", bot.start_command))
    application.add_handler(CommandHandler("subscribe", bot.subscribe_command))
    application.add_handler(CommandHandler("status", bot.status_command))
    application.add_handler(CommandHandler("predictions", bot.predictions_command))
    application.add_handler(CommandHandler("stats", bot.stats_command))
    application.add_handler(CommandHandler("support", bot.support_command))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("premium", bot.premium_command))
    application.add_handler(CommandHandler("admin", bot.admin_command))
    application.add_handler(CallbackQueryHandler(bot.button_callback))
    
    webhook_thread = threading.Thread(target=run_webhook_server, daemon=True)
    webhook_thread.start()
    
    logger.info("✅ OK Virtuals Betting Bot Started!")
    print("=" * 50)
    print("🎯 OK VIRTUALS BOT RUNNING (PAYSTACK)")
    print("=" * 50)
    print(f"💚 Health: http://0.0.0.0:{CONFIG.PORT}/health")
    print(f"💰 Price: ₦{CONFIG.SUBSCRIPTION_AMOUNT / 100:.0f}")
    print(f"📱 Support: @okvirtual001")
    print(f"💳 Payment: Paystack")
    print("=" * 50)
    
    max_retries = 5
    retry_count = 0
    
    while not shutdown_flag and retry_count < max_retries:
        try:
            async def post_init(application):
                await bot.setup_bot_commands()
            
            application.post_init = post_init
            
            application.run_polling(
                drop_pending_updates=True,
                close_loop=False,
                stop_signals=None
            )
            break
            
        except Conflict:
            retry_count += 1
            if retry_count < max_retries:
                wait_time = min(retry_count * 10, 60)
                logger.info(f"Conflict. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                logger.error("Max retries reached")
                break
                
        except (NetworkError, TimedOut):
            retry_count += 1
            if retry_count < max_retries:
                logger.info("Network error. Retrying in 30s...")
                time.sleep(30)
            else:
                logger.error("Max retries reached")
                break
                
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            break
    
    if bot.subscription_monitor:
        bot.subscription_monitor.stop()
    
    logger.info("Bot stopped")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")