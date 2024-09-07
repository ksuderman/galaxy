from typing import List

from galaxy.util import logging

from pydantic import (
    BaseModel,
    Field,
)

log = logging.getLogger(__name__)
log.info("Created log for %s", __name__)


class LoggerLevelInfo(BaseModel):
    name: str = Field(
        ...,
        title="Logger Name",
        description="The name of the logger."
    )
    level: str = Field(
        ...,
        title="Logger Level",
        description="The level of the logger."
    )
    effective: str = Field(
        ...,
        title="Effective Level",
        description="The effective level of the logger."
    )


class LoggerLevels(BaseModel):
    loggers: List[LoggerLevelInfo] = Field(
        ...,
        title="Loggers",
        description="The list of loggers and their levels."
    )


class LoggingManager:
    def __init__(self):
        log.info("Created LoggingManager")

    async def _get_logger_info(self, trans, logger) -> LoggerLevelInfo:
        return {"name": logger.name, "level": logging.getLevelName(logger.level), "effective": logging.getLevelName(logger.getEffectiveLevel())}

    async def index(self, trans) -> List[str]:
        log.trace("Getting log index")
        logger_dict = logging.Logger.manager.loggerDict
        loggers = [name for name in logger_dict if isinstance(logger_dict[name], logging.Logger)]
        return loggers

    async def get(self, trans, name=None) -> LoggerLevelInfo:
        if name is None:
            return self.index()
        return self.get_logger_level(name)

    # def get_loggers(self, trans) -> List[str]:
    #     return self.index()
    #
    # def get_logger_levels(self, trans) -> LoggerLevels:
    #     log.debug("Getting logger levels")
    #     logger_info = {}
    #     for name in self.index():
    #         logger = logging.getLogger(name)
    #         logger_info[name] =  self._get_logger_info(logger)
    #     return logger_info
    #
    # def get_logger_level(self, trans, name) -> LoggerLevelInfo:
    #     log.debug("Getting level for logger %s", name)
    #     loggers = self.index()
    #     if name in loggers:
    #         logger = logging.getLogger(name)
    #         return self._get_logger_info(logger)
    #     log.warning("Logger %s not found", name)
    #     return {"name": name, "level": logging.NOTSET, "effective": logging.NOTSET}

    def set_logger_level(self, trans, name, level):
        log.debug("Setting level for logger %s to %s", name, level)
        logger = logging.getLogger(name)
        logger.setLevel(level)