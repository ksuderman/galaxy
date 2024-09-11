import logging
from typing import Dict, List

from galaxy.managers.context import ProvidesUserContext

from pydantic import (
    BaseModel,
    Field,
)

from galaxy.webapps.galaxy.api import DependsOnTrans

log = logging.getLogger(__name__)

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


# class LoggerLevels(BaseModel):
#     loggers: Dict[str, LoggerLevelInfo] = Field(
#         ...,
#         title="Loggers",
#         description="The list of loggers and their levels."
#     )


class LoggingManager:

    def _get_logger_info(self, logger) -> LoggerLevelInfo:
        return LoggerLevelInfo(
            name=logger.name,
            level=logging.getLevelName(logger.level),
            effective=logging.getLevelName(logger.getEffectiveLevel())
        )

    def index(self, trans: ProvidesUserContext = DependsOnTrans) -> List[str]:
        log.trace("Getting log index")
        logger_dict = logging.Logger.manager.loggerDict
        loggers = [name for name in logger_dict if isinstance(logger_dict[name], logging.Logger)]
        return loggers

    # def get(self, name:str , trans: ProvidesUserContext = DependsOnTrans) -> LoggerLevelInfo:
    #     log.info("Getting logger info for %s", name)
    #     if name is None:
    #         return self.index()
    #     return self.get_logger_level(name, trans)

    # def get_loggers(self, trans) -> List[str]:
    #     return self.index()
    #
    def get_logger_levels(self, trans: ProvidesUserContext = DependsOnTrans) -> Dict[str, LoggerLevelInfo]:
        log.trace("Getting logger levels")
        loggers = {}
        for name in self.index():
            logger = logging.getLogger(name)
            loggers[name] = self._get_logger_info(logger)
        return loggers

    def get_logger_level(self, name, trans: ProvidesUserContext = DependsOnTrans) -> LoggerLevelInfo:
        log.trace("Getting level for logger %s", name)
        loggers = self.index(trans)
        if name in loggers:
            logger = logging.getLogger(name)
            return self._get_logger_info(logger)
        log.warning("Logger %s not found", name)
        return LoggerLevelInfo(name=name, level="NOTSET", effective="NOTSET")

    def set_logger_level(self, name, level, trans: ProvidesUserContext = DependsOnTrans) -> LoggerLevelInfo:
        log.debug("Setting level for logger %s to %s", name, level)
        logger = logging.getLogger(name)
        logger.setLevel(level)
        return self._get_logger_info(logger)

    def test(self, trans: ProvidesUserContext = DependsOnTrans) -> str:
        log.trace("TRACE message")
        log.debug("DEBUG message")
        log.info("INFO message")
        log.warning("WARNING message")
        log.error("ERROR message")
        log.critical("CRITICAL message")
        return "Test OK"