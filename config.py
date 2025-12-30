import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Основные настройки
    BOT_TOKEN = os.getenv('BOT_TOKEN', '7370973281:AAGdnM2SdekWwSF5alb5vnt0UWAN5QZ1dCQ')
    ADMIN_ID = int(os.getenv('ADMIN_ID', 6646433980))
    DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///bot.db')
    
    # Настройки тарифов (в звездах)
    TARIFFS = {
        'basic': {
            'name': 'Базовый',
            'stars': 500,  # 500 звёзд
            'channels_limit': 2,
            'posts_per_day': 5,
            'duration_days': 30
        },
        'premium': {
            'name': 'Премиум',
            'stars': 1000,
            'channels_limit': 5,
            'posts_per_day': 15,
            'duration_days': 30
        }
    }
    
    # Настройка приватного канала для подписчиков
    PRIVATE_CHANNEL_ID = os.getenv('PRIVATE_CHANNEL_ID', '')
    PRIVATE_CHANNEL_LINK = os.getenv('PRIVATE_CHANNEL_LINK', '')
    
    # Время в часах до кика после окончания подписки
    KICK_AFTER_EXPIRY = 2
