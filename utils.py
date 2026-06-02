import re
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
import pytz


def extract_channel_username(text: str) -> Optional[str]:
    """Извлекает username Telegram-канала из текста/ссылки."""
    patterns = [
        r'(?:https?://)?t(?:elegram)?\.me/([a-zA-Z0-9_]+)',
        r'@([a-zA-Z0-9_]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def extract_youtube_channel_id(text: str) -> Optional[str]:
    """Извлекает ID канала YouTube из ссылки или @username."""
    patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})',
        r'(?:https?://)?(?:www\.)?youtube\.com/@([a-zA-Z0-9_-]+)',
        r'@([a-zA-Z0-9_-]+)',
        r'UC[a-zA-Z0-9_-]{22}'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def extract_youtube_video_id(text: str) -> Optional[str]:
    """Извлекает ID видео из ссылки на YouTube."""
    patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def extract_youtube_channel_username(text: str) -> Optional[str]:
    """Извлекает @username канала YouTube."""
    patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/@([a-zA-Z0-9_-]+)',
        r'@([a-zA-Z0-9_-]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def extract_video_id_from_url(url: str) -> Optional[str]:
    """
    Извлекает ID видео из ссылки на YouTube.
    """
    patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def calculate_score(post: dict, criteria: dict, post_time: datetime = None) -> Tuple[int, bool]:
    """Рассчитывает рейтинг поста на основе критериев."""
    views = post.get("views", 0)
    likes = post.get("likes", 0)
    reactions = post.get("reactions", 0)  # Для совместимости с Telegram
    
    min_views = criteria.get("min_views", 0)
    min_likes = criteria.get("min_likes", 0)
    min_reactions = criteria.get("min_reactions", 0)  # Для совместимости
    
    passes_criteria = True
    if min_views and views < min_views:
        passes_criteria = False
    if min_likes and likes < min_likes:
        passes_criteria = False
    if min_reactions and reactions < min_reactions:
        passes_criteria = False
    
    if not min_views and not min_likes and not min_reactions:
        passes_criteria = True
    
    if passes_criteria:
        score = 0
        if min_views:
            score += (views // 1000) * 10
        if min_likes:
            score += likes
        if min_reactions:
            score += reactions
        if post.get("thumbnail_url") or post.get("has_media", False):
            score += 5
        if score == 0:
            score = 1
        return (score, False)
    else:
        return (-1, True)


def clean_caption(text: str, exclude_phrases: List[str] = None) -> str:
    """Очищает текст от ссылок, рекламы и стоп-фраз."""
    if not text:
        return ""
    
    # Удаление ссылок на Telegram
    text = re.sub(r'(?:https?://)?t\.me/\S+', '', text)
    text = re.sub(r'(?:https?://)?telegram\.me/\S+', '', text)
    text = re.sub(r'@[a-zA-Z0-9_]+', '', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    
    # Удаление рекламных призывов
    ad_patterns = [
        r'[Пп]одписывай(?:те)?(?:сь)?\s*(?:на\s*)?(?:наш(?:и|у|его)?\s*)?(?:канал(?:ы|ов)?|паблик[и]?|сообщество|групп[уы])\s*(?:@?\w+\s*)?(?:[,.]?\s*(?:@?\w+\s*)*)*[.|!]?',
        r'[Сс]тавь(?:те)?\s*(?:лайк|👍|❤️?|🔥|класс)[^.]*\.?',
        r'[Пп]ереход(?:и|ите)?\s*по\s*ссылк[еи][^.]*\.?',
        r'[Пп]одпи(?:шись|сывайся|шитесь)[^.]*\.?',
        r'(?:MDK|MAX)\s*[|]\s*(?:MDK|MAX)',
        r'📢\s*@?\w+\s*[➡️👉→]+\s*@?\w+',
        r'Наши?\s*каналы?\s*[➡️👉→]*\s*@?\w+',
    ]
    for pattern in ad_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    # Удаление подписей источников со стрелками
    text = re.sub(r'[📢📣🔔➡️👉⬇️👇→]+[^.!?\n]{0,150}$', '', text)
    text = re.sub(r'\s*➡️\s*\S+\s*$', '', text)
    text = re.sub(r'\s*→\s*\S+\s*$', '', text)
    text = re.sub(r'\s*⬇️\s*\S+\s*$', '', text)
    text = re.sub(r'\s*👇\s*\S+\s*$', '', text)
    
    # Стоп-фразы
    if exclude_phrases:
        for phrase in exclude_phrases:
            phrase = phrase.strip()
            if phrase:
                escaped = re.escape(phrase)
                text = re.sub(escaped, '', text, flags=re.IGNORECASE)
    
    # Очистка форматирования
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r' +', ' ', text)
    text = text.strip()
    
    # Ограничение длины
    if len(text) > 1024:
        text = text[:1021] + "..."
    
    return text


def calculate_next_post_time(project) -> Optional[datetime]:
    """Рассчитывает следующее время публикации."""
    moscow_tz = pytz.timezone("Europe/Moscow")
    now_moscow = datetime.now(moscow_tz)
    
    current_hour = now_moscow.hour
    if current_hour < project.active_hours_start:
        return now_moscow.replace(hour=project.active_hours_start, minute=0, second=0, microsecond=0)
    
    if current_hour >= project.active_hours_end:
        return now_moscow.replace(hour=project.active_hours_start, minute=0, second=0, microsecond=0) + timedelta(days=1)
    
    next_time = now_moscow + timedelta(hours=project.post_interval_hours)
    if next_time.hour >= project.active_hours_end:
        next_time = now_moscow.replace(hour=project.active_hours_start, minute=0, second=0, microsecond=0) + timedelta(days=1)
    
    return next_time


def get_moscow_time() -> datetime:
    """Возвращает текущее время в Москве."""
    moscow_tz = pytz.timezone("Europe/Moscow")
    return datetime.now(moscow_tz)


def format_datetime(dt: datetime) -> str:
    """Форматирует дату в читаемый вид."""
    if not dt:
        return "никогда"
    moscow_tz = pytz.timezone("Europe/Moscow")
    if dt.tzinfo is None:
        dt = moscow_tz.localize(dt)
    return dt.strftime("%d.%m.%Y %H:%M")


def format_number(num: int) -> str:
    """Форматирует число с суффиксами K, M."""
    if num >= 1000000:
        return f"{num/1000000:.1f}M"
    elif num >= 1000:
        return f"{num/1000:.1f}K"
    return str(num)


def parse_number(text: str) -> int:
    """Парсит число из текста (поддерживает K, M)."""
    if not text:
        return 0
    text = str(text).strip().upper().replace(" ", "")
    text = text.replace(",", ".")
    
    if "K" in text:
        return int(float(text.replace("K", "")) * 1000)
    elif "M" in text:
        return int(float(text.replace("M", "")) * 1000000)
    else:
        try:
            clean = re.sub(r'[^\d.]', '', text)
            if clean:
                return int(float(clean))
        except:
            pass
    return 0