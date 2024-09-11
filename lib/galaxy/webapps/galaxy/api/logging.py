"""
API to allow admin users to view and set logging levels for Galaxy loggers.
"""

import logging

from fastapi import Path, Query

from galaxy.managers.context import ProvidesUserContext
from galaxy.managers.logging import LoggingManager, LoggerLevelInfo

from . import (
    depends,
    DependsOnTrans,
    Router
)

log = logging.getLogger(__name__)

router = Router(tags=['logging'])

LoggerNamePathParam = Path(
    ...,
    title="Logger Name",
    description="The name of the logger to get or set the level for."
)

LoggerLevelQueryParam = Query(
    ...,
    title="Logger Level",
    description="The level to set the logger to."
)

@router.cbv
class FastApiLoggingManager:
    manager: LoggingManager = depends(LoggingManager)

    @router.get('/api/logging', summary='Get all loggers and their levels') #, response_class=List, response_description='A List[Dict] of loggers and their levels')
    def index(self, trans=DependsOnTrans):
        return self.manager.get_logger_levels(trans)

    @router.get('/api/logging/config/{logger_name}', summary='Get the level of a logger', response_description='The level of the logger')
    def get(self, logger_name, trans: ProvidesUserContext = DependsOnTrans):
        return self.manager.get_logger_level(logger_name, trans)

    @router.post('/api/logging/config/{logger_name}', summary='Set the level of a logger') #, response_class=LoggerLevelInfo, response_description='The level of the logger')
    def set(self, logger_name, level, trans: ProvidesUserContext = DependsOnTrans):
        return self.manager.set_logger_level(logger_name, level, trans)

    @router.get('/api/logging/test', summary='Test the logging API')
    def test(self, trans: ProvidesUserContext = DependsOnTrans):
        return self.manager.test(trans)