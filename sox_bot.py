import requests
import smtplib
from email.message import EmailMessage
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

EMAIL_ADDRESS = os.getenv('EMAIL_ADDRESS')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

def get_redsox_game():
    url = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/2/schedule"
    response = requests.get(url)
    data = response.json()
    today = datetime.now().strftime("%Y-%m-%d")

    for event in data['events']:
        if today in event['date']:
            game_time = event['date']
            opponent = event['name'].replace("Boston Red Sox vs ", "").replace(" vs Boston Red Sox", "")
            return True, datetime.fromisoformat(game_time), opponent
    return False, None, None

def send_email(subject, body):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = 'bbragg1@terpmail.umd.edu'
    msg.set_content(body)

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        smtp.send_message(msg)

# Test
# send_email("Test Email from Sox Bot", "If this is in my inbox, the bot is working!")

has_game, game_time, opponent = get_redsox_game()
if has_game:
    send_email("Red Sox Play TODAY!", f"Red Sox play at {game_time.strftime('%I:%M %p')} against {opponent}.")
else:
    print("No Red Sox game today.")
