import logging
import asyncio
import sys
from datetime import datetime, timedelta
from typing import Optional

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    LabeledPrice,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters
)
from telegram.constants import ParseMode
from telegram.error import Conflict

from config import Config
from database import init_db, get_or_create_user, get_user_subscription_info, User, UserChannel, ScheduledPost, Payment
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
import pytz

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация базы данных
session = init_db()

# Инициализация планировщика
scheduler = AsyncIOScheduler(timezone="UTC")

class TelegramBot:
    def __init__(self):
        self.config = Config
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        db_user = get_or_create_user(session, user.id, user.username, user.first_name, user.last_name)
        
        welcome_text = (
            f"👋 Привет, {user.first_name}!\n\n"
            "🤖 Я бот для автоматической публикации контента в Telegram каналах.\n\n"
            "✨ <b>Возможности:</b>\n"
            "• Публикация постов с медиа\n"
            "• Планирование публикаций\n"
            "• Подписка через Telegram Stars\n"
            "• Управление несколькими каналами\n\n"
            "📊 <b>Тарифы:</b>\n"
        )
        
        for key, tariff in self.config.TARIFFS.items():
            welcome_text += (
                f"• <b>{tariff['name']}</b> - {tariff['stars']} звёзд\n"
                f"  └ {tariff['channels_limit']} канала, {tariff['posts_per_day']} постов/день\n"
            )
        
        keyboard = [
            [InlineKeyboardButton("📅 Запланировать пост", callback_data="schedule_post")],
            [InlineKeyboardButton("💎 Тарифы и подписка", callback_data="tariffs")],
            [InlineKeyboardButton("📊 Мои каналы", callback_data="my_channels"),
             InlineKeyboardButton("📝 Мои посты", callback_data="my_posts")],
            [InlineKeyboardButton("👤 Профиль", callback_data="profile")]
        ]
        
        if user.id == self.config.ADMIN_ID:
            keyboard.append([InlineKeyboardButton("⚙️ Админ панель", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def schedule_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню планирования поста"""
        query = update.callback_query
        await query.answer()
        
        user_info = get_user_subscription_info(session, query.from_user.id)
        
        if not user_info or not user_info['is_active']:
            await query.edit_message_text(
                "❌ У вас нет активной подписки!\n"
                "Приобретите тариф, чтобы использовать бота.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💎 Тарифы", callback_data="tariffs")]
                ])
            )
            return
        
        # Проверка лимита постов на сегодня
        if user_info['posts_today'] >= self.config.TARIFFS[user_info['tariff']]['posts_per_day']:
            await query.edit_message_text(
                f"❌ Вы исчерпали лимит постов на сегодня ({user_info['posts_today']}/"
                f"{self.config.TARIFFS[user_info['tariff']]['posts_per_day']}).\n"
                "Лимит обновится в 00:00 по UTC.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Профиль", callback_data="profile")]
                ])
            )
            return
        
        keyboard = [
            [InlineKeyboardButton("🚀 Опубликовать сейчас", callback_data="post_now")],
            [InlineKeyboardButton("⏰ Через час", callback_data="post_1h")],
            [InlineKeyboardButton("🕐 Через 3 часа", callback_data="post_3h")],
            [InlineKeyboardButton("📅 Выбрать дату", callback_data="custom_date")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(
            "📅 <b>Выберите время публикации:</b>\n\n"
            "Формат даты: <code>2025.12.31 14:30</code>\n"
            "Часовой пояс: UTC",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        context.user_data['post_step'] = 'select_time'
    
    async def handle_time_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка выбора времени"""
        query = update.callback_query
        await query.answer()
        
        now = datetime.utcnow()
        
        if query.data == "post_now":
            schedule_time = now
        elif query.data == "post_1h":
            schedule_time = now + timedelta(hours=1)
        elif query.data == "post_3h":
            schedule_time = now + timedelta(hours=3)
        elif query.data == "custom_date":
            await query.edit_message_text(
                "📝 <b>Введите дату и время в формате:</b>\n"
                "<code>2025.12.31 14:30</code>\n\n"
                "Пример: <code>2024.01.15 09:00</code>",
                parse_mode=ParseMode.HTML
            )
            context.user_data['post_step'] = 'waiting_custom_date'
            return
        else:
            return
        
        context.user_data['schedule_time'] = schedule_time
        await self.request_post_content(update, context)
    
    async def request_post_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Запрос контента для поста"""
        query = update.callback_query
        
        schedule_time = context.user_data.get('schedule_time')
        if schedule_time:
            time_str = schedule_time.strftime("%Y.%m.%d %H:%M")
            text = f"🕐 <b>Время публикации:</b> {time_str} (UTC)\n\n"
        else:
            text = ""
        
        text += (
            "📝 <b>Отправьте контент для поста:</b>\n\n"
            "• Текст поста\n"
            "• Фото/видео/документ с подписью\n\n"
            "Или нажмите отмена:"
        )
        
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="main_menu")]]
        
        if query:
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        
        context.user_data['post_step'] = 'waiting_content'
    
    async def handle_custom_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода пользовательской даты"""
        try:
            date_str = update.message.text.strip()
            schedule_time = datetime.strptime(date_str, "%Y.%m.%d %H:%M")
            schedule_time = pytz.UTC.localize(schedule_time)
            
            now = datetime.now(pytz.UTC)
            if schedule_time < now:
                await update.message.reply_text(
                    "❌ Нельзя выбрать прошедшее время!\n"
                    "Введите будущую дату:"
                )
                return
            
            context.user_data['schedule_time'] = schedule_time
            await self.request_post_content(update, context)
            
        except ValueError:
            await update.message.reply_text(
                "❌ Неверный формат даты!\n"
                "Используйте: <code>2025.12.31 14:30</code>\n"
                "Попробуйте снова:",
                parse_mode=ParseMode.HTML
            )
    
    async def handle_post_content(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка контента поста"""
        user_id = update.effective_user.id
        
        if 'post_step' not in context.user_data:
            return
        
        # Получаем информацию о каналах пользователя
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            await update.message.reply_text("❌ Пользователь не найден!")
            return
            
        channels = [c for c in user.channels if c.is_active]
        
        if not channels:
            await update.message.reply_text(
                "❌ У вас нет подключенных каналов!\n"
                "Добавьте каналы в настройках."
            )
            return
        
        # Создаем клавиатуру для выбора канала
        keyboard = []
        for channel in channels:
            keyboard.append([InlineKeyboardButton(
                f"📢 {channel.channel_name}", 
                callback_data=f"select_channel_{channel.id}"
            )])
        
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="main_menu")])
        
        # Сохраняем контент
        message = update.message
        context.user_data['post_content'] = message.text or message.caption
        context.user_data['post_media'] = None
        
        if message.photo:
            context.user_data['post_media'] = message.photo[-1].file_id
            context.user_data['media_type'] = 'photo'
        elif message.video:
            context.user_data['post_media'] = message.video.file_id
            context.user_data['media_type'] = 'video'
        elif message.document:
            context.user_data['post_media'] = message.document.file_id
            context.user_data['media_type'] = 'document'
        
        await message.reply_text(
            "📢 <b>Выберите канал для публикации:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        context.user_data['post_step'] = 'select_channel'
    
    async def confirm_and_schedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Подтверждение и сохранение запланированного поста"""
        query = update.callback_query
        await query.answer()
        
        channel_id = int(query.data.split('_')[-1])
        channel = session.query(UserChannel).get(channel_id)
        
        if not channel:
            await query.edit_message_text("❌ Канал не найден!")
            return
        
        # Сохраняем пост в БД
        new_post = ScheduledPost(
            user_id=channel.user_id,
            channel_id=channel_id,
            content=context.user_data.get('post_content', ''),
            media_type=context.user_data.get('media_type'),
            media_file_id=context.user_data.get('post_media'),
            schedule_time=context.user_data['schedule_time'],
            is_published=False
        )
        
        session.add(new_post)
        session.commit()
        
        # Планируем публикацию
        await self.schedule_publication(new_post.id, context.application)
        
        time_str = context.user_data['schedule_time'].strftime("%Y.%m.%d %H:%M")
        
        await query.edit_message_text(
            f"✅ <b>Пост запланирован!</b>\n\n"
            f"📅 <b>Время:</b> {time_str} (UTC)\n"
            f"📢 <b>Канал:</b> {channel.channel_name}\n\n"
            f"Пост будет опубликован автоматически.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 Новый пост", callback_data="schedule_post")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
            ])
        )
        
        # Очищаем временные данные
        for key in ['post_step', 'schedule_time', 'post_content', 'post_media', 'media_type']:
            if key in context.user_data:
                del context.user_data[key]
    
    async def schedule_publication(self, post_id: int, application):
        """Планирование публикации поста"""
        post = session.query(ScheduledPost).get(post_id)
        
        if not post:
            return
        
        trigger = DateTrigger(run_date=post.schedule_time)
        
        scheduler.add_job(
            self.publish_scheduled_post,
            trigger,
            args=[post_id, application],
            id=f"post_{post_id}",
            replace_existing=True
        )
    
    async def publish_scheduled_post(self, post_id: int, application):
        """Публикация запланированного поста"""
        post = session.query(ScheduledPost).get(post_id)
        
        if not post or post.is_published:
            return
        
        channel = post.channel
        
        try:
            if post.media_type == 'photo':
                await application.bot.send_photo(
                    chat_id=channel.channel_id,
                    photo=post.media_file_id,
                    caption=post.content or None,
                    parse_mode=ParseMode.HTML
                )
            elif post.media_type == 'video':
                await application.bot.send_video(
                    chat_id=channel.channel_id,
                    video=post.media_file_id,
                    caption=post.content or None,
                    parse_mode=ParseMode.HTML
                )
            elif post.media_type == 'document':
                await application.bot.send_document(
                    chat_id=channel.channel_id,
                    document=post.media_file_id,
                    caption=post.content or None,
                    parse_mode=ParseMode.HTML
                )
            else:
                await application.bot.send_message(
                    chat_id=channel.channel_id,
                    text=post.content or " ",
                    parse_mode=ParseMode.HTML
                )
            
            post.is_published = True
            session.commit()
            
            # Уведомляем пользователя
            await application.bot.send_message(
                chat_id=post.user.telegram_id,
                text=f"✅ Пост опубликован в канале '{channel.channel_name}'!"
            )
            
        except Exception as e:
            logger.error(f"Ошибка публикации поста {post_id}: {e}")
            await application.bot.send_message(
                chat_id=post.user.telegram_id,
                text=f"❌ Ошибка публикации поста в '{channel.channel_name}': {str(e)}"
            )
    
    async def show_tariffs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показ тарифов"""
        query = update.callback_query
        await query.answer()
        
        text = "💎 <b>Доступные тарифы:</b>\n\n"
        
        for key, tariff in self.config.TARIFFS.items():
            text += (
                f"✨ <b>{tariff['name']}</b>\n"
                f"   Стоимость: {tariff['stars']} звёзд\n"
                f"   Каналов: {tariff['channels_limit']}\n"
                f"   Постов в день: {tariff['posts_per_day']}\n"
                f"   Длительность: {tariff['duration_days']} дней\n\n"
            )
        
        keyboard = []
        for key, tariff in self.config.TARIFFS.items():
            keyboard.append([InlineKeyboardButton(
                f"Купить {tariff['name']} - {tariff['stars']} звёзд",
                callback_data=f"buy_{key}"
            )])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def process_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка покупки тарифа"""
        query = update.callback_query
        await query.answer()
        
        tariff_key = query.data.split('_')[1]
        tariff = self.config.TARIFFS.get(tariff_key)
        
        if not tariff:
            await query.edit_message_text("❌ Тариф не найден!")
            return
        
        # Здесь должен быть код для создания платежа через Telegram Stars
        # Вместо этого сделаем симуляцию платежа для демонстрации
        
        user_id = query.from_user.id
        user = session.query(User).filter_by(telegram_id=user_id).first()
        
        # Создаем запись о платеже
        payment = Payment(
            user_id=user.id,
            amount=tariff['stars'],
            tariff=tariff_key,
            is_completed=True  # В реальности проверять через API
        )
        session.add(payment)
        
        # Активируем подписку
        from datetime import datetime, timedelta
        user.tariff = tariff_key
        user.subscription_end = datetime.utcnow() + timedelta(days=tariff['duration_days'])
        session.commit()
        
        # Отправляем приглашение в приватный канал
        if self.config.PRIVATE_CHANNEL_LINK:
            await query.edit_message_text(
                f"✅ <b>Подписка активирована!</b>\n\n"
                f"Тариф: {tariff['name']}\n"
                f"Действует до: {user.subscription_end.strftime('%Y.%m.%d %H:%M')} UTC\n\n"
                f"Приглашение в приватный канал: {self.config.PRIVATE_CHANNEL_LINK}\n\n"
                f"⚠️ Подписка автоматически отменится через {tariff['duration_days']} дней.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📅 Запланировать пост", callback_data="schedule_post")],
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
                ])
            )
        else:
            await query.edit_message_text(
                f"✅ <b>Подписка активирована!</b>\n\n"
                f"Тариф: {tariff['name']}\n"
                f"Действует до: {user.subscription_end.strftime('%Y.%m.%d %H:%M')} UTC\n\n"
                f"⚠️ Свяжитесь с администратором для получения доступа к приватному каналу.",
                parse_mode=ParseMode.HTML
            )
    
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Админ панель"""
        query = update.callback_query
        await query.answer()
        
        if query.from_user.id != self.config.ADMIN_ID:
            await query.edit_message_text("❌ Доступ запрещен!")
            return
        
        # Статистика
        total_users = session.query(User).count()
        active_users = session.query(User).filter(
            User.subscription_end > datetime.utcnow()
        ).count()
        
        total_payments = session.query(Payment).filter_by(is_completed=True).count()
        total_revenue = sum(p.amount for p in session.query(Payment).filter_by(is_completed=True).all())
        
        scheduled_posts = session.query(ScheduledPost).filter_by(is_published=False).count()
        published_posts = session.query(ScheduledPost).filter_by(is_published=True).count()
        
        text = (
            f"⚙️ <b>Админ панель</b>\n\n"
            f"👥 <b>Пользователи:</b>\n"
            f"• Всего: {total_users}\n"
            f"• Активных: {active_users}\n\n"
            f"💰 <b>Финансы:</b>\n"
            f"• Всего платежей: {total_payments}\n"
            f"• Общий доход: {total_revenue} звёзд\n\n"
            f"📊 <b>Посты:</b>\n"
            f"• Запланировано: {scheduled_posts}\n"
            f"• Опубликовано: {published_posts}"
        )
        
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("📥 Экспорт БД", callback_data="export_db")],
            [InlineKeyboardButton("⚙️ Настройка тарифов", callback_data="admin_tariffs")],
            [InlineKeyboardButton("📢 Управление каналами", callback_data="admin_channels")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def export_database(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Экспорт базы данных"""
        query = update.callback_query
        await query.answer()
        
        if query.from_user.id != self.config.ADMIN_ID:
            return
        
        # Экспортируем пользователей
        users = session.query(User).all()
        user_data = []
        
        for user in users:
            user_data.append({
                'id': user.telegram_id,
                'username': user.username,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'tariff': user.tariff,
                'subscription_end': user.subscription_end.isoformat() if user.subscription_end else None,
                'balance': user.balance
            })
        
        # Сохраняем в файл
        import json
        import io
        
        data = {
            'users': user_data,
            'export_date': datetime.utcnow().isoformat()
        }
        
        json_data = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        file = io.BytesIO(json_data.encode())
        file.name = f"bot_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        
        await context.bot.send_document(
            chat_id=self.config.ADMIN_ID,
            document=file,
            caption="📊 Экспорт базы данных пользователей"
        )
        
        await query.edit_message_text(
            "✅ База данных экспортирована и отправлена вам в личные сообщения.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 В админку", callback_data="admin_panel")]
            ])
        )
    
    async def main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Возврат в главное меню"""
        query = update.callback_query
        await query.answer()
        
        user = update.effective_user
        
        keyboard = [
            [InlineKeyboardButton("📅 Запланировать пост", callback_data="schedule_post")],
            [InlineKeyboardButton("💎 Тарифы и подписка", callback_data="tariffs")],
            [InlineKeyboardButton("📊 Мои каналы", callback_data="my_channels"),
             InlineKeyboardButton("📝 Мои посты", callback_data="my_posts")],
            [InlineKeyboardButton("👤 Профиль", callback_data="profile")]
        ]
        
        if user.id == self.config.ADMIN_ID:
            keyboard.append([InlineKeyboardButton("⚙️ Админ панель", callback_data="admin_panel")])
        
        await query.edit_message_text(
            f"🏠 <b>Главное меню</b>\n\n"
            f"Привет, {user.first_name}!\n"
            f"Выберите действие:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def show_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показ профиля пользователя"""
        query = update.callback_query
        await query.answer()
        
        user_info = get_user_subscription_info(session, query.from_user.id)
        
        if user_info and user_info['is_active']:
            tariff_name = self.config.TARIFFS[user_info['tariff']]['name']
            status = "✅ Активна"
            end_date = user_info['subscription_end'].strftime("%Y.%m.%d %H:%M")
        else:
            tariff_name = "Нет"
            status = "❌ Не активна"
            end_date = "—"
        
        text = (
            f"👤 <b>Ваш профиль</b>\n\n"
            f"🆔 ID: {query.from_user.id}\n"
            f"📛 Имя: {query.from_user.first_name}\n"
            f"📧 Юзернейм: @{query.from_user.username or 'не указан'}\n\n"
            f"💎 <b>Подписка:</b>\n"
            f"• Тариф: {tariff_name}\n"
            f"• Статус: {status}\n"
            f"• До: {end_date} UTC\n"
            f"• Каналов: {user_info['channels_count'] if user_info else 0}\n"
            f"• Постов сегодня: {user_info['posts_today'] if user_info else 0}"
        )
        
        keyboard = [
            [InlineKeyboardButton("💎 Тарифы", callback_data="tariffs")],
            [InlineKeyboardButton("📊 Мои каналы", callback_data="my_channels")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    async def check_subscriptions(self, application):
        """Проверка подписок и кик просроченных пользователей"""
        now = datetime.utcnow()
        
        expired_users = session.query(User).filter(
            User.subscription_end < now - timedelta(hours=self.config.KICK_AFTER_EXPIRY),
            User.joined_channel == True
        ).all()
        
        for user in expired_users:
            try:
                # Пытаемся кикнуть из канала
                await application.bot.ban_chat_member(
                    chat_id=self.config.PRIVATE_CHANNEL_ID,
                    user_id=user.telegram_id
                )
                
                # Разбаниваем, чтобы пользователь мог вступить снова
                await application.bot.unban_chat_member(
                    chat_id=self.config.PRIVATE_CHANNEL_ID,
                    user_id=user.telegram_id
                )
                
                user.joined_channel = False
                session.commit()
                
                # Уведомляем пользователя
                await application.bot.send_message(
                    chat_id=user.telegram_id,
                    text="❌ Ваша подписка истекла. Доступ к приватному каналу закрыт."
                )
                
            except Exception as e:
                logger.error(f"Ошибка при кике пользователя {user.telegram_id}: {e}")
    
    def setup_handlers(self, application):
        """Настройка обработчиков"""
        
        # Команды
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("admin", self.admin_panel))
        
        # Обработчики callback-запросов
        application.add_handler(CallbackQueryHandler(self.schedule_post, pattern="^schedule_post$"))
        application.add_handler(CallbackQueryHandler(self.handle_time_selection, pattern="^post_"))
        application.add_handler(CallbackQueryHandler(self.show_tariffs, pattern="^tariffs$"))
        application.add_handler(CallbackQueryHandler(self.process_payment, pattern="^buy_"))
        application.add_handler(CallbackQueryHandler(self.admin_panel, pattern="^admin_panel$"))
        application.add_handler(CallbackQueryHandler(self.export_database, pattern="^export_db$"))
        application.add_handler(CallbackQueryHandler(self.main_menu, pattern="^main_menu$"))
        application.add_handler(CallbackQueryHandler(self.show_profile, pattern="^profile$"))
        application.add_handler(CallbackQueryHandler(self.confirm_and_schedule, pattern="^select_channel_"))
        application.add_handler(CallbackQueryHandler(self.request_post_content, pattern="^custom_date$"))
        
        # Обработчики сообщений
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(r'^\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}$'),
            self.handle_custom_date
        ))
        
        application.add_handler(MessageHandler(
            filters.TEXT | filters.PHOTO | filters.VIDEO | filters.ATTACHMENT,
            self.handle_post_content
        ))
    
    def run_with_retry(self):
        """Запуск бота с повторными попытками при конфликте"""
        max_retries = 3
        retry_delay = 10  # секунд
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Starting bot (attempt {attempt + 1}/{max_retries})...")
                
                application = Application.builder().token(self.config.BOT_TOKEN).build()
                
                self.setup_handlers(application)
                
                # Запускаем планировщик
                scheduler.start()
                
                # Запускаем проверку подписок каждые 30 минут
                scheduler.add_job(
                    self.check_subscriptions,
                    'interval',
                    minutes=30,
                    args=[application]
                )
                
                logger.info("Bot started successfully!")
                application.run_polling(
                    allowed_updates=Update.ALL_TYPES,
                    close_loop=False
                )
                break
                
            except Conflict as e:
                logger.warning(f"Conflict detected: {e}")
                if attempt < max_retries - 1:
                    logger.info(f"Waiting {retry_delay} seconds before retry...")
                    import time
                    time.sleep(retry_delay)
                    # Увеличиваем задержку для следующей попытки
                    retry_delay *= 2
                else:
                    logger.error("Max retries reached. Exiting.")
                    raise
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                raise

def main():
    """Главная функция"""
    bot = TelegramBot()
    
    try:
        bot.run_with_retry()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
