#!/usr/bin/env python3
import os
import logging
import asyncio
import sqlite3
import json
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv

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
    
    required_fields = ['BOT_TOKEN', 'FLUTTERWAVE_SECRET_KEY', 'FLUTTERWAVE_PUBLIC_KEY', 
                      'PREMIUM_CHANNEL_ID', 'PREMIUM_CHANNEL_LINK']
    
    missing_fields = [field for field in required_fields if not getattr(config, field)]
    if missing_fields:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_fields)}")
    
    return config

# Load config
CONFIG = load_config()

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
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
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
                ''', (user_id, username, first_name, datetime.now(timezone.utc).isoformat()))
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

class PremiumBot:
    def __init__(self, config: Config):
        self.config = config
        self.db = DatabaseManager(config.DATABASE_PATH)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
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

Ready to upgrade your gaming experience?
        """
        
        keyboard = [
            [InlineKeyboardButton("🚀 Upgrade to Premium", callback_data="upgrade")],
            [InlineKeyboardButton("ℹ️ Learn More", callback_data="learn_more")],
            [InlineKeyboardButton("📞 Support", callback_data="support")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def upgrade_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
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
        query = update.callback_query
        await query.answer()
        
        plan_id = query.data.split('_')[1]
        plan_info = PLANS.get(plan_id)
        
        if not plan_info:
            await query.edit_message_text("❌ Invalid plan selected. Please try again.")
            return
        
        price_naira = plan_info['amount'] / 100
        payment_message = f"""
💳 **Payment Details**

📦 **Plan**: {plan_info['name']}
💰 **Amount**: ₦{price_naira:.0f}
⏱️ **Duration**: {plan_info['duration_days']} days

🔗 **Payment Link Coming Soon!**

For now, please contact support to complete your payment.
        """
        
        keyboard = [
            [InlineKeyboardButton("📞 Contact Support", callback_data="support")],
            [InlineKeyboardButton("⬅️ Back", callback_data="upgrade")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(payment_message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def learn_more(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

📊 **Success Rate**: Our premium members report 3x better gaming performance

🔒 **Secure**: All payments processed through trusted payment gateway

💪 **Community**: Join 1000+ satisfied premium members
        """
        
        keyboard = [
            [InlineKeyboardButton("🚀 Upgrade Now", callback_data="upgrade")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(info_message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def support(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        support_message = """
📞 **Customer Support**

Need help? We're here for you!

🕐 **Support Hours**: 24/7
📧 **Email**: support@yourgamingbot.com
💬 **Telegram**: @your_support_bot

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
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        
        try:
            if query.data == "upgrade":
                await self.upgrade_menu(update, context)
            elif query.data.startswith("plan_"):
                await self.process_plan_selection(update, context)
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

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == '/health':
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
        pass

def run_health_server():
    try:
        server = HTTPServer(('0.0.0.0', CONFIG.PORT), HealthHandler)
        logger.info(f"Health check server started on port {CONFIG.PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server error: {str(e)}")

async def main():
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
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CallbackQueryHandler(bot.button_handler))
    
    # Start health check server
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    logger.info("Premium Gaming Bot started successfully!")
    print("Premium Gaming Bot is running...")
    print(f"Health check server running on port {CONFIG.PORT}")
    
    try:
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
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")