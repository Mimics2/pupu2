from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, timedelta
import pytz

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(100))
    first_name = Column(String(100))
    last_name = Column(String(100))
    balance = Column(Integer, default=0)  # Звёзды
    tariff = Column(String(50))
    subscription_end = Column(DateTime)
    joined_channel = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    channels = relationship("UserChannel", back_populates="user")
    posts = relationship("ScheduledPost", back_populates="user")
    payments = relationship("Payment", back_populates="user")

class UserChannel(Base):
    __tablename__ = 'user_channels'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    channel_id = Column(String(100), nullable=False)
    channel_name = Column(String(200))
    channel_link = Column(String(500))
    is_active = Column(Boolean, default=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="channels")
    posts = relationship("ScheduledPost", back_populates="channel")

class ScheduledPost(Base):
    __tablename__ = 'scheduled_posts'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    channel_id = Column(Integer, ForeignKey('user_channels.id'))
    content = Column(Text)
    media_type = Column(String(20))  # photo, video, document, etc
    media_file_id = Column(String(500))
    schedule_time = Column(DateTime, nullable=False)
    is_published = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="posts")
    channel = relationship("UserChannel", back_populates="posts")

class Payment(Base):
    __tablename__ = 'payments'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    amount = Column(Integer)  # Звёзды
    tariff = Column(String(50))
    is_completed = Column(Boolean, default=False)
    telegram_payment_id = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="payments")

# Инициализация базы данных
def init_db():
    from config import Config
    
    if Config.DATABASE_URL.startswith('postgresql'):
        engine = create_engine(Config.DATABASE_URL)
    else:
        engine = create_engine(Config.DATABASE_URL, connect_args={'check_same_thread': False})
    
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()

# Функции для работы с пользователями
def get_or_create_user(session, telegram_id, username, first_name, last_name):
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if not user:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name
        )
        session.add(user)
        session.commit()
    return user

def get_user_subscription_info(session, user_id):
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        return None
    
    now = datetime.utcnow()
    is_active = user.subscription_end and user.subscription_end > now
    
    return {
        'tariff': user.tariff,
        'subscription_end': user.subscription_end,
        'is_active': is_active,
        'channels_count': len([c for c in user.channels if c.is_active]),
        'posts_today': len([p for p in user.posts 
                          if p.created_at.date() == now.date() and not p.is_published])
    }
