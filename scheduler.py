import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta
from sqlalchemy import select, update
from database import AsyncSessionLocal, is_post_parsed, mark_post_parsed
from models import User, Project, SourceChannel, TargetChannel, PostQueue, PublishedPost
from scrapers import YouTubeScraper
from posters import TelegramPoster
from utils import calculate_score, get_moscow_time
from config import Config

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, poster: TelegramPoster):
        self.poster = poster
        self._running = False
        self._tasks = {}
        self._last_daily_cleanup = None
        self._last_check = {}

    async def start(self):
        self._running = True
        logger.info("🟢 Scheduler started")
        
        while self._running:
            try:
                await self._check_projects()
                await self._check_daily_tasks()
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)

    async def _check_daily_tasks(self):
        now = get_moscow_time()
        if now.hour == 9 and now.minute == 0:
            today = now.date()
            if self._last_daily_cleanup != today:
                self._last_daily_cleanup = today
                from database import clear_parsed_cache
                await clear_parsed_cache()
                logger.info("🧹 Parsed URLs cache cleared (daily)")

    async def _check_projects(self):
        now = datetime.utcnow()
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Project).where(Project.is_active == True))
            projects = result.scalars().all()
        
        for project in projects:
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(User).where(User.telegram_id == project.user_id))
                user = result.scalar_one_or_none()
                if not user:
                    continue
                
                if not user.is_admin:
                    has_access = False
                    if user.subscription_active and user.subscription_ends_at and user.subscription_ends_at > now:
                        has_access = True
                    elif user.trial_ends_at and user.trial_ends_at > now:
                        has_access = True
                    if not has_access:
                        continue
                
                interval = project.check_interval_minutes
                if not user.is_admin:
                    interval = max(interval, user.min_check_interval_minutes)
                
                last_check = self._last_check.get(project.id)
                if last_check:
                    elapsed = (now - last_check).total_seconds() / 60
                    if elapsed < interval:
                        continue
                
                self._last_check[project.id] = now
                
                task_key = f"project_{project.id}"
                if task_key not in self._tasks or self._tasks[task_key].done():
                    task = asyncio.create_task(self._process_project(project))
                    self._tasks[task_key] = task
                    logger.info(f"⏰ Project '{project.name}' (ID: {project.id}) scheduled")

    async def _download_thumbnail(self, url: str, save_path: str) -> bool:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        if len(content) > 1000:
                            with open(save_path, "wb") as f:
                                f.write(content)
                            return True
            return False
        except Exception as e:
            logger.error(f"Thumbnail download error: {e}")
            return False

    async def _download_full_video(self, scraper, video_url: str, save_path: str) -> bool:
        try:
            return await scraper.download_full_video(video_url, save_path)
        except Exception as e:
            logger.error(f"Full video download error: {e}")
            return False

    async def _get_last_scheduled_time(self, project_id: int) -> datetime:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(PostQueue)
                .where(PostQueue.project_id == project_id, PostQueue.status == "pending")
                .order_by(PostQueue.scheduled_time.desc())
                .limit(1)
            )
            last_queued = result.scalar_one_or_none()
            if last_queued:
                return last_queued.scheduled_time
            return None

    async def _process_project(self, project: Project):
        logger.info(f"🔍 Processing project '{project.name}' (ID: {project.id})")
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.telegram_id == project.user_id))
            user = result.scalar_one_or_none()
            if not user:
                return
            
            if not user.is_admin:
                has_access = False
                now = datetime.utcnow()
                if user.subscription_active and user.subscription_ends_at and user.subscription_ends_at > now:
                    has_access = True
                elif user.trial_ends_at and user.trial_ends_at > now:
                    has_access = True
                if not has_access:
                    return
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SourceChannel).where(SourceChannel.project_id == project.id, SourceChannel.is_active == True)
            )
            sources = result.scalars().all()
            
            result = await session.execute(
                select(TargetChannel).where(TargetChannel.project_id == project.id, TargetChannel.is_active == True)
            )
            target = result.scalar_one_or_none()
        
        if not sources or not target:
            logger.warning(f"⚠️ Project '{project.name}' has no sources or target")
            return
        
        logger.info(f"📊 Project '{project.name}': {len(sources)} sources → {target.channel_title or '—'}")
        
        posts_to_publish = []
        total_parsed = 0
        
        async with YouTubeScraper() as scraper:
            for source in sources:
                logger.info(f"📡 Fetching from '{source.name}' (type: {source.source_type})")
                
                try:
                    videos = []
                    if source.source_type == "channel":
                        if not source.youtube_channel_id:
                            logger.warning(f"Channel ID missing for source {source.id}")
                            continue
                        videos = await scraper.get_videos_from_channel(source.youtube_channel_id, limit=20)
                    
                    elif source.source_type == "link":
                        if not source.youtube_link_url:
                            logger.warning(f"Link URL missing for source {source.id}")
                            continue
                        video = await scraper.get_video_by_url(source.youtube_link_url)
                        if video:
                            videos = [video]
                    
                    elif source.source_type == "search":
                        query = source.youtube_search_query
                        if not query:
                            logger.warning(f"Search query missing for source {source.id}")
                            continue
                        videos = await scraper.search_videos(
                            query=query,
                            country=source.youtube_country,
                            category=source.youtube_category,
                            content_type=source.youtube_content_type,
                            limit=20
                        )
                    
                    logger.info(f"📨 '{source.name}': {len(videos)} videos fetched")
                
                except Exception as e:
                    logger.error(f"❌ Failed to fetch from '{source.name}': {e}")
                    continue
                
                best_video = None
                best_score = -1
                
                for video in videos:
                    if await is_post_parsed(project.id, video["url"]):
                        continue
                    
                    if source.max_age_hours and source.max_age_hours > 0:
                        if video.get("published_at"):
                            try:
                                published = datetime.fromisoformat(video["published_at"].replace("Z", "+00:00"))
                                age_hours = (datetime.utcnow() - published).total_seconds() / 3600
                                if age_hours > source.max_age_hours:
                                    logger.debug(f"⏭️ Video too old: {age_hours:.1f}h > {source.max_age_hours}h")
                                    continue
                            except:
                                pass
                    
                    if source.include_keywords:
                        keywords = [k.strip().lower() for k in source.include_keywords.split(",") if k.strip()]
                        video_text = (video.get("title", "") + " " + video.get("description", "")).lower()
                        if not any(keyword in video_text for keyword in keywords):
                            logger.debug(f"⏭️ No keywords in video '{video.get('title', '')}'")
                            continue
                    
                    if source.media_filter == "shorts_only":
                        if not video.get("is_shorts", False):
                            continue
                    elif source.media_filter == "long_only":
                        if video.get("is_shorts", False):
                            continue
                    
                    video["source_name"] = source.name
                    video["source_type"] = source.source_type
                    video["media_filter"] = source.media_filter
                    video["remove_original_text"] = source.remove_original_text
                    video["max_video_duration"] = source.max_video_duration
                    video["exclude_phrases"] = source.exclude_phrases
                    
                    score, is_fallback = calculate_score(video, source.criteria)
                    if is_fallback:
                        continue
                    
                    if score > best_score:
                        best_score = score
                        best_video = video
                
                if best_video:
                    if source.max_video_duration and source.max_video_duration > 0:
                        dur = best_video.get("duration_seconds", 0)
                        if dur > 0 and dur > source.max_video_duration:
                            logger.info(f"⏰ Video too long from '{source.name}': {dur}s > {source.max_video_duration}s")
                            continue
                    
                    logger.info(
                        f"🏆 Selected from '{source.name}': score={best_score}, "
                        f"title='{best_video.get('title', '')[:30]}...', "
                        f"duration={best_video.get('duration_seconds', 0)}s"
                    )
                    
                    await mark_post_parsed(project.id, source.id, best_video["url"])
                    total_parsed += 1
                    
                    download_mode = 'full_shorts'
                    media_downloaded = False
                    media_path = None
                    media_type = None
                    
                    if download_mode == 'full_shorts':
                        video_url = best_video.get("url")
                        if video_url:
                            filename = f"{uuid.uuid4()}.mp4"
                            media_path = os.path.join(Config.TEMP_DIR, filename)
                            if await self._download_full_video(scraper, video_url, media_path):
                                media_downloaded = True
                                media_type = "video"
                                logger.info(f"💾 Full video saved: {media_path}")
                    
                    if not media_downloaded:
                        logger.warning(f"⚠️ Media download failed for '{source.name}'")
                        continue
                    
                    best_video["media_path"] = media_path
                    best_video["media_type"] = media_type
                    
                    has_text = bool(best_video.get("title", "").strip()) or bool(best_video.get("description", "").strip())
                    if not has_text and not media_downloaded:
                        logger.info(f"📭 Empty video from '{source.name}', skipping")
                        continue
                    
                    posts_to_publish.append(best_video)
                    
                    async with AsyncSessionLocal() as session:
                        await session.execute(
                            update(SourceChannel)
                            .where(SourceChannel.id == source.id)
                            .values(last_parsed=datetime.utcnow(), last_post_url=best_video["url"])
                        )
                        await session.commit()
                else:
                    logger.info(f"😴 '{source.name}': no suitable videos")
        
        if posts_to_publish:
            logger.info(f"📤 Found {len(posts_to_publish)} videos to queue")
            
            msk_now = get_moscow_time().replace(tzinfo=None)
            interval_minutes = max(project.post_interval_minutes, user.min_post_interval_minutes)
            start_hour = project.active_hours_start
            end_hour = project.active_hours_end
            
            last_scheduled_utc = await self._get_last_scheduled_time(project.id)
            
            # Вычисляем время для ПЕРВОГО видео
            if last_scheduled_utc:
                last_scheduled_msk = last_scheduled_utc + timedelta(hours=3)
                next_time = last_scheduled_msk + timedelta(minutes=interval_minutes)
            else:
                next_time = msk_now
            
            # Если первое время в прошлом — сдвигаем на ближайший будущий слот
            if next_time <= msk_now:
                minutes_since_start = (msk_now.hour - start_hour) * 60 + msk_now.minute
                if minutes_since_start < 0:
                    next_time = msk_now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
                else:
                    slots = (minutes_since_start + interval_minutes - 1) // interval_minutes
                    next_time = msk_now.replace(hour=start_hour, minute=0, second=0, microsecond=0) + timedelta(minutes=slots * interval_minutes)
            
            if next_time.hour >= end_hour:
                next_time = next_time.replace(hour=start_hour, minute=0, second=0, microsecond=0) + timedelta(days=1)
            
            # Разносим все видео с интервалом
            for i, video in enumerate(posts_to_publish):
                if i > 0:
                    next_time = next_time + timedelta(minutes=interval_minutes)
                    if next_time.hour >= end_hour:
                        next_time = next_time.replace(hour=start_hour, minute=0, second=0, microsecond=0) + timedelta(days=1)
                
                utc_time = next_time - timedelta(hours=3)
                
                await self.poster.add_to_queue(
                    project_id=project.id,
                    target_channel_id=target.id,
                    post_data=video,
                    scheduled_time=utc_time,
                    platform=target.platform
                )
                logger.info(f"📅 Video {i+1} scheduled for {next_time.strftime('%d.%m.%Y %H:%M')} MSK")
            
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Project).where(Project.id == project.id))
                db_project = result.scalar_one()
                today = datetime.utcnow().date()
                if db_project.last_reset.date() < today:
                    db_project.posts_parsed_today = 0
                    db_project.posts_posted_today = 0
                    db_project.last_reset = datetime.utcnow()
                db_project.posts_parsed_today += total_parsed
                await session.commit()
        
        logger.info(f"✅ Project '{project.name}' processing completed")

    async def stop(self):
        self._running = False
        for task_key, task in self._tasks.items():
            if not task.done():
                task.cancel()
        logger.info("🔴 Scheduler stopped")