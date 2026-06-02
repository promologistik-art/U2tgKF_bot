import logging
from io import BytesIO
from telegram import Update
from telegram.ext import ContextTypes
from scrapers import YouTubeScraper
from utils import format_number

logger = logging.getLogger(__name__)


async def test_scraper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестирует парсер YouTube."""
    if not context.args:
        await update.message.reply_text(
            "ℹ️ /test [channel_id | video_url | search_query]\n"
            "Примеры:\n"
            "/test UCxxxxx — канал\n"
            "/test https://youtube.com/watch?v=xxxxx — видео\n"
            "/test как приготовить пиццу — поиск"
        )
        return
    
    query = context.args[0]
    msg = await update.message.reply_text(f"🔍 Тестирую YouTube: {query[:50]}...")
    
    async with YouTubeScraper() as scraper:
        videos = []
        
        # Пробуем как URL видео
        video = await scraper.get_video_by_url(query)
        if video:
            videos = [video]
        
        # Пробуем как channel_id
        if not videos:
            channel_id = scraper._extract_channel_id(query)
            if channel_id:
                videos = await scraper.get_videos_from_channel(channel_id, limit=5)
        
        # Пробуем как поисковый запрос
        if not videos:
            videos = await scraper.search_videos(query, limit=5)
        
        if videos:
            text = f"📨 Найдено: {len(videos)}\n\n"
            for i, v in enumerate(videos[:5], 1):
                text += f"{i}. <b>{v['title'][:50]}...</b>\n"
                text += f"   👁 {format_number(v['views'])} | ❤️ {format_number(v['likes'])} | 💬 {format_number(v['comments'])}\n"
                text += f"   📹 {v['url']}\n"
                text += f"   🕐 {v['duration_seconds']} сек\n\n"
            await msg.edit_text(text, parse_mode="HTML")
        else:
            await msg.edit_text("❌ Видео не найдены")


async def debug_reactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает детальную информацию о видео (аналог реакций для YouTube)."""
    if not context.args:
        await update.message.reply_text("ℹ️ /debug_reactions [video_url]\nПример: /debug_reactions https://youtube.com/watch?v=xxxxx")
        return
    
    url = context.args[0]
    msg = await update.message.reply_text(f"🔍 Анализирую видео: {url[:50]}...")
    
    async with YouTubeScraper() as scraper:
        video = await scraper.get_video_by_url(url)
        
        if not video:
            await msg.edit_text("❌ Видео не найдено")
            return
        
        # Формируем отчёт
        result_lines = []
        result_lines.append(f"=== Детали видео ===")
        result_lines.append(f"Название: {video['title']}")
        result_lines.append(f"Канал: {video['channel_title']} ({video['channel_id']})")
        result_lines.append(f"Просмотры: {format_number(video['views'])}")
        result_lines.append(f"Лайки: {format_number(video['likes'])}")
        result_lines.append(f"Комментарии: {format_number(video['comments'])}")
        result_lines.append(f"Длительность: {video['duration_seconds']} сек")
        result_lines.append(f"Шортс: {'✅' if video['is_shorts'] else '❌'}")
        result_lines.append(f"Опубликовано: {video['published_at']}")
        result_lines.append(f"Ссылка: {video['url']}")
        result_lines.append(f"Превью: {video['thumbnail_url']}")
        
        full_result = "\n".join(result_lines)
        
        # Отправляем как файл
        result_file = BytesIO(full_result.encode('utf-8'))
        result_file.name = f"video_{video['video_id']}.txt"
        
        await msg.delete()
        await update.message.reply_document(
            document=result_file,
            filename=f"video_{video['video_id']}.txt",
            caption=f"🔍 Детальный анализ видео"
        )