"""
API to allow admin users to view and set logging levels for Galaxy loggers.
"""

from galaxy.util import logging

from typing import List
from fastapi import Path, Query
from galaxy.managers.logging import LoggingManager

from . import (
    depends,
    DependsOnTrans,
    Router
)

log = logging.getLogger(__name__)

router = Router(tags=['logging'])

LoggerName = Path(
    ...,
    title="Logger Name",
    description="The name of the logger to get or set the level for."
)

LoggerLevel = Query(
    ...,
    title="Logger Level",
    description="The level to set the logger to."
)

@router.cbv
class FastApiLoggingManager:
    manager: LoggingManager = depends(LoggingManager)

    @router.get('/api/logging', summary='Get all loggers and their levels', response_class=Lo) #, response_class=List, response_description='A List[Dict] of loggers and their levels')
    def index(self):
        return self.manager.index()

    # @router.get('/api/logging/{logger_name}', summary='Get the level of a logger', response_description='The level of the logger')
    # def get(self, logger_name: LoggerName):
    #     return self.manager.get_logger_level(logger_name)
    #
    # @router.post('/api/logging/{logger_name}', summary='Set the level of a logger', response_description='The level of the logger')
    # def set(self, logger_name: LoggerName, level: LoggerLevel):
    #     return self.manager.set_logger_level(logger_name, level)