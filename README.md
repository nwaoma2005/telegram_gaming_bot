import os
import logging
import asyncio
import sqlite3
import psycopg2
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import requests
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import json
from dotenv import load_dotenv
from urllib.parse import urlparse
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
FLUTTERWAVE_SECRET_KEY = os.getenv("FLUTTERWAVE_SECRET_KEY")
FLUTTERWAVE_PUBLIC_KEY = os.getenv("FLUTTERWAVE_PUBLIC_KEY")
PREMIUM_CHANNEL_ID = os.getenv("PREMIUM_CHANNEL_ID")
PREMIUM_CHANNEL_LINK = os.getenv("PREMIUM_CHANNEL_LINK")
DATABASE_URL = os.getenv("DATABASE_URL")  # For PostgreSQL if needed
PORT = int(os.getenv("PORT", 8000))

# Subscription plans (amounts in kobo - 100 kobo = ₦1)
PLANS = {
    "daily": {"name": "Daily Plan", "amount": 100, "duration_days": 1},
    "weekly": {"name": "Weekly Plan", "amount": 500, "duration_days": 7},
    "monthly": {"name": "Monthly Plan", "amount": 1500, "duration_days": 30},
    "yearly": {"name": "Yearly Plan", "amount": 15000, "duration_days": 365}
}

class DatabaseManager:
    def __init__(self):
        self.db_url = DATABASE_URL
        if self.db_url and self.db_url.startswith('postgresql'):
            self.use_postgresql = True
            logger.info("Using PostgreSQL database")
            self.init_postgresql()
        else:
            self.use_postgresql = False
            self.db_path = "premium_bot.db"
            logger.info("Using SQLite database")
            self.init_sqlite()
    
    def get_connection(self):
        """Get database connection"""
        if self.use_postgresql:
            return psycopg2.connect(self.db_url)
        else:
            return sqlite3.connect(self.db_path)
    
    def init_postgresql(self):
        """Initialize PostgreSQL database"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    subscription_plan TEXT,
                    subscription_start TIMESTAMP,
                    subscription_end TIMESTAMP,
                    is_premium BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Payments table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    transaction_ref TEXT UNIQUE,
                    amount REAL,
                    plan_type TEXT,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("PostgreSQL database initialized successfully")
            
        except Exception as e:
            logger.error(f"PostgreSQL initialization error: {str(e)}")
            # Fallback to SQLite
            self.use_postgresql = False
            self.db_path = "premium_bot.db"
            self.init_sqlite()
    
    def init_sqlite(self):
        """Initialize SQLite database"""
        try:
            # Create database directory if it doesn't exist
            db_dir = os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else '.'
            if not os.path.exists(db_dir):
                os.makedirs(db_dir)
                
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    subscription_plan TEXT,
                    subscription_start DATE,
                    subscription_end DATE,
                    is_premium BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Payments table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    transaction_ref TEXT UNIQUE,
                    amount REAL,
                    plan_type TEXT,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("SQLite database initialized successfully")
            
        except Exception as e:
            logger.error(f"SQLite initialization error: {str(e)}")
    
    def add_user(self, user_id: int, username: str = None, first_name: str = None):
        """Add or update user in database"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if self.use_postgresql:
                cursor.execute('''
                    INSERT INTO users (user_id, username, first_name)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name
                ''', (user_id, username, first_name))
            else:
                cursor.execute('''
                    INSERT OR REPLACE INTO users (user_id, username, first_name)
                    VALUES (?, ?, ?)
                ''', (user_id, username, first_name))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error adding user {user_id}: {str(e)}")
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user information"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if self.use_postgresql:
                cursor.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
            else:
                cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                columns = ['user_id', 'username', 'first_name', 'subscription_plan', 
                          'subscription_start', 'subscription_end', 'is_premium', 'created_at']
                return dict(zip(columns, row))
            return None
            
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {str(e)}")
            return None
    
    def update_subscription(self, user_id: int, plan: str, start_date: datetime, end_date: datetime):
        """Update user subscription"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if self.use_postgresql:
                cursor.execute('''
                    UPDATE users 
                    SET subscription_plan = %s, subscription_start = %s, subscription_end = %s, is_premium = TRUE
                    WHERE user_id = %s
                ''', (plan, start_date, end_date, user_id))
            else:
                cursor.execute('''
                    UPDATE users 
                    SET subscription_plan = ?, subscription_start = ?, subscription_end = ?, is_premium = TRUE
                    WHERE user_id = ?
                ''', (plan, start_date, end_date, user_id))
            
            conn.commit()
            conn.close()
            logger.info(f"Updated subscription for user {user_id}: {plan}")
            
        except Exception as e:
            logger.error(f"Error updating subscription for user {user_id}: {str(e)}")
    
    def expire_user_subscription(self, user_id: int):
        """Expire user subscription"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if self.use_postgresql:
                cursor.execute('''
                    UPDATE users 
                    SET is_premium = FALSE, subscription_plan = NULL
                    WHERE user_id = %s
                ''', (user_id,))
            else:
                cursor.execute('''
                    UPDATE users 
                    SET is_premium = FALSE, subscription_plan = NULL
                    WHERE user_id = ?
                ''', (user_id,))
            
            conn.commit()
            conn.close()
            logger.info(f"Expired subscription for user {user_id}")
            
        except Exception as e:
            logger.error(f"Error expiring subscription for user {user_id}: {str(e)}")
    
    def get_expired_users(self) -> list:
        """Get users with expired subscriptions"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            current_time = datetime.now()
            if self.use_postgresql:
                cursor.execute('''
                    SELECT user_id FROM users 
                    WHERE is_premium = TRUE AND subscription_end < %s
                ''', (current_time,))
            else:
                cursor.execute('''
                    SELECT user_id FROM users 
                    WHERE is_premium = TRUE AND subscription_end < ?
                ''', (current_time,))
            
            expired_users = [row[0] for row in cursor.fetchall()]
            conn.close()
            return expired_users
            
        except Exception as e:
            logger.error(f"Error getting expired users: {str(e)}")
            return []
    
    def add_payment_record(self, user_id: int, transaction_ref: str, amount: float, plan_type: str):
        """Add payment record"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if self.use_postgresql:
                cursor.execute('''
                    INSERT INTO payments (user_id, transaction_ref, amount, plan_type, status)
                    VALUES (%s, %s, %s, %s, 'pending')
                ''', (user_id, transaction_ref, amount, plan_type))
            else:
                cursor.execute('''
                    INSERT INTO payments (user_id, transaction_ref, amount, plan_type, status)
                    VALUES (?, ?, ?, ?, 'pending')
                ''', (user_id, transaction_ref, amount, plan_type))
            
            conn.commit()
            conn.close()
            logger.info(f"Added payment record: {transaction_ref}")
            
        except Exception as e:
            logger.error(f"Error adding payment record: {str(e)}")
    
    def update_payment_status(self, transaction_ref: str, status: str):
        """Update payment status"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if self.use_postgresql:
                cursor.execute('''
                    UPDATE payments SET status = %s WHERE transaction_ref = %s
                ''', (status, transaction_ref))
            else:
                cursor.execute('''
                    UPDATE payments SET status = ? WHERE transaction_ref = ?
                ''', (status, transaction_ref))
            
            conn.commit()
            conn.close()
            logger.info(f"Updated payment status: {transaction_ref} -> {status}")
            
        except Exception as e:
            logger.error(f"Error updating payment status: {str(e)}")

class FlutterwavePayment:
    def __init__(self):
        self.base_url = "https://api.flutterwave.com/v3"
        self.secret_key = FLUTTERWAVE_SECRET_KEY
        self.public_key = FLUTTERWAVE_PUBLIC_KEY
    
    def create_payment_link(self, user_id: int, amount: float, plan_name: str) -> Dict[str, Any]:
        """Create payment link with Flutterwave"""
        try:
            tx_ref = f"premium_bot_{user_id}_{uuid.uuid4().hex[:8]}"
            
            payload = {
                "tx_ref": tx_ref,
                "amount": amount / 100,  # Convert kobo to naira
                "currency": "NGN",
                "redirect_url": "https://webhook.site/unique-id",  # Replace with your webhook
                "meta": {
                    "user_id": user_id,
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
                    "logo": "https://your-logo-url.com/logo.png"
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
            
            if response.status_code == 200:
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
            else:
                logger.error(f"Flutterwave API HTTP error {response.status_code}: {response.text}")
                return {"status": "error", "message": "Payment service temporarily unavailable"}
                
        except requests.exceptions.Timeout:
            logger.error("Flutterwave API timeout")
            return {"status": "error", "message": "Payment service timeout"}
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
            
            if response.status_code == 200:
                data = response.json()
                return data
            else:
                logger.error(f"Payment verification HTTP error {response.status_code}: {response.text}")
                return {"status": "error", "message": "Verification failed"}
                
        except requests.exceptions.Timeout:
            logger.error("Payment verification timeout")
            return {"status": "error", "message": "Verification timeout"}
        except Exception as e:
            logger.error(f"Payment verification error: {str(e)}")
            return {"status": "error", "message": "Verification service unavailable"}

class PremiumBot:
    def __init__(self):
        self.db = DatabaseManager()
        self.payment = FlutterwavePayment()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command handler"""
        user = update.effective_user
        self.db.add_user(user.id, user.username, user.first_name)
        
        welcome_message = f"""
🎮 **Welcome to Premium Gaming Bot!** 🎮

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
        
        await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def upgrade_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show upgrade options"""
        query = update.callback_query
        await query.answer()
        
        user_data = self.db.get_user(query.from_user.id)
        
        if user_data and user_data['is_premium']:
            try:
                if isinstance(user_data['subscription_end'], str):
                    end_date = datetime.strptime(user_data['subscription_end'], '%Y-%m-%d %H:%M:%S')
                else:
                    end_date = user_data['subscription_end']
                    
                await query.edit_message_text(
                    f"✅ You already have an active premium subscription!\n"
                    f"📅 Expires: {end_date.strftime('%B %d, %Y at %H:%M')}\n"
                    f"🎯 Plan: {user_data['subscription_plan'].title()}"
                )
                return
            except Exception as e:
                logger.error(f"Error parsing subscription end date: {str(e)}")
        
        upgrade_message = """
💎 **Choose Your Premium Plan** 💎

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
        
        plan_id = query.data.split('_')[1]
        plan_info = PLANS.get(plan_id)
        
        if not plan_info:
            await query.edit_message_text("❌ Invalid plan selected. Please try again.")
            return
        
        user_id = query.from_user.id
        
        await query.edit_message_text("🔄 Creating payment link... Please wait.")
        
        # Create payment link
        payment_result = self.payment.create_payment_link(
            user_id, 
            plan_info['amount'], 
            plan_id
        )
        
        if payment_result['status'] == 'success':
            # Store payment record
            self.db.add_payment_record(
                user_id, 
                payment_result['tx_ref'], 
                plan_info['amount'], 
                plan_id
            )
            
            price_naira = plan_info['amount'] / 100
            payment_message = f"""
💳 **Payment Details** 💳

📦 **Plan**: {plan_info['name']}
💰 **Amount**: ₦{price_naira:.0f}
⏱️ **Duration**: {plan_info['duration_days']} days

🔗 **Click the button below to complete your payment**

⚡ After successful payment, click "I've Paid" to verify and get instant access!
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
    
    async def verify_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Verify payment and grant access"""
        query = update.callback_query
        await query.answer()
        
        tx_ref = query.data.split('_', 1)[1]
        user_id = query.from_user.id
        
        await query.edit_message_text("🔄 Verifying your payment... Please wait.")
        
        # Verify payment with Flutterwave
        verification_result = self.payment.verify_payment(tx_ref)
        
        if (verification_result.get('status') == 'success' and 
            verification_result.get('data', {}).get('status') == 'successful'):
            
            # Payment successful - grant access
            try:
                plan_type = verification_result['data']['meta']['plan']
                plan_info = PLANS.get(plan_type)
                
                if not plan_info:
                    await query.edit_message_text("❌ Invalid plan in payment data. Contact support.")
                    return
                
                start_date = datetime.now()
                end_date = start_date + timedelta(days=plan_info['duration_days'])
                
                self.db.update_subscription(user_id, plan_type, start_date, end_date)
                self.db.update_payment_status(tx_ref, 'completed')
                
                success_message = f"""
✅ **Payment Successful!** ✅

🎉 Welcome to Premium Gaming!

📦 **Plan**: {plan_info['name']}
📅 **Valid Until**: {end_date.strftime('%B %d, %Y at %H:%M')}

🔗 **Premium Channel Access**: {PREMIUM_CHANNEL_LINK}

🎮 You now have access to:
• Exclusive gaming strategies
• Advanced tips and predictions
• VIP community
• Priority support

Enjoy your premium experience! 🚀
                """
                
                keyboard = [[InlineKeyboardButton("🎮 Join Premium Channel", url=PREMIUM_CHANNEL_LINK)]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(success_message, reply_markup=reply_markup, parse_mode='Markdown')
                
            except Exception as e:
                logger.error(f"Error processing successful payment: {str(e)}")
                await query.edit_message_text(
                    "✅ Payment successful but there was an error setting up your account. "
                    "Please contact support with your payment reference.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📞 Contact Support", callback_data="support")]])
                )
                
        else:
            # Payment not successful or pending
            await query.edit_message_text(
                "❌ Payment verification failed or payment is still pending.\n\n"
                "If you've already paid, please wait a few minutes and try verifying again.\n"
                "If the problem persists, contact support.",
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
                if isinstance(user_data['subscription_end'], str):
                    end_date = datetime.strptime(user_data['subscription_end'], '%Y-%m-%d %H:%M:%S')
                else:
                    end_date = user_data['subscription_end']
                    
                current_time = datetime.now()
                
                if end_date > current_time:
                    time_left = end_date - current_time
                    days_left = time_left.days
                    
                    status_message = f"""
✅ **Premium Subscription Active** ✅

📦 **Plan**: {user_data['subscription_plan'].title()}
📅 **Expires**: {end_date.strftime('%B %d, %Y at %H:%M')}
⏰ **Time Left**: {days_left} days

🎮 **Premium Channel**: {PREMIUM_CHANNEL_LINK}
                    """
                    
                    keyboard = [[InlineKeyboardButton("🎮 Access Premium Channel", url=PREMIUM_CHANNEL_LINK)]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                else:
                    # Subscription expired
                    self.db.expire_user_subscription(user_id)
                    status_message = """
❌ **Subscription Expired** ❌

Your premium subscription has expired. Upgrade now to regain access to premium features!
                    """
                    
                    keyboard = [[InlineKeyboardButton("🚀 Upgrade Now", callback_data="upgrade")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
            except Exception as e:
                logger.error(f"Error parsing subscription date: {str(e)}")
                status_message = """
❌ **Error checking subscription** ❌

There was an error checking your subscription status. Please contact support.
                """
                keyboard = [[InlineKeyboardButton("📞 Contact Support", callback_data="support")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            status_message = """
📋 **Free Account** 📋

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
        except Exception as e:
            logger.error(f"Error handling button {query.data}: {str(e)}")
            await query.answer("❌ Something went wrong. Please try again.")
    
    async def learn_more(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show more information about the service"""
        query = update.callback_query
        await query.answer()
        
        info_message = """
📚 **About Premium Gaming Bot** 📚

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
📞 **Customer Support** 📞

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

We typically respond within 1 hour
keyboard = [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(support_message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def check_expired_subscriptions(self, context: ContextTypes.DEFAULT_TYPE):
        """Background task to check and handle expired subscriptions"""
        expired_users = self.db.get_expired_users()
        
        for user_id in expired_users:
            try:
                # Expire the subscription in database
                self.db.expire_user_subscription(user_id)
                
                # Send expiration notification
                await context.bot.send_message(
                    chat_id=user_id,
                    text="""
⏰ **Subscription Expired** ⏰

Your premium subscription has expired. You no longer have access to the premium channel.

🚀 **Renew Now** to continue enjoying premium features!
                    """,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🚀 Renew Subscription", callback_data="upgrade")]
                    ]),
                    parse_mode='Markdown'
                )
                
                # Try to remove from premium channel (optional)
                if PREMIUM_CHANNEL_ID:
                    try:
                        await context.bot.ban_chat_member(PREMIUM_CHANNEL_ID, user_id)
                        await context.bot.unban_chat_member(PREMIUM_CHANNEL_ID, user_id)
                        logger.info(f"Removed expired user {user_id} from premium channel")
                    except Exception as e:
                        logger.error(f"Failed to remove user {user_id} from premium channel: {str(e)}")
                
            except Exception as e:
                logger.error(f"Error processing expired user {user_id}: {str(e)}")

# Health check endpoint for Render
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running!')
    
    def log_message(self, format, *args):
        # Suppress HTTP server logs
        pass

def run_health_server():
    """Run health check server for Render"""
    try:
        server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
        logger.info(f"Health check server started on port {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server error: {str(e)}")

async def main():
    """Start the bot"""
    # Validate required environment variables
    required_vars = {
        'BOT_TOKEN': BOT_TOKEN,
        'FLUTTERWAVE_SECRET_KEY': FLUTTERWAVE_SECRET_KEY,
        'FLUTTERWAVE_PUBLIC_KEY': FLUTTERWAVE_PUBLIC_KEY,
        'PREMIUM_CHANNEL_ID': PREMIUM_CHANNEL_ID,
        'PREMIUM_CHANNEL_LINK': PREMIUM_CHANNEL_LINK
    }
    
    missing_vars = [var for var, value in required_vars.items() if not value]
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        print(f"❌ Error: Missing required environment variables: {', '.join(missing_vars)}")
        print("Please check your environment variables and try again.")
        return
    
    logger.info("All required environment variables found")
    
    # Initialize bot
    try:
        bot = PremiumBot()
        logger.info("Bot initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize bot: {str(e)}")
        return
    
    # Create application
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        logger.info("Telegram application created successfully")
    except Exception as e:
        logger.error(f"Failed to create Telegram application: {str(e)}")
        return
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("status", bot.check_subscription_status))
    application.add_handler(CommandHandler("help", bot.start))  # Alias for start
    application.add_handler(CallbackQueryHandler(bot.button_handler))
    
    # Schedule background task to check expired subscriptions every hour
    application.job_queue.run_repeating(bot.check_expired_subscriptions, interval=3600, first=10)
    
    # Start health check server in background (required for Render)
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Start the bot
    logger.info("🚀 Premium Gaming Bot started successfully!")
    print("🎮 Premium Gaming Bot is running...")
    print(f"💡 Health check server running on port {PORT}")
    print("✅ Bot is ready to accept users!")
    
    try:
        await application.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error(f"Bot polling error: {str(e)}")
    finally:
        logger.info("Bot stopped")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        print("\n👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        print(f"\n❌ Fatal error: {str(e)}")