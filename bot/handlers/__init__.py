"""Handler modules for the Telegram bot."""

from bot.handlers.commands import CommandHandlers
from bot.handlers.menus import MenuHandlers
from bot.handlers.callbacks import CallbackHandlers

__all__ = ['CommandHandlers', 'MenuHandlers', 'CallbackHandlers']