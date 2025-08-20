# src/logger.py
import copy
from typing import Type, Callable, Awaitable

from creart import AbstractCreator, CreateTargetInfo, exists_module
from loguru import logger
from prompt_toolkit import print_formatted_text, ANSI
from src.utils import safely_create_task


class GlobalLogger:
    def __init__(self):
        logger.remove()
        self.logger = copy.deepcopy(logger)
        self.logger.add(lambda msg: print_formatted_text(ANSI(msg), end=""), colorize=True,
                        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green>"
                               + " | <level>{level}</level>"
                               + " - <level>{message}</level>",
                        level="INFO")


class LoggerCreator(AbstractCreator):
    targets = (
        CreateTargetInfo("src.logger", "GlobalLogger"),
    )

    @staticmethod
    def available() -> bool:
        return exists_module("src.logger")

    @staticmethod
    def create(create_type: Type[GlobalLogger]) -> GlobalLogger:
        return create_type()


class RipLogger:
    item_type: str
    item_id: str
    full_name: str
    log_callback: Callable[[str], Awaitable[None]] = None

    def __init__(self, _type: str, item_id: str, log_callback: Callable[[str], Awaitable[None]] = None):
        self.item_type = _type
        self.item_id = item_id
        self.log_callback = log_callback
        self.logger = copy.deepcopy(logger)
        self.logger.remove()
        self._configure_logger()

    def _configure_logger(self, full_name: str = None):
        if full_name:
            self.full_name = full_name
            item_identifier = f"<b>{self.full_name}</b>"
        else:
            item_identifier = f"<b>{self.item_id}</b>"

        log_format = ("<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green>"
                      + f" | <b>{self.item_type.upper()}</b>"
                      + f" | {item_identifier}"
                      + " | <level>{level}</level>"
                      + " - <level>{message}</level>")

        def sync_log_callback(msg):
            if self.log_callback:
                safely_create_task(self.log_callback(msg))
        
        # We need a synchronous sink for loguru
        sink = sync_log_callback if self.log_callback else lambda msg: print_formatted_text(ANSI(msg), end="")

        self.logger.add(sink, colorize=bool(not self.log_callback), format=log_format, level="INFO")

    def create(self):
        self.logger.info("Start ripping...")

    def set_fullname(self, artist: str, name: str = None):
        if not name:
            full_name = artist
        else:
            full_name = f"{artist} - {name}"
        
        self.logger.remove()
        self._configure_logger(full_name=full_name)


    def not_exist(self):
        self.logger.error(
            f"Unable to download {self.item_type}. This {self.item_type} does not exist in all available storefronts")

    def already_exist(self):
        self.logger.info("Song already exists")

    def lyrics_not_exist(self):
        self.logger.warning("Lyrics do not exist")

    def audio_not_exist(self):
        self.logger.error("Failed to download song. Audio does not exist")

    def lossless_audio_not_exist(self):
        self.logger.error("Failed to download song. Lossless audio does not exist")

    def lossless_audio_not_exist_aac(self):
        self.logger.warning("Lossless audio does not exist. Using aac-legacy to rip")

    def downloading(self):
        self.logger.info("Downloading song...")

    def decrypting(self):
        self.logger.info("Decrypting song...")

    def failed_integrity(self):
        self.logger.warning("Song did not pass the integrity check!")

    def saved(self):
        self.logger.success("Song saved!")

    def done(self):
        self.logger.success("Finished ripping")

    def selected_codec(self, selected_codec):
        self.logger.info(f"Selected codec: {selected_codec}")