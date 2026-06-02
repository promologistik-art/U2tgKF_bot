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
from utils import calculate_score, get_moscow_time, extract_video_id_from_url
from config import Config

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, poster: TelegramPoster):
        self.poster = poster
        self._running = False
        self._tasks = {}
        self._last_daily_report = None
        self._last_check = {}

    async def start(self):
        self._running = True
        logger.info("🟢 YouTube Scheduler started")
        
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
            if self._last_daily_report != today:
                self._last_daily_report = today
                await self._send_daily_report()

    async def _send_daily_report(self):
        try:
            now = datetime.utcnow()
            yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(User))
                users_count = len(result.scalars().all())
                
                result = await session.execute(select(Project).where(Project.is_active == True))
                projects = result.scalars().all()
                projects_count = len(projects)
                
                result = await session.execute(select(SourceChannel).where(SourceChannel.is_active == True))
                sources_count = len(result.scalars().all())
                
                result = await session.execute(
                    select(PostQueue).where(PostQueue.created_at >= yesterday, PostQueue.created_at < today_start)
                )
                total_parsed = len(result.scalars().all())
                
                result = await session.execute(
                    select(PostQueue).where(PostQueue.status == "published", PostQueue.published_at >= yesterday, PostQueue.published_at < today_start)
                )
                published_posts = result.scalars().all()
                total_posted = len(published_posts)
                
                project_posted = {}
                for p in published_posts:
                    project_posted[p.project_id] = project_posted.get(p.project_id, 0) + 1
                
                top3_ids = sorted(project_posted, key=project_posted.get, reverse=True)[:3]
                top3 = []
                for pid in top3_ids:
                    for p in projects:
                        if p.id == pid:
                            top3.append((p.name, project_posted[pid]))
                            break
                
                result = await session.execute(select(PostQueue).where(PostQueue.status == "pending"))
                pending = len(result.scalars().all())
                
                result = await session.execute(
                    select(PostQueue).where(PostQueue.status == "failed", PostQueue.created_at >= yesterday, PostQueue.created_at < today_start)
                )
                failed = len(result.scalars().all())
            
            yesterday_str = yesterday.strftime('%d.%m.%Y')
            text = f"📊 <b>U2TG отчёт за {yesterday_str}</b>\n\n"
            text += f"👥 Пользователей: {users_count}\n"
            text += f"📁 Проектов: {projects_count}\n"
            text += f"📥 Источников: {sources_count}\n"
            text += f"🔄 Спарсено: {total_parsed}\n"
            text += f"📤 Опубликовано: {total_posted}\n"
            text += f"📬 В очереди: {pending}\n"
            text += f"❌ Ошибок публикации: {failed}\n"
            
            if top3:
                text += f"\n🏆 <b>Топ-{len(top3)} активных проекта:</b>\n"
                for name, count in top3:
                    text += f"• «{name}» — {count} постов\n"
            
            from telegram import Bot
            bot = Bot(token=Config.BOT_TOKEN)
            await bot.send_message(chat_id=Config.ADMIN_ID, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Daily report failed: {e}")

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

    async def _download_media(self, video_url: str, save_path: str, max_duration: int = None) -> bool:
        """Скачивает видео через yt-dlp. Если не установлен — скачивает превью."""
        try:
            import yt_dlp
            
            ydl_opts = {
                'outtmpl': save_path.replace('.mp4', '') + '.%(ext)s',
                'format': 'best[height<=720]',
                'quiet': True,
                'no_warnings': True,
                'max_filesize': 50 * 1024 * 1024,  # 50 MB max
            }
            
            if max_duration:
                ydl_opts['duration'] = max_duration
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._do_download(ydl_opts, video_url))
            
            # yt-dlp может изменить расширение
            import glob
            base = save_path.replace('.mp4', '')
            files = glob.glob(base + '.*')
            if files:
                real_path = files[0]
                if os.path.getsize(real_path) > 1000:
                    # Переименовываем в ожидаемый путь
                    if real_path != save_path:
                        os.rename(real_path, save_path)
                    return True
            
            return False
        except ImportError:
            logger.info("yt-dlp not installed, downloading thumbnail instead")
            return await self._download_thumbnail(video_url, save_path.replace('.mp4', '.jpg'))
        except Exception as e:
            logger.error(f"Video download failed: {e}")
            return False

    def _do_download(self, ydl_opts, url):
        import yt_dlp
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    async def _download_thumbnail(self, url: str, save_path: str) -> bool:
        """Скачивает превью видео."""
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

    async def _process_project(self, project: Project):
        logger.info(f"🔍 Processing YouTube project '{project.name}' (ID: {project.id})")
        
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
                            continue
                        videos = await scraper.get_videos_from_channel(source.youtube_channel_id, limit=20)
                    elif source.source_type == "link":
                        if not source.youtube_link_url:
                            continue
                        video = await scraper.get_video_by_url(source.youtube_link_url)
                        if video:
                            videos = [video]
                    elif source.source_type == "search":
                        query = source.youtube_search_query
                        if not query:
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
                                    continue
                            except:
                                pass
                    
                    if source.include_keywords:
                        keywords = [k.strip().lower() for k in source.include_keywords.split(",") if k.strip()]
                        video_text = (video.get("title", "") + " " + video.get("description", "")).lower()
                        if not any(keyword in video_text for keyword in keywords):
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
                            logger.info(f"⏰ Video too long: {dur}s > {source.max_video_duration}s")
                            continue
                    
                    logger.info(
                        f"🏆 Selected from '{source.name}': score={best_score}, "
                        f"title='{best_video.get('title', '')[:30]}...'"
                    )
                    
                    await mark_post_parsed(project.id, source.id, best_video["url"])
                    total_parsed += 1
                    
                    # Скачивание видео (или превью если yt-dlp не установлен)
                    media_downloaded = False
                    video_url = best_video.get("url", "")
                    if video_url:
                        ext = "mp4"
                        filename = f"{uuid.uuid4()}.{ext}"
                        media_path = os.path.join(Config.TEMP_DIR, filename)
                        
                        if await self._download_media(video_url, media_path, source.max_video_duration):
                            best_video["media_path"] = media_path
                            best_video["media_type"] = "video"
                            media_downloaded = True
                            logger.info(f"💾 Video saved: {media_path}")
                        else:
                            # Fallback: скачиваем превью
                            thumb_url = best_video.get("thumbnail_url", "")
                            if thumb_url:
                                thumb_path = os.path.join(Config.TEMP_DIR, f"{uuid.uuid4()}.jpg")
                                if await self._download_thumbnail(thumb_url, thumb_path):
                                    best_video["media_path"] = thumb_path
                                    best_video["media_type"] = "photo"
                                    media_downloaded = True
                    
                    has_text = bool(best_video.get("title", "").strip())
                    if not has_text and not media_downloaded:
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
            interval_minutes = max(project.post_interval_hours, user.min_post_interval_minutes)
            start_hour = project.active_hours_start
            end_hour = project.active_hours_end
            
            minutes_since_start = (msk_now.hour - start_hour) * 60 + msk_now.minute
            if minutes_since_start < 0:
                next_time = msk_now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            else:
                slots = (minutes_since_start + interval_minutes - 1) // interval_minutes
                next_time = msk_now.replace(hour=start_hour, minute=0, second=0, microsecond=0) + timedelta(minutes=slots * interval_minutes)
            
            if next_time.hour >= end_hour:
                next_time = next_time.replace(hour=start_hour, minute=0, second=0, microsecond=0) + timedelta(days=1)
            
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
                logger.info(f"📅 Post {i+1} scheduled for {next_time.strftime('%d.%m.%Y %H:%M')} MSK")
            
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
        logger.info("🔴 YouTube Scheduler stopped")