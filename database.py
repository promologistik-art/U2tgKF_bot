import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, text
from datetime import datetime, timedelta
from config import Config
from models import Base, User, Project

logger = logging.getLogger(__name__)

os.makedirs(Config.TEMP_DIR, exist_ok=True)
os.makedirs(Config.BACKUP_DIR, exist_ok=True)

DATABASE_URL = Config.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=5, max_overflow=10)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

parsed_urls = {}


async def init_db():
    """Создаёт таблицы и добавляет недостающие колонки."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # === ДОБАВЛЕНИЕ КОЛОНКИ download_mode (исправленная версия) ===
    table_name = f"{Config.TABLE_PREFIX}source_channels"
    async with AsyncSessionLocal() as session:
        # Проверяем существование колонки через information_schema
        result = await session.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table_name AND column_name = 'download_mode'"
                ")"
            ),
            {"table_name": table_name}
        )
        exists = result.scalar()
        
        if not exists:
            try:
                await session.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN download_mode VARCHAR DEFAULT 'preview';")
                )
                await session.commit()
                logger.info(f"✅ Added column 'download_mode' to {table_name}")
            except Exception as e:
                logger.error(f"Failed to add column: {e}")
                await session.rollback()
        else:
            logger.info(f"ℹ️ Column 'download_mode' already exists in {table_name}")
    # ============================================================
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == Config.ADMIN_ID))
        admin = result.scalar_one_or_none()
        if not admin:
            admin = User(
                telegram_id=Config.ADMIN_ID, is_admin=True, tariff="unlimited",
                max_projects=999, max_sources_per_project=999,
                min_post_interval_minutes=1, min_check_interval_minutes=5,
                subscription_active=True,
                trial_ends_at=datetime.utcnow() + timedelta(days=36500)
            )
            session.add(admin)
            await session.commit()
            logger.info("Admin created")
        
        result = await session.execute(select(Project).where(Project.user_id == Config.ADMIN_ID))
        if not result.scalars().all():
            project = Project(user_id=Config.ADMIN_ID, name="Админский")
            session.add(project)
            await session.commit()
    
    logger.info(f"✅ Database initialized (prefix: {Config.TABLE_PREFIX})")


async def is_post_parsed(project_id: int, post_url: str) -> bool:
    cache_key = f"{project_id}:{post_url}"
    if cache_key in parsed_urls:
        return True
    async with AsyncSessionLocal() as session:
        from models import ParsedPost
        result = await session.execute(
            select(ParsedPost).where(
                ParsedPost.project_id == project_id,
                ParsedPost.post_url == post_url
            )
        )
        exists = result.scalar_one_or_none() is not None
        if exists:
            parsed_urls[cache_key] = True
        return exists


async def mark_post_parsed(project_id: int, source_channel_id: int, post_url: str):
    cache_key = f"{project_id}:{post_url}"
    parsed_urls[cache_key] = True
    async with AsyncSessionLocal() as session:
        from models import ParsedPost
        result = await session.execute(
            select(ParsedPost).where(
                ParsedPost.project_id == project_id,
                ParsedPost.post_url == post_url
            )
        )
        if result.scalar_one_or_none():
            return
        post = ParsedPost(
            project_id=project_id,
            source_channel_id=source_channel_id,
            post_url=post_url
        )
        session.add(post)
        try:
            await session.commit()
        except:
            await session.rollback()


async def clear_parsed_cache():
    count = len(parsed_urls)
    parsed_urls.clear()
    logger.info(f"🧹 Parsed URLs cache cleared ({count} entries)")