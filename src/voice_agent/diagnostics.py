"""Read-only SIM7600 diagnostic commands shared by the UI and hardware service."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

QUERIES = {
    "ping": ("AT", ()),
    "sim": ("AT+CPIN?", ("+CPIN:",)),
    "signal": ("AT+CSQ", ("+CSQ:",)),
    "registration": ("AT+CEREG?", ("+CEREG:",)),
    "network": ("AT+CPSI?", ("+CPSI:",)),
    "calls": ("AT+CLCC", ("+CLCC:",)),
}


class DiagnosticQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: Literal["ping", "sim", "signal", "registration", "network", "calls"]
