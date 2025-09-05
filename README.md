# Premium Gaming Telegram Bot

A fully-featured Telegram bot for managing premium gaming subscriptions with Flutterwave payment integration.

## 🚀 Features

- **Multi-tier Subscriptions**: Daily, Weekly, Monthly, and Yearly plans
- **Flutterwave Integration**: Secure payment processing
- **Automatic Channel Management**: Auto-add/remove users from premium channels
- **Subscription Tracking**: Monitor and handle subscription expiration
- **Database Support**: SQLite (development) and PostgreSQL (production)
- **Cloud Ready**: Optimized for Render, Heroku, Railway deployment

## 🛠 Setup Instructions

### 1. Clone Repository
```bash
git clone https://github.com/yourusername/premium-gaming-bot.git
cd premium-gaming-bot
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env` and fill in your values:
```bash
cp .env.example .env
```

Required environment variables:
- `BOT_TOKEN`: Get from @BotFather on Telegram
- `FLUTTERWAVE_SECRET_KEY`: From Flutterwave dashboard
- `FLUTTERWAVE_PUBLIC_KEY`: From Flutterwave dashboard  
- `PREMIUM_CHANNEL_ID`: Your premium channel ID
- `PREMIUM_CHANNEL_LINK`: Your premium channel invite link
- `DATABASE_URL`: PostgreSQL URL (optional for production)

### 4. Getting Required Values

#### Bot Token:
1. Message @BotFather on Telegram
2. Create new bot with `/newbot`
3. Copy the token

#### Flutterwave Keys:
1. Sign up at [flutterwave.com](https://flutterwave.com)
2. Go to Settings > API Keys
3. Copy Secret and Public keys

#### Channel ID:
1. Add @userinfobot to your premium channel
2. Copy the channel ID (negative number)

### 5. Local Development
```bash
python bot.py
```

## 🌐 Deployment Options

### Render (Free Tier)
1. Connect GitHub repository to Render
2. Add environment variables in Render dashboard
3. Deploy automatically

### Railway
```bash
railway login
railway init
railway add --database postgresql
railway deploy
```

### Heroku
```bash
heroku create your-bot-name
heroku config:set BOT_TOKEN=your_token
# Add other environment variables
git push heroku main
```

## 📊 Subscription Plans

Default plans (customize amounts in `bot.py`):
- **Daily**: ₦1.00 (1 day access)
- **Weekly**: ₦5.00 (7 days access) 
- **Monthly**: ₦15.00 (30 days access)
- **Yearly**: ₦150.00 (365 days access)

## 🤖 Bot Commands

- `/start` - Welcome message and main menu
- `/status` - Check subscription status

## 🔧 Configuration

Edit `PLANS` dictionary in `bot.py` to modify subscription plans:
```python
PLANS = {
    "daily": {"name": "Daily Plan", "amount": 100, "duration_days": 1},
    # amounts in kobo (100 kobo = ₦1)
}
```

## 📝 Database Schema

### Users Table
- `user_id` (Primary Key)
- `username`
- `first_name`
- `subscription_plan`
- `subscription_start`
- `subscription_end`
- `is_premium`
- `created_at`

### Payments Table
- `id` (Primary Key)
- `user_id` (Foreign Key)
- `transaction_ref`
- `amount`
- `plan_type`
- `status`
- `created_at`

## 🔒 Security Features

- Environment variable protection
- Payment verification
- Database transaction safety
- Error handling and logging

## 🚨 Production Considerations

1. **Database**: Use PostgreSQL for production (set `DATABASE_URL`)
2. **Monitoring**: Set up UptimeRobot for 24/7 availability
3. **Logging**: Configure proper log aggregation
4. **Backup**: Regular database backups
5. **Scaling**: Consider multiple bot instances for high traffic

## 📞 Support

For support and customization:
- Email: your-email@domain.com
- Telegram: @your_support_bot

## 📄 License

MIT License - feel free to modify and distribute.

## 🤝 Contributing

1. Fork the repository
2. Create feature branch
3. Commit changes
4. Push to branch
5. Open Pull Request

---

⭐ **Star this repo if it helped you!**