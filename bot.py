#!/usr/bin/env python3
"""
YouTube Content Bot — U2TG
Version: 1.2.0 (08.06.2026) — Reply-based source addition
"""

import asyncio
import logging
import sys
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters
)

from config import Config
from database import init_db
from worker_reg import register_self
from handlers import (
    start, help_command, cancel,
    my_projects, projects_callback, project_menu_callback, handle_project_name,
    back_to_projects_callback,
    add_source_start,
    youtube_source_type_callback,
    youtube_country_callback,
    youtube_category_callback,
    youtube_content_type_callback,
    add_source_criteria,
    media_filter_callback,
    duration_callback,
    remove_text_callback,
    add_keywords_skip_callback,
    handle_source_input,
    handle_edit_reply,
    my_sources, edit_source_callback, delete_source_callback,
    confirm_delete_source_callback, cancel_delete_source_callback,
    back_to_sources_callback,
    edit_source_start,
    edit_media_filter_callback,
    edit_remove_text_callback,
    add_target_start, add_target_forward, add_target_continue_callback,
    my_targets, delete_target_callback,
    set_interval_start, set_interval_callback,
    set_post_interval_start, set_post_interval_callback,
    set_post_start_time_callback,
    set_signature_start, set_signature_input,
    set_interval_start_callback, set_post_interval_start_callback,
    set_signature_start_callback,
    status, project_stats,
    parse_now, queue_status, post_now,
    clear_old_queue, clear_failed_queue, clear_all_queue, clear_project_queue,
    reset_history,
    admin_panel, admin_callback, admin_back_callback,
    admin_set_tariff_start, admin_extend_trial_start,
    broadcast_start, broadcast_send,
    test_scraper, debug_reactions,
    setup_bot_commands
)

from posters import TelegramPoster
from scheduler import Scheduler
from post_scheduler import PostScheduler
from backup import BackupService, AutoBackup
from cleanup import TempCleaner

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def main():
    await init_db()
    logger.info("Database initialized")
    
    await register_self()
    
    app = Application.builder().token(Config.BOT_TOKEN).build()
    
    await app.bot.delete_webhook()
    logger.info("Webhook deleted")
    
    await setup_bot_commands(app)
    
    poster = TelegramPoster(app.bot)
    scheduler = Scheduler(poster)
    post_scheduler = PostScheduler(poster)
    
    app.bot_data['scheduler'] = scheduler
    app.bot_data['post_scheduler'] = post_scheduler
    app.bot_data['poster'] = poster
    
    scheduler_task = asyncio.create_task(scheduler.start())
    post_scheduler_task = asyncio.create_task(post_scheduler.start())
    
    backup_service = BackupService()
    auto_backup = AutoBackup(backup_service)
    auto_backup_task = asyncio.create_task(auto_backup.start())
    
    temp_cleaner = TempCleaner()
    temp_cleaner_task = asyncio.create_task(temp_cleaner.start())
    
    # ============ Command Handlers ============
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("test", test_scraper))
    app.add_handler(CommandHandler("debug_reactions", debug_reactions))
    app.add_handler(CommandHandler("my_projects", my_projects))
    app.add_handler(CommandHandler("my_sources", my_sources))
    app.add_handler(CommandHandler("my_targets", my_targets))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("project_stats", project_stats))
    app.add_handler(CommandHandler("parse", parse_now))
    app.add_handler(CommandHandler("queue", queue_status))
    app.add_handler(CommandHandler("postnow", post_now))
    app.add_handler(CommandHandler("clear_queue", clear_old_queue))
    app.add_handler(CommandHandler("clear_failed", clear_failed_queue))
    app.add_handler(CommandHandler("clear_all", clear_all_queue))
    app.add_handler(CommandHandler("clear_project", clear_project_queue))
    app.add_handler(CommandHandler("reset_history", reset_history))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("admin_set_tariff", admin_set_tariff_start))
    app.add_handler(CommandHandler("admin_extend_trial", admin_extend_trial_start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("add_source", add_source_start))
    app.add_handler(CommandHandler("add_target", add_target_start))
    app.add_handler(CommandHandler("set_interval", set_interval_start))
    app.add_handler(CommandHandler("set_post_interval", set_post_interval_start))
    app.add_handler(CommandHandler("set_signature", set_signature_start))
    app.add_handler(CommandHandler("broadcast", broadcast_start))
    
    # ============ CallbackQueryHandlers ============
    app.add_handler(CallbackQueryHandler(admin_back_callback, pattern="^admin_back$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_|user_manage_|tariff_set_|user_tariff_|extend_user_|deactivate_user_|activate_user_|tariff_for_|set_tariff_|admin_set_tariff|admin_extend_trial|admin_deactivate|admin_activate)"))
    
    # U2TG source addition callbacks
    app.add_handler(CallbackQueryHandler(youtube_source_type_callback, pattern="^u2tg_type_"))
    app.add_handler(CallbackQueryHandler(youtube_country_callback, pattern="^u2tg_country_"))
    app.add_handler(CallbackQueryHandler(youtube_category_callback, pattern="^u2tg_category_"))
    app.add_handler(CallbackQueryHandler(youtube_content_type_callback, pattern="^u2tg_content_"))
    app.add_handler(CallbackQueryHandler(add_source_criteria, pattern="^u2tg_criteria_"))
    app.add_handler(CallbackQueryHandler(media_filter_callback, pattern="^u2tg_media_"))
    app.add_handler(CallbackQueryHandler(duration_callback, pattern="^u2tg_duration_"))
    app.add_handler(CallbackQueryHandler(remove_text_callback, pattern="^u2tg_text_"))
    app.add_handler(CallbackQueryHandler(add_keywords_skip_callback, pattern="^u2tg_keywords_skip"))
    
    # Source management
    app.add_handler(CallbackQueryHandler(edit_source_callback, pattern="^edit_source_"))
    app.add_handler(CallbackQueryHandler(delete_source_callback, pattern="^del_source_"))
    app.add_handler(CallbackQueryHandler(confirm_delete_source_callback, pattern="^confirm_delete_source$"))
    app.add_handler(CallbackQueryHandler(cancel_delete_source_callback, pattern="^cancel_delete_source$"))
    app.add_handler(CallbackQueryHandler(back_to_sources_callback, pattern="^back_to_sources$"))
    app.add_handler(CallbackQueryHandler(edit_source_start, pattern="^edit_(criteria|media|text|phrases|clear_phrases|keywords)_"))
    app.add_handler(CallbackQueryHandler(edit_media_filter_callback, pattern="^edit_media_"))
    app.add_handler(CallbackQueryHandler(edit_remove_text_callback, pattern="^edit_text_"))
    
    app.add_handler(CallbackQueryHandler(delete_target_callback, pattern="^del_target_"))
    app.add_handler(CallbackQueryHandler(project_menu_callback, pattern="^project_menu_"))
    app.add_handler(CallbackQueryHandler(back_to_projects_callback, pattern="^back_to_projects$"))
    app.add_handler(CallbackQueryHandler(projects_callback, pattern="^(create_project|select_project_|delete_project_|confirm_delete_|cancel_delete|stats_project_|project_sources_|project_change_target_)"))
    app.add_handler(CallbackQueryHandler(set_interval_start_callback, pattern="^project_set_check_"))
    app.add_handler(CallbackQueryHandler(set_post_interval_start_callback, pattern="^project_set_post_"))
    app.add_handler(CallbackQueryHandler(set_signature_start_callback, pattern="^project_set_signature_"))
    app.add_handler(CallbackQueryHandler(add_target_continue_callback, pattern="^add_target_continue$"))
    app.add_handler(CallbackQueryHandler(set_interval_callback, pattern="^interval_"))
    app.add_handler(CallbackQueryHandler(set_post_interval_callback, pattern="^post_"))
    app.add_handler(CallbackQueryHandler(set_post_start_time_callback, pattern="^starttime_"))
    
    # ============ Message Handlers ============
    # Reply handler для добавления/редактирования источников (должен быть первым)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_source_input))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_edit_reply))
    # Обработчик названия проекта
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_project_name))
    # Пересланные сообщения для add_target
    app.add_handler(MessageHandler(filters.FORWARDED, add_target_forward))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=["message", "callback_query"])
    
    logger.info("🟢 U2TG started (version 1.2.0)")
    
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        scheduler_task.cancel()
        post_scheduler_task.cancel()
        auto_backup_task.cancel()
        temp_cleaner_task.cancel()
        await scheduler.stop()
        await post_scheduler.stop()
        await auto_backup.stop()
        await temp_cleaner.stop()
        await poster.stop()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("🔴 U2TG stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)