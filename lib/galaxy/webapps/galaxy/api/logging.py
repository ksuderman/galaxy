"""
API to allow admin users to view and set logging levels for Galaxy loggers.
"""

import logging

from fastapi import Path, Query

from galaxy.managers.context import ProvidesUserContext
from galaxy.managers.logging import LoggingManager

from . import depends, DependsOnTrans, Router

log = logging.getLogger(__name__)

router = Router(tags=["logging"])

LoggerNamePathParam = Path(..., title="Logger Name", description="The name of the logger to get or set the level for.")

LoggerLevelQueryParam = Query(..., title="Logger Level", description="The level to set the logger to.")


@router.cbv
class FastApiLoggingManager:
    manager: LoggingManager = depends(LoggingManager)

    @router.get(
        "/api/logging", summary="Get all loggers and their levels"
    )  # , response_class=List, response_description='A List[Dict] of loggers and their levels')
    def index(self, trans=DependsOnTrans):
        log.info("Getting all logger leverls")
        return self.manager.get_log_levels()

    @router.get(
        "/api/logging/{logger_name}",
        summary="Get the level of a logger",
        response_description="The level of the logger",
    )
    def get(self, logger_name, trans: ProvidesUserContext = DependsOnTrans):
        log.info("Getting log level for %s", logger_name)
        return self.manager.get_log_level(logger_name)

    @router.post(
        "/api/logging/{logger_name}", summary="Set the level of a logger"
    )  # , response_class=LoggerLevelInfo, response_description='The level of the logger')
    def set(self, logger_name, level, trans: ProvidesUserContext = DependsOnTrans):
        log.info("Setting log level for %s to %s", logger_name, level)
        trans.app.queue_worker.send_control_task("set_log_level", kwargs={"name":logger_name, "level":level})
        return self.manager.get_log_level(logger_name)

    # @router.get("/api/logging/test", summary="Test the logging API")
    # def test(self, trans: ProvidesUserContext = DependsOnTrans):
    #     return self.manager.test(trans)
