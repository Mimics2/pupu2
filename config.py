import os

class Config:
    # Основные настройки (Railway сам установит переменные окружения)
    BOT_TOKEN = os.environ.get('BOT_TOKEN', '7370973281:AAGdnM2SdekWwSF5alb5vnt0UWAN5QZ1dCQ')
    ADMIN_ID = int(os.environ.get('ADMIN_ID', 6646433980))
    
    # Получаем DATABASE_URL от Railway
    DATABASE_URL = os.environ.get('DATABASE_URL')
    
    # Если Railway не предоставил URL, используем SQLite для совместимости
    if not DATABASE_URL:
        DATABASE_URL = 'sqlite:///bot.db'
    elif DATABASE_URL.startswith('postgresql'):
        # Railway использует postgres://, а SQLAlchemy требует postgresql://
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    
    # Настройки тарифов (в звездах)
    TARIFFS = {
        'basic': {
            'name': 'Базовый',
            'stars': 500,
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
    
    # Настройка приватного канала
    PRIVATE_CHANNEL_ID = os.environ.get('PRIVATE_CHANNEL_ID', '')
    PRIVATE_CHANNEL_LINK = os.environ.get('PRIVATE_CHANNEL_LINK', '')
    
    # Время в часах до кика
    KICK_AFTER_EXPIRY = 2
