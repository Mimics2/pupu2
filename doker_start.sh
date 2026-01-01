#!/bin/bash
# Скрипт для запуска бота с проверкой базы данных

echo "Проверка базы данных..."

# Проверяем соединение с базой данных
python -c "
from sqlalchemy import create_engine
from config import Config
import sys

try:
    engine = create_engine(Config.DATABASE_URL)
    conn = engine.connect()
    conn.close()
    print('✅ Соединение с базой данных успешно')
except Exception as e:
    print(f'❌ Ошибка подключения к базе данных: {e}')
    sys.exit(1)
"

# Если PostgreSQL, проверяем структуру таблиц
if [[ $DATABASE_URL == *"postgresql"* ]]; then
    echo "Проверка структуры таблиц PostgreSQL..."
    python -c "
from sqlalchemy import create_engine, inspect
from config import Config

engine = create_engine(Config.DATABASE_URL)
inspector = inspect(engine)

tables = ['users', 'user_channels', 'scheduled_posts', 'payments']
for table in tables:
    if inspector.has_table(table):
        print(f'✅ Таблица {table} существует')
    else:
        print(f'❌ Таблица {table} отсутствует')
"
fi

echo "Запуск бота..."
exec python bot.py
