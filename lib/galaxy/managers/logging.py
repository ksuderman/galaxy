import os.path
import re
import logging
from typing import Dict, List
from watchdog.observers import Observer
from galaxy.exceptions import AdminRequiredException
from galaxy.managers.context import ProvidesUserContext

from pydantic import (
    BaseModel,
    Field,
)

from galaxy.util.watcher import Watcher, EventHandler
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


import os
import time
import threading


# Function to check the modification times of the files in a directory
# def get_files_mod_times(directory):
#     mod_times = {}
#     for filename in os.listdir(directory):
#         filepath = os.path.join(directory, filename)
#         if os.path.isfile(filepath):
#             mod_times[filepath] = os.path.getmtime(filepath)
#     return mod_times
#
#
# # Function to monitor a directory for changes and run `f` when a file is modified
# def monitor_directory(directory, f, interval=1):
#     # Get the initial modification times for the files in the directory
#     previous_mod_times = get_files_mod_times(directory)
#
#     def check_for_changes():
#         nonlocal previous_mod_times
#         while True:
#             time.sleep(interval)  # Check every `interval` seconds
#             current_mod_times = get_files_mod_times(directory)
#             # Compare current modification times with previous ones
#             for filepath, mod_time in current_mod_times.items():
#                 if filepath not in previous_mod_times or previous_mod_times[filepath] != mod_time:
#                     # If a file is new or its modification time has changed, trigger the function
#                     print(f"File changed: {filepath}")
#                     f(filepath)
#             # Update the previous modification times for the next check
#             previous_mod_times = current_mod_times
#
#     # Start monitoring in a separate thread
#     thread = threading.Thread(target=check_for_changes)
#     thread.daemon = True  # Ensure the thread exits when the main program does
#     thread.start()
#
#
# # Example usage
# def on_file_changed(file_path):
#     print(f"Custom function triggered for file: {file_path}")
#
#
# # Directory to monitor
# directory_to_watch = "/path/to/directory"
#
# # Start monitoring the directory
# monitor_directory(directory_to_watch, on_file_changed)


class LoggingWatcher:
    def __init__(self, directory, f, interval=1):
        self.directory = directory
        self.times = dict()
        self.handler = f
        self.interval = interval
        self.running = False
    def check_for_changes(self):
        self.running = True
        while self.running:
            time.sleep(self.interval)
            for f in os.listdir(self.directory):
                filepath = os.path.join(self.directory, f)
                if os.path.isfile(filepath):
                    t = os.path.getmtime(filepath)
                    if filepath not in self.times or self.times[filepath] != t:
                        previous = -1
                        if filepath in self.times:
                            previous = self.times[filepath]
                        log.debug("%s was modified. Old: %d New: %d", filepath, previous, t)
                        self.times[filepath] = t
                        self.handler(filepath)


    def start(self):
        for f in os.listdir(self.directory):
            filepath = os.path.join(self.directory, f)
            if os.path.isfile(filepath):
                t = os.path.getmtime(filepath)
                self.times[filepath] = t
        thread = threading.Thread(target=self.check_for_changes)
        thread.daemon = True
        thread.start()

    def stop(self):
        self.running = False


class LoggingWatcherEventHandler(EventHandler):
    def on_modified(self, event):
        # TODO Check if moving or deleting the file fires a modified event.
        log.info("Modified: %s", event.src_path)
        # if os.path.isdir(event.src_path):
        #     # We don't care about directories
        #     return
        # with open(event.src_path) as f:
        #     new_level = f.read().strip()
        # logging.getLogger(event.src_path).setLevel(new_level)
        # log.info("Set level for %s to %s", event.src_path, new_level)


class LoggingManager:

    def __init__(self):
        log.info("Initializing LoggingManager")
        # Delete all files in the WATCH_DIR
        if os.path.exists(WATCH_DIR):
            log.debug("Clearing watch directory: %s", WATCH_DIR)
            for f in os.listdir(WATCH_DIR):
                os.remove(os.path.join(WATCH_DIR, f))
        else:
            try:
                os.makedirs(WATCH_DIR)
                log.debug("Created watch directory: %s", WATCH_DIR)
            except OSError as e:
                log.error("Unable to create watch directory: %s", e)
                log.warning("Setting log levels will not be possible.")
                return

        # Save all logger levels to the WATCH_DIR using the logger name as the
        # file name.
        log.debug("Saving current logging levels")
        for name in logging.Logger.manager.loggerDict:
            if isinstance(logging.Logger.manager.loggerDict[name], logging.Logger):
                with open(os.path.join(WATCH_DIR, name), 'w') as f:
                    f.write(logging.getLevelName(logging.getLogger(name).level))

        # Now watch the directory for changes made by the API handler.
        log.debug("Configuring a directory watcher.")
        def file_changed(filepath):
            log.info("Modified: %s", filepath)
            with open(filepath) as f:
                new_level = f.read().strip()
            name = os.path.basename(filepath)
            logging.getLogger(name).setLevel(new_level)
            log.info("Set level for %s to %s", name, new_level)

        # self.watcher = Watcher(Observer, LoggingWatcherEventHandler)
        # self.watcher.watch_directory(WATCH_DIR)
        # self.watcher.start()
        self.watcher = LoggingWatcher(WATCH_DIR, file_changed)
        self.watcher.start()
        log.info("Started watcher for %s", WATCH_DIR)

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
        log.info("Getting a list of all configured loggers")
        logger_dict = logging.Logger.manager.loggerDict
        loggers = [name for name in logger_dict if isinstance(logger_dict[name], logging.Logger)]
        return loggers

    def get_logger_levels(self, trans: ProvidesUserContext = DependsOnTrans) -> Dict[str, LoggerLevelInfo]:
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

    def get_logger_level(self, name, trans: ProvidesUserContext = DependsOnTrans) -> LoggerLevelInfo|List[LoggerLevelInfo]:
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
        loggers = self.index(trans)
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
        log.info("Setting level for logger %s to %s", name, level)
        if os.path.exists(WATCH_DIR):
            log.debug("Attempting to open %s/%s", WATCH_DIR, name)
            with open(os.path.join(WATCH_DIR, name), 'w') as f:
                f.write(level)
            return LoggerLevelInfo(name=name, level=level, effective=level)
        logger = logging.getLogger(name)
        logger.setLevel(level)
        return self._get_logger_info(logger)

    def set_logger_levels(self, name_regex: str, level: int | str, trans: ProvidesUserContext = DependsOnTrans) -> List[
        str]:
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
        accept = lambda name: name == name_regex
        if name_regex.endswith(".*"):
            name_regex = name_regex[:-2]
            accept = lambda name: name.startswith(name_regex)
        names = [name for name in logging.Logger.manager.loggerDict if accept(name)]
        log.info("Setting logging level to %s for %d loggers", name_regex, len(names))
        if os.path.exists(WATCH_DIR):
            log.debug("Writing to the watched directory %s", WATCH_DIR)
            for name in names:
                with open(os.path.join(WATCH_DIR, name), 'w') as f:
                    f.write(level)
        else:
            log.debug("Changing the log levels directly.")
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
