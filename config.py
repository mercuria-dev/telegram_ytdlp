from dotenv import load_dotenv
import os

load_dotenv()
bot_token = os.getenv('BOT_TOKEN')
channel_id = int(os.getenv('CHANNEL_ID'))
channel_link = os.getenv('CHANNEL_LINK')
api_id = int(os.getenv('API_ID'))
api_hash = os.getenv('API_HASH')
admin_list = os.getenv('ADMIN_LIST').split(",")
stars_price = int(os.getenv('STARS_PRICE', '1'))
free_whitelist = os.getenv('FREE_WHITELIST', '').split(',')
