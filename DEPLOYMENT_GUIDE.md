# 🚀 Complete Deployment Guide

## File Structure
Your GitHub repository should have this structure:
```
premium-gaming-bot/
├── bot.py                    # Main bot file
├── requirements.txt          # Python dependencies
├── render.yaml              # Render deployment config
├── Procfile                 # Alternative deployment config
├── .env.example             # Environment template
├── .gitignore               # Git ignore rules
├── README.md                # Documentation
├── DEPLOYMENT_GUIDE.md      # This file
└── .github/
    └── workflows/
        └── keep-alive.yml   # Keep bot awake (optional)
```

## 📋 Step-by-Step Deployment

### Step 1: Prepare Your Files
1. Save each artifact as a separate file with the exact names shown above
2. Create the `.github/workflows/` directory structure
3. **DO NOT** create a `.env` file in your repository (security risk)

### Step 2: Create GitHub Repository
```bash
# Initialize git repository
git init
git add .
git commit -m "Initial commit: Premium Gaming Bot"

# Create repository on GitHub and push
git remote add origin https://github.com/yourusername/premium-gaming-bot.git
git branch -M main
git push -u origin main
```

### Step 3: Get Required Credentials

#### 🤖 Bot Token (Required)
1. Open Telegram and message @BotFather
2. Send `/newbot`
3. Choose a name: "Premium Gaming Bot"
4. Choose username: "your_premium_gaming_bot"
5. Copy the token that looks like: `1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ`

#### 💳 Flutterwave API Keys (Required)
1. Go to [flutterwave.com](https://flutterwave.com) and sign up
2. Complete account verification
3. Go to Settings > API Keys
4. Copy both:
   - **Secret Key**: `FLWSECK-xxxxx`
   - **Public Key**: `FLWPUBK-xxxxx`

#### 📺 Premium Channel Setup (Required)
1. Create a new Telegram channel
2. Make it private
3. Add @userinfobot to get the channel ID
4. Create an invite link
5. Add your bot as admin with "Invite Users" permission

### Step 4: Deploy to Render

#### 4.1 Create Render Account
1. Go to [render.com](https://render.com)
2. Sign up with your GitHub account
3. Authorize Render to access your repositories

#### 4.2 Create Web Service
1. Click "New +" > "Web Service"
2. Select your `premium-gaming-bot` repository
3. Configure:
   - **Name**: `premium-gaming-bot`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Plan**: `Free`

#### 4.3 Add Environment Variables
In Render dashboard, go to Environment and add:

| Key | Value | Example |
|-----|-------|---------|
| `BOT_TOKEN` | Your bot token | `1234567890:ABCdefGHI...` |
| `FLUTTERWAVE_SECRET_KEY` | Your secret key | `FLWSECK-xxxxx` |
| `FLUTTERWAVE_PUBLIC_KEY` | Your public key | `FLWPUBK-xxxxx` |
| `PREMIUM_CHANNEL_ID` | Channel ID | `-1001234567890` |
| `PREMIUM_CHANNEL_LINK` | Invite link | `https://t.me/+xxxxx` |

#### 4.4 Deploy
1. Click "Create Web Service"
2. Wait for build to complete (5-10 minutes)
3. Check logs for any errors

### Step 5: Test Your Bot

#### 5.1 Basic Test
1. Find your bot on Telegram
2. Send `/start`
3. Check if welcome message appears

#### 5.2 Payment Test
1. Click "Upgrade to Premium"
2. Select a plan
3. Click "Pay Now" (don't actually pay in test mode)
4. Check if Flutterwave payment page opens

#### 5.3 Status Test
1. Send `/status` command
2. Check if it shows your subscription status

### Step 6: Optional - Keep Bot Awake

#### 6.1 UptimeRobot Method (Recommended)
1. Sign up at [uptimerobot.com](https://uptimerobot.com)
2. Add new monitor:
   - **Type**: HTTP(s)
   - **URL**: Your Render app URL
   - **Interval**: 5 minutes
3. This prevents the 15-minute sleep on free tier

#### 6.2 GitHub Actions Method
1. Go to your repository settings > Secrets
2. Add secret: `RENDER_URL` with your app URL
3. The workflow will ping every 10 minutes

## 🔧 Customization Options

### Modify Subscription Plans
Edit in `bot.py`:
```python
PLANS = {
    "daily": {"name": "Daily Plan", "amount": 200, "duration_days": 1},    # ₦2
    "weekly": {"name": "Weekly Plan", "amount": 1000, "duration_days": 7}, # ₦10
    # Amount in kobo (100 kobo = ₦1)
}
```

### Change Bot Messages
Edit the welcome message and other text in the bot methods.

### Add More Features
- Referral system
- Admin panel
- Analytics dashboard
- Multiple channels

## 🚨 Troubleshooting

### Common Issues:

#### Bot Not Responding
- Check Render logs for errors
- Verify BOT_TOKEN is correct
- Ensure bot is started (@BotFather)

#### Payment Links Not Working
- Verify Flutterwave keys
- Check API key permissions
- Ensure account is verified

#### Channel Access Issues
- Bot must be admin in premium channel
- Check PREMIUM_CHANNEL_ID format (negative number)
- Verify invite link is valid

#### Database Errors
- Default SQLite works for small scale
- For production, add PostgreSQL DATABASE_URL

### View Logs
1. Go to Render dashboard
2. Click your service
3. Go to "Logs" tab
4. Check for error messages

## 💰 Render Free Tier Limits

- **750 hours/month**: About 31 days if always running
- **512 MB RAM**: Sufficient for small-medium bots
- **Sleep after 15 min**: Use UptimeRobot to prevent
- **No persistent storage**: Database resets on restart

For production, consider upgrading to paid plans.

## 🎯 Going Live

### Pre-launch Checklist:
- ✅ All environment variables set
- ✅ Bot tested end-to-end
- ✅ Payment flow verified
- ✅ Premium channel configured
- ✅ UptimeRobot monitoring active
- ✅ Support contact updated
- ✅ Pricing finalized

### Launch:
1. Share bot username with users
2. Promote in gaming communities
3. Monitor logs for issues
4. Collect user feedback
5. Iterate and improve

---

🚀 **Your bot is now live and ready to earn!**