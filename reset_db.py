#!/usr/bin/env python3
"""
Скрипт для сброса и пересоздания базы данных
"""

from database import Base, init_db
from config import Config
import sys

def reset_database():
    """Сбрасывает и пересоздает все таблицы"""
    print("Сброс базы данных...")
    
    from sqlalchemy import create_engine
    from sqlalchemy_utils import database_exists, create_database, drop_database
    
    # Получаем URL базы данных
    db_url = Config.DATABASE_URL
    
    if db_url.startswith('sqlite'):
        # Для SQLite просто удаляем файл
        import os
        if os.path.exists('bot.db'):
            os.remove('bot.db')
            print("SQLite база данных удалена")
    else:
        # Для PostgreSQL используем SQLAlchemy-utils
        try:
            if database_exists(db_url):
                drop_database(db_url)
                print("PostgreSQL база данных удалена")
                create_database(db_url)
                print("PostgreSQL база данных создана заново")
            else:
                create_database(db_url)
                print("PostgreSQL база данных создана")
        except ImportError:
            print("Установите sqlalchemy-utils: pip install sqlalchemy-utils")
            sys.exit(1)
    
    # Создаем таблицы
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    
    print("✅ Таблицы созданы успешно!")
    print(f"Используемая БД: {db_url}")

if __name__ == '__main__':
    reset_database()
