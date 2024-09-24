import logging
from typing import Dict, List, re

from galaxy.exceptions import AdminRequiredException
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
        '''
        Get the level and effective level of a logger

        :param logger: The logger to get the info for
        :type logger: logging.Logger

        :return: The level and effective level of the logger
        :rtype: LoggerLevelInfo
        '''
        return LoggerLevelInfo(
            name=logger.name,
            level=logging.getLevelName(logger.level),
            effective=logging.getLevelName(logger.getEffectiveLevel())
        )

    def index(self, trans: ProvidesUserContext = DependsOnTrans) -> List[str]:
        '''
        Gets the names of all the currently configured loggers.

        :return: The names of all the currently configured loggers
        :rtype: List[str]
        '''
        # if not trans.user_is_admin:
        #     log.warning("Only admins can get log index")
        #     raise AdminRequiredException()
        log.trace("Getting log index")
        logger_dict = logging.Logger.manager.loggerDict
        loggers = [name for name in logger_dict if isinstance(logger_dict[name], logging.Logger)]
        return loggers

    def get_logger_levels(self, trans: ProvidesUserContext = DependsOnTrans) -> Dict[str, LoggerLevelInfo]:
        '''
        Get the logging levels for all currently configured loggers.

        :return: The logging levels for all currently configured loggers
        :rtype: Dict[str, LoggerLevelInfo]
        '''
        if not trans.user_is_admin:
            log.warning("Only admins can get log levels")
            raise AdminRequiredException()
        log.trace("Getting logger levels")
        loggers = {}
        for name in self.index():
            logger = logging.getLogger(name)
            loggers[name] = self._get_logger_info(logger)
        return loggers

    def get_logger_level(self, name, trans: ProvidesUserContext = DependsOnTrans) -> LoggerLevelInfo:
        '''
        Get the log level for a specific logger.

        :param name: The name of the logger to get the level for
        :type name: str

        :return: The log level for the logger
        :rtype: LoggerLevelInfo
        '''
        # if not trans.user_is_admin:
        #     log.warning("Only admins can get log level")
        #     raise AdminRequiredException()
        log.trace("Getting level for logger %s", name)
        loggers = self.index(trans)
        if name in loggers:
            logger = logging.getLogger(name)
            return self._get_logger_info(logger)
        log.warning("Logger %s not found", name)
        return LoggerLevelInfo(name=name, level="NOTSET", effective="NOTSET")

    def set_logger_level(self, name, level, trans: ProvidesUserContext = DependsOnTrans) -> LoggerLevelInfo:
        '''
        Set the log level for a specific logger.

        :param name: The name of the logger to set the level for
        :type name: str
        :param level: The level to set the logger to
        :type level: str

        :return: The log level for the logger
        :rtype: LoggerLevelInfo
        '''
        # if not trans.user_is_admin:
        #     log.warning("Only admins can set log level")
        #     raise AdminRequiredException()
        log.debug("Setting level for logger %s to %s", name, level)
        logger = logging.getLogger(name)
        logger.setLevel(level)
        return self._get_logger_info(logger)

    def set_logger_levels(self, name_regex: str, level: str, trans: ProvidesUserContext = DependsOnTrans) -> List[str]:
        '''
        Set the log level for all loggers that match a regex.

        :param name_regex: The regex to match the loggers to set the level for
        :type name_regex: str
        :param level: The level to set the loggers to
        :type level: str

        :return: The log levels for the loggers
        :rtype: Dict[str, LoggerLevelInfo]
        '''
        # if not trans.user_is_admin:
        #     log.warning("Only admins can set log levels")
        #     raise AdminRequiredException()
        pattern = re.compile(name_regex)
        names = [ name for name in logging.Logger.manager.loggerDict if pattern.match(name) ]
        for name in names:
            logging.getLogger(name).setLevel(level)

        return names


    def test(self, trans: ProvidesUserContext = DependsOnTrans) -> str:
        log.trace("TRACE message")
        log.debug("DEBUG message")
        log.info("INFO message")
        log.warning("WARNING message")
        log.error("ERROR message")
        log.critical("CRITICAL message")
        return "Test OK"