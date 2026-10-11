#!/usr/bin/env python3
"""
QNAP-OME: Multi-channel Notification System
Sends alerts via Telegram, Email, ntfy, or webhooks.
"""

import os
import sys
import json
import yaml
import logging
import smtplib
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

DATA_DIR = Path(__file__).parent.parent / "data"
CONFIG_DIR = Path(__file__).parent.parent / "config"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_config():
    """Load notification configuration."""
    config_file = CONFIG_DIR / "notifications.yaml"
    if not config_file.exists():
        logger.error(f"Config not found: {config_file}")
        sys.exit(1)
    
    with open(config_file) as f:
        return yaml.safe_load(f)


def load_analysis():
    """Load latest analysis text."""
    analysis_file = DATA_DIR / "latest_analysis.txt"
    if not analysis_file.exists():
        logger.warning("No analysis file found")
        return None
    
    with open(analysis_file) as f:
        return f.read()


def load_anomalies():
    """Load current anomalies."""
    anomaly_file = DATA_DIR / "current_anomalies.json"
    if not anomaly_file.exists():
        return None
    
    with open(anomaly_file) as f:
        return json.load(f)


def format_alert_message(anomalies_data, analysis):
    """Format alert for human consumption."""
    anomalies = anomalies_data['anomalies']
    count = anomalies_data['count']
    timestamp = anomalies_data['detected_at']
    
    # Count by severity
    critical = [a for a in anomalies if a['severity'] == 'critical']
    warnings = [a for a in anomalies if a['severity'] == 'warning']
    
    message = f"""🚨 QNAP-OME Fleet Alert

📊 Summary: {count} anomaly/anomalies detected
⏰ Time: {timestamp}

🔴 CRITICAL ({len(critical)}):
"""
    
    for a in critical:
        message += f"  • {a['server_name']}: {a['message']}\n"
    
    if warnings:
        message += f"\n🟡 WARNINGS ({len(warnings)}):\n"
        for a in warnings:
            message += f"  • {a['server_name']}: {a['message']}\n"
    
    if analysis:
        # Extract urgency from analysis
        urgency = "UNKNOWN"
        if "Urgency: IMMEDIATE" in analysis:
            urgency = "🔥 IMMEDIATE"
        elif "Urgency: SOON" in analysis:
            urgency = "⚠️ SOON"
        elif "Urgency: ROUTINE" in analysis:
            urgency = "📋 ROUTINE"
        
        message += f"\n🎯 Urgency: {urgency}\n"
        message += "\n📝 Analysis:\n"
        message += analysis[:500] + "..." if len(analysis) > 500 else analysis
    
    return message


def send_telegram(config, message):
    """Send alert via Telegram bot."""
    telegram = config.get('telegram', {})
    if not telegram:
        return False
    
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', telegram.get('bot_token'))
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', telegram.get('chat_id'))
    
    if not bot_token or not chat_id:
        logger.warning("Telegram credentials not configured")
        return False
    
    # Filter by severity levels
    levels = telegram.get('levels', ['critical'])
    anomalies = load_anomalies()
    if not anomalies:
        return False
    
    should_send = any(
        a['severity'] in levels 
        for a in anomalies['anomalies']
    )
    
    if not should_send:
        logger.info("Skipping Telegram (severity filter)")
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("Telegram alert sent")
            return True
        else:
            logger.error(f"Telegram error: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram failed: {e}")
        return False


def send_email(config, message):
    """Send alert via SMTP email."""
    email_config = config.get('email', {})
    if not email_config:
        return False
    
    # Filter by severity
    levels = email_config.get('levels', ['critical'])
    anomalies = load_anomalies()
    if not anomalies:
        return False
    
    should_send = any(
        a['severity'] in levels
        for a in anomalies['anomalies']
    )
    
    if not should_send:
        logger.info("Skipping email (severity filter)")
        return False
    
    smtp_server = email_config.get('smtp_server')
    smtp_port = email_config.get('smtp_port', 587)
    username = os.environ.get('EMAIL_USER', email_config.get('username'))
    password = os.environ.get('EMAIL_PASS', email_config.get('password'))
    recipients = email_config.get('recipients', [])
    
    if not all([smtp_server, username, password, recipients]):
        logger.warning("Email credentials not fully configured")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = username
        msg['To'] = ', '.join(recipients)
        msg['Subject'] = "🚨 QNAP-OME Fleet Alert"
        
        msg.attach(MIMEText(message, 'plain'))
        
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(username, password)
        server.send_message(msg)
        server.quit()
        
        logger.info("Email alert sent")
        return True
        
    except Exception as e:
        logger.error(f"Email failed: {e}")
        return False


def send_ntfy(config, message):
    """Send alert via ntfy.sh (or self-hosted)."""
    ntfy_config = config.get('ntfy', {})
    if not ntfy_config:
        return False
    
    # Filter by severity
    levels = ntfy_config.get('levels', ['info', 'warning', 'critical'])
    anomalies = load_anomalies()
    if not anomalies:
        return False
    
    should_send = any(
        a['severity'] in levels
        for a in anomalies['anomalies']
    )
    
    if not should_send:
        logger.info("Skipping ntfy (severity filter)")
        return False
    
    topic = ntfy_config.get('topic')
    server = ntfy_config.get('server', 'https://ntfy.sh')
    
    if not topic:
        logger.warning("ntfy topic not configured")
        return False
    
    # Determine priority based on severities
    has_critical = any(a['severity'] == 'critical' for a in anomalies['anomalies'])
    priority = 5 if has_critical else 3
    
    try:
        headers = {
            'Title': 'QNAP-OME Alert',
            'Priority': str(priority),
            'Tags': 'warning' if has_critical else 'notice'
        }
        
        resp = requests.post(
            f"{server}/{topic}",
            data=message.encode(),
            headers=headers,
            timeout=10
        )
        
        if resp.status_code == 200:
            logger.info("ntfy alert sent")
            return True
        else:
            logger.error(f"ntfy error: {resp.text}")
            return False
            
    except Exception as e:
        logger.error(f"ntfy failed: {e}")
        return False


def send_webhook(config, message):
    """Send alert via generic webhook."""
    webhook = config.get('webhook', {})
    if not webhook:
        return False
    
    url = webhook.get('url')
    if not url:
        logger.warning("Webhook URL not configured")
        return False
    
    try:
        payload = {
            'timestamp': datetime.utcnow().isoformat(),
            'anomaly_count': load_anomalies()['count'] if load_anomalies() else 0,
            'message': message,
            'analysis': load_analysis()
        }
        
        resp = requests.post(url, json=payload, timeout=10)
        
        if resp.status_code in [200, 204]:
            logger.info("Webhook alert sent")
            return True
        else:
            logger.error(f"Webhook error: {resp.text}")
            return False
            
    except Exception as e:
        logger.error(f"Webhook failed: {e}")
        return False


def main():
    config = load_config()
    message = format_alert_message(load_anomalies(), load_analysis())
    
    if not message:
        logger.warning("No message to send")
        sys.exit(0)
    
    sent_count = 0
    
    # Send to configured channels
    if send_telegram(config, message):
        sent_count += 1
    
    if send_email(config, message):
        sent_count += 1
    
    if send_ntfy(config, message):
        sent_count += 1
    
    if send_webhook(config, message):
        sent_count += 1
    
    if sent_count > 0:
        logger.info(f"Alert sent to {sent_count} channel(s)")
    else:
        logger.warning("No notification channels configured or triggered")
    
    sys.exit(0)


if __name__ == '__main__':
    main()
