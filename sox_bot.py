import requests
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
import schedule
import time
import os
from dotenv import load_dotenv
import pytz


load_dotenv()

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

def get_redsox_game():
    url = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/2/schedule"
    response = requests.get(url)
    data = response.json()
    today = datetime.now(pytz.timezone("US/Eastern")).strftime("%Y-%m-%d")

    for event in data['events']:
        if today in event['date']:
            utc_time = datetime.fromisoformat(event['date'].replace("Z", "+00:00"))
            eastern = pytz.timezone("US/Eastern")
            game_time = utc_time.astimezone(eastern)

            opponent = event['name'].replace("Boston Red Sox vs ", "").replace(" vs Boston Red Sox", "")
            return True, game_time, opponent
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
        print("Email sent!")

def game_start_email(game_time, opponent):
    send_email("The Red Sox are ON NOW!", f"The Red Sox game against {opponent} is starting now!")
    print(f"Sent game start reminder for {opponent} at {datetime.now().strftime('%I:%M %p')}")

def daily_task():
    print("Running daily Red Sox check...")
    has_game, game_time, opponent = get_redsox_game()
    if has_game:
        send_email("Red Sox Play TODAY!", f"Red Sox play at {game_time.strftime('%I:%M %p')} against {opponent}.")

        now = datetime.now()
        delay = (game_time + timedelta(minutes=5)) - now
        seconds = delay.total_seconds()

        if seconds > 0:
            print(f"Scheduling game start email in {int(seconds)} seconds")
            schedule.every(seconds).seconds.do(game_start_email, game_time=game_time, opponent=opponent)
        else:
            print("Game already started or starting soon — not scheduling game start email")
    else:
        send_email("No Sox Game Today...")

schedule.every().day.at("13:00").do(daily_task)  

while True:
    schedule.run_pending()
    time.sleep(60)
