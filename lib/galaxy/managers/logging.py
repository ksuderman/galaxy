import os.path
import logging
from typing import Dict, List

from pydantic import (
    BaseModel,
    Field,
)

from galaxy.webapps.galaxy.api import DependsOnTrans

log = logging.getLogger(__name__)

# TODO Should we read this from the config?
WATCH_DIR = os.environ.get('GALAXY_LOGGING_WATCH_DIR', '/galaxy/server/database/logging')


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


class LoggingManager:

    def __init__(self):
        log.info("Initializing LoggingManager")

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

    def index(self) -> List[str]:
        '''
        Gets the names of all the currently configured loggers.

        :return: The names of all the currently configured loggers
        :rtype: List[str]
        '''
        # if not trans.user_is_admin:
        #     log.warning("Only admins can get log index")
        #     raise AdminRequiredException()
        log.info("Getting a list of all configured loggers")
        logger_dict = logging.Logger.manager.loggerDict
        loggers = [name for name in logger_dict if isinstance(logger_dict[name], logging.Logger)]
        return loggers

    def get_log_levels(self) -> Dict[str, LoggerLevelInfo]:
        '''
        Get the logging levels for all currently configured loggers.

        :return: The logging levels for all currently configured loggers
        :rtype: Dict[str, LoggerLevelInfo]
        '''
        # if not trans.user_is_admin:
        #     log.warning("Only admins can get log levels")
        #     raise AdminRequiredException()
        log.info("Getting levels for all loggers")
        loggers = {}
        for name in self.index():
            logger = logging.getLogger(name)
            loggers[name] = self._get_logger_info(logger)
        return loggers

    def get_log_level(self, name) -> LoggerLevelInfo|List[LoggerLevelInfo]:
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
        log.info("Getting level for logger %s", name)
        loggers = self.index()
        if name.endswith(".*"):
            result = []
            pattern = name[:-2]
            for logger_name in [ logger for logger in loggers if logger.startswith(pattern)]:
                logger = logging.getLogger(logger_name)
                result.append(self._get_logger_info(logger))
            return result
        elif name in loggers:
            logger = logging.getLogger(name)
            return self._get_logger_info(logger)
        log.warning("Logger %s not found", name)
        return LoggerLevelInfo(name=name, level="NOTSET", effective="NOTSET")

    def set_log_level(self, name, level) -> List[LoggerLevelInfo]:
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
        log.info("Setting level for logger %s to %s", name, level)
        result = []
        loggers = self.index()
        if name.endswith(".*"):
            pattern = name[:-2]
            for logger_name in [ logger for logger in loggers if logger.startswith(pattern)]:
                logger = logging.getLogger(logger_name)
                logger.setLevel(level)
                result.append(self._get_logger_info(logger))
            return result
        elif name in loggers:
            logger = logging.getLogger(name)
            logger.setLevel(level)
            result.append(self._get_logger_info(logger))
        else:
            log.warning("Logger %s not found", name)
        return result

    # def set_logger_level(self, name: str, level: int | str, trans: ProvidesUserContext = DependsOnTrans) -> List[
    #     str]:
    #     '''
    #     Set the log level for all loggers that match a regex.
    #
    #     :param name_regex: The regex to match the loggers to set the level for
    #     :type name_regex: str
    #     :param level: The level to set the loggers to
    #     :type level: str
    #
    #     :return: The log levels for the loggers
    #     :rtype: Dict[str, LoggerLevelInfo]
    #     '''
    #     # if not trans.user_is_admin:
    #     #     log.warning("Only admins can set log levels")
    #     #     raise AdminRequiredException()
    #     accept = lambda name: name == name_regex
    #     if name_regex.endswith(".*"):
    #         name_regex = name_regex[:-2]
    #         accept = lambda name: name.startswith(name_regex)
    #     names = [name for name in logging.Logger.manager.loggerDict if accept(name)]
    #     log.info("Setting logging level to %s for %d loggers", name_regex, len(names))
    #     if os.path.exists(WATCH_DIR):
    #         log.debug("Writing to the watched directory %s", WATCH_DIR)
    #         for name in names:
    #             with open(os.path.join(WATCH_DIR, name), 'w') as f:
    #                 f.write(level)
    #     else:
    #         log.debug("Changing the log levels directly.")
    #         for name in names:
    #             logging.getLogger(name).setLevel(level)
    #
    #     return names

    def test(self) -> str:
        log.trace("TRACE message")
        log.debug("DEBUG message")
        log.info("INFO message")
        log.warning("WARNING message")
        log.error("ERROR message")
        log.critical("CRITICAL message")
        return "Test OK"
