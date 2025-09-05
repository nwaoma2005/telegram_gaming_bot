#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
import requests
import uuid
import signal
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import time

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

@dataclass
class Config:
    BOT_TOKEN: str
    FLUTTERWAVE_SECRET_KEY: str
    FLUTTERWAVE_PUBLIC_KEY: str
    PREMIUM_CHANNEL_ID: str
    PREMIUM_CHANNEL_LINK: str
    DATABASE_PATH: str = "./premium_bot.db"
    PORT: int = 8000
    WEBHOOK_URL: str = ""
    ADMIN_USER_ID: str = ""

def load_config() -> Config:
    """Load and validate configuration from environment variables"""
    config = Config(
        BOT_TOKEN=os.getenv("BOT_TOKEN", ""),
        FLUTTERWAVE_SECRET_KEY=os.getenv("FLUTTERWAVE_SECRET_KEY", ""),
        FLUTTERWAVE_PUBLIC_KEY=os.getenv("FLUTTERWAVE_PUBLIC_KEY", ""),
        PREMIUM_CHANNEL_ID=os.getenv("PREMIUM_CHANNEL_ID", ""),
        PREMIUM_CHANNEL_LINK=os.getenv("PREMIUM_CHANNEL_LINK", ""),
        DATABASE_PATH=os.getenv("DATABASE_PATH", "./premium_bot.db"),
        PORT=int(os.getenv("PORT", 8000)),
        WEBHOOK_URL=os.getenv("WEBHOOK_URL", "https://webhook.site/unique-id"),
        ADMIN_USER_ID=os.getenv("ADMIN_USER_ID", "")
    )
    
    # Validate required fields
    required_fields = ['BOT_TOKEN', 'FLUTTERWAVE_SECRET_KEY', 'FLUTTERWAVE_PUBLIC_KEY', 
                      'PREMIUM_CHANNEL_ID', 'PREMIUM_CHANNEL_LINK']
    
    missing_fields = [field for field in required_fields if not getattr(config, field)]
    if missing_fields:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_fields)}")
    
    return config

# Global configuration
try:
    CONFIG = load_config()
    logger.info("Configuration loaded successfully")
except ValueError as e:
    logger.error(f"Configuration error: {e}")
    sys.exit(1)

# Subscription plans (amounts in kobo - 100 kobo = 1 NGN)
PLANS = {
    "daily": {"name": "Daily Plan", "amount": 100, "duration_days": 1},
    "weekly": {"name": "Weekly Plan", "amount": 500, "duration_days": 7},
    "monthly": {"name": "Monthly Plan", "amount": 1500, "duration_days": 30},
    "yearly": {"name": "Yearly Plan", "amount": 15000, "duration_days": 365}
}

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            raise e
        finally:
            if conn:
                conn.close()
    
    def init_database(self):
        """Initialize SQLite database with proper schema"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        subscription_plan TEXT,
                        subscription_start TEXT,
                        subscription_end TEXT,
                        is_premium INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        transaction_ref TEXT UNIQUE,
                        amount REAL,
                        plan_type TEXT,
                        status TEXT DEFAULT 'pending',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (user_id)
                    )
                ''')
                
                # Create indexes for better performance
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_premium ON users (is_premium)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_subscription_end ON users (subscription_end)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments (user_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_payments_status ON payments (status)')
                
                conn.commit()
                logger.info("Database initialized successfully")
                
        except Exception as e:
            logger.error(f"Database initialization error: {str(e)}")
            raise
    
    def sanitize_string(self, value: str, max_length: int = 100) -> Optional[str]:
        """Sanitize string input"""
        if value is None:
            return None
        return str(value)[:max_length].strip()
    
    def add_user(self, user_id: int, username: str = None, first_name: str = None):
        """Add or update user in database"""
        try:
            username = self.sanitize_string(username)
            first_name = self.sanitize_string(first_name)
            
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT OR REPLACE INTO users (user_id, username, first_name, updated_at)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, username, first_name, datetime.now(timezone.utc).isoformat()))
                
                conn.commit()
                logger.info(f"User {user_id} added/updated successfully")
                
        except Exception as e:
            logger.error(f"Error adding user {user_id}: {str(e)}")
            raise
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user information"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                row = cursor.fetchone()
                
                if row:
                    columns = ['user_id', 'username', 'first_name', 'subscription_plan', 
                              'subscription_start', 'subscription_end', 'is_premium', 'created_at', 'updated_at']
                    return dict(zip(columns, row))
                return None
                
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {str(e)}")
            return None
    
    def update_subscription(self, user_id: int, plan: str, start_date: datetime, end_date: datetime):
        """Update user subscription"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    UPDATE users 
                    SET subscription_plan = ?, subscription_start = ?, subscription_end = ?, 
                        is_premium = 1, updated_at = ?
                    WHERE user_id = ?
                ''', (plan, start_date.isoformat(), end_date.isoformat(), 
                     datetime.now(timezone.utc).isoformat(), user_id))
                
                conn.commit()
                logger.info(f"Updated subscription for user {user_id}: {plan}")
                
        except Exception as e:
            logger.error(f"Error updating subscription for user {user_id}: {str(e)}")
            raise
    
    def expire_user_subscription(self, user_id: int):
        """Expire user subscription"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    UPDATE users 
                    SET is_premium = 0, subscription_plan = NULL, updated_at = ?
                    WHERE user_id = ?
                ''', (datetime.now(timezone.utc).isoformat(), user_id))
                
                conn.commit()
                logger.info(f"Expired subscription for user {user_id}")
                
        except Exception as e:
            logger.error(f"Error expiring subscription for user {user_id}: {str(e)}")
            raise
    
    def get_expired_users(self) -> List[int]:
        """Get users with expired subscriptions"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                current_time = datetime.now(timezone.utc).isoformat()
                cursor.execute('''
                    SELECT user_id FROM users 
                    WHERE is_premium = 1 AND subscription_end < ?
                ''', (current_time,))
                
                expired_users = [row[0] for row in cursor.fetchall()]
                return expired_users
                
        except Exception as e:
            logger.error(f"Error getting expired users: {str(e)}")
            return []
    
    def add_payment_record(self, user_id: int, transaction_ref: str, amount: float, plan_type: str):
        """Add payment record"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO payments (user_id, transaction_ref, amount, plan_type, status, created_at)
                    VALUES (?, ?, ?, ?, 'pending', ?)
                ''', (user_id, transaction_ref, amount, plan_type, datetime.now(timezone.utc).isoformat()))
                
                conn.commit()
                logger.info(f"Added payment record: {transaction_ref}")
                
        except Exception as e:
            logger.error(f"Error adding payment record: {str(e)}")
            raise
    
    def update_payment_status(self, transaction_ref: str, status: str):
        """Update payment status"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    UPDATE payments SET status = ?, updated_at = ? WHERE transaction_ref = ?
                ''', (status, datetime.now(timezone.utc).isoformat(), transaction_ref))
                
                conn.commit()
                logger.info(f"Updated payment status: {transaction_ref} -> {status}")
                
        except Exception as e:
            logger.error(f"Error updating payment status: {str(e)}")
            raise
    
    def get_payment_record(self, transaction_ref: str) -> Optional[Dict]:
        """Get payment record by transaction reference"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('SELECT * FROM payments WHERE transaction_ref = ?', (transaction_ref,))
                row = cursor.fetchone()
                
                if row:
                    columns = ['id', 'user_id', 'transaction_ref', 'amount', 'plan_type', 
                              'status', 'created_at', 'updated_at']
                    return dict(zip(columns, row))
                return None
                
        except Exception as e:
            logger.error(f"Error getting payment record {transaction_ref}: {str(e)}")
            return None

class FlutterwavePayment:
    def __init__(self, secret_key: str, public_key: str):
        self.base_url = "https://api.flutterwave.com/v3"
        self.secret_key = secret_key
        self.public_key = public_key
    
    def create_payment_link(self, user_id: int, amount: float, plan_name: str) -> Dict[str, Any]:
        """Create payment link with Flutterwave"""
        try:
            tx_ref = f"premium_bot_{user_id}_{uuid.uuid4().hex[:8]}_{int(time.time())}"
            
            payload = {
                "tx_ref": tx_ref,
                "amount": amount / 100,  # Convert from kobo to naira
                "currency": "NGN",
                "redirect_url": CONFIG.WEBHOOK_URL,
                "meta": {
                    "user_id": str(user_id),
                    "plan": plan_name
                },
                "customer": {
                    "email": f"user{user_id}@telegram.bot",
                    "phonenumber": "08012345678",
                    "name": f"User {user_id}"
                },
                "customizations": {
                    "title": "Premium Gaming Access",
                    "description": f"Payment for {PLANS[plan_name]['name']}",
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

class RateLimiter:
    def __init__(self):
        self.requests = {}
        self.max_requests_per_minute = 5
    
    def is_allowed(self, user_id: int) -> bool:
        """Check if user is within rate limits"""
        current_time = time.time()
        minute_ago = current_time - 60
        
        if user_id not in self.requests:
            self.requests[user_id] = []
        
        # Remove old requests
        self.requests[user_id] = [req_time for req_time in self.requests[user_id] if req_time > minute_ago]
        
        # Check if under limit
        if len(self.requests[user_id]) < self.max_requests_per_minute:
            self.requests[user_id].append(current_time)
            return True
        
        return False

class PremiumBot:
    def __init__(self, config: Config):
        self.config = config
        self.db = DatabaseManager(config.DATABASE_PATH)
        self.payment = FlutterwavePayment(config.FLUTTERWAVE_SECRET_KEY, config.FLUTTERWAVE_PUBLIC_KEY)
        self.rate_limiter = RateLimiter()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command handler"""
        # Handle both direct messages and callback queries
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            user = query.from_user
            message_func = query.edit_message_text
        else:
            user = update.effective_user
            message_func = update.message.reply_text
        
        # Add user to database
        try:
            self.db.add_user(user.id, user.username, user.first_name)
        except Exception as e:
            logger.error(f"Error adding user {user.id}: {str(e)}")
        
        welcome_message = f"""
🎮 **Welcome to Premium Gaming Bot!**

Hello {user.first_name}! 👋

I'm your gaming companion bot, designed to help you access premium gaming resources and exclusive content.

**What I offer:**
🆓 **Free Channel**: Daily gaming tips and basic resources
💎 **Premium Channel**: Exclusive content including:
   • Advanced gaming strategies
   • Early access to new games
   • Premium game guides
   • VIP community access
   • Special tournaments and events

**Premium Benefits:**
✨ High-accuracy gaming predictions
🎯 Exclusive insider tips
🏆 Priority customer support
📊 Detailed analytics and statistics
🎁 Weekly bonus content

Ready to upgrade your gaming experience?
        """
        
        keyboard = [
            [InlineKeyboardButton("🚀 Upgrade to Premium", callback_data="upgrade")],
            [InlineKeyboardButton("ℹ️ Learn More", callback_data="learn_more")],
            [InlineKeyboardButton("📞 Support", callback_data="support")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message_func(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def upgrade_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show upgrade options"""
        query = update.callback_query
        await query.answer()
        
        user_data = self.db.get_user(query.from_user.id)
        
        if user_data and user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                current_time = datetime.now(timezone.utc)
                
                if end_date > current_time:
                    await query.edit_message_text(
                        f"✅ You already have an active premium subscription!\n"
                        f"📅 Expires: {end_date.strftime('%B %d, %Y at %H:%M UTC')}\n"
                        f"🎯 Plan: {user_data['subscription_plan'].title()}"
                    )
                    return
                else:
                    # Subscription expired, update database
                    self.db.expire_user_subscription(query.from_user.id)
            except Exception as e:
                logger.error(f"Error parsing subscription end date: {str(e)}")
        
        upgrade_message = """
💎 **Choose Your Premium Plan**

Select the plan that best fits your gaming needs:

📊 **All plans include:**
• Access to premium gaming channel
• Exclusive gaming strategies
• Priority support
• Advanced analytics
• VIP community access
        """
        
        keyboard = []
        for plan_id, plan_info in PLANS.items():
            price_naira = plan_info['amount'] / 100
            keyboard.append([
                InlineKeyboardButton(
                    f"{plan_info['name']} - ₦{price_naira:.0f}",
                    callback_data=f"plan_{plan_id}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(upgrade_message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def process_plan_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process selected plan and create payment link"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        # Check rate limiting
        if not self.rate_limiter.is_allowed(user_id):
            await query.edit_message_text(
                "⏳ Please wait before making another payment request.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="upgrade")]])
            )
            return
        
        plan_id = query.data.split('_')[1]
        plan_info = PLANS.get(plan_id)
        
        if not plan_info:
            await query.edit_message_text("❌ Invalid plan selected. Please try again.")
            return
        
        await query.edit_message_text("🔄 Creating payment link... Please wait.")
        
        try:
            payment_result = self.payment.create_payment_link(user_id, plan_info['amount'], plan_id)
            
            if payment_result['status'] == 'success':
                self.db.add_payment_record(user_id, payment_result['tx_ref'], plan_info['amount'], plan_id)
                
                price_naira = plan_info['amount'] / 100
                payment_message = f"""
💳 **Payment Details**

📦 **Plan**: {plan_info['name']}
💰 **Amount**: ₦{price_naira:.0f}
⏱️ **Duration**: {plan_info['duration_days']} days

🔗 **Click the button below to complete your payment**

⚡ After successful payment, wait 30 seconds then click "I've Paid" to verify and get instant access!
                """
                
                keyboard = [
                    [InlineKeyboardButton("💳 Pay Now", url=payment_result['link'])],
                    [InlineKeyboardButton("✅ I've Paid - Verify", callback_data=f"verify_{payment_result['tx_ref']}")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="upgrade")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(payment_message, reply_markup=reply_markup, parse_mode='Markdown')
            else:
                error_message = payment_result.get('message', 'Payment link creation failed')
                await query.edit_message_text(
                    f"❌ {error_message}\n\nPlease try again later or contact support.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="upgrade")]])
                )
        except Exception as e:
            logger.error(f"Error processing plan selection: {str(e)}")
            await query.edit_message_text(
                "❌ An error occurred. Please try again later or contact support.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="upgrade")]])
            )
    
    async def verify_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Verify payment and grant access"""
        query = update.callback_query
        await query.answer()
        
        tx_ref = query.data.split('_', 1)[1]
        user_id = query.from_user.id
        
        # Check if payment record exists
        payment_record = self.db.get_payment_record(tx_ref)
        if not payment_record or payment_record['user_id'] != user_id:
            await query.edit_message_text(
                "❌ Payment record not found. Please contact support.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📞 Support", callback_data="support")]])
            )
            return
        
        # Check if already verified
        if payment_record['status'] == 'completed':
            await query.edit_message_text(
                "✅ This payment has already been processed. You should have access to the premium channel.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎮 Premium Channel", url=self.config.PREMIUM_CHANNEL_LINK)]])
            )
            return
        
        await query.edit_message_text("🔄 Verifying your payment... Please wait.")
        
        # Add small delay to allow payment processing
        await asyncio.sleep(2)
        
        try:
            verification_result = self.payment.verify_payment(tx_ref)
            
            if (verification_result.get('status') == 'success' and 
                verification_result.get('data', {}).get('status') == 'successful'):
                
                try:
                    plan_type = verification_result['data']['meta']['plan']
                    plan_info = PLANS.get(plan_type)
                    
                    if not plan_info:
                        await query.edit_message_text(
                            "❌ Invalid plan in payment data. Please contact support with reference: " + tx_ref,
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📞 Support", callback_data="support")]])
                        )
                        return
                    
                    start_date = datetime.now(timezone.utc)
                    end_date = start_date + timedelta(days=plan_info['duration_days'])
                    
                    self.db.update_subscription(user_id, plan_type, start_date, end_date)
                    self.db.update_payment_status(tx_ref, 'completed')
                    
                    success_message = f"""
✅ **Payment Successful!**

🎉 Welcome to Premium Gaming!

📦 **Plan**: {plan_info['name']}
📅 **Valid Until**: {end_date.strftime('%B %d, %Y at %H:%M UTC')}

🔗 **Premium Channel Access**: {self.config.PREMIUM_CHANNEL_LINK}

🎮 You now have access to:
• Exclusive gaming strategies
• Advanced tips and predictions
• VIP community
• Priority support

Enjoy your premium experience! 🚀
                    """
                    
                    keyboard = [[InlineKeyboardButton("🎮 Join Premium Channel", url=self.config.PREMIUM_CHANNEL_LINK)]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await query.edit_message_text(success_message, reply_markup=reply_markup, parse_mode='Markdown')
                    
                except KeyError as e:
                    logger.error(f"Missing key in payment verification response: {str(e)}")
                    await query.edit_message_text(
                        "✅ Payment successful but there was an error processing your subscription. "
                        f"Please contact support with reference: {tx_ref}",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📞 Support", callback_data="support")]])
                    )
                except Exception as e:
                    logger.error(f"Error processing successful payment: {str(e)}")
                    await query.edit_message_text(
                        "✅ Payment successful but there was an error setting up your account. "
                        f"Please contact support with reference: {tx_ref}",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📞 Support", callback_data="support")]])
                    )
                    
            else:
                await query.edit_message_text(
                    "❌ Payment verification failed or payment is still pending.\n\n"
                    "If you've already paid, please wait a few minutes and try verifying again.\n"
                    "If the problem persists, contact support.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Try Again", callback_data=f"verify_{tx_ref}")],
                        [InlineKeyboardButton("📞 Support", callback_data="support")]
                    ])
                )
        except Exception as e:
            logger.error(f"Error during payment verification: {str(e)}")
            await query.edit_message_text(
                "❌ Error verifying payment. Please try again later or contact support.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data=f"verify_{tx_ref}")],
                    [InlineKeyboardButton("📞 Support", callback_data="support")]
                ])
            )
    
    async def check_subscription_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check user's subscription status"""
        user_id = update.effective_user.id
        user_data = self.db.get_user(user_id)
        
        if not user_data:
            await update.message.reply_text("❌ User not found. Please start the bot first with /start")
            return
        
        if user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                current_time = datetime.now(timezone.utc)
                
                if end_date > current_time:
                    time_left = end_date - current_time
                    days_left = time_left.days
                    hours_left = time_left.seconds // 3600
                    
                    status_message = f"""
✅ **Premium Subscription Active**

📦 **Plan**: {user_data['subscription_plan'].title()}
📅 **Expires**: {end_date.strftime('%B %d, %Y at %H:%M UTC')}
⏰ **Time Left**: {days_left} days, {hours_left} hours

🎮 **Premium Channel**: {self.config.PREMIUM_CHANNEL_LINK}
                    """
                    
                    keyboard = [[InlineKeyboardButton("🎮 Access Premium Channel", url=self.config.PREMIUM_CHANNEL_LINK)]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                else:
                    self.db.expire_user_subscription(user_id)
                    status_message = """
❌ **Subscription Expired**

Your premium subscription has expired. Upgrade now to regain access to premium features!
                    """
                    
                    keyboard = [[InlineKeyboardButton("🚀 Upgrade Now", callback_data="upgrade")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
            except Exception as e:
                logger.error(f"Error parsing subscription date: {str(e)}")
                status_message = """
❌ **Error checking subscription**

There was an error checking your subscription status. Please contact support.
                """
                keyboard = [[InlineKeyboardButton("📞 Contact Support", callback_data="support")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            status_message = """
📋 **Free Account**

You currently have a free account. Upgrade to premium to access exclusive gaming content!
            """
            
            keyboard = [[InlineKeyboardButton("🚀 Upgrade to Premium", callback_data="upgrade")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(status_message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all button callbacks"""
        query = update.callback_query
        
        try:
            if query.data == "upgrade":
                await self.upgrade_menu(update, context)
            elif query.data.startswith("plan_"):
                await self.process_plan_selection(update, context)
            elif query.data.startswith("verify_"):
                await self.verify_payment(update, context)
            elif query.data == "learn_more":
                await self.learn_more(update, context)
            elif query.data == "support":
                await self.support(update, context)
            elif query.data == "back_to_menu":
                await self.start(update, context)
            else:
                await query.answer("❌ Unknown action.")
        except Exception as e:
            logger.error(f"Error handling button {query.data}: {str(e)}")
            await query.answer("❌ Something went wrong. Please try again.")
    
    async def learn_more(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show more information about the service"""
        query = update.callback_query
        await query.answer()
        
        info_message = """
📚 **About Premium Gaming Bot**

🎯 **Our Mission**: To provide gamers with the most accurate and valuable gaming insights.

💎 **Premium Features**:
• 90%+ accuracy rate on predictions
• Daily exclusive gaming tips
• Advanced strategy guides
• VIP community access
• Priority customer support
• Weekly bonus content
• Early access to new features

📊 **Success Rate**: Our premium members report 3x better gaming performance

🔒 **Secure**: All payments processed through trusted Flutterwave gateway

💪 **Community**: Join 1000+ satisfied premium members
        """
        
        keyboard = [
            [InlineKeyboardButton("🚀 Upgrade Now", callback_data="upgrade")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(info_message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def support(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show support information"""
        query = update.callback_query
        await query.answer()
        
        support_message = """
📞 **Customer Support**

Need help? We're here for you!

🕐 **Support Hours**: 24/7
📧 **Email**: support@yourgamingbot.com
💬 **Telegram**: @your_support_bot
📱 **WhatsApp**: +234 XXX XXX XXXX

**Common Issues:**
• Payment problems
• Channel access issues
• Subscription questions
• Technical support

We typically respond within 1 hour! 🚀
        """
        
        keyboard = [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(support_message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def admin_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show admin statistics (only for admin users)"""
        user_id = update.effective_user.id
        
        if str(user_id) != CONFIG.ADMIN_USER_ID:
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Get total users
                cursor.execute("SELECT COUNT(*) FROM users")
                total_users = cursor.fetchone()[0]
                
                # Get premium users
                cursor.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1")
                premium_users = cursor.fetchone()[0]
                
                # Get total revenue (completed payments)
                cursor.execute("SELECT SUM(amount) FROM payments WHERE status = 'completed'")
                total_revenue_kobo = cursor.fetchone()[0] or 0
                total_revenue_naira = total_revenue_kobo / 100
                
                # Get payments in last 24 hours
                yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
                cursor.execute("SELECT COUNT(*) FROM payments WHERE created_at > ? AND status = 'completed'", (yesterday,))
                recent_payments = cursor.fetchone()[0]
                
                stats_message = f"""
📊 **Admin Statistics**

👥 **Users**: {total_users} total, {premium_users} premium
💰 **Revenue**: ₦{total_revenue_naira:.2f} total
📈 **Recent**: {recent_payments} payments (24h)
⏰ **Updated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
                """
                
                await update.message.reply_text(stats_message, parse_mode='Markdown')
                
        except Exception as e:
            logger.error(f"Error getting admin stats: {str(e)}")
            await update.message.reply_text("❌ Error retrieving statistics.")
    
    async def check_expired_subscriptions(self, context: ContextTypes.DEFAULT_TYPE):
        """Background task to check and handle expired subscriptions"""
        try:
            expired_users = self.db.get_expired_users()
            
            for user_id in expired_users:
                try:
                    self.db.expire_user_subscription(user_id)
                    
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="""
⏰ **Subscription Expired**

Your premium subscription has expired. You no longer have access to the premium channel.

**Renew Now** to continue enjoying premium features!
                        """,
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🚀 Renew Subscription", callback_data="upgrade")]
                        ]),
                        parse_mode='Markdown'
                    )
                    
                    # Remove user from premium channel
                    if CONFIG.PREMIUM_CHANNEL_ID:
                        try:
                            await context.bot.ban_chat_member(CONFIG.PREMIUM_CHANNEL_ID, user_id)
                            logger.info(f"Removed expired user {user_id} from premium channel")
                        except Exception as e:
                            logger.error(f"Failed to remove user {user_id} from premium channel: {str(e)}")
                    
                except Exception as e:
                    logger.error(f"Error processing expired user {user_id}: {str(e)}")
            
            if expired_users:
                logger.info(f"Processed {len(expired_users)} expired subscriptions")
                
        except Exception as e:
            logger.error(f"Error in expired subscriptions check: {str(e)}")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle health check requests"""
        try:
            if self.path == '/health':
                # Perform health checks
                health_status = {
                    "status": "healthy",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "service": "Premium Gaming Bot"
                }
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(health_status).encode())
            else:
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'Premium Gaming Bot is running!')
        except Exception as e:
            logger.error(f"Health check error: {str(e)}")
            self.send_response(500)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress default logging"""
        pass

def run_health_server():
    """Run health check server for Render"""
    try:
        server = HTTPServer(('0.0.0.0', CONFIG.PORT), HealthHandler)
        logger.info(f"Health check server started on port {CONFIG.PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server error: {str(e)}")

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)

async def main():
    """Start the bot"""
    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info("Starting Premium Gaming Bot...")
    
    try:
        bot = PremiumBot(CONFIG)
        logger.info("Bot initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize bot: {str(e)}")
        return
    
    try:
        application = Application.builder().token(CONFIG.BOT_TOKEN).build()
        logger.info("Telegram application created successfully")
    except Exception as e:
        logger.error(f"Failed to create Telegram application: {str(e)}")
        return
    
    # Add command handlers
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("status", bot.check_subscription_status))
    application.add_handler(CommandHandler("help", bot.start))
    application.add_handler(CommandHandler("stats", bot.admin_stats))
    application.add_handler(CallbackQueryHandler(bot.button_handler))
    
    # Schedule expired subscription checks every hour
    application.job_queue.run_repeating(bot.check_expired_subscriptions, interval=3600, first=10)
    
    # Start health check server in background thread
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    logger.info("Premium Gaming Bot started successfully!")
    print("Premium Gaming Bot is running...")
    print(f"Health check server running on port {CONFIG.PORT}")
    print("Bot is ready to accept users!")
    
    try:
        # Use webhook if URL is provided, otherwise use polling
        if CONFIG.WEBHOOK_URL and not CONFIG.WEBHOOK_URL.startswith("https://webhook.site"):
            webhook_url = f"{CONFIG.WEBHOOK_URL}/webhook"
            await application.run_webhook(
                listen="0.0.0.0",
                port=CONFIG.PORT,
                webhook_url=webhook_url,
                drop_pending_updates=True
            )
        else:
            await application.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error(f"Bot running error: {str(e)}")
    finally:
        logger.info("Bot stopped")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        print("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        print(f"Fatal error: {str(e)}")