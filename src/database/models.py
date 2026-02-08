"""SQLAlchemy ORM модели для всех таблиц."""

from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime, Date,
    ForeignKey, JSON, func
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Order(Base):
    """Заказы с Автор24."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    avtor24_id = Column(String(20), unique=True, nullable=False)
    title = Column(Text, nullable=False)
    work_type = Column(String(100))
    subject = Column(String(200))
    description = Column(Text)
    pages_min = Column(Integer)
    pages_max = Column(Integer)
    font_size = Column(Integer, default=14)
    line_spacing = Column(Float, default=1.5)
    required_uniqueness = Column(Integer)
    antiplagiat_system = Column(String(50))
    deadline = Column(DateTime)
    budget_rub = Column(Integer)

    # Наша работа
    bid_price = Column(Integer)
    bid_comment = Column(Text)
    bid_placed_at = Column(DateTime)
    score = Column(Integer)
    status = Column(String(30), default="new")

    generated_file_path = Column(Text)
    uniqueness_percent = Column(Float)

    # Финансы
    income_rub = Column(Integer)
    api_cost_usd = Column(Float)
    api_tokens_used = Column(Integer)

    # Мета
    customer_username = Column(String(100))
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Связи
    messages = relationship("Message", back_populates="order")
    action_logs = relationship("ActionLog", back_populates="order")
    notifications = relationship("Notification", back_populates="order")
    api_usages = relationship("ApiUsage", back_populates="order")


class Message(Base):
    """Сообщения чата с заказчиком."""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    direction = Column(String(10))  # 'incoming' или 'outgoing'
    text = Column(Text, nullable=False)
    is_auto_reply = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

    order = relationship("Order", back_populates="messages")


class ActionLog(Base):
    """Логи действий бота."""

    __tablename__ = "action_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(50))
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    details = Column(Text)
    created_at = Column(DateTime, default=func.now())

    order = relationship("Order", back_populates="action_logs")


class DailyStat(Base):
    """Ежедневная статистика."""

    __tablename__ = "daily_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False)
    bids_placed = Column(Integer, default=0)
    orders_accepted = Column(Integer, default=0)
    orders_delivered = Column(Integer, default=0)
    income_rub = Column(Integer, default=0)
    api_cost_usd = Column(Float, default=0)
    api_tokens_used = Column(Integer, default=0)


class Notification(Base):
    """Уведомления для дашборда."""

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(30), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    title = Column(Text, nullable=False)
    body = Column(JSON, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

    order = relationship("Order", back_populates="notifications")


class BotSetting(Base):
    """Настройки бота, редактируемые через дашборд."""

    __tablename__ = "bot_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class ApiUsage(Base):
    """Детальный трекинг использования API."""

    __tablename__ = "api_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    model = Column(String(50), nullable=False)
    purpose = Column(String(30), nullable=False)
    input_tokens = Column(Integer, nullable=False)
    output_tokens = Column(Integer, nullable=False)
    cost_usd = Column(Float, nullable=False)
    created_at = Column(DateTime, default=func.now())

    order = relationship("Order", back_populates="api_usages")
