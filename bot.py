#!/usr/bin/env python3
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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import Conflict, NetworkError, TimedOut
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
    DATABASE_PATH: str = "./premium_bot.db"
    PORT: int = 10000
    WEBHOOK_URL: str = ""
    ADMIN_USER_ID: str = ""

def load_config() -> Config:
    config = Config(
        BOT_TOKEN=os.getenv("BOT_TOKEN", ""),
        FLUTTERWAVE_SECRET_KEY=os.getenv("FLUTTERWAVE_SECRET_KEY", ""),
        FLUTTERWAVE_PUBLIC_KEY=os.getenv("FLUTTERWAVE_PUBLIC_KEY", ""),
        PREMIUM_CHANNEL_ID=os.getenv("PREMIUM_CHANNEL_ID", ""),
        PREMIUM_CHANNEL_LINK=os.getenv("PREMIUM_CHANNEL_LINK", ""),
        DATABASE_PATH=os.getenv("DATABASE_PATH", "./premium_bot.db"),
        PORT=int(os.getenv("PORT", 10000)),
        WEBHOOK_URL=os.getenv("WEBHOOK_URL", "https://webhook.site/unique-id"),
        ADMIN_USER_ID=os.getenv("ADMIN_USER_ID", "")
    )
    
    if not config.BOT_TOKEN:
        raise ValueError("BOT_TOKEN is required")
    
    return config

# Load config
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

# Bot commands for BotFather
BOT_COMMANDS = [
    BotCommand("start", "🚀 Start the bot and see main menu"),
    BotCommand("upgrade", "💎 View premium subscription plans"),
    BotCommand("status", "📊 Check your subscription status"),
    BotCommand("plans", "📋 View all available plans"),
    BotCommand("support", "📞 Get customer support information"),
    BotCommand("help", "❓ Get help and learn more about the bot"),
    BotCommand("contact", "📧 Get contact information"),
    BotCommand("premium", "🎮 Access premium channel link"),
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
                    INSERT OR REPLACE INTO users (user_id, username, first_name, updated_at)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, username or "", first_name or "", datetime.now(timezone.utc).isoformat()))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error adding user {user_id}: {str(e)}")
    
    def get_user(self, user_id: int) -> Optional[Dict]:
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
                
        except Exception as e:
            logger.error(f"Error updating subscription for user {user_id}: {str(e)}")
            raise
    
    def add_payment_record(self, user_id: int, transaction_ref: str, amount: float, plan_type: str):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO payments (user_id, transaction_ref, amount, plan_type, status, created_at)
                    VALUES (?, ?, ?, ?, 'pending', ?)
                ''', (user_id, transaction_ref, amount, plan_type, datetime.now(timezone.utc).isoformat()))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error adding payment record: {str(e)}")
            raise
    
    def update_payment_status(self, transaction_ref: str, status: str):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE payments SET status = ?, updated_at = ? WHERE transaction_ref = ?
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
                
                if row:
                    columns = ['id', 'user_id', 'transaction_ref', 'amount', 'plan_type', 
                              'status', 'created_at', 'updated_at']
                    return dict(zip(columns, row))
                return None
                
        except Exception as e:
            logger.error(f"Error getting payment record: {str(e)}")
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
        self.application = None
    
    async def setup_bot_commands(self):
        """Setup bot commands for BotFather menu"""
        try:
            await self.application.bot.set_my_commands(BOT_COMMANDS)
            logger.info("Bot commands set successfully")
        except Exception as e:
            logger.error(f"Error setting bot commands: {str(e)}")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        # Add user to database
        self.db.add_user(user.id, user.username, user.first_name)
        
        welcome_text = f"""🎮 Welcome to Premium Gaming Bot!

Hello {user.first_name}! 

I'm your gaming companion bot for premium gaming resources and exclusive content.

What I offer:
🆓 Free Channel: Daily gaming tips and basic resources
💎 Premium Channel: Exclusive content including:
   • Advanced gaming strategies
   • Early access to new games
   • Premium game guides
   • VIP community access

Premium Benefits:
✨ High-accuracy gaming predictions
🎯 Exclusive insider tips
🏆 Priority customer support
📊 Detailed analytics and statistics

🔥 Ready to upgrade your gaming experience?

📚 Use /help to see all available commands"""
        
        keyboard = [
            [InlineKeyboardButton("💎 Upgrade to Premium", callback_data="upgrade")],
            [InlineKeyboardButton("📊 Check Status", callback_data="status")],
            [InlineKeyboardButton("ℹ️ Learn More", callback_data="learn_more")],
            [InlineKeyboardButton("📞 Support", callback_data="support")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def upgrade_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /upgrade command"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        user_data = self.db.get_user(user.id)
        
        if user_data and user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                current_time = datetime.now(timezone.utc)
                
                if end_date > current_time:
                    await update.message.reply_text(
                        f"✅ You already have an active premium subscription!\n"
                        f"📅 Expires: {end_date.strftime('%B %d, %Y at %H:%M UTC')}\n"
                        f"🎯 Plan: {user_data['subscription_plan'].title()}\n\n"
                        f"🎮 Access your premium channel: {self.config.PREMIUM_CHANNEL_LINK}"
                    )
                    return
            except Exception as e:
                logger.error(f"Error parsing subscription date: {str(e)}")
        
        upgrade_text = """💎 Choose Your Premium Plan

Select the plan that best fits your gaming needs:

📊 All plans include:
• Access to premium gaming channel
• Exclusive gaming strategies
• Priority support
• Advanced analytics
• VIP community access"""
        
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
        
        await query.edit_message_text(upgrade_text, reply_markup=reply_markup)
    
    async def process_plan_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                payment_text = f"""💳 Payment Details

📦 Plan: {plan_info['name']}
💰 Amount: ₦{price_naira:.0f}
⏱️ Duration: {plan_info['duration_days']} days

🔗 Click the button below to complete your payment

⚡ After successful payment, wait 30 seconds then click "I've Paid" to verify and get instant access!"""
                
                keyboard = [
                    [InlineKeyboardButton("💳 Pay Now", url=payment_result['link'])],
                    [InlineKeyboardButton("✅ I've Paid - Verify", callback_data=f"verify_{payment_result['tx_ref']}")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="upgrade")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(payment_text, reply_markup=reply_markup)
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
                    
                    success_text = f"""✅ Payment Successful!

🎉 Welcome to Premium Gaming!

📦 Plan: {plan_info['name']}
📅 Valid Until: {end_date.strftime('%B %d, %Y at %H:%M UTC')}

🔗 Premium Channel Access: {self.config.PREMIUM_CHANNEL_LINK}

🎮 You now have access to:
• Exclusive gaming strategies
• Advanced tips and predictions
• VIP community
• Priority support

Enjoy your premium experience! 🚀"""
                    
                    keyboard = [[InlineKeyboardButton("🎮 Join Premium Channel", url=self.config.PREMIUM_CHANNEL_LINK)]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await query.edit_message_text(success_text, reply_markup=reply_markup)
                    
                except KeyError as e:
                    logger.error(f"Missing key in payment verification response: {str(e)}")
                    await query.edit_message_text(
                        "✅ Payment successful but there was an error processing your subscription. "
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
    
    async def learn_more(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        info_text = """📚 About Premium Gaming Bot

🎯 **Our Mission:** To provide gamers with the most accurate and valuable gaming insights.

💎 **Premium Features:**
• 90%+ accuracy rate on predictions
• Daily exclusive gaming tips
• Advanced strategy guides
• VIP community access
• Priority customer support
• Weekly bonus content
• Early access to new games
• Premium analytics dashboard

📊 **Success Rate:** Our premium members report 3x better gaming performance

🔒 **Secure Payments:** All transactions processed through trusted Flutterwave gateway with bank-level security

💪 **Community:** Join 1000+ satisfied premium members in our exclusive community

🎮 **What Makes Us Different:**
• Professional gaming analysts
• Real-time market insights
• Proven track record
• 24/7 customer support
• Mobile-friendly platform

🏆 **Join the winning team today!**"""
        
        keyboard = [
            [InlineKeyboardButton("💎 Upgrade Now", callback_data="upgrade")],
            [InlineKeyboardButton("📋 View Plans", callback_data="upgrade")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(info_text, reply_markup=reply_markup)
    
    async def support(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        support_text = """📞 Customer Support

Need help? We're here for you 24/7!

**Contact Methods:**
💬 Telegram: @blessednwaoma
📱 WhatsApp: +2347042551379
📧 Email: blessednwaoma7@gmail.com

**Support Hours:** 24/7 Available
**Response Time:** Within 1 hour

**Common Issues We Help With:**
• Payment problems
• Channel access issues
• Subscription questions
• Technical support
• Account management
• Billing inquiries

**Quick Tips:**
• Include your user ID when contacting support
• Describe your issue clearly
• Mention any error messages you see

We're committed to providing excellent customer service!"""
        
        keyboard = [
            [InlineKeyboardButton("💬 Contact on Telegram", url="https://t.me/blessednwaoma")],
            [InlineKeyboardButton("📱 WhatsApp Support", url="https://wa.me/2347042551379")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(support_text, reply_markup=reply_markup)
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            elif query.data == "status":
                user = query.from_user
                # Create a mock update for status command
                mock_update = Update(
                    update_id=query.update.update_id,
                    message=query.message
                )
                mock_update.effective_user = user
                await self.status_command(mock_update, context)
            elif query.data == "back_to_menu":
                user = query.from_user
                welcome_text = f"""🎮 Welcome to Premium Gaming Bot!

Hello {user.first_name}! 

Ready to upgrade your gaming experience?

📚 Use /help to see all available commands"""
                
                keyboard = [
                    [InlineKeyboardButton("💎 Upgrade to Premium", callback_data="upgrade")],
                    [InlineKeyboardButton("📊 Check Status", callback_data="status")],
                    [InlineKeyboardButton("ℹ️ Learn More", callback_data="learn_more")],
                    [InlineKeyboardButton("📞 Support", callback_data="support")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(welcome_text, reply_markup=reply_markup)
            else:
                await query.answer("❌ Unknown action.")
        except Exception as e:
            logger.error(f"Error handling button {query.data}: {str(e)}")
            await query.answer("❌ Something went wrong. Please try again.")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            health_status = {
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service": "Premium Gaming Bot"
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
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info("Starting Premium Gaming Bot...")
    
    # Initialize bot
    bot = PremiumBot(CONFIG)
    logger.info("Bot initialized successfully")
    
    # Build application with conflict resolution
    application = Application.builder().token(CONFIG.BOT_TOKEN).build()
    bot.application = application
    logger.info("Telegram application created successfully")
    
    # Add command handlers
    application.add_handler(CommandHandler("start", bot.start_command))
    application.add_handler(CommandHandler("upgrade", bot.upgrade_command))
    application.add_handler(CommandHandler("status", bot.status_command))
    application.add_handler(CommandHandler("plans", bot.plans_command))
    application.add_handler(CommandHandler("support", bot.support_command))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("contact", bot.contact_command))
    application.add_handler(CommandHandler("premium", bot.premium_command))
    
    # Add callback query handler
    application.add_handler(CallbackQueryHandler(bot.button_callback))
    
    # Start health check server in background
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    logger.info("Premium Gaming Bot started successfully!")
    print("Premium Gaming Bot is running...")
    print(f"Health check server running on port {CONFIG.PORT}")
    print("\n🤖 Available Commands:")
    for cmd in BOT_COMMANDS:
        print(f"  /{cmd.command} - {cmd.description}")
    
    # Main loop with conflict handling and restart capability
    max_retries = 5
    retry_count = 0
    
    while not shutdown_flag and retry_count < max_retries:
        try:
            logger.info(f"Starting bot polling (attempt {retry_count + 1}/{max_retries})")
            
            # Setup bot commands
            async def setup_commands():
                await bot.setup_bot_commands()
            
            # Run setup in the application's context
            application.job_queue.run_once(lambda context: setup_commands(), when=0)
            
            application.run_polling(
                drop_pending_updates=True,
                close_loop=False,
                stop_signals=None
            )
            break
            
        except Conflict as e:
            retry_count += 1
            logger.warning(f"Telegram conflict detected: {str(e)}")
            if retry_count < max_retries:
                wait_time = min(retry_count * 10, 60)  # Progressive backoff, max 60 seconds
                logger.info(f"Waiting {wait_time} seconds before retry {retry_count + 1}/{max_retries}")
                time.sleep(wait_time)
            else:
                logger.error("Max retries reached. Please ensure no other bot instances are running.")
                break
                
        except (NetworkError, TimedOut) as e:
            retry_count += 1
            logger.warning(f"Network error: {str(e)}")
            if retry_count < max_retries:
                logger.info(f"Retrying in 30 seconds... (attempt {retry_count + 1}/{max_retries})")
                time.sleep(30)
            else:
                logger.error("Max retries reached due to network errors.")
                break
                
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            break
    
    logger.info("Bot stopped")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}") to Menu", callback_data="back_to_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(upgrade_text, reply_markup=reply_markup)
    
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
                    status_text = f"""✅ Premium Subscription Active

👤 User: {user.first_name}
🎯 Plan: {user_data['subscription_plan'].title()}
📅 Started: {start_date.strftime('%B %d, %Y')}
⏰ Expires: {end_date.strftime('%B %d, %Y at %H:%M UTC')}
📊 Days Remaining: {days_remaining} days

🎮 Premium Channel: {self.config.PREMIUM_CHANNEL_LINK}"""
                else:
                    status_text = f"""❌ Premium Subscription Expired

👤 User: {user.first_name}
🎯 Last Plan: {user_data['subscription_plan'].title()}
📅 Expired: {end_date.strftime('%B %d, %Y at %H:%M UTC')}

💎 Renew your subscription to regain access to premium features!"""
            except Exception as e:
                logger.error(f"Error parsing subscription dates: {str(e)}")
                status_text = "❌ Error retrieving subscription status. Please contact support."
        else:
            status_text = f"""📊 Subscription Status

👤 User: {user.first_name}
💎 Status: Free User
🎯 Premium: Not Active

🚀 Upgrade to premium to unlock exclusive gaming content and features!"""
        
        keyboard = [
            [InlineKeyboardButton("💎 Upgrade Now", callback_data="upgrade")],
            [InlineKeyboardButton("📞 Support", callback_data="support")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(status_text, reply_markup=reply_markup)
    
    async def plans_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /plans command"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        plans_text = """📋 Premium Subscription Plans

Choose the perfect plan for your gaming needs:

"""
        
        for plan_id, plan_info in PLANS.items():
            price_naira = plan_info['amount'] / 100
            plans_text += f"""💎 **{plan_info['name']}**
💰 Price: ₦{price_naira:.0f}
⏰ Duration: {plan_info['duration_days']} days
💸 Daily Cost: ₦{price_naira/plan_info['duration_days']:.2f}

"""
        
        plans_text += """✨ All plans include:
• Exclusive gaming strategies
• Premium predictions
• VIP community access
• Priority customer support
• Advanced analytics
• Early access to content"""
        
        keyboard = [
            [InlineKeyboardButton("💎 Subscribe Now", callback_data="upgrade")],
            [InlineKeyboardButton("📊 Check Status", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(plans_text, reply_markup=reply_markup)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        help_text = """❓ Help & Commands Guide

**Available Commands:**
/start - 🚀 Start the bot and see main menu
/upgrade - 💎 View premium subscription plans
/status - 📊 Check your subscription status
/plans - 📋 View all available plans
/support - 📞 Get customer support
/help - ❓ Show this help message
/contact - 📧 Get contact information
/premium - 🎮 Get premium channel link

**About Premium Gaming Bot:**
🎯 Mission: Provide accurate gaming insights and strategies
💎 Premium Features: 90%+ accuracy predictions, exclusive tips, VIP community
🔒 Secure: Flutterwave payment processing
📊 Success Rate: 3x better gaming performance for premium members

**Getting Started:**
1. Use /plans to see subscription options
2. Use /upgrade to subscribe
3. Complete payment via secure link
4. Get instant access to premium content

**Need Help?**
Use /support or /contact for assistance!"""
        
        keyboard = [
            [InlineKeyboardButton("💎 Upgrade Now", callback_data="upgrade")],
            [InlineKeyboardButton("📞 Support", callback_data="support")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(help_text, reply_markup=reply_markup)
    
    async def support_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /support command"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        support_text = """📞 Customer Support

Need help? We're here for you 24/7!

**Contact Methods:**
💬 Telegram: @blessednwaoma
📱 WhatsApp: +2347042551379
📧 Email: blessednwaoma7@gmail.com

**Support Hours:** 24/7 Available
**Response Time:** Within 1 hour

**Common Issues We Help With:**
• Payment problems
• Channel access issues
• Subscription questions
• Technical support
• Account management
• Billing inquiries

**Quick Tips:**
• Include your user ID when contacting support
• Describe your issue clearly
• Mention any error messages you see

We're committed to providing excellent customer service!"""
        
        keyboard = [
            [InlineKeyboardButton("💬 Contact on Telegram", url="https://t.me/blessednwaoma")],
            [InlineKeyboardButton("📱 WhatsApp Support", url="https://wa.me/2347042551379")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(support_text, reply_markup=reply_markup)
    
    async def contact_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /contact command"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        contact_text = """📧 Contact Information

**Get in touch with us:**

**Primary Contact:**
👤 Name: Blessed Nwaoma
💬 Telegram: @blessednwaoma
📱 WhatsApp: +2347042551379
📧 Email: blessednwaoma7@gmail.com

**Response Times:**
• Telegram: Instant - 30 minutes
• WhatsApp: 5 minutes - 1 hour
• Email: 1 - 6 hours

**Best Contact Method:**
💬 Telegram for fastest response!

**Business Hours:**
🕐 Available: 24/7
🌍 Timezone: WAT (West Africa Time)

Feel free to reach out for any questions, support, or feedback!"""
        
        keyboard = [
            [InlineKeyboardButton("💬 Message on Telegram", url="https://t.me/blessednwaoma")],
            [InlineKeyboardButton("📱 WhatsApp Chat", url="https://wa.me/2347042551379")],
            [InlineKeyboardButton("📞 Support", callback_data="support")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(contact_text, reply_markup=reply_markup)
    
    async def premium_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /premium command"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        user_data = self.db.get_user(user.id)
        
        if user_data and user_data['is_premium']:
            try:
                end_date = datetime.fromisoformat(user_data['subscription_end'])
                current_time = datetime.now(timezone.utc)
                
                if end_date > current_time:
                    premium_text = f"""🎮 Premium Channel Access

✅ Your premium subscription is active!

🔗 **Premium Channel Link:**
{self.config.PREMIUM_CHANNEL_LINK}

📊 **Your Status:**
• Plan: {user_data['subscription_plan'].title()}
• Expires: {end_date.strftime('%B %d, %Y')}

Enjoy exclusive gaming content! 🚀"""
                    
                    keyboard = [
                        [InlineKeyboardButton("🎮 Join Premium Channel", url=self.config.PREMIUM_CHANNEL_LINK)],
                        [InlineKeyboardButton("📊 Check Status", callback_data="status")]
                    ]
                else:
                    premium_text = """❌ Premium Access Expired

Your premium subscription has expired. Renew now to regain access to exclusive gaming content!"""
                    
                    keyboard = [
                        [InlineKeyboardButton("💎 Renew Subscription", callback_data="upgrade")],
                        [InlineKeyboardButton("📋 View Plans", callback_data="plans")]
                    ]
            except Exception as e:
                logger.error(f"Error checking premium status: {str(e)}")
                premium_text = "❌ Error checking premium status. Please contact support."
                keyboard = [[InlineKeyboardButton("📞 Support", callback_data="support")]]
        else:
            premium_text = """🎮 Premium Channel Access

❌ You don't have an active premium subscription.

Upgrade now to access:
• Exclusive gaming strategies
• Premium predictions
• VIP community
• Advanced analytics
• Priority support"""
            
            keyboard = [
                [InlineKeyboardButton("💎 Upgrade Now", callback_data="upgrade")],
                [InlineKeyboardButton("📋 View Plans", callback_data="plans")]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(premium_text, reply_markup=reply_markup)
    
    async def upgrade_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                        f"🎯 Plan: {user_data['subscription_plan'].title()}\n\n"
                        f"🎮 Premium Channel: {self.config.PREMIUM_CHANNEL_LINK}"
                    )
                    return
            except Exception as e:
                logger.error(f"Error parsing subscription date: {str(e)}")
        
        upgrade_text = """💎 Choose Your Premium Plan

Select the plan that best fits your gaming needs:

📊 All plans include:
• Access to premium gaming channel
• Exclusive gaming strategies
• Priority support
• Advanced analytics
• VIP community access"""
        
        keyboard = []
        for plan_id, plan_info in PLANS.items():
            price_naira = plan_info['amount'] / 100
            keyboard.append([
                InlineKeyboardButton(
                    f"{plan_info['name']} - ₦{price_naira:.0f}",
                    callback_data=f"plan_{plan_id}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("⬅️ Back