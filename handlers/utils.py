import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from database import AsyncSessionLocal
from models import User, Project, SourceChannel, TargetChannel
from config import Config
from .constants import CURRENT_PROJECT_KEY

logger = logging.getLogger(__name__)

TARIFF_LIMITS = {
    "trial": {"max_projects": 1, "max_sources_per_project": 3, "min_post_interval": 120, "min_check_interval": 180, "name": "Пробный"},
    "basic": {"max_projects": 1, "max_sources_per_project": 3, "min_post_interval": 120, "min_check_interval": 120, "name": "Базовый"},
    "standard": {"max_projects": 3, "max_sources_per_project": 5, "min_post_interval": 60, "min_check_interval": 120, "name": "Стандарт"},
    "pro": {"max_projects": 10, "max_sources_per_project": 10, "min_post_interval": 30, "min_check_interval": 60, "name": "PRO"},
    "unlimited": {"max_projects": 999, "max_sources_per_project": 999, "min_post_interval": 1, "min_check_interval": 60, "name": "Безлимит"},
    "god": {"max_projects": 9999, "max_sources_per_project": 9999, "min_post_interval": 1, "min_check_interval": 60, "name": "God"}
}


def get_tariff_limits(tariff: str) -> dict:
    """Возвращает лимиты для тарифа."""
    return TARIFF_LIMITS.get(tariff, TARIFF_LIMITS["trial"])


async def get_current_project(telegram_id: int, context: ContextTypes.DEFAULT_TYPE) -> Project:
    """Возвращает текущий проект пользователя из кэша или первый активный."""
    project_id = context.user_data.get(CURRENT_PROJECT_KEY)
    async with AsyncSessionLocal() as session:
        if project_id:
            result = await session.execute(
                select(Project).where(Project.id == project_id, Project.user_id == telegram_id)
            )
            project = result.scalar_one_or_none()
            if project:
                return project
        # Если кэш пуст или проект не найден — берём первый активный
        result = await session.execute(
            select(Project).where(Project.user_id == telegram_id, Project.is_active == True).order_by(Project.id)
        )
        project = result.scalars().first()
        if project:
            context.user_data[CURRENT_PROJECT_KEY] = project.id
        return project


async def require_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Project:
    """Требует наличия проекта. Если нет — отправляет сообщение и возвращает None."""
    telegram_id = update.effective_user.id
    project = await get_current_project(telegram_id, context)
    if not project:
        await update.message.reply_text(
            "❌ У вас нет проектов.\nСоздайте через /my_projects"
        )
        return None
    return project


async def is_admin(telegram_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        return user and user.is_admin


async def check_user_access(telegram_id: int) -> tuple:
    """Проверяет доступ пользователя к боту."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            return False, "❌ Пользователь не найден", None
        if user.is_admin:
            return True, "", user
        
        now = datetime.utcnow()
        if user.subscription_active and user.subscription_ends_at and user.subscription_ends_at > now:
            return True, "", user
        if user.trial_ends_at and user.trial_ends_at > now:
            days_left = (user.trial_ends_at - now).days
            return True, f"🎁 Триал активен ({days_left} дн.)", user
        
        return False, "❌ Доступ закончился", user


async def check_action_limit(user: User, action: str, **kwargs) -> tuple:
    """Проверяет, не превышен ли лимит для действия."""
    limits = get_tariff_limits(user.tariff)
    
    if action == "create_project":
        count = await get_user_projects_count(user.telegram_id)
        if count >= limits["max_projects"]:
            return False, f"❌ Лимит проектов ({limits['max_projects']})"
    
    elif action == "add_source":
        project_id = kwargs.get("project_id")
        if project_id:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(func.count()).select_from(SourceChannel).where(SourceChannel.project_id == project_id)
                )
                if result.scalar() >= limits["max_sources_per_project"]:
                    return False, f"❌ Лимит источников ({limits['max_sources_per_project']})"
    
    elif action == "set_post_interval":
        interval = kwargs.get("interval_minutes", 0)
        if interval < limits["min_post_interval"]:
            return False, f"❌ Мин. интервал: {limits['min_post_interval']} мин"
    
    elif action == "set_check_interval":
        interval = kwargs.get("interval_minutes", 0)
        if interval < limits["min_check_interval"]:
            return False, f"❌ Мин. интервал: {limits['min_check_interval']} мин"
    
    return True, ""


async def setup_bot_commands(application):
    """Устанавливает список команд бота в меню."""
    from telegram import BotCommand
    commands = [
        BotCommand("start", "🏠 Главное меню"),
        BotCommand("my_projects", "📁 Мои проекты"),
        BotCommand("add_source", "📥 Добавить источник"),
        BotCommand("add_target", "📤 Добавить цель"),
        BotCommand("my_sources", "📊 Источники"),
        BotCommand("my_targets", "🎯 Цели"),
        BotCommand("set_interval", "⏰ Интервал парсинга"),
        BotCommand("set_post_interval", "📅 Интервал публикации"),
        BotCommand("set_signature", "✍️ Подпись"),
        BotCommand("status", "📈 Статистика"),
        BotCommand("parse", "🔄 Парсинг сейчас"),
        BotCommand("queue", "📬 Очередь"),
        BotCommand("postnow", "🚀 Пост сейчас"),
        BotCommand("help", "📋 Помощь"),
    ]
    await application.bot.set_my_commands(commands)


async def get_sources_count(project_id: int) -> int:
    """Возвращает количество активных источников в проекте."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count()).select_from(SourceChannel).where(SourceChannel.project_id == project_id)
        )
        return result.scalar()


async def get_project_target(project_id: int) -> TargetChannel:
    """Возвращает целевой канал проекта."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TargetChannel).where(TargetChannel.project_id == project_id, TargetChannel.is_active == True)
        )
        return result.scalar_one_or_none()


async def get_user_projects_count(telegram_id: int) -> int:
    """Возвращает количество проектов пользователя."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count()).select_from(Project).where(Project.user_id == telegram_id)
        )
        return result.scalar()


async def send_project_ready_message(update: Update, project_name: str):
    """Отправляет сообщение о готовности проекта."""
    text = (
        f"✅ <b>Проект «{project_name}» готов!</b>\n\n"
        f"• /set_interval — настроить частоту парсинга\n"
        f"• /set_post_interval — настроить интервал публикации\n"
        f"• /set_signature — добавить подпись\n"
        f"• /parse — запустить первый парсинг"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def update_user_limits(user: User, tariff: str):
    """Обновляет лимиты пользователя в соответствии с тарифом."""
    limits = get_tariff_limits(tariff)
    user.tariff = tariff
    user.max_projects = limits["max_projects"]
    user.max_sources_per_project = limits["max_sources_per_project"]
    user.min_post_interval_minutes = limits["min_post_interval"]
    user.min_check_interval_minutes = limits["min_check_interval"]
    async with AsyncSessionLocal() as session:
        await session.merge(user)
        await session.commit()