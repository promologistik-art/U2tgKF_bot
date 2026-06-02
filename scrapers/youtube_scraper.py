import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from config import Config

logger = logging.getLogger(__name__)


class YouTubeScraper:
    def __init__(self):
        self.youtube = build('youtube', 'v3', developerKey=Config.YOUTUBE_API_KEY)
        self.max_results = Config.YOUTUBE_MAX_RESULTS

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get_channel_info(self, channel_id: str) -> Optional[Dict]:
        """Получает информацию о канале по ID."""
        try:
            request = self.youtube.channels().list(
                part='snippet,statistics',
                id=channel_id
            )
            response = request.execute()
            
            items = response.get('items', [])
            if not items:
                return None
            
            channel = items[0]
            return {
                'id': channel['id'],
                'title': channel['snippet']['title'],
                'description': channel['snippet']['description'],
                'subscriber_count': int(channel['statistics'].get('subscriberCount', 0)),
                'video_count': int(channel['statistics'].get('videoCount', 0)),
                'view_count': int(channel['statistics'].get('viewCount', 0))
            }
        except HttpError as e:
            logger.error(f"YouTube API error (channel info): {e}")
            return None

    async def get_videos_from_channel(self, channel_id: str, limit: int = 20) -> List[Dict]:
        """Получает последние видео с канала."""
        try:
            request = self.youtube.search().list(
                part='snippet',
                channelId=channel_id,
                maxResults=limit,
                order='date',
                type='video'
            )
            response = request.execute()
            
            video_ids = [item['id']['videoId'] for item in response.get('items', []) if item['id']['kind'] == 'youtube#video']
            if not video_ids:
                return []
            
            # Получаем детали видео (просмотры, лайки, длительность)
            videos = await self._get_video_details(video_ids)
            return videos
        except HttpError as e:
            logger.error(f"YouTube API error (channel videos): {e}")
            return []

    async def get_video_by_url(self, url: str) -> Optional[Dict]:
        """Получает одно видео по ссылке."""
        # Извлекаем ID видео из URL
        video_id = self._extract_video_id(url)
        if not video_id:
            logger.warning(f"Could not extract video ID from URL: {url}")
            return None
        
        videos = await self._get_video_details([video_id])
        return videos[0] if videos else None

    async def search_videos(
        self, 
        query: str, 
        country: Optional[str] = None, 
        category: Optional[str] = None,
        content_type: str = "all",
        limit: int = 20
    ) -> List[Dict]:
        """Поиск видео по запросу с фильтрами."""
        try:
            # Определяем тип контента (shorts/long)
            video_duration = None
            if content_type == "shorts":
                video_duration = "short"  # до 60 секунд
            elif content_type == "long":
                video_duration = "long"  # более 20 минут
            
            # Строим запрос
            search_params = {
                'part': 'snippet',
                'q': query,
                'maxResults': limit,
                'type': 'video',
                'order': 'relevance',
                'videoDuration': video_duration,
                'publishedAfter': (datetime.utcnow() - timedelta(days=Config.YOUTUBE_SEARCH_DAYS)).isoformat() + 'Z'
            }
            
            if country:
                search_params['regionCode'] = country.upper()
            
            request = self.youtube.search().list(**search_params)
            response = request.execute()
            
            video_ids = [item['id']['videoId'] for item in response.get('items', []) if item['id']['kind'] == 'youtube#video']
            if not video_ids:
                return []
            
            videos = await self._get_video_details(video_ids)
            return videos
        except HttpError as e:
            logger.error(f"YouTube API error (search): {e}")
            return []

    async def _get_video_details(self, video_ids: List[str]) -> List[Dict]:
        """Получает детали видео (просмотры, лайки, длительность)."""
        if not video_ids:
            return []
        
        try:
            videos = []
            # Разбиваем на чанки по 50 (ограничение API)
            for i in range(0, len(video_ids), 50):
                chunk = video_ids[i:i+50]
                request = self.youtube.videos().list(
                    part='snippet,statistics,contentDetails',
                    id=','.join(chunk)
                )
                response = request.execute()
                
                for item in response.get('items', []):
                    video = self._parse_video(item)
                    videos.append(video)
            
            return videos
        except HttpError as e:
            logger.error(f"YouTube API error (video details): {e}")
            return []

    def _parse_video(self, item: Dict) -> Dict:
        """Парсит ответ API в единый формат."""
        snippet = item.get('snippet', {})
        statistics = item.get('statistics', {})
        content_details = item.get('contentDetails', {})
        
        # Получаем длительность в секундах
        duration_str = content_details.get('duration', 'PT0S')
        duration_seconds = self._parse_duration(duration_str)
        
        # Определяем тип видео (shorts или обычное)
        is_shorts = duration_seconds <= 60
        
        video_id = item['id']
        thumbnails = snippet.get('thumbnails', {})
        thumbnail_url = thumbnails.get('high', {}).get('url') or thumbnails.get('medium', {}).get('url') or thumbnails.get('default', {}).get('url')
        
        return {
            'url': f"https://www.youtube.com/watch?v={video_id}",
            'video_id': video_id,
            'title': snippet.get('title', ''),
            'description': snippet.get('description', ''),
            'channel_id': snippet.get('channelId', ''),
            'channel_title': snippet.get('channelTitle', ''),
            'published_at': snippet.get('publishedAt', ''),
            'views': int(statistics.get('viewCount', 0)),
            'likes': int(statistics.get('likeCount', 0)),
            'comments': int(statistics.get('commentCount', 0)),
            'duration_seconds': duration_seconds,
            'is_shorts': is_shorts,
            'thumbnail_url': thumbnail_url,
            'media_type': 'video'
        }

    def _parse_duration(self, duration_str: str) -> int:
        """Парсит длительность видео из ISO 8601 (PT1H2M3S)."""
        import re
        pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
        match = re.match(pattern, duration_str)
        if not match:
            return 0
        
        hours = int(match.group(1)) if match.group(1) else 0
        minutes = int(match.group(2)) if match.group(2) else 0
        seconds = int(match.group(3)) if match.group(3) else 0
        return hours * 3600 + minutes * 60 + seconds

    def _extract_video_id(self, url: str) -> Optional[str]:
        """Извлекает ID видео из URL."""
        import re
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

    def _extract_channel_id(self, url_or_input: str) -> Optional[str]:
        """Извлекает ID канала из URL или ввода."""
        import re
        patterns = [
            r'(?:https?://)?(?:www\.)?youtube\.com/@([a-zA-Z0-9_-]+)',
            r'(?:https?://)?(?:www\.)?youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})',
            r'@([a-zA-Z0-9_-]+)',
            r'UC[a-zA-Z0-9_-]{22}'
        ]
        for pattern in patterns:
            match = re.search(pattern, url_or_input)
            if match:
                # Если нашли @username, нужно преобразовать в channel_id через API
                username = match.group(1)
                if not username.startswith('UC'):
                    channel_id = self._get_channel_id_by_username(username)
                    if channel_id:
                        return channel_id
                else:
                    return username
        return None

    async def _get_channel_id_by_username(self, username: str) -> Optional[str]:
        """Получает ID канала по username (через API)."""
        try:
            request = self.youtube.channels().list(
                part='id',
                forHandle=username
            )
            response = request.execute()
            items = response.get('items', [])
            if items:
                return items[0]['id']
            return None
        except HttpError:
            return None