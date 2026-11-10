"""Contrato común de los sub-agentes: todos devuelven un Signal y nada más."""
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Direction = Literal["LONG", "SHORT", "NEUTRAL"]

AGENT_LABELS = {
    "historian": "1 · Historiador y Predictor",
    "macro": "2 · Macro y Catalizadores",
    "smc": "3 · SMC Intradía",
    "wyckoff": "4 · Wyckoff y Perfil de Volumen",
    "sentiment": "5 · Tendencia y Sentimiento",
}


class Signal(BaseModel):
    """Salida estricta de un sub-agente. Sin texto libre más allá de una línea."""
    model_config = ConfigDict(extra="forbid")

    agent: str
    symbol: str
    direction: Direction
    confidence: int = Field(ge=0, le=100)
    support: float | None = None
    resistance: float | None = None
    rationale: str
    veto: bool = False  # True = pedir al jefe que no abra operaciones nuevas
    ts: float = Field(default_factory=time.time)
    version: str = ""  # generación que emitió la señal, p. ej. "SMC_Scalper_v3"
    features: dict[str, float] = Field(default_factory=dict)  # medidas sobre las que actúan las reglas mutadas

    @field_validator("rationale", mode="before")
    @classmethod
    def _one_line(cls, v):
        return " ".join(str(v).split())[:160]


def make(agent: str, symbol: str, score: float, rationale: str, floor: int = 20, **kw) -> Signal:
    """Convierte un score en [-1, 1] en un Signal; por debajo de `floor` es NEUTRAL."""
    conf = int(min(100, abs(score) * 100))
    direction = "NEUTRAL" if conf < floor else "LONG" if score > 0 else "SHORT"
    return Signal(agent=agent, symbol=symbol, direction=direction, confidence=conf, rationale=rationale, **kw)


def neutral(agent: str, symbol: str, rationale: str, **kw) -> Signal:
    return Signal(agent=agent, symbol=symbol, direction="NEUTRAL", confidence=0, rationale=rationale, **kw)
