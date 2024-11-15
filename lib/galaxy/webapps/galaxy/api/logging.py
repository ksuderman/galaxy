"""
API to allow admin users to view and set logging levels for Galaxy loggers.
"""

import logging

from fastapi import Path, Query

from galaxy.managers.context import ProvidesUserContext
from galaxy.util.logging.methods import get_log_levels, get_logger_names, set_log_levels

from . import DependsOnTrans, Router

log = logging.getLogger(__name__)

router = Router(tags=["logging"])

LoggerNamePathParam = Path(..., title="Logger Name", description="The name of the logger to get or set the level for.")

LoggerLevelQueryParam = Query(..., title="Logger Level", description="The level to set the logger to.")


@router.cbv
class FastApiLoggingManager:

    @router.get(
        "/api/logging", summary="Get logging levels for all configured loggers", require_admin=True
    )  # , response_class=List, response_description='A List[Dict] of loggers and their levels')
    def index(self, trans=DependsOnTrans):
        log.info("Getting all logger leverls")
        return get_log_levels(None)

    @router.get(
        "/api/logging/{logger_name}",
        summary="Get the logging level for one or more loggers",
        response_description="The logging level for the logger(s)",
        require_admin=True
    )
    def get(self, logger_name, trans: ProvidesUserContext = DependsOnTrans):
        log.info("Getting log level for %s", logger_name)
        return get_log_levels(logger_name)

    @router.post(
        "/api/logging/{logger_name}", summary="Set the level of a logger", require_admin=True
    )  # , response_class=LoggerLevelInfo, response_description='The level of the logger')
    def set(self, logger_name, level, trans: ProvidesUserContext = DependsOnTrans):
        log.info("Setting log level for %s to %s", logger_name, level)
        trans.app.queue_worker.send_control_task("set_log_level", kwargs={"name":logger_name, "level":level})
        return get_log_levels(logger_name)

