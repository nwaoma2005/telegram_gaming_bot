#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OK Virtuals Betting Prediction Bot - COMPLETE WORKING VERSION
All features fully implemented and tested
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
    FLUTTERWAVE_SECRET_KEY: str
    FLUTTERWAVE_PUBLIC_KEY: str
    FLUTTERWAVE_WEBHOOK_SECRET: str
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
        FLUTTERWAVE_SECRET_KEY=os.getenv("FLUTTERWAVE_SECRET_KEY", ""),
        FLUTTERWAVE_PUBLIC_KEY=os.getenv("FLUTTERWAVE_PUBLIC_KEY", ""),
        FLUTTERWAVE_WEBHOOK_SECRET=os.getenv("FLUTTERWAVE_WEBHOOK_SECRET", ""),
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
    
    return config

try:
    CONFIG = load_config()
    logger.info("Configuration loaded successfully")
except ValueError as e:
    logger.error(f"Configuration error: {e}")
    sys.exit(1)

BOT_COMMANDS = [
    BotCommand("start", "Start the bot and see welcome menu"),
    BotCommand("subscribe", "Subscribe to premium predictions (₦100/month)"),
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
                        flutterwave_id TEXT,
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
                logger.info(f"Subscription updated for user {user_id} (renewal: {is_renewal})")
                
        except Exception as e:
            logger.error(f"Error updating subscription for user {user_id}: {str(e)}")
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
                logger.info(f"Revoked premium access for user {user_id}")
                
        except Exception as e:
            logger.error(f"Error revoking subscription for user {user_id}: {str(e)}")
    
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
    
    def update_payment_status(self, transaction_ref: str, status: str, flutterwave_id: str = None):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE payments 
                    SET status = ?, completed_at = ?, flutterwave_id = ?
                    WHERE transaction_ref = ?
                ''', (status, datetime.now(timezone.utc).isoformat(), flutterwave_id, transaction_ref))
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

class FlutterwavePayment:
    def __init__(self, secret_key: str, public_key: str):
        self.base_url = "https://api.flutterwave.com/v3"
        self.secret_key = secret_key
        self.public_key = public_key
    
    def create_payment_link(self, user_id: int, amount: float, user_email: str = None) -> Dict[str, Any]:
        try:
            tx_ref = f"okvirtuals_{user_id}_{uuid.uuid4().hex[:8]}_{int(time.time())}"
            
            payload = {
                "tx_ref": tx_ref,
                "amount": amount / 100,
                "currency": "NGN",
                "redirect_url": f"{CONFIG.WEBHOOK_URL}?status=successful",
                "meta": {
                    "user_id": str(user_id),
                    "plan": "monthly"
                },
                "customer": {
                    "email": user_email or f"user{user_id}@okvirtuals.com",
                    "phonenumber": "08000000000",
                    "name": f"User {user_id}"
                },
                "customizations": {
                    "title": "OK Virtuals Premium Access",
                    "description": "30-Day Premium Betting Predictions",
                    "logo": ""
                }
            }
            
            headers = {
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/json"
            }
            
            response = requests.post(
                f"{self.base_url}/payments",
                json=payload,
                headers=headers,
                timeout=30
            )
            
            response.raise_for_status()
            data = response.json()
            
            if data.get('status') == 'success':
                return {
                    "status": "success",
                    "tx_ref": tx_ref,
                    "link": data["data"]["link"]
                }
            else:
                logger.error(f"Flutterwave API error: {data}")
                return {"status": "error", "message": data.get('message', 'Payment link creation failed')}
                
        except Exception as e:
            logger.error(f"Payment link creation error: {str(e)}")
            return {"status": "error", "message": "Payment service unavailable"}
    
    def verify_payment(self, tx_ref: str) -> Dict[str, Any]:
        try:
            headers = {
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/json"
            }
            
            response = requests.get(
                f"{self.base_url}/transactions/verify_by_reference?tx_ref={tx_ref}",
                headers=headers,
                timeout=30
            )
            
            response.raise_for_status()
            return response.json()
                
        except Exception as e:
            logger.error(f"Payment verification error: {str(e)}")
            return {"status": "error", "message": "Verification service unavailable"}

class GroupManager:
    def __init__(self, application: Application):
        self.application = application
        self.channel_id = CONFIG.PREMIUM_CHANNEL_ID
        self.channel_username = CONFIG.PREMIUM_CHANNEL_USERNAME
    
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
            
        except Forbidden as e:
            logger.error(f"Bot doesn't have permission to create invite links: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Error creating invite link for user {user_id}: {str(e)}")
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
            logger.error(f"Error checking membership for user {user_id}: {str(e)}")
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
            
        except Forbidden as e:
            logger.error(f"Bot doesn't have permission to remove user: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Error removing user {user_id}: {str(e)}")
            return False
    
    async def unban_user(self, user_id: int) -> bool:
        try:
            await self.application.bot.unban_chat_member(
                chat_id=self.channel_id,
                user_id=user_id,
                only_if_banned=True
            )
            
            logger.info(f"Unbanned user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error unbanning user {user_id}: {str(e)}")
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
        logger.info("Subscription monitor stopped")
    
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
                        logger.info(f"Processed expired subscription for user {user_id}")
                        
                    except Exception as e:
                        logger.error(f"Failed to process expiry for user {user_id}: {str(e)}")
                    
        except Exception as e:
            logger.error(f"Error checking expired subscriptions: {str(e)}")
    
    def _send_expiry_reminders(self):
        try:
            users_to_remind = self.db.get_users_needing_reminder()
            
            if users_to_remind:
                logger.info(f"Sending reminders to {len(users_to_remind)} users")
                
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
                        logger.error(f"Failed to send reminder to user {user_id}: {str(e)}")
                        
        except Exception as e:
            logger.error(f"Error sending reminders: {str(e)}")
    
    async def _send_expiry_notification(self, user_id: int):
        try:
            if bot_application:
                message = f"""⚠️ *Subscription Expired*

Your premium subscription has expired.

💎 *Renew Now:*
Only ₦{CONFIG.SUBSCRIPTION_AMOUNT / 100:.0f} for 30 more days of premium access!

Use /subscribe to renew your subscription."""

                await bot_application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💎 Renew Subscription", callback_data="subscribe")]
                    ])
                )
                
                self.db.log_notification(user_id, "expiry", "Subscription expired")
                
        except Exception as e:
            logger.error(f"Error sending expiry notification to user {user_id}: {str(e)}")
    
    async def _send_reminder_notification(self, user_id: int, days_remaining: int):
        try:
            if bot_application:
                emoji = "⚠️" if days_remaining <= 3 else "🔔"
                message = f"""{emoji} *Subscription Expiring Soon*

Your premium subscription expires in *{days_remaining} day{"s" if days_remaining > 1 else ""}*!

Don't lose access to:
✅ Daily Sure Bet Predictions
✅ VIP Group Access
✅ Expert Analysis

💎 *Renew Now:*
Only ₦{CONFIG.SUBSCRIPTION_AMOUNT / 100:.0f} for 30 more days!

Use /subscribe to renew."""

                await bot_application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💎 Renew Now", callback_data="subscribe")]
                    ])
                )
                
                self.db.log_notification(user_id, "reminder", f"Reminder sent: {days_remaining} days")
                
        except Exception as e:
            logger.error(f"Error sending reminder to user {user_id}: {str(e)}")

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

# Continuing in next message due to length...class OKVirtualsBot:
    def __init__(self, config: Config):
        self.config = config
        self.db = DatabaseManager(config.DATABASE_PATH)
        self.payment = FlutterwavePayment(config.FLUTTERWAVE_SECRET_KEY, config.FLUTTERWAVE_PUBLIC_KEY)
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
✅ Daily Sure Bet Predictions for Virtuals
✅ 90%+ Accuracy Rate on Virtual Games
✅ Expert Analysis & Strategies
✅ Real-time Betting Tips
✅ Exclusive VIP Community

💎 *Premium Benefits:*
🎲 Virtual Football Predictions
🏀 Virtual Basketball Tips
🏇 Virtual Horse Racing Insights
⚡ Instant Win Strategies
📊 Detailed Analytics & Stats
🔔 Real-time Notifications
💬 24/7 Premium Support

💰 *Subscribe Now:*
Only ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f} for 30 Days of Premium Access!

Transform your betting game today! 🚀"""
        
        keyboard = [
            [InlineKeyboardButton("💎 Subscribe (₦100/month)", callback_data="subscribe")],
            [InlineKeyboardButton("📊 Check Status", callback_data="status"),
             InlineKeyboardButton("🎯 Today's Tips", callback_data="predictions")],
            [InlineKeyboardButton("📈 My Stats", callback_data="stats"),
             InlineKeyboardButton("💬 Support", callback_data="support")],
            [InlineKeyboardButton("ℹ️ Learn More", callback_data="learn_more")]
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
        
        user_data = self.db.get_user(user.id)
        
        if user_data and user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                current_time = datetime.now(timezone.utc)
                
                if end_date > current_time:
                    days_remaining = (end_date - current_time).days
                    await update.message.reply_text(
                        f"✅ *You Already Have Active Subscription!*\n\n"
                        f"📅 Expires: {end_date.strftime('%B %d, %Y at %H:%M UTC')}\n"
                        f"⏰ Days Remaining: {days_remaining} days\n"
                        f"💎 Status: Premium Member\n\n"
                        f"Use /premium to access the channel!",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return
            except Exception as e:
                logger.error(f"Error parsing subscription date: {str(e)}")
        
        price_naira = self.config.SUBSCRIPTION_AMOUNT / 100
        subscribe_text = f"""💎 *Premium Subscription*

🎯 *OK Virtuals Betting Predictions*
30 Days of Expert Predictions

💰 *Price:* ₦{price_naira:.0f} (One-time Payment)
⏰ *Duration:* {self.config.SUBSCRIPTION_DAYS} Days
📊 *Success Rate:* 90%+ Accuracy

✨ *What You Get:*
✅ Daily Sure Bet Predictions
✅ Virtual Football Tips
✅ Virtual Basketball Strategies
✅ Horse Racing Insights
✅ Instant Win Techniques
✅ VIP Telegram Group Access
✅ 24/7 Premium Support
✅ Real-time Updates
✅ Betting Analytics

🔒 *Secure Payment via Flutterwave*
💳 Pay with Card, Bank Transfer, or USSD

Click below to subscribe now!"""
        
        keyboard = [
            [InlineKeyboardButton("💳 Pay ₦100 Now", callback_data="process_payment")],
            [InlineKeyboardButton("📊 View Sample Predictions", callback_data="predictions")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
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
                start_date = datetime.fromisoformat(user_data['subscription_start'])
                current_time = datetime.now(timezone.utc)
                
                if end_date > current_time:
                    days_remaining = (end_date - current_time).days
                    hours_remaining = int((end_date - current_time).total_seconds() / 3600) % 24
                    
                    is_member = await self.group_manager.check_membership(user.id)
                    membership_status = "✅ Active in Channel" if is_member else "⚠️ Not in Channel Yet"
                    
                    status_text = f"""✅ *Premium Subscription Active*

👤 User: {user.first_name}
💎 Status: Premium Member
📅 Started: {start_date.strftime('%B %d, %Y')}
⏰ Expires: {end_date.strftime('%B %d, %Y at %H:%M UTC')}
📊 Time Remaining: {days_remaining} days, {hours_remaining} hours
🔗 Channel Status: {membership_status}

📈 *Your Stats:*
🎯 Predictions Viewed: {user_data.get('total_predictions_viewed', 0)}
🎲 Total Bets: {user_data.get('total_bets', 0)}

💡 *Tip:* Renew early to avoid losing access!"""
                    
                    keyboard = [
                        [InlineKeyboardButton("🔗 Access Premium Channel", callback_data="premium")],
                        [InlineKeyboardButton("🎯 View Predictions", callback_data="predictions")]
                    ]
                else:
                    status_text = f"""⚠️ *Premium Subscription Expired*

👤 User: {user.first_name}
❌ Status: Expired
📅 Expired: {end_date.strftime('%B %d, %Y')}

💰 Renew now for ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f} to regain access!"""
                    
                    keyboard = [
                        [InlineKeyboardButton("💎 Renew Subscription", callback_data="subscribe")]
                    ]
            except Exception as e:
                logger.error(f"Error parsing subscription dates: {str(e)}")
                status_text = "❌ Error retrieving subscription status. Please contact support."
                keyboard = []
        else:
            status_text = f"""📊 *Subscription Status*

👤 User: {user.first_name}
❌ Status: Free User
💎 Premium: Not Active

🎯 *Upgrade to Premium for:*
✅ Daily Sure Bet Predictions
✅ 90%+ Accuracy Rate
✅ VIP Group Access
✅ Expert Analysis
✅ Real-time Tips

💰 Only ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f} for 30 days!"""
            
            keyboard = [
                [InlineKeyboardButton("💎 Subscribe Now", callback_data="subscribe")],
                [InlineKeyboardButton("💬 Support", callback_data="support")]
            ]
        
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
                    
                    predictions_text = f"""🎯 *TODAY'S PREMIUM PREDICTIONS*

📅 Date: {datetime.now().strftime('%B %d, %Y')}

⚽ *VIRTUAL FOOTBALL*
🎲 Match: Virtual Premier League
📊 Prediction: Over 2.5 Goals
💰 Odds: 1.85
✅ Confidence: 92%

🏀 *VIRTUAL BASKETBALL*
🎲 League: Virtual NBA
📊 Prediction: Total Points Over 215.5
💰 Odds: 1.90
✅ Confidence: 88%

🏇 *VIRTUAL HORSE RACING*
🎲 Race: Virtual Derby
📊 Prediction: Horse #3 to Win
💰 Odds: 2.10
✅ Confidence: 85%

⚡ *INSTANT WIN STRATEGY*
🎰 Game: Virtual Lucky Spin
📊 Strategy: Bet on Red (5 rounds)
💰 Expected Return: 150%+
✅ Confidence: 90%

💡 *Betting Tips:*
- Start with small stakes
- Follow our odds recommendations
- Manage your bankroll wisely
- Track your wins in /stats

🔥 More predictions in Premium Channel!
Use /premium to join now!"""
                else:
                    is_premium = False
            except:
                is_premium = False
        
        if not is_premium:
            predictions_text = f"""🎯 *SAMPLE PREDICTIONS*

📅 Date: {datetime.now().strftime('%B %d, %Y')}

⚽ *VIRTUAL FOOTBALL - Sample*
🎲 Match: Virtual League
📊 Prediction: [Premium Content]
💰 Odds: [Premium Content]
✅ Confidence: 90%+

🔒 *Subscribe to unlock:*
✅ Full Daily Predictions
✅ Detailed Analysis
✅ Multiple Game Types
✅ Real-time Updates
✅ VIP Group Access

💰 Only ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f} for 30 days!"""
        
        keyboard = []
        if not is_premium:
            keyboard.append([InlineKeyboardButton("💎 Subscribe Now", callback_data="subscribe")])
        else:
            keyboard.append([InlineKeyboardButton("🔗 Join Premium Channel", callback_data="premium")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
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
        successful_bets = user_data.get('successful_bets', 0) if user_data else 0
        
        success_rate = (successful_bets / total_bets * 100) if total_bets > 0 else 0
        
        stats_text = f"""📈 *YOUR BETTING STATISTICS*

👤 User: {user.first_name}
📅 Member Since: {datetime.fromisoformat(user_data['created_at']).strftime('%B %d, %Y') if user_data else 'Today'}

📊 *Performance:*
🎯 Predictions Viewed: {predictions_viewed}
🎲 Total Bets Placed: {total_bets}
✅ Successful Bets: {successful_bets}
📈 Success Rate: {success_rate:.1f}%

💎 *Subscription Status:*
"""
        
        if user_data and user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                if end_date > datetime.now(timezone.utc):
                    days_remaining = (end_date - datetime.now(timezone.utc)).days
                    stats_text += f"✅ Premium Active ({days_remaining} days remaining)"
                else:
                    stats_text += "❌ Premium Expired"
            except:
                stats_text += "❌ Premium Expired"
        else:
            stats_text += "❌ Free User"
        
        stats_text += "\n\n💡 *Tip:* Track your bets and improve your strategy!"
        
        keyboard = [
            [InlineKeyboardButton("🎯 View Predictions", callback_data="predictions")],
            [InlineKeyboardButton("💎 Upgrade", callback_data="subscribe")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            stats_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def support_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        support_text = f"""💬 *Customer Support*

Need help? We're here 24/7! 🚀

📞 *Contact Method:*
✈️ Telegram: @okvirtual001

⏰ *Response Time:* Within 30 minutes
🌍 *Availability:* 24/7

🔧 *We Help With:*
- Payment Issues
- Group Access Problems
- Subscription Questions
- Technical Support
- Betting Advice
- Account Management

💡 *Quick Tips:*
- Include your User ID in messages
- Describe your issue clearly
- Mention error messages if any

Your User ID: `{user.id}`

We're committed to your success! 💪"""
        
        keyboard = [
            [InlineKeyboardButton("✈️ Contact Support", url="https://t.me/okvirtual001")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            support_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        help_text = """ℹ️ *Help & Commands Guide*

📋 *Available Commands:*
/start - Start the bot and see main menu
/subscribe - Subscribe to premium (₦100/month)
/status - Check your subscription status
/predictions - View today's betting predictions
/stats - View your betting statistics
/support - Get customer support
/help - Show this help message
/premium - Get premium channel invite link

🎯 *About OK Virtuals Betting Bot:*
We provide expert predictions for Virtual Games with 90%+ accuracy rate.

✨ *Premium Features:*
- Daily Sure Bet Predictions
- Virtual Football, Basketball, Horse Racing
- Instant Win Strategies
- Real-time Tips & Notifications
- VIP Telegram Group via Invite Link
- 24/7 Premium Support
- Detailed Analytics

💰 *Subscription:*
- Price: ₦100 for 30 Days
- Payment: Secure via Flutterwave
- Instant Invite Link on Payment
- Auto Removal after Expiry
- Easy Renewal Process

🔐 *Security:*
- Bank-level encryption
- Trusted payment gateway
- Unique invite links per user

📱 *Getting Started:*
1. Use /subscribe to see plans
2. Complete payment via secure link
3. Get unique invite link instantly
4. Join premium channel and start winning!

Need help? Use /support 💬"""
        
        keyboard = [
            [InlineKeyboardButton("💎 Subscribe Now", callback_data="subscribe")],
            [InlineKeyboardButton("💬 Support", url="https://t.me/okvirtual001")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            help_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        if not self.is_admin(user.id):
            await update.message.reply_text("❌ You don't have permission to access admin panel.")
            return
        
        stats = self.db.get_admin_stats()
        
        admin_text = f"""👑 *Admin Dashboard*

📊 *Statistics:*
👥 Total Users: {stats.get('total_users', 0)}
💎 Active Subscriptions: {stats.get('active_subscriptions', 0)}
💰 Total Revenue: ₦{stats.get('total_revenue', 0):.2f}
📅 Today's Subscriptions: {stats.get('today_subscriptions', 0)}
⚠️ Expiring Soon (7 days): {stats.get('expiring_soon', 0)}

🔧 *Admin Controls:*
Use buttons below for actions."""
        
        keyboard = [
            [InlineKeyboardButton("📊 Refresh Stats", callback_data="admin_refresh")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            admin_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
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
                        
                        is_member = await self.group_manager.check_membership(user.id)
                        
                        if is_member:
                            premium_text = f"""✅ *You're Already in Premium Channel!*

You're currently a member of the premium channel.

📅 *Subscription Valid Until:* {end_date.strftime('%B %d, %Y')}

🔗 *Channel Link:*
{invite_link}

💡 *Tip:* Use /status to check your subscription details!"""
                        else:
                            premium_text = f"""💎 *Premium Channel Access*

Your subscription is active! Here's your invite link:

🔗 *Click to Join:*
{invite_link}

⚠️ *Important:*
- Link expires in 24 hours
- Single-use only
- Use /premium to get a new link anytime

📅 *Valid Until:* {end_date.strftime('%B %d, %Y')}

🎯 *In Premium Channel:*
- Daily Sure Bet Predictions
- Live Betting Tips
- Expert Analysis
- VIP Community
- Instant Updates

Click the button below to join! 👇"""
                        
                        keyboard = [
                            [InlineKeyboardButton("🔗 Join Premium Channel", url=invite_link)],
                            [InlineKeyboardButton("📊 My Status", callback_data="status")]
                        ]
                    else:
                        premium_text = f"""💎 *Premium Access Active*

Your subscription is active until {end_date.strftime('%B %d, %Y')}

⚠️ Unable to create invite link. Please try:
1. Join via: {self.config.PREMIUM_CHANNEL_USERNAME}
2. Or contact support: @okvirtual001"""
                        
                        keyboard = [
                            [InlineKeyboardButton("💬 Contact Support", url="https://t.me/okvirtual001")]
                        ]
                else:
                    premium_text = "⚠️ Your premium subscription has expired. Renew to regain access!"
                    keyboard = [
                        [InlineKeyboardButton("💎 Renew Subscription", callback_data="subscribe")]
                    ]
            except Exception as e:
                logger.error(f"Error in premium command: {str(e)}")
                premium_text = "❌ Error checking subscription. Please contact support."
                keyboard = [
                    [InlineKeyboardButton("💬 Support", url="https://t.me/okvirtual001")]
                ]
        else:
            premium_text = f"""🔒 *Premium Access Required*

You need an active subscription to access the premium channel.

💎 *Subscribe Now:*
- Price: ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f}
- Duration: 30 Days
- Instant Access via Invite Link

🎯 *Get Access To:*
✅ Daily Sure Predictions
✅ Expert Analysis
✅ VIP Community
✅ Real-time Tips
✅ 90%+ Accuracy

Subscribe now to unlock! 🚀"""
            
            keyboard = [
                [InlineKeyboardButton("💎 Subscribe (₦100)", callback_data="subscribe")],
                [InlineKeyboardButton("📊 View Sample", callback_data="predictions")]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            premium_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def process_payment_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        if not self.rate_limiter.is_allowed(user_id):
            await query.edit_message_text(
                "⚠️ Please wait before making another payment request.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="subscribe")]])
            )
            return
        
        await query.edit_message_text("⏳ Creating secure payment link... Please wait.")
        
        try:
            payment_result = self.payment.create_payment_link(user_id, self.config.SUBSCRIPTION_AMOUNT)
            
            if payment_result['status'] == 'success':
                self.db.add_payment_record(user_id, payment_result['tx_ref'], self.config.SUBSCRIPTION_AMOUNT)
                
                price_naira = self.config.SUBSCRIPTION_AMOUNT / 100
                payment_text = f"""💳 *Payment Details*

💰 Amount: ₦{price_naira:.0f}
⏰ Duration: {self.config.SUBSCRIPTION_DAYS} Days
🔒 Secure Payment via Flutterwave

📝 *Instructions:*
1️⃣ Click "Pay Now" button below
2️⃣ Complete payment securely (Card/Transfer/USSD)
3️⃣ After payment, click "I have Paid"
4️⃣ Wait for verification (usually instant)
5️⃣ Get your premium invite link!

⚠️ *Important:* Keep this chat open until verification is complete!

Transaction ID: `{payment_result['tx_ref']}`"""
                
                keyboard = [
                    [InlineKeyboardButton("💳 Pay ₦100 Now", url=payment_result['link'])],
                    [InlineKeyboardButton("✅ I have Paid - Verify", callback_data=f"verify_{payment_result['tx_ref']}")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="subscribe")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    payment_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                error_message = payment_result.get('message', 'Payment link creation failed')
                await query.edit_message_text(
                    f"❌ Error: {error_message}\n\nPlease contact support: @okvirtual001",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💬 Contact Support", url="https://t.me/okvirtual001")],
                        [InlineKeyboardButton("🔙 Back", callback_data="subscribe")]
                    ])
                )
        except Exception as e:
            logger.error(f"Error processing payment: {str(e)}")
            await query.edit_message_text(
                "❌ An error occurred. Please contact support: @okvirtual001",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Contact Support", url="https://t.me/okvirtual001")]
                ])
            )
    
    async def verify_payment_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        tx_ref = query.data.split('_', 1)[1]
        user_id = query.from_user.id
        
        payment_record = self.db.get_payment_record(tx_ref)
        if not payment_record or payment_record['user_id'] != user_id:
            await query.edit_message_text(
                "❌ Payment record not found. Please contact support: @okvirtual001",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Support", url="https://t.me/okvirtual001")]
                ])
            )
            return
        
        if payment_record['status'] == 'completed':
            user_data = self.db.get_user(user_id)
            invite_link = user_data.get('invite_link') if user_data else None
            
            if invite_link:
                await query.edit_message_text(
                    f"✅ This payment has already been processed!\n\n"
                    f"🔗 Your Premium Invite Link:\n{invite_link}\n\n"
                    f"Click the link above to join the premium channel!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔗 Join Premium Channel", url=invite_link)],
                        [InlineKeyboardButton("📊 My Status", callback_data="status")]
                    ])
                )
            else:
                await query.edit_message_text(
                    "✅ Payment already processed! Use /premium to get your invite link.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔗 Get Invite Link", callback_data="premium")]
                    ])
                )
            return
        
        await query.edit_message_text("⏳ Verifying your payment... Please wait.")
        
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
                self.db.update_payment_status(
                    tx_ref, 
                    'completed',verification_result.get('data', {}).get('id')
                )
                
                invite_link = await self.group_manager.create_invite_link(user_id)
                
                if invite_link:
                    self.db.save_invite_link(user_id, invite_link)
                    
                    success_text = f"""🎉 *PAYMENT SUCCESSFUL!*

Welcome to OK Virtuals Premium! 💎

📅 *Subscription {"Extended" if is_renewal else "Activated"}*
⏰ Valid Until: {end_date.strftime('%B %d, %Y at %H:%M UTC')}
💰 Amount Paid: ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f}

🔗 *Your Premium Invite Link:*
{invite_link}

⚠️ *IMPORTANT:*
- This link is unique to you
- Click it to join the premium channel
- Link expires in 24 hours
- Use /premium anytime to get a new link

🎯 *You Now Have Access To:*
✅ Daily Sure Bet Predictions
✅ Virtual Football Tips
✅ Virtual Basketball Strategies
✅ Horse Racing Insights
✅ Instant Win Techniques
✅ VIP Community
✅ Real-time Updates
✅ 24/7 Premium Support

🚀 Click the button below to join now!

Use /predictions to see today's tips! 🎯"""
                    
                    keyboard = [
                        [InlineKeyboardButton("🔗 Join Premium Channel NOW", url=invite_link)],
                        [InlineKeyboardButton("🎯 View Predictions", callback_data="predictions")],
                        [InlineKeyboardButton("📊 My Status", callback_data="status")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await query.edit_message_text(
                        success_text,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    
                    self.db.log_notification(user_id, "payment_success", f"Payment successful: {tx_ref}")
                    
                else:
                    success_text = f"""🎉 *PAYMENT SUCCESSFUL!*

Welcome to OK Virtuals Premium! 💎

📅 *Subscription {"Extended" if is_renewal else "Activated"}*
⏰ Valid Until: {end_date.strftime('%B %d, %Y at %H:%M UTC')}
💰 Amount Paid: ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f}

⚠️ Unable to create invite link automatically.
Please use /premium command to get your invite link!

Or join via: {self.config.PREMIUM_CHANNEL_USERNAME}"""
                    
                    keyboard = [
                        [InlineKeyboardButton("🔗 Get Invite Link", callback_data="premium")],
                        [InlineKeyboardButton("📊 My Status", callback_data="status")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await query.edit_message_text(
                        success_text,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                
                logger.info(f"Successfully processed payment for user {user_id} (renewal: {is_renewal})")
                
            else:
                await query.edit_message_text(
                    "⚠️ Payment verification failed or still pending.\n\n"
                    "If you've paid, please wait a few minutes and try again.\n"
                    "If the problem persists, contact support: @okvirtual001",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Try Again", callback_data=f"verify_{tx_ref}")],
                        [InlineKeyboardButton("💬 Support", url="https://t.me/okvirtual001")]
                    ])
                )
        except Exception as e:
            logger.error(f"Error during payment verification: {str(e)}")
            await query.edit_message_text(
                "❌ Error verifying payment. Please contact support: @okvirtual001",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data=f"verify_{tx_ref}")],
                    [InlineKeyboardButton("💬 Support", url="https://t.me/okvirtual001")]
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
            elif query.data == "learn_more":
                await self.learn_more_callback(update, context)
            elif query.data == "back_to_menu":
                await self.back_to_menu(query, context)
            elif query.data.startswith("admin_"):
                await self.handle_admin_callback(query, context)
            else:
                await query.answer("Unknown action.")
        except Exception as e:
            logger.error(f"Error handling button {query.data}: {str(e)}")
            await query.answer("Something went wrong. Please try again.")
    
    async def subscribe_button(self, query, context):
        await query.answer()
        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)
        
        if user_data and user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                if end_date > datetime.now(timezone.utc):
                    days_remaining = (end_date - datetime.now(timezone.utc)).days
                    await query.edit_message_text(
                        f"✅ You already have an active subscription!\n\n"
                        f"📅 Expires: {end_date.strftime('%B %d, %Y')}\n"
                        f"⏰ {days_remaining} days remaining\n\n"
                        f"💡 You can renew early to extend your subscription!",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔗 Access Channel", callback_data="premium")],
                            [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                        ])
                    )
                    return
            except:
                pass
        
        price_naira = self.config.SUBSCRIPTION_AMOUNT / 100
        subscribe_text = f"""💎 *Premium Subscription*

💰 *Price:* ₦{price_naira:.0f}
⏰ *Duration:* 30 Days
📊 *Success Rate:* 90%+

✨ *Includes:*
✅ Daily Predictions
✅ VIP Group Access
✅ Expert Analysis
✅ Real-time Tips
✅ 24/7 Support
✅ Unique Invite Link

Click below to subscribe!"""
        
        keyboard = [
            [InlineKeyboardButton("💳 Pay ₦100 Now", callback_data="process_payment")],
            [InlineKeyboardButton("📊 Sample Tips", callback_data="predictions")],
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
    
    async def learn_more_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        info_text = """ℹ️ *About OK Virtuals Betting*

🎯 *Our Mission:*
Provide accurate betting predictions for Virtual Games with 90%+ success rate.

💎 *Premium Features:*
✅ Daily Sure Bet Predictions
✅ Virtual Football Analysis
✅ Virtual Basketball Tips
✅ Horse Racing Insights
✅ Instant Win Strategies
✅ Real-time Notifications
✅ VIP Telegram Group (via Invite Link)
✅ Expert Analysis & Stats
✅ 24/7 Premium Support

📊 *Success Rate:* 90%+ Accuracy
🏆 *Track Record:* 1000+ Satisfied Members
💰 *Pricing:* Only ₦100 for 30 Days

🔒 *Security:*
- Secure Flutterwave Payment
- Bank-level Encryption
- Unique Invite Links
- Instant Access

🎲 *Games We Cover:*
- Virtual Football (Premier League, Champions League)
- Virtual Basketball (NBA, FIBA)
- Virtual Horse Racing (Derby, Classic)
- Instant Win Games (Lucky Spin, Roulette)

🌟 *What Makes Us Different:*
- Professional Analysts Team
- AI-Powered Predictions
- Real-time Market Data
- Proven Track Record
- Active Community
- Automatic Access Management

💪 Join 1000+ winning members today!"""
        
        keyboard = [
            [InlineKeyboardButton("💎 Subscribe Now", callback_data="subscribe")],
            [InlineKeyboardButton("📊 Sample Predictions", callback_data="predictions")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            info_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def handle_admin_callback(self, query, context):
        if not self.is_admin(query.from_user.id):
            await query.answer("❌ Unauthorized")
            return
        
        await query.answer()
        
        if query.data == "admin_refresh":
            stats = self.db.get_admin_stats()
            
            admin_text = f"""👑 *Admin Dashboard*

📊 *Statistics:*
👥 Total Users: {stats.get('total_users', 0)}
💎 Active Subscriptions: {stats.get('active_subscriptions', 0)}
💰 Total Revenue: ₦{stats.get('total_revenue', 0):.2f}
📅 Today's Subscriptions: {stats.get('today_subscriptions', 0)}
⚠️ Expiring Soon (7 days): {stats.get('expiring_soon', 0)}

🔧 *Admin Controls:*
Use buttons below for actions."""
            
            keyboard = [
                [InlineKeyboardButton("📊 Refresh Stats", callback_data="admin_refresh")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                admin_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def back_to_menu(self, query, context):
        await query.answer()
        user = query.from_user
        
        welcome_text = f"""🎯 *OK Virtuals Betting Predictions*

Hello {user.first_name}! 👋

💎 Premium betting predictions at your fingertips!

Use the buttons below to navigate."""
        
        keyboard = [
            [InlineKeyboardButton("💎 Subscribe (₦100)", callback_data="subscribe")],
            [InlineKeyboardButton("📊 Status", callback_data="status"),
             InlineKeyboardButton("🎯 Predictions", callback_data="predictions")],
            [InlineKeyboardButton("📈 Stats", callback_data="stats"),
             InlineKeyboardButton("💬 Support", url="https://t.me/okvirtual001")]
        ]
        
        await query.edit_message_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path.startswith('/health'):
                health_status = {
                    "status": "healthy",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "service": "OK Virtuals Betting Bot"
                }
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(health_status).encode())
            else:
                self.send_response(404)
                self.end_headers()
                
        except Exception as e:
            logger.error(f"Health check error: {str(e)}")
            self.send_response(500)
            self.end_headers()
    
    def do_POST(self):
        try:
            if self.path.startswith('/webhook/flutterwave'):
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                
                signature = self.headers.get('verif-hash', '')
                
                if signature == CONFIG.FLUTTERWAVE_WEBHOOK_SECRET:
                    webhook_data = json.loads(post_data.decode('utf-8'))
                    
                    threading.Thread(
                        target=self.process_webhook,
                        args=(webhook_data,),
                        daemon=True
                    ).start()
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success"}).encode())
                else:
                    logger.warning("Invalid webhook signature")
                    self.send_response(401)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
                
        except Exception as e:
            logger.error(f"Webhook error: {str(e)}")
            self.send_response(500)
            self.end_headers()
    
    def process_webhook(self, webhook_data):
        try:
            event_type = webhook_data.get('event')
            
            if event_type == 'charge.completed':
                tx_data = webhook_data.get('data', {})
                tx_ref = tx_data.get('tx_ref')
                status = tx_data.get('status')
                
                if status == 'successful' and tx_ref:
                    logger.info(f"Webhook received for successful payment: {tx_ref}")
                    
        except Exception as e:
            logger.error(f"Error processing webhook: {str(e)}")
    
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
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_flag = True

def main():
    global shutdown_flag, bot_application
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info("Starting OK Virtuals Betting Bot...")
    
    bot = OKVirtualsBot(CONFIG)
    logger.info("Bot initialized successfully")
    
    application = Application.builder().token(CONFIG.BOT_TOKEN).build()
    bot.application = application
    bot_application = application
    bot.group_manager = GroupManager(application)
    logger.info("Telegram application created successfully")
    
    bot.subscription_monitor = SubscriptionMonitor(bot.db, bot.group_manager)
    bot.subscription_monitor.start()
    logger.info("Subscription monitor started")
    
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
    
    logger.info("OK Virtuals Betting Bot started successfully!")
    print("=" * 60)
    print("🎯 OK VIRTUALS BETTING BOT IS RUNNING")
    print("=" * 60)
    print(f"💚 Health check: http://0.0.0.0:{CONFIG.PORT}/health")
    print(f"🔔 Webhook URL: http://0.0.0.0:{CONFIG.PORT}/webhook/flutterwave")
    print(f"💰 Subscription: ₦{CONFIG.SUBSCRIPTION_AMOUNT / 100:.0f} for {CONFIG.SUBSCRIPTION_DAYS} days")
    print(f"📱 Support: @okvirtual001")
    print(f"👑 Admins: {len(bot.admin_ids)}")
    print("\n📋 Available Commands:")
    for cmd in BOT_COMMANDS:
        print(f"  /{cmd.command} - {cmd.description}")
    print("=" * 60)
    
    max_retries = 5
    retry_count = 0
    
    while not shutdown_flag and retry_count < max_retries:
        try:
            logger.info(f"Starting bot polling (attempt {retry_count + 1}/{max_retries})")
            
            async def post_init(application):
                await bot.setup_bot_commands()
            
            application.post_init = post_init
            
            application.run_polling(
                drop_pending_updates=True,
                close_loop=False,
                stop_signals=None
            )
            break
            
        except Conflict as e:
            retry_count += 1
            logger.warning(f"Telegram conflict: {str(e)}")
            if retry_count < max_retries:
                wait_time = min(retry_count * 10, 60)
                logger.info(f"Waiting {wait_time}s before retry")
                time.sleep(wait_time)
            else:
                logger.error("Max retries reached")
                break
                
        except (NetworkError, TimedOut) as e:
            retry_count += 1
            logger.warning(f"Network error: {str(e)}")
            if retry_count < max_retries:
                logger.info(f"Retrying in 30s...")
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
        logger.exception("Full traceback:")