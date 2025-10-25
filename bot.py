#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enhanced Virtuals Betting Prediction Bot
Automated group management, payment confirmation, and betting analytics
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
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, ChatMember
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.error import Conflict, NetworkError, TimedOut, TelegramError, Forbidden, BadRequest
from telegram.constants import ParseMode
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
shutdown_flag = False

@dataclass
class Config:
    BOT_TOKEN: str
    FLUTTERWAVE_SECRET_KEY: str
    FLUTTERWAVE_PUBLIC_KEY: str
    PREMIUM_CHANNEL_ID: str
    PREMIUM_CHANNEL_LINK: str
    DATABASE_PATH: str = "./virtuals_betting_bot.db"
    PORT: int = 10000
    WEBHOOK_URL: str = ""
    ADMIN_USER_ID: str = ""
    SUBSCRIPTION_AMOUNT: int = 10000  # 100 NGN in kobo
    SUBSCRIPTION_DAYS: int = 30

def load_config() -> Config:
    config = Config(
        BOT_TOKEN=os.getenv("BOT_TOKEN", ""),
        FLUTTERWAVE_SECRET_KEY=os.getenv("FLUTTERWAVE_SECRET_KEY", ""),
        FLUTTERWAVE_PUBLIC_KEY=os.getenv("FLUTTERWAVE_PUBLIC_KEY", ""),
        PREMIUM_CHANNEL_ID=os.getenv("PREMIUM_CHANNEL_ID", ""),  # Must be numeric ID like -1001234567890
        PREMIUM_CHANNEL_LINK=os.getenv("PREMIUM_CHANNEL_LINK", ""),
        DATABASE_PATH=os.getenv("DATABASE_PATH", "./virtuals_betting_bot.db"),
        PORT=int(os.getenv("PORT", 10000)),
        WEBHOOK_URL=os.getenv("WEBHOOK_URL", ""),
        ADMIN_USER_ID=os.getenv("ADMIN_USER_ID", ""),
        SUBSCRIPTION_AMOUNT=int(os.getenv("SUBSCRIPTION_AMOUNT", 10000)),  # 100 NGN default
        SUBSCRIPTION_DAYS=int(os.getenv("SUBSCRIPTION_DAYS", 30))
    )
    
    if not config.BOT_TOKEN:
        raise ValueError("BOT_TOKEN is required")
    if not config.PREMIUM_CHANNEL_ID:
        raise ValueError("PREMIUM_CHANNEL_ID is required for auto group management")
    
    return config

# Load config
try:
    CONFIG = load_config()
    logger.info("Configuration loaded successfully")
except ValueError as e:
    logger.error(f"Configuration error: {e}")
    sys.exit(1)

# Bot commands
BOT_COMMANDS = [
    BotCommand("start", "Start the bot and see welcome menu"),
    BotCommand("subscribe", "Subscribe to premium predictions (₦100/month)"),
    BotCommand("status", "Check your subscription status"),
    BotCommand("predictions", "View today's betting predictions"),
    BotCommand("stats", "View your betting statistics"),
    BotCommand("support", "Get customer support"),
    BotCommand("help", "Get help and bot information"),
    BotCommand("premium", "Access premium channel"),
]

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    @contextmanager
    def get_connection(self):
        conn = None
        try:
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
                
                # Users table with enhanced tracking
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
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Payments table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        transaction_ref TEXT UNIQUE,
                        amount REAL,
                        status TEXT DEFAULT 'pending',
                        payment_method TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        completed_at TEXT,
                        FOREIGN KEY (user_id) REFERENCES users (user_id)
                    )
                ''')
                
                # Predictions table for analytics
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT,
                        game_type TEXT,
                        prediction TEXT,
                        odds TEXT,
                        result TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # User predictions tracking
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        prediction_id INTEGER,
                        viewed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        bet_placed INTEGER DEFAULT 0,
                        result TEXT,
                        FOREIGN KEY (user_id) REFERENCES users (user_id),
                        FOREIGN KEY (prediction_id) REFERENCES predictions (id)
                    )
                ''')
                
                # Subscription history
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS subscription_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        start_date TEXT,
                        end_date TEXT,
                        amount REAL,
                        status TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
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
                    INSERT OR REPLACE INTO users (user_id, username, first_name, last_active, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, username or "", first_name or "", 
                     datetime.now(timezone.utc).isoformat(),
                     datetime.now(timezone.utc).isoformat()))
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
    
    def update_subscription(self, user_id: int, start_date: datetime, end_date: datetime):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users 
                    SET subscription_start = ?, subscription_end = ?, 
                        is_premium = 1, updated_at = ?
                    WHERE user_id = ?
                ''', (start_date.isoformat(), end_date.isoformat(), 
                     datetime.now(timezone.utc).isoformat(), user_id))
                
                # Add to subscription history
                cursor.execute('''
                    INSERT INTO subscription_history (user_id, start_date, end_date, amount, status)
                    VALUES (?, ?, ?, ?, 'active')
                ''', (user_id, start_date.isoformat(), end_date.isoformat(), CONFIG.SUBSCRIPTION_AMOUNT / 100))
                
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error updating subscription for user {user_id}: {str(e)}")
            raise
    
    def revoke_subscription(self, user_id: int):
        """Revoke premium access for expired subscription"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users 
                    SET is_premium = 0, updated_at = ?
                    WHERE user_id = ?
                ''', (datetime.now(timezone.utc).isoformat(), user_id))
                conn.commit()
                logger.info(f"Revoked premium access for user {user_id}")
                
        except Exception as e:
            logger.error(f"Error revoking subscription for user {user_id}: {str(e)}")
    
    def get_expired_subscriptions(self) -> List[Dict]:
        """Get list of users with expired subscriptions"""
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
    
    def update_payment_status(self, transaction_ref: str, status: str):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE payments 
                    SET status = ?, completed_at = ?
                    WHERE transaction_ref = ?
                ''', (status, datetime.now(timezone.utc).isoformat(), transaction_ref))
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
        """Update user betting statistics"""
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

class FlutterwavePayment:
    def __init__(self, secret_key: str, public_key: str):
        self.base_url = "https://api.flutterwave.com/v3"
        self.secret_key = secret_key
        self.public_key = public_key
    
    def create_payment_link(self, user_id: int, amount: float) -> Dict[str, Any]:
        """Create payment link with Flutterwave"""
        try:
            tx_ref = f"virtuals_bet_{user_id}_{uuid.uuid4().hex[:8]}_{int(time.time())}"
            
            payload = {
                "tx_ref": tx_ref,
                "amount": amount / 100,  # Convert from kobo to naira
                "currency": "NGN",
                "redirect_url": CONFIG.WEBHOOK_URL,
                "meta": {
                    "user_id": str(user_id),
                    "subscription_type": "monthly"
                },
                "customer": {
                    "email": f"user{user_id}@virtualsbet.com",
                    "phonenumber": "08012345678",
                    "name": f"User {user_id}"
                },
                "customizations": {
                    "title": "Virtuals Betting Premium",
                    "description": "30-Day Premium Betting Predictions Access",
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
                
        except requests.RequestException as e:
            logger.error(f"Payment link creation request error: {str(e)}")
            return {"status": "error", "message": "Payment service temporarily unavailable"}
        except Exception as e:
            logger.error(f"Payment link creation error: {str(e)}")
            return {"status": "error", "message": "Payment service unavailable"}
    
    def verify_payment(self, tx_ref: str) -> Dict[str, Any]:
        """Verify payment status with Flutterwave"""
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
                
        except requests.RequestException as e:
            logger.error(f"Payment verification request error: {str(e)}")
            return {"status": "error", "message": "Verification service temporarily unavailable"}
        except Exception as e:
            logger.error(f"Payment verification error: {str(e)}")
            return {"status": "error", "message": "Verification service unavailable"}

class GroupManager:
    """Manages automatic adding/removing users from premium group"""
    
    def __init__(self, application: Application):
        self.application = application
        self.channel_id = CONFIG.PREMIUM_CHANNEL_ID
    
    async def add_user_to_group(self, user_id: int) -> bool:
        """Add user to premium group"""
        try:
            # Unban user first (in case they were previously removed)
            await self.application.bot.unban_chat_member(
                chat_id=self.channel_id,
                user_id=user_id,
                only_if_banned=True
            )
            
            # Create invite link for the user
            invite_link = await self.application.bot.create_chat_invite_link(
                chat_id=self.channel_id,
                member_limit=1,
                expire_date=int(time.time()) + 300  # 5 minutes expiry
            )
            
            logger.info(f"Created invite link for user {user_id}")
            return True
            
        except Forbidden as e:
            logger.error(f"Bot doesn't have permission to manage group: {str(e)}")
            return False
        except BadRequest as e:
            logger.error(f"Bad request when adding user {user_id}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Error adding user {user_id} to group: {str(e)}")
            return False
    
    async def remove_user_from_group(self, user_id: int) -> bool:
        """Remove user from premium group"""
        try:
            await self.application.bot.ban_chat_member(
                chat_id=self.channel_id,
                user_id=user_id,
                revoke_messages=False
            )
            
            # Immediately unban so they can rejoin if they subscribe again
            await self.application.bot.unban_chat_member(
                chat_id=self.channel_id,
                user_id=user_id
            )
            
            logger.info(f"Removed user {user_id} from premium group")
            return True
            
        except Forbidden as e:
            logger.error(f"Bot doesn't have permission to remove user: {str(e)}")
            return False
        except BadRequest as e:
            logger.error(f"Bad request when removing user {user_id}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Error removing user {user_id} from group: {str(e)}")
            return False
    
    async def check_user_membership(self, user_id: int) -> bool:
        """Check if user is member of premium group"""
        try:
            member = await self.application.bot.get_chat_member(
                chat_id=self.channel_id,
                user_id=user_id
            )
            return member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
        except Exception as e:
            logger.error(f"Error checking membership for user {user_id}: {str(e)}")
            return False

class SubscriptionMonitor:
    """Background task to monitor and revoke expired subscriptions"""
    
    def __init__(self, db: DatabaseManager, group_manager: GroupManager):
        self.db = db
        self.group_manager = group_manager
        self.running = False
        self.thread = None
    
    def start(self):
        """Start the subscription monitor"""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.thread.start()
            logger.info("Subscription monitor started")
    
    def stop(self):
        """Stop the subscription monitor"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Subscription monitor stopped")
    
    def _monitor_loop(self):
        """Main monitoring loop - runs every hour"""
        while self.running:
            try:
                self._check_expired_subscriptions()
                # Sleep for 1 hour
                time.sleep(3600)
            except Exception as e:
                logger.error(f"Error in subscription monitor: {str(e)}")
                time.sleep(300)  # Sleep 5 minutes on error
    
    def _check_expired_subscriptions(self):
        """Check for and handle expired subscriptions"""
        try:
            expired_users = self.db.get_expired_subscriptions()
            
            if expired_users:
                logger.info(f"Found {len(expired_users)} expired subscriptions")
                
                for user in expired_users:
                    user_id = user['user_id']
                    
                    # Revoke premium access in database
                    self.db.revoke_subscription(user_id)
                    
                    # Remove from group (async call)
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            self.group_manager.remove_user_from_group(user_id)
                        )
                        loop.close()
                        logger.info(f"Removed expired user {user_id} from premium group")
                    except Exception as e:
                        logger.error(f"Failed to remove user {user_id} from group: {str(e)}")
                    
        except Exception as e:
            logger.error(f"Error checking expired subscriptions: {str(e)}")

class RateLimiter:
    def __init__(self):
        self.requests = {}
        self.max_requests_per_minute = 10
    
    def is_allowed(self, user_id: int) -> bool:
        """Check if user is within rate limits"""
        current_time = time.time()
        minute_ago = current_time - 60
        
        if user_id not in self.requests:
            self.requests[user_id] = []
        
        self.requests[user_id] = [req_time for req_time in self.requests[user_id] if req_time > minute_ago]
        
        if len(self.requests[user_id]) < self.max_requests_per_minute:
            self.requests[user_id].append(current_time)
            return True
        
        return False

class VirtualsBettingBot:
    def __init__(self, config: Config):
        self.config = config
        self.db = DatabaseManager(config.DATABASE_PATH)
        self.payment = FlutterwavePayment(config.FLUTTERWAVE_SECRET_KEY, config.FLUTTERWAVE_PUBLIC_KEY)
        self.rate_limiter = RateLimiter()
        self.application = None
        self.group_manager = None
        self.subscription_monitor = None
    
    async def setup_bot_commands(self):
        """Setup bot commands for BotFather menu"""
        try:
            await self.application.bot.set_my_commands(BOT_COMMANDS)
            logger.info("Bot commands set successfully")
        except Exception as e:
            logger.error(f"Error setting bot commands: {str(e)}")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        welcome_text = f"""🎯 *Welcome to Virtuals Betting Predictions!*

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
Only ₦100 for 30 Days of Premium Access!

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
        """Handle /subscribe command"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        user_data = self.db.get_user(user.id)
        
        # Check if already subscribed
        if user_data and user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                current_time = datetime.now(timezone.utc)
                
                if end_date > current_time:
                    await update.message.reply_text(
                        f"✅ *You Already Have Active Subscription!*\n\n"
                        f"📅 Expires: {end_date.strftime('%B %d, %Y at %H:%M UTC')}\n"
                        f"💎 Status: Premium Member\n\n"
                        f"🔗 Premium Channel: {self.config.PREMIUM_CHANNEL_LINK}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return
            except Exception as e:
                logger.error(f"Error parsing subscription date: {str(e)}")
        
        price_naira = self.config.SUBSCRIPTION_AMOUNT / 100
        subscribe_text = f"""💎 *Premium Subscription*

🎯 *Virtuals Betting Predictions*
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
        """Handle /status command"""
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
                    status_text = f"""✅ *Premium Subscription Active*

👤 User: {user.first_name}
💎 Status: Premium Member
📅 Started: {start_date.strftime('%B %d, %Y')}
⏰ Expires: {end_date.strftime('%B %d, %Y')}
📊 Days Remaining: {days_remaining} days

📈 *Your Stats:*
🎯 Predictions Viewed: {user_data.get('total_predictions_viewed', 0)}
🎲 Total Bets: {user_data.get('total_bets', 0)}

🔗 Premium Channel: {self.config.PREMIUM_CHANNEL_LINK}"""
                else:
                    status_text = f"""⚠️ *Premium Subscription Expired*

👤 User: {user.first_name}
❌ Status: Expired
📅 Expired: {end_date.strftime('%B %d, %Y')}

💰 Renew now for ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f} to regain access!"""
            except Exception as e:
                logger.error(f"Error parsing subscription dates: {str(e)}")
                status_text = "❌ Error retrieving subscription status. Please contact support."
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
        """Handle /predictions command - show sample or premium predictions"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        user_data = self.db.get_user(user.id)
        is_premium = user_data and user_data['is_premium']
        
        if is_premium:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                if end_date > datetime.now(timezone.utc):
                    # Update stats
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
• Start with small stakes
• Follow our odds recommendations
• Manage your bankroll wisely
• Track your wins in /stats

🔥 More predictions in Premium Channel!
🔗 {self.config.PREMIUM_CHANNEL_LINK}"""
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
            keyboard.append([InlineKeyboardButton("📊 View My Stats", callback_data="stats")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            predictions_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command - show user statistics"""
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
        """Handle /support command"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        support_text = """💬 *Customer Support*

Need help? We're here 24/7! 🚀

📞 *Contact Methods:*
✈️ Telegram: @blessednwaoma
📱 WhatsApp: +2347042551379
📧 Email: blessednwaoma7@gmail.com

⏰ *Response Time:* Within 1 hour
🌍 *Availability:* 24/7

🔧 *We Help With:*
• Payment Issues
• Group Access Problems
• Subscription Questions
• Technical Support
• Betting Advice
• Account Management

💡 *Quick Tips:*
• Include your User ID in messages
• Describe your issue clearly
• Mention error messages if any

Your User ID: `{0}`

We're committed to your success! 💪"""
        
        keyboard = [
            [InlineKeyboardButton("✈️ Telegram Support", url="https://t.me/blessednwaoma")],
            [InlineKeyboardButton("📱 WhatsApp", url="https://wa.me/2347042551379")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            support_text.format(user.id),
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
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
/premium - Access premium channel link

🎯 *About Virtuals Betting Bot:*
We provide expert predictions for Virtual Games with 90%+ accuracy rate.

✨ *Premium Features:*
• Daily Sure Bet Predictions
• Virtual Football, Basketball, Horse Racing
• Instant Win Strategies
• Real-time Tips & Notifications
• VIP Telegram Group
• 24/7 Premium Support
• Detailed Analytics

💰 *Subscription:*
• Price: ₦100 for 30 Days
• Payment: Secure via Flutterwave
• Auto Group Access
• Auto Removal after expiry

🔐 *Security:*
• Bank-level encryption
• Trusted payment gateway
• No credit card required

📱 *Getting Started:*
1. Use /subscribe to see plans
2. Complete payment via secure link
3. Get instant premium access
4. Start winning with our predictions!

Need help? Use /support 💬"""
        
        keyboard = [
            [InlineKeyboardButton("💎 Subscribe Now", callback_data="subscribe")],
            [InlineKeyboardButton("💬 Support", callback_data="support")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            help_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def premium_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /premium command - access to premium channel"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        user_data = self.db.get_user(user.id)
        
        if user_data and user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                if end_date > datetime.now(timezone.utc):
                    premium_text = f"""💎 *Premium Channel Access*

✅ Your subscription is active!

🔗 *Premium Channel Link:*
{self.config.PREMIUM_CHANNEL_LINK}

📅 *Valid Until:* {end_date.strftime('%B %d, %Y')}

🎯 *In Premium Channel:*
• Daily Sure Bet Predictions
• Live Betting Tips
• Expert Analysis
• VIP Community
• Instant Updates

Click the button below to join! 👇"""
                    
                    keyboard = [
                        [InlineKeyboardButton("🔗 Join Premium Channel", url=self.config.PREMIUM_CHANNEL_LINK)],
                        [InlineKeyboardButton("📊 My Status", callback_data="status")]
                    ]
                else:
                    premium_text = "⚠️ Your premium subscription has expired. Renew to regain access!"
                    keyboard = [
                        [InlineKeyboardButton("💎 Renew Subscription", callback_data="subscribe")]
                    ]
            except:
                premium_text = "❌ You don't have an active subscription."
                keyboard = [
                    [InlineKeyboardButton("💎 Subscribe Now", callback_data="subscribe")]
                ]
        else:
            premium_text = f"""🔒 *Premium Access Required*

You need an active subscription to access the premium channel.

💎 *Subscribe Now:*
• Price: ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f}
• Duration: 30 Days
• Instant Access

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
        """Process payment initiation"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        # Rate limiting
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
2️⃣ Complete payment securely
3️⃣ Wait 30 seconds after payment
4️⃣ Click "I have Paid" to verify
5️⃣ Get instant premium access!

⚠️ *Important:* Don't close this chat until verification is complete!

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
                    f"❌ Error: {error_message}\n\nPlease try again later or contact support.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="subscribe")]])
                )
        except Exception as e:
            logger.error(f"Error processing payment: {str(e)}")
            await query.edit_message_text(
                "❌ An error occurred. Please try again later or contact support.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Support", callback_data="support")]])
            )
    
    async def verify_payment_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Verify payment and grant access"""
        query = update.callback_query
        await query.answer()
        
        tx_ref = query.data.split('_', 1)[1]
        user_id = query.from_user.id
        
        payment_record = self.db.get_payment_record(tx_ref)
        if not payment_record or payment_record['user_id'] != user_id:
            await query.edit_message_text(
                "❌ Payment record not found. Please contact support.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Support", callback_data="support")]])
            )
            return
        
        if payment_record['status'] == 'completed':
            await query.edit_message_text(
                "✅ This payment has already been processed!\n\n"
                f"🔗 Join Premium Channel: {self.config.PREMIUM_CHANNEL_LINK}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Join Now", url=self.config.PREMIUM_CHANNEL_LINK)]])
            )
            return
        
        await query.edit_message_text("⏳ Verifying your payment... Please wait.")
        
        try:
            verification_result = self.payment.verify_payment(tx_ref)
            
            if (verification_result.get('status') == 'success' and 
                verification_result.get('data', {}).get('status') == 'successful'):
                
                # Calculate subscription dates
                start_date = datetime.now(timezone.utc)
                end_date = start_date + timedelta(days=self.config.SUBSCRIPTION_DAYS)
                
                # Update database
                self.db.update_subscription(user_id, start_date, end_date)
                self.db.update_payment_status(tx_ref, 'completed')
                
                # Add user to premium group
                try:
                    await self.group_manager.add_user_to_group(user_id)
                    group_access = "✅ You've been added to the premium group!"
                except Exception as e:
                    logger.error(f"Error adding user to group: {str(e)}")
                    group_access = "⚠️ Please join the group manually using the link below."
                
                success_text = f"""🎉 *PAYMENT SUCCESSFUL!*

Welcome to Premium! 💎

📅 *Subscription Active*
⏰ Valid Until: {end_date.strftime('%B %d, %Y at %H:%M UTC')}
💰 Amount Paid: ₦{self.config.SUBSCRIPTION_AMOUNT / 100:.0f}

{group_access}

🔗 *Premium Channel:*
{self.config.PREMIUM_CHANNEL_LINK}

🎯 *You Now Have Access To:*
✅ Daily Sure Bet Predictions
✅ Virtual Football Tips
✅ Virtual Basketball Strategies
✅ Horse Racing Insights
✅ Instant Win Techniques
✅ VIP Community
✅ Real-time Updates
✅ 24/7 Premium Support

🚀 Start winning with our predictions!

Use /predictions to see today's tips! 🎯"""
                
                keyboard = [
                    [InlineKeyboardButton("🔗 Join Premium Channel", url=self.config.PREMIUM_CHANNEL_LINK)],
                    [InlineKeyboardButton("🎯 View Predictions", callback_data="predictions")],
                    [InlineKeyboardButton("📊 My Status", callback_data="status")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    success_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
                
                logger.info(f"Successfully processed payment for user {user_id}")
                
            else:
                await query.edit_message_text(
                    "⚠️ Payment verification failed or still pending.\n\n"
                    "If you've paid, please wait a few minutes and try again.\n"
                    "If the problem persists, contact support.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Try Again", callback_data=f"verify_{tx_ref}")],
                        [InlineKeyboardButton("💬 Support", callback_data="support")]
                    ])
                )
        except Exception as e:
            logger.error(f"Error during payment verification: {str(e)}")
            await query.edit_message_text(
                "❌ Error verifying payment. Please try again or contact support.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data=f"verify_{tx_ref}")],
                    [InlineKeyboardButton("💬 Support", callback_data="support")]
                ])
            )
    
    async def learn_more_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed information about the bot"""
        query = update.callback_query
        await query.answer()
        
        info_text = """ℹ️ *About Virtuals Betting Predictions*

🎯 *Our Mission:*
Provide accurate betting predictions for Virtual Games with 90%+ success rate.

💎 *Premium Features:*
✅ Daily Sure Bet Predictions
✅ Virtual Football Analysis
✅ Virtual Basketball Tips
✅ Horse Racing Insights
✅ Instant Win Strategies
✅ Real-time Notifications
✅ VIP Telegram Group
✅ Expert Analysis & Stats
✅ 24/7 Premium Support

📊 *Success Rate:* 90%+ Accuracy
🏆 *Track Record:* 1000+ Satisfied Members
💰 *Pricing:* Only ₦100 for 30 Days

🔒 *Security:*
• Secure Flutterwave Payment
• Bank-level Encryption
• No Credit Card Required
• Instant Access

🎲 *Games We Cover:*
• Virtual Football (Premier League, Champions League)
• Virtual Basketball (NBA, FIBA)
• Virtual Horse Racing (Derby, Classic)
• Instant Win Games (Lucky Spin, Roulette)

🌟 *What Makes Us Different:*
• Professional Analysts Team
• AI-Powered Predictions
• Real-time Market Data
• Proven Track Record
• Active Community

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
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all button callbacks"""
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
            elif query.data == "learn_more":
                await self.learn_more_callback(update, context)
            elif query.data == "back_to_menu":
                await self.back_to_menu(query, context)
            else:
                await query.answer("Unknown action.")
        except Exception as e:
            logger.error(f"Error handling button {query.data}: {str(e)}")
            await query.answer("Something went wrong. Please try again.")
    
    async def subscribe_button(self, query, context):
        """Handle subscribe button click"""
        await query.answer()
        user_id = query.from_user.id
        user_data = self.db.get_user(user_id)
        
        if user_data and user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                if end_date > datetime.now(timezone.utc):
                    await query.edit_message_text(
                        f"✅ You already have an active subscription until {end_date.strftime('%B %d, %Y')}!",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
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
        """Handle status button - reuse status command logic"""
        await query.answer()
        mock_update = type('obj', (object,), {
            'message': query.message,
            'effective_user': query.from_user
        })()
        await self.status_command(mock_update, context)
    
    async def predictions_button(self, query, context):
        """Handle predictions button"""
        await query.answer()
        mock_update = type('obj', (object,), {
            'message': query.message,
            'effective_user': query.from_user
        })()
        await self.predictions_command(mock_update, context)
    
    async def stats_button(self, query, context):
        """Handle stats button"""
        await query.answer()
        mock_update = type('obj', (object,), {
            'message': query.message,
            'effective_user': query.from_user
        })()
        await self.stats_command(mock_update, context)
    
    async def support_button(self, query, context):
        """Handle support button"""
        await query.answer()
        mock_update = type('obj', (object,), {
            'message': query.message,
            'effective_user': query.from_user
        })()
        await self.support_command(mock_update, context)
    
    async def back_to_menu(self, query, context):
        """Return to main menu"""
        await query.answer()
        user = query.from_user
        
        welcome_text = f"""🎯 *Virtuals Betting Predictions*

Hello {user.first_name}! 👋

💎 Premium betting predictions at your fingertips!

Use the buttons below to navigate."""
        
        keyboard = [
            [InlineKeyboardButton("💎 Subscribe (₦100)", callback_data="subscribe")],
            [InlineKeyboardButton("📊 Status", callback_data="status"),
             InlineKeyboardButton("🎯 Predictions", callback_data="predictions")],
            [InlineKeyboardButton("📈 Stats", callback_data="stats"),
             InlineKeyboardButton("💬 Support", callback_data="support")]
        ]
        
        await query.edit_message_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            health_status = {
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service": "Virtuals Betting Bot"
            }
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(health_status).encode())
        except Exception as e:
            logger.error(f"Health check error: {str(e)}")
            self.send_response(500)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

def run_health_server():
    try:
        server = HTTPServer(('0.0.0.0', CONFIG.PORT), HealthHandler)
        logger.info(f"Health check server started on port {CONFIG.PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server error: {str(e)}")

def signal_handler(signum, frame):
    global shutdown_flag
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_flag = True

def main():
    global shutdown_flag
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info("Starting Virtuals Betting Bot...")
    
    bot = VirtualsBettingBot(CONFIG)
    logger.info("Bot initialized successfully")
    
    application = Application.builder().token(CONFIG.BOT_TOKEN).build()
    bot.application = application
    bot.group_manager = GroupManager(application)
    logger.info("Telegram application created successfully")
    
    # Initialize subscription monitor
    bot.subscription_monitor = SubscriptionMonitor(bot.db, bot.group_manager)
    bot.subscription_monitor.start()
    logger.info("Subscription monitor started")
    
    # Add command handlers
    application.add_handler(CommandHandler("start", bot.start_command))
    application.add_handler(CommandHandler("subscribe", bot.subscribe_command))
    application.add_handler(CommandHandler("status", bot.status_command))
    application.add_handler(CommandHandler("predictions", bot.predictions_command))
    application.add_handler(CommandHandler("stats", bot.stats_command))
    application.add_handler(CommandHandler("support", bot.support_command))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("premium", bot.premium_command))
    
    # Add callback query handler
    application.add_handler(CallbackQueryHandler(bot.button_callback))
    
    # Start health check server
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    logger.info("Virtuals Betting Bot started successfully!")
    print("🎯 Virtuals Betting Bot is running...")
    print(f"💚 Health check server on port {CONFIG.PORT}")
    print(f"💰 Subscription: ₦{CONFIG.SUBSCRIPTION_AMOUNT / 100:.0f} for {CONFIG.SUBSCRIPTION_DAYS} days")
    print("\n📋 Available Commands:")
    for cmd in BOT_COMMANDS:
        print(f"  /{cmd.command} - {cmd.description}")
    
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
                logger.info(f"Waiting {wait_time}s before retry {retry_count + 1}/{max_retries}")
                time.sleep(wait_time)
            else:
                logger.error("Max retries reached. Ensure no other instances running.")
                break
                
        except (NetworkError, TimedOut) as e:
            retry_count += 1
            logger.warning(f"Network error: {str(e)}")
            if retry_count < max_retries:
                logger.info(f"Retrying in 30s... (attempt {retry_count + 1}/{max_retries})")
                time.sleep(30)
            else:
                logger.error("Max retries reached due to network errors.")
                break
                
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            break
    
    # Cleanup
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