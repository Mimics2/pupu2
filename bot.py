import os
import asyncio
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pytz

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    ChatInviteLink
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters,
    ContextTypes,
    ConversationHandler
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN', '8473070442:AAEVztTu1k81VufGAmWVQbX3vpue6ARlj38')
ADMIN_ID = 6646433980  # Ваш ID администратора

# Московское время
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# Состояния для ConversationHandler
SETUP_TARIFF, SETUP_CHANNEL_ID, SETUP_CHANNEL_NAME = range(3)
EDIT_TARIFF_SELECT, EDIT_TARIFF_FIELD, EDIT_TARIFF_VALUE = range(3, 6)

class SubscriptionBot:
    def __init__(self, token: str):
        self.token = token
        self.application = Application.builder().token(token).build()
        
        # Хранилище данных
        self.user_subscriptions: Dict[int, Dict] = {}  # Подписки пользователей
        self.user_stats: Dict[int, Dict] = {}  # Статистика пользователей
        self.invite_links: Dict[str, ChatInviteLink] = {}  # Ссылки-приглашения
        
        # Тарифные планы
        self.subscription_plans = self.load_settings()
        
        # Временные данные для админ-настроек
        self.admin_temp_data = {}
        
        self.setup_handlers()
        self.setup_job_queue()
    
    def load_settings(self):
        """Загрузить настройки тарифов"""
        try:
            with open('subscription_settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
                return settings
        except FileNotFoundError:
            # Создаем дефолтные настройки
            default_settings = {
                "basic": {
                    "name": "💰 Базовый - $1/месяц",
                    "price": 1,
                    "posts_per_day": 2,
                    "channels_limit": 1,
                    "channel_id": "",      # ID приватного канала
                    "channel_name": "",    # Название канала для отображения
                    "duration_days": 30,
                    "invite_link": ""      # Приватная ссылка на канал
                },
                "standard": {
                    "name": "💎 Стандартный - $3/месяц",
                    "price": 3,
                    "posts_per_day": 6,
                    "channels_limit": 3,
                    "channel_id": "",
                    "channel_name": "",
                    "duration_days": 30,
                    "invite_link": ""
                },
                "premium": {
                    "name": "🚀 Премиум - $5/месяц",
                    "price": 5,
                    "posts_per_day": -1,
                    "channels_limit": -1,
                    "channel_id": "",
                    "channel_name": "",
                    "duration_days": 30,
                    "invite_link": ""
                }
            }
            self.save_settings(default_settings)
            return default_settings
        except Exception as e:
            logger.error(f"Ошибка загрузки настроек: {e}")
            return {}
    
    def save_settings(self, settings=None):
        """Сохранить настройки тарифов"""
        if settings is None:
            settings = self.subscription_plans
            
        try:
            with open('subscription_settings.json', 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек: {e}")
    
    def is_admin(self, user_id: int) -> bool:
        """Проверить является ли пользователь администратором"""
        return user_id == ADMIN_ID
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        # Команды для всех пользователей
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("time", self.current_time))
        self.application.add_handler(CommandHandler("tariffs", self.show_tariffs))
        self.application.add_handler(CommandHandler("check", self.check_subscription))
        self.application.add_handler(CommandHandler("mysub", self.my_subscription))
        
        # Админ команды
        self.application.add_handler(CommandHandler("admin", self.admin_panel))
        self.application.add_handler(CommandHandler("setup", self.setup_channel))
        self.application.add_handler(CommandHandler("test", self.test_channel))
        
        # Conversation handler для настройки тарифов
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("edittariff", self.edit_tariff_start)],
            states={
                SETUP_TARIFF: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.edit_tariff_select)
                ],
                SETUP_CHANNEL_ID: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.edit_tariff_channel_id)
                ],
                SETUP_CHANNEL_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.edit_tariff_channel_name)
                ],
                EDIT_TARIFF_SELECT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.edit_tariff_field_select)
                ],
                EDIT_TARIFF_FIELD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.edit_tariff_field_value)
                ],
                EDIT_TARIFF_VALUE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.edit_tariff_save)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.cancel_edit)]
        )
        self.application.add_handler(conv_handler)
        
        # Callback query обработчики
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Обработчики сообщений
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_handler))
    
    def setup_job_queue(self):
        """Настройка фоновых задач"""
        job_queue = self.application.job_queue
        if job_queue:
            # Проверка истекших подписок каждый день
            job_queue.run_repeating(self.check_expired_subscriptions, interval=86400, first=10)
    
    async def check_expired_subscriptions(self, context):
        """Проверка истекших подписок"""
        try:
            now = datetime.now(MOSCOW_TZ)
            expired_users = []
            
            for user_id, sub_data in list(self.user_subscriptions.items()):
                if "expires_at" in sub_data:
                    expires_at = datetime.fromisoformat(sub_data["expires_at"]).replace(tzinfo=MOSCOW_TZ)
                    if now > expires_at:
                        expired_users.append(user_id)
            
            for user_id in expired_users:
                del self.user_subscriptions[user_id]
                logger.info(f"Подписка пользователя {user_id} истекла")
                
            if expired_users:
                logger.info(f"Удалено {len(expired_users)} истекших подписок")
                
        except Exception as e:
            logger.error(f"Ошибка проверки подписок: {e}")
    
    async def create_invite_link(self, plan_type: str, user_id: int) -> Optional[str]:
        """Создать приватную ссылку на канал"""
        try:
            plan_config = self.subscription_plans.get(plan_type)
            if not plan_config:
                logger.error(f"Тариф {plan_type} не найден")
                return None
            
            channel_id = plan_config.get('channel_id')
            
            if not channel_id:
                logger.error(f"ID канала для тарифа {plan_type} не настроен")
                return None
            
            # Проверяем доступ бота к каналу
            try:
                bot_member = await self.application.bot.get_chat_member(
                    chat_id=channel_id,
                    user_id=self.application.bot.id
                )
                
                if bot_member.status not in ['administrator', 'creator']:
                    logger.error(f"Бот не является администратором канала {channel_id}")
                    return None
                    
            except Exception as e:
                logger.error(f"Ошибка проверки доступа бота: {e}")
                return None
            
            # Создаем уникальную ссылку-приглашение
            try:
                invite_link = await self.application.bot.create_chat_invite_link(
                    chat_id=channel_id,
                    name=f"Sub_{plan_type}_{user_id}",
                    expire_date=datetime.now() + timedelta(hours=24),
                    member_limit=1,
                    creates_join_request=False
                )
                
                # Сохраняем ссылку в настройках тарифа
                self.subscription_plans[plan_type]['invite_link'] = invite_link.invite_link
                self.save_settings()
                
                logger.info(f"Создана ссылка для пользователя {user_id} на тариф {plan_type}")
                return invite_link.invite_link
                
            except Exception as e:
                logger.error(f"Ошибка создания ссылки: {e}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка в create_invite_link: {e}")
            return None
    
    async def check_channel_subscription(self, user_id: int, plan_type: str) -> bool:
        """Проверить подписку пользователя на приватный канал"""
        try:
            plan_config = self.subscription_plans.get(plan_type)
            if not plan_config:
                return False
            
            channel_id = plan_config.get('channel_id')
            
            if not channel_id:
                return False
            
            # Пытаемся получить информацию о пользователе в канале
            try:
                chat_member = await self.application.bot.get_chat_member(
                    chat_id=channel_id,
                    user_id=user_id
                )
                
                # Проверяем статус
                status = chat_member.status
                return status in ['member', 'administrator', 'creator', 'restricted']
                
            except Exception as e:
                error_msg = str(e).lower()
                if 'user not found' in error_msg or 'user not participant' in error_msg:
                    return False
                else:
                    logger.error(f"Ошибка проверки подписки: {e}")
                    return False
                    
        except Exception as e:
            logger.error(f"Ошибка проверки канала: {e}")
            return False
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        
        current_time = self.format_moscow_time()
        user_plan = self.get_user_plan(user_id)
        
        keyboard = [
            [InlineKeyboardButton("💳 Тарифы", callback_data="show_tariffs")],
            [InlineKeyboardButton("🔍 Проверить подписку", callback_data="check_subscription")],
            [InlineKeyboardButton("📋 Моя подписка", callback_data="my_subscription")],
            [InlineKeyboardButton("🕐 Текущее время", callback_data="current_time")]
        ]
        
        if self.is_admin(user_id):
            keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = f"🤖 Бот для приватных подписок\n"
        welcome_text += f"🕐 Московское время: <b>{current_time}</b>\n\n"
        
        if self.is_admin(user_id):
            welcome_text += "👑 Вы администратор\n"
        elif user_plan["plan"] == "free":
            welcome_text += "❌ У вас нет активной подписки\n"
            welcome_text += "💳 Выберите тарифный план для получения доступа\n"
        else:
            plan_config = self.subscription_plans[user_plan["plan"]]
            welcome_text += f"✅ Ваш тариф: {plan_config['name']}\n"
            
            if "expires_at" in user_plan:
                expires_at = datetime.fromisoformat(user_plan["expires_at"]).replace(tzinfo=MOSCOW_TZ)
                days_left = (expires_at - datetime.now(MOSCOW_TZ)).days
                welcome_text += f"⏳ Дней осталось: {days_left}\n"
        
        await update.message.reply_text(
            welcome_text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    
    async def show_tariffs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать все тарифы"""
        text = "💳 Доступные тарифы:\n\n"
        
        for plan_key, plan_config in self.subscription_plans.items():
            text += f"{plan_config['name']}\n"
            text += f"💰 Цена: ${plan_config['price']}/месяц\n"
            text += f"📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
            text += f"📢 Каналов: {'∞' if plan_config['channels_limit'] == -1 else plan_config['channels_limit']}\n"
            text += f"⏳ Длительность: {plan_config.get('duration_days', 30)} дней\n"
            
            if plan_config.get('channel_id'):
                text += f"🔒 Приватный канал: ✅\n"
                if plan_config.get('invite_link'):
                    text += f"🔗 Ссылка доступна после оплаты\n"
            else:
                text += f"🔒 Приватный канал: ⚠️ (не настроен)\n"
            
            text += "\n"
        
        keyboard = []
        for plan_key in self.subscription_plans:
            keyboard.append([
                InlineKeyboardButton(
                    f"Выбрать {self.subscription_plans[plan_key]['name'].split('-')[0].strip()}",
                    callback_data=f"select_tariff_{plan_key}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        if update.message:
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    async def check_subscription(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Проверить подписку пользователя"""
        user_id = update.effective_user.id
        
        if update.message:
            await update.message.reply_text("🔍 Проверяю ваши подписки...")
            message = update.message
        else:
            await update.callback_query.edit_message_text("🔍 Проверяю ваши подписки...")
            message = update.callback_query.message
        
        # Проверяем подписку на все тарифы
        subscribed_tariffs = []
        
        for plan_key, plan_config in self.subscription_plans.items():
            if plan_config.get('channel_id'):
                is_subscribed = await self.check_channel_subscription(user_id, plan_key)
                if is_subscribed:
                    subscribed_tariffs.append(plan_key)
        
        if subscribed_tariffs:
            # Пользователь подписан на один или несколько каналов
            # Даем доступ к самому дорогому тарифу
            plan_order = ["premium", "standard", "basic"]
            selected_plan = None
            
            for plan in plan_order:
                if plan in subscribed_tariffs:
                    selected_plan = plan
                    break
            
            if selected_plan:
                plan_config = self.subscription_plans[selected_plan]
                expires_at = datetime.now(MOSCOW_TZ) + timedelta(days=plan_config.get('duration_days', 30))
                
                self.user_subscriptions[user_id] = {
                    "plan": selected_plan,
                    "subscribed_at": datetime.now(MOSCOW_TZ).isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "channel_id": plan_config.get('channel_id')
                }
                
                await message.edit_text(
                    f"✅ Подписка подтверждена!\n\n"
                    f"Тариф: {plan_config['name']}\n"
                    f"📢 Канал: {plan_config.get('channel_name', 'Приватный канал')}\n"
                    f"📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
                    f"⏳ Действует до: {expires_at.strftime('%d.%m.%Y %H:%M')}\n\n"
                    f"🎉 Теперь у вас есть доступ к функциям бота!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🚀 Начать", callback_data="back_to_main")]
                    ])
                )
        else:
            await message.edit_text(
                "❌ Подписка не обнаружена!\n\n"
                "У вас нет доступа ни к одному приватному каналу.\n"
                "Выберите тариф и подпишитесь на канал для получения доступа.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Выбрать тариф", callback_data="show_tariffs")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ])
            )
    
    async def my_subscription(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать информацию о текущей подписке"""
        user_id = update.effective_user.id
        user_plan = self.get_user_plan(user_id)
        
        if user_plan["plan"] == "free":
            text = "❌ У вас нет активной подписки.\n"
            text += "💳 Выберите тариф и подпишитесь на канал для получения доступа."
            
            keyboard = [[InlineKeyboardButton("💳 Тарифы", callback_data="show_tariffs")]]
        else:
            plan_config = self.subscription_plans[user_plan["plan"]]
            
            text = f"📋 Ваша подписка:\n\n"
            text += f"📛 Тариф: {plan_config['name']}\n"
            text += f"📢 Канал: {plan_config.get('channel_name', 'Приватный канал')}\n"
            
            if "expires_at" in user_plan:
                expires_at = datetime.fromisoformat(user_plan["expires_at"]).replace(tzinfo=MOSCOW_TZ)
                days_left = (expires_at - datetime.now(MOSCOW_TZ)).days
                text += f"⏳ Дней осталось: {days_left}\n"
            
            text += f"📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
            text += f"🔗 Ссылка на канал доступна в меню тарифа"
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
        
        if update.message:
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Админ панель"""
        user_id = update.effective_user.id
        
        if not self.is_admin(user_id):
            if update.message:
                await update.message.reply_text("❌ У вас нет доступа к админ панели")
            else:
                await update.callback_query.answer("❌ У вас нет доступа", show_alert=True)
            return
        
        total_users = len(self.user_subscriptions)
        active_subscriptions = len([sub for sub in self.user_subscriptions.values() 
                                  if not self.is_subscription_expired(list(self.user_subscriptions.keys())[list(self.user_subscriptions.values()).index(sub)])])
        
        keyboard = [
            [InlineKeyboardButton("⚙️ Настройка тарифов", callback_data="admin_settings")],
            [InlineKeyboardButton("🔗 Управление ссылками", callback_data="admin_links")],
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("👥 Управление пользователями", callback_data="admin_users")],
            [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
        ]
        
        text = f"👑 Админ Панель\n\n"
        text += f"📊 Всего пользователей: {total_users}\n"
        text += f"💳 Активных подписок: {active_subscriptions}\n"
        text += f"📋 Тарифов настроено: {len(self.subscription_plans)}\n\n"
        text += "Выберите действие:"
        
        if update.message:
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    async def setup_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Настройка приватного канала для тарифа"""
        user_id = update.effective_user.id
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ Только администратор может настраивать каналы")
            return
        
        if not context.args:
            await update.message.reply_text(
                "🔧 Настройка приватного канала:\n\n"
                "Использование: /setup <тариф> <id_канала> <название>\n\n"
                "Пример:\n"
                "/setup basic -1001234567890 Мой_Приватный_Канал\n\n"
                "Доступные тарифы:\n" +
                "\n".join([f"• {key}: {self.subscription_plans[key]['name']}" for key in self.subscription_plans])
            )
            return
        
        if len(context.args) < 3:
            await update.message.reply_text("❌ Недостаточно аргументов. Формат: /setup <тариф> <id_канала> <название>")
            return
        
        plan_type = context.args[0].lower()
        channel_id = context.args[1]
        channel_name = " ".join(context.args[2:])
        
        if plan_type not in self.subscription_plans:
            await update.message.reply_text(f"❌ Неизвестный тариф: {plan_type}")
            return
        
        # Проверяем формат ID канала
        if not channel_id.startswith('-100'):
            await update.message.reply_text(
                "❌ Неверный формат ID канала\n"
                "Должно начинаться с '-100' для супергрупп/каналов"
            )
            return
        
        # Проверяем доступ бота к каналу
        try:
            chat = await self.application.bot.get_chat(channel_id)
            
            # Проверяем, является ли бот администратором
            bot_member = await self.application.bot.get_chat_member(
                chat_id=channel_id,
                user_id=self.application.bot.id
            )
            
            if bot_member.status not in ['administrator', 'creator']:
                await update.message.reply_text(
                    f"⚠️ Бот не является администратором этого канала!\n\n"
                    f"Добавьте @{self.application.bot.username} в канал как администратора"
                )
                return
                
        except Exception as e:
            error_msg = str(e).lower()
            if 'chat not found' in error_msg:
                await update.message.reply_text(
                    "❌ Канал не найден\n"
                    "Убедитесь что:\n"
                    "1. Канал существует\n"
                    "2. ID канала правильный\n"
                    "3. Бот добавлен в канал"
                )
            else:
                await update.message.reply_text(f"❌ Ошибка настройки канала: {str(e)[:200]}")
            return
        
        # Сохраняем настройки
        self.subscription_plans[plan_type]['channel_id'] = channel_id
        self.subscription_plans[plan_type]['channel_name'] = channel_name
        self.save_settings()
        
        await update.message.reply_text(
            f"✅ Канал настроен для тарифа {plan_type}!\n\n"
            f"📋 Тариф: {self.subscription_plans[plan_type]['name']}\n"
            f"🆔 ID канала: {channel_id}\n"
            f"📢 Название: {channel_name}\n\n"
            f"Теперь можно создавать приватные ссылки для этого канала!"
        )
    
    async def test_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Тестирование доступа к каналу"""
        user_id = update.effective_user.id
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ Только администратор может тестировать каналы")
            return
        
        if not context.args:
            await update.message.reply_text(
                "🔍 Тестирование доступа к каналу:\n\n"
                "Использование: /test <тариф>\n\n"
                "Пример: /test basic\n\n"
                "Доступные тарифы:\n" +
                "\n".join([f"• {key}: {self.subscription_plans[key]['name']}" for key in self.subscription_plans])
            )
            return
        
        plan_type = context.args[0].lower()
        
        if plan_type not in self.subscription_plans:
            await update.message.reply_text(f"❌ Неизвестный тариф: {plan_type}")
            return
        
        plan_config = self.subscription_plans[plan_type]
        channel_id = plan_config.get('channel_id')
        
        if not channel_id:
            await update.message.reply_text(f"❌ Для тарифа {plan_type} не настроен канал")
            return
        
        await update.message.reply_text("🔍 Проверяем доступ к каналу...")
        
        try:
            # Проверяем информацию о канале
            chat = await self.application.bot.get_chat(channel_id)
            
            # Проверяем статус бота
            bot_member = await self.application.bot.get_chat_member(
                chat_id=channel_id,
                user_id=self.application.bot.id
            )
            
            # Пытаемся создать тестовую ссылку
            test_link = None
            try:
                invite_link = await self.application.bot.create_chat_invite_link(
                    chat_id=channel_id,
                    name="TEST_LINK",
                    expire_date=datetime.now() + timedelta(minutes=5),
                    member_limit=1
                )
                test_link = invite_link.invite_link
            except Exception as e:
                test_link_error = str(e)
            
            # Формируем отчет
            report = f"📊 Отчет по каналу для тарифа {plan_type}:\n\n"
            report += f"📋 Тариф: {plan_config['name']}\n"
            report += f"🆔 ID канала: {channel_id}\n"
            report += f"📢 Название: {chat.title}\n"
            report += f"👤 Участников: {chat.member_count if chat.member_count else 'Неизвестно'}\n\n"
            
            report += f"🤖 Статус бота: {bot_member.status}\n"
            
            if test_link:
                report += f"🔗 Тестовая ссылка (действует 5 мин):\n{test_link}\n\n"
                report += f"✅ Канал настроен правильно! Можно создавать ссылки."
            else:
                report += f"❌ Не удалось создать ссылку: {test_link_error}\n\n"
                report += f"⚠️ Проверьте права бота в канале!"
            
            await update.message.reply_text(report, disable_web_page_preview=True)
            
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка тестирования: {str(e)[:300]}")
    
    async def edit_tariff_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начало редактирования тарифа"""
        user_id = update.effective_user.id
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ Только администратор может редактировать тарифы")
            return ConversationHandler.END
        
        keyboard = []
        for plan_key, plan_config in self.subscription_plans.items():
            keyboard.append([InlineKeyboardButton(plan_config['name'], callback_data=f"edit_{plan_key}")])
        
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit")])
        
        await update.message.reply_text(
            "⚙️ Выберите тариф для редактирования:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return SETUP_TARIFF
    
    async def edit_tariff_select(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка выбора тарифа"""
        text = update.message.text
        
        # Ищем тариф по имени
        selected_plan = None
        for plan_key, plan_config in self.subscription_plans.items():
            if plan_config['name'] == text:
                selected_plan = plan_key
                break
        
        if not selected_plan:
            await update.message.reply_text("❌ Тариф не найден. Попробуйте снова:")
            return SETUP_TARIFF
        
        context.user_data['edit_plan'] = selected_plan
        
        keyboard = [
            [InlineKeyboardButton("📝 Название", callback_data="edit_name")],
            [InlineKeyboardButton("💰 Цена", callback_data="edit_price")],
            [InlineKeyboardButton("📊 Постов в день", callback_data="edit_posts")],
            [InlineKeyboardButton("📢 Лимит каналов", callback_data="edit_channels")],
            [InlineKeyboardButton("⏳ Длительность", callback_data="edit_duration")],
            [InlineKeyboardButton("🔗 Настроить канал", callback_data="setup_channel")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit")]
        ]
        
        await update.message.reply_text(
            f"Выбран тариф: {self.subscription_plans[selected_plan]['name']}\n"
            "Выберите что редактировать:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return EDIT_TARIFF_SELECT
    
    async def edit_tariff_field_select(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка выбора поля для редактирования"""
        text = update.message.text
        
        field_map = {
            "название": "name",
            "цена": "price", 
            "постов в день": "posts_per_day",
            "лимит каналов": "channels_limit",
            "длительность": "duration_days"
        }
        
        field = None
        for key, value in field_map.items():
            if key in text.lower():
                field = value
                break
        
        if not field:
            await update.message.reply_text("❌ Неизвестное поле. Выберите из списка:")
            return EDIT_TARIFF_SELECT
        
        context.user_data['edit_field'] = field
        
        plan_key = context.user_data['edit_plan']
        current_value = self.subscription_plans[plan_key].get(field, "")
        
        await update.message.reply_text(
            f"Текущее значение: {current_value}\n"
            f"Введите новое значение:"
        )
        
        return EDIT_TARIFF_FIELD
    
    async def edit_tariff_field_value(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка нового значения поля"""
        new_value = update.message.text
        field = context.user_data['edit_field']
        plan_key = context.user_data['edit_plan']
        
        # Валидация в зависимости от поля
        try:
            if field in ['price', 'posts_per_day', 'channels_limit', 'duration_days']:
                if new_value == '-1':
                    new_value = -1
                else:
                    new_value = float(new_value) if field == 'price' else int(new_value)
                    if new_value < 0 and new_value != -1:
                        raise ValueError("Значение не может быть отрицательным")
            
            # Сохраняем изменения
            self.subscription_plans[plan_key][field] = new_value
            self.save_settings()
            
            await update.message.reply_text(
                f"✅ Поле '{field}' обновлено!\n"
                f"Новое значение: {new_value}"
            )
            
        except ValueError as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}\nПопробуйте снова:")
            return EDIT_TARIFF_FIELD
        
        return ConversationHandler.END
    
    async def edit_tariff_channel_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ID канала"""
        channel_id = update.message.text
        plan_key = context.user_data['edit_plan']
        
        if not channel_id.startswith('-100'):
            await update.message.reply_text("❌ ID канала должен начинаться с '-100'. Попробуйте снова:")
            return SETUP_CHANNEL_ID
        
        self.subscription_plans[plan_key]['channel_id'] = channel_id
        context.user_data['channel_id'] = channel_id
        
        await update.message.reply_text(
            f"✅ ID канала сохранен: {channel_id}\n"
            f"Теперь введите название канала:"
        )
        
        return SETUP_CHANNEL_NAME
    
    async def edit_tariff_channel_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка названия канала"""
        channel_name = update.message.text
        plan_key = context.user_data['edit_plan']
        
        self.subscription_plans[plan_key]['channel_name'] = channel_name
        self.save_settings()
        
        await update.message.reply_text(
            f"✅ Канал настроен!\n\n"
            f"Тариф: {self.subscription_plans[plan_key]['name']}\n"
            f"🆔 ID канала: {self.subscription_plans[plan_key]['channel_id']}\n"
            f"📢 Название: {channel_name}"
        )
        
        return ConversationHandler.END
    
    async def edit_tariff_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Сохранение изменений тарифа"""
        return ConversationHandler.END
    
    async def cancel_edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отмена редактирования"""
        await update.message.reply_text("❌ Редактирование отменено")
        return ConversationHandler.END
    
    async def current_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать текущее время в Москве"""
        current_time = self.format_moscow_time()
        
        if update.message:
            await update.message.reply_text(
                f"🕐 Текущее время в Москве:\n<b>{current_time}</b>",
                parse_mode="HTML"
            )
        else:
            await update.callback_query.edit_message_text(
                f"🕐 Текущее время в Москве:\n<b>{current_time}</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ])
            )
    
    def format_moscow_time(self, dt=None):
        """Форматировать время в Москве"""
        if dt is None:
            dt = datetime.now(MOSCOW_TZ)
        return dt.strftime('%d.%m.%Y %H:%M')
    
    def get_user_plan(self, user_id: int) -> Dict:
        """Получить тарифный план пользователя"""
        # Админ всегда имеет безлимит
        if self.is_admin(user_id):
            return {"plan": "admin", "subscribed_at": datetime.now(MOSCOW_TZ).isoformat()}
        
        return self.user_subscriptions.get(user_id, {"plan": "free"})
    
    def is_subscription_expired(self, user_id: int) -> bool:
        """Проверить истекла ли подписка пользователя"""
        if user_id not in self.user_subscriptions:
            return True
        
        user_plan = self.user_subscriptions[user_id]
        if "expires_at" not in user_plan:
            return True
        
        try:
            expires_at = datetime.fromisoformat(user_plan["expires_at"]).replace(tzinfo=MOSCOW_TZ)
            return datetime.now(MOSCOW_TZ) > expires_at
        except:
            return True
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        if data == "back_to_main":
            await self.start_from_query(query)
        elif data == "show_tariffs":
            await self.show_tariffs(update, context)
        elif data == "check_subscription":
            await self.check_subscription(update, context)
        elif data == "my_subscription":
            await self.my_subscription(update, context)
        elif data == "current_time":
            await self.current_time(update, context)
        elif data == "admin_panel":
            await self.admin_panel(update, context)
        elif data == "admin_settings":
            await self.admin_settings(query)
        elif data == "admin_links":
            await self.admin_links(query)
        elif data == "admin_stats":
            await self.admin_stats(query)
        elif data == "admin_users":
            await self.admin_users(query)
        elif data.startswith("select_tariff_"):
            plan_type = data.replace("select_tariff_", "")
            await self.select_tariff(query, plan_type)
        elif data.startswith("get_link_"):
            plan_type = data.replace("get_link_", "")
            await self.get_invite_link(query, plan_type, user_id)
        elif data.startswith("regenerate_link_"):
            plan_type = data.replace("regenerate_link_", "")
            await self.regenerate_link(query, plan_type, user_id)
    
    async def start_from_query(self, query):
        """Старт из callback query"""
        user_id = query.from_user.id
        current_time = self.format_moscow_time()
        user_plan = self.get_user_plan(user_id)
        
        keyboard = [
            [InlineKeyboardButton("💳 Тарифы", callback_data="show_tariffs")],
            [InlineKeyboardButton("🔍 Проверить подписку", callback_data="check_subscription")],
            [InlineKeyboardButton("📋 Моя подписка", callback_data="my_subscription")],
            [InlineKeyboardButton("🕐 Текущее время", callback_data="current_time")]
        ]
        
        if self.is_admin(user_id):
            keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = f"🤖 Бот для приватных подписок\n"
        welcome_text += f"🕐 Московское время: <b>{current_time}</b>\n\n"
        
        if self.is_admin(user_id):
            welcome_text += "👑 Вы администратор\n"
        elif user_plan["plan"] == "free":
            welcome_text += "❌ У вас нет активной подписки\n"
            welcome_text += "💳 Выберите тарифный план для получения доступа\n"
        else:
            plan_config = self.subscription_plans[user_plan["plan"]]
            welcome_text += f"✅ Ваш тариф: {plan_config['name']}\n"
            
            if "expires_at" in user_plan:
                expires_at = datetime.fromisoformat(user_plan["expires_at"]).replace(tzinfo=MOSCOW_TZ)
                days_left = (expires_at - datetime.now(MOSCOW_TZ)).days
                welcome_text += f"⏳ Дней осталось: {days_left}\n"
        
        await query.edit_message_text(
            welcome_text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    
    async def select_tariff(self, query, plan_type: str):
        """Выбор тарифа"""
        plan_config = self.subscription_plans.get(plan_type)
        
        if not plan_config:
            await query.edit_message_text("❌ Тариф не найден")
            return
        
        text = f"📋 Детали тарифа:\n\n{plan_config['name']}\n"
        text += f"💰 Цена: ${plan_config['price']}/месяц\n"
        text += f"📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
        text += f"📢 Каналов: {'∞' if plan_config['channels_limit'] == -1 else plan_config['channels_limit']}\n"
        text += f"⏳ Длительность: {plan_config.get('duration_days', 30)} дней\n\n"
        
        if plan_config.get('channel_id'):
            text += f"📢 Приватный канал: {plan_config.get('channel_name', 'Доступ по ссылке')}\n\n"
            text += "🔗 Для получения доступа нажмите кнопку ниже:"
            
            keyboard = [
                [InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"get_link_{plan_type}")],
                [InlineKeyboardButton("🔙 К тарифам", callback_data="show_tariffs")]
            ]
        else:
            text += "⚠️ Приватный канал еще не настроен.\nОбратитесь к администратору."
            
            keyboard = [
                [InlineKeyboardButton("🔙 К тарифам", callback_data="show_tariffs")]
            ]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def get_invite_link(self, query, plan_type: str, user_id: int):
        """Получить приватную ссылку"""
        plan_config = self.subscription_plans.get(plan_type)
        
        if not plan_config:
            await query.edit_message_text("❌ Тариф не найден")
            return
        
        channel_id = plan_config.get('channel_id')
        
        if not channel_id:
            await query.edit_message_text("❌ Канал не настроен для этого тарифа")
            return
        
        # Создаем новую ссылку
        invite_link = await self.create_invite_link(plan_type, user_id)
        
        if invite_link:
            text = f"🔗 Ваша приватная ссылка:\n\n"
            text += f"Тариф: {plan_config['name']}\n"
            text += f"Канал: {plan_config.get('channel_name', 'Приватный канал')}\n"
            text += f"⏱ Ссылка действует 24 часа\n\n"
            text += f"{invite_link}\n\n"
            text += "📋 Инструкция:\n"
            text += "1. Нажмите на ссылку выше\n"
            text += "2. Нажмите 'Присоединиться' в Telegram\n"
            text += "3. Вернитесь в бота и нажмите '🔍 Проверить подписку'"
            
            keyboard = [
                [InlineKeyboardButton("🔍 Проверить подписку", callback_data="check_subscription")],
                [InlineKeyboardButton("🔄 Новая ссылка", callback_data=f"regenerate_link_{plan_type}")],
                [InlineKeyboardButton("🔙 К тарифам", callback_data="show_tariffs")]
            ]
            
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                disable_web_page_preview=False
            )
        else:
            await query.edit_message_text(
                "❌ Не удалось создать ссылку.\n"
                "Возможные причины:\n"
                "• Бот не администратор канала\n"
                "• У бота нет прав создавать ссылки\n"
                "• Канал не существует\n\n"
                "Обратитесь к администратору.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"select_tariff_{plan_type}")]
                ])
            )
    
    async def regenerate_link(self, query, plan_type: str, user_id: int):
        """Создать новую ссылку"""
        await self.get_invite_link(query, plan_type, user_id)
    
    async def admin_settings(self, query):
        """Настройки тарифов для админа"""
        user_id = query.from_user.id
        
        if not self.is_admin(user_id):
            await query.answer("❌ У вас нет доступа", show_alert=True)
            return
        
        text = "⚙️ Настройка тарифов:\n\n"
        
        for plan_key, plan_config in self.subscription_plans.items():
            text += f"📋 {plan_config['name']}\n"
            text += f"   💰 Цена: ${plan_config['price']}/месяц\n"
            text += f"   📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
            text += f"   📢 Каналов: {'∞' if plan_config['channels_limit'] == -1 else plan_config['channels_limit']}\n"
            text += f"   🔒 Приватный канал: {'✅' if plan_config.get('channel_id') else '❌'}\n"
            if plan_config.get('channel_id'):
                text += f"   🆔 ID канала: {plan_config.get('channel_id')}\n"
                text += f"   📢 Название: {plan_config.get('channel_name', 'Не указано')}\n"
                text += f"   🔗 Ссылка: {'✅' if plan_config.get('invite_link') else '❌'}\n"
            text += "\n"
        
        keyboard = []
        for plan_key in self.subscription_plans:
            keyboard.append([
                InlineKeyboardButton(f"⚙️ {self.subscription_plans[plan_key]['name'].split('-')[0].strip()}", 
                                   callback_data=f"admin_edit_{plan_key}")
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def admin_links(self, query):
        """Управление ссылками для админа"""
        user_id = query.from_user.id
        
        if not self.is_admin(user_id):
            await query.answer("❌ У вас нет доступа", show_alert=True)
            return
        
        text = "🔗 Управление ссылками:\n\n"
        
        for plan_key, plan_config in self.subscription_plans.items():
            if plan_config.get('channel_id'):
                text += f"📋 {plan_config['name']}\n"
                text += f"   Канал: {plan_config.get('channel_name', 'Без названия')}\n"
                
                if plan_config.get('invite_link'):
                    text += f"   🔗 Текущая ссылка: Есть\n"
                    text += f"   {plan_config['invite_link']}\n"
                else:
                    text += f"   🔗 Текущая ссылка: Нет\n"
                
                text += "\n"
        
        keyboard = []
        for plan_key, plan_config in self.subscription_plans.items():
            if plan_config.get('channel_id'):
                keyboard.append([
                    InlineKeyboardButton(f"🔄 Обновить {plan_config['name'].split('-')[0].strip()}", 
                                       callback_data=f"admin_refresh_{plan_key}")
                ])
        
        keyboard.append([InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )
    
    async def admin_stats(self, query):
        """Статистика для админа"""
        user_id = query.from_user.id
        
        if not self.is_admin(user_id):
            await query.answer("❌ У вас нет доступа", show_alert=True)
            return
        
        total_users = len(self.user_subscriptions)
        active_subscriptions = len([sub for sub in self.user_subscriptions.values() 
                                  if not self.is_subscription_expired(list(self.user_subscriptions.keys())[list(self.user_subscriptions.values()).index(sub)])])
        
        plan_stats = {}
        for user_sub in self.user_subscriptions.values():
            plan = user_sub.get('plan', 'free')
            plan_stats[plan] = plan_stats.get(plan, 0) + 1
        
        text = "📊 Статистика бота:\n\n"
        text += f"👥 Всего пользователей: {total_users}\n"
        text += f"💳 Активных подписок: {active_subscriptions}\n\n"
        
        text += "📋 Распределение по тарифам:\n"
        for plan_key, plan_config in self.subscription_plans.items():
            count = plan_stats.get(plan_key, 0)
            text += f"   {plan_config['name']}: {count}\n"
        
        free_users = total_users - sum(plan_stats.values())
        text += f"   ❌ Без подписки: {free_users}\n\n"
        
        text += f"📈 Конверсия: {((total_users - free_users) / total_users * 100 if total_users > 0 else 0):.1f}%"
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")]
            ])
        )
    
    async def admin_users(self, query):
        """Управление пользователями"""
        user_id = query.from_user.id
        
        if not self.is_admin(user_id):
            await query.answer("❌ У вас нет доступа", show_alert=True)
            return
        
        if not self.user_subscriptions:
            text = "👥 Нет активных пользователей с подписками"
        else:
            text = "👥 Пользователи с подписками:\n\n"
            
            for uid, sub_data in list(self.user_subscriptions.items())[:10]:  # Показываем первых 10
                plan = sub_data.get('plan', 'free')
                plan_name = self.subscription_plans.get(plan, {}).get('name', 'Неизвестно')
                
                if "expires_at" in sub_data:
                    expires_at = datetime.fromisoformat(sub_data["expires_at"]).replace(tzinfo=MOSCOW_TZ)
                    days_left = (expires_at - datetime.now(MOSCOW_TZ)).days
                    status = f"✅ ({days_left} дн.)" if days_left > 0 else "❌ Истекла"
                else:
                    status = "❌ Нет данных"
                
                text += f"👤 ID: {uid}\n"
                text += f"   📦 {plan_name}\n"
                text += f"   📊 Статус: {status}\n\n"
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить", callback_data="admin_users")],
                [InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")]
            ])
        )
    
    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстовых сообщений"""
        # Пропускаем команды
        if update.message.text.startswith('/'):
            return
        
        # Простое эхо для нераспознанных сообщений
        await update.message.reply_text(
            "🤖 Используйте команды или кнопки для взаимодействия с ботом.\n"
            "/start - Начать работу\n"
            "/tariffs - Посмотреть тарифы\n"
            "/check - Проверить подписку"
        )

def main():
    """Основная функция запуска"""
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Установите BOT_TOKEN в переменные окружения или в коде!")
        print("1. Получите токен у @BotFather")
        print("2. Установите его в Railway как переменную окружения BOT_TOKEN")
        print("3. Или замените 'YOUR_BOT_TOKEN_HERE' на ваш токен в коде")
        return
    
    bot = SubscriptionBot(BOT_TOKEN)
    
    print("=" * 50)
    print("🤖 Бот для приватных подписок запущен!")
    print("=" * 50)
    print(f"👑 ID администратора: {ADMIN_ID}")
    print("🕐 Московское время")
    print("🔒 Приватные каналы: ✅")
    print("💳 Платные подписки: ✅")
    print("⚙️ Админ-панель: ✅")
    print("=" * 50)
    print("\n📋 Основные команды:")
    print("/start - Начать работу")
    print("/tariffs - Посмотреть тарифы")
    print("/check - Проверить подписку")
    print("/mysub - Моя подписка")
    print("/time - Текущее время")
    print("\n👑 Админ команды:")
    print("/admin - Админ панель")
    print("/setup <тариф> <id_канала> <название> - Настройка канала")
    print("/test <тариф> - Тестирование канала")
    print("/edittariff - Редактирование тарифов")
    print("=" * 50)
    
    bot.application.run_polling()

if __name__ == "__main__":
    main()
