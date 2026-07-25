"""The one shared HTTP resolver + logger every route module registers on.

Lives in its own leaf module so route modules and helpers can import `app`
(for @app.<verb> decorators and app.current_event) without circular imports --
api.py imports the route modules, never the other way around.
"""
from __future__ import annotations

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver

logger = Logger(service="api")

app = APIGatewayHttpResolver()
