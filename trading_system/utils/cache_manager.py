"""Caché persistente en disco (diskcache) para datos de mercado y noticias."""
import diskcache

import config

_cache: diskcache.Cache | None = None


def _c() -> diskcache.Cache:
    global _cache
    if _cache is None:
        _cache = diskcache.Cache(str(config.CACHE_DIR))
    return _cache


def cache_get(key: str):
    """Valor guardado, o None si no existe o ya caducó."""
    return _c().get(key)


def cache_set(key: str, value, ttl: float) -> None:
    _c().set(key, value, expire=ttl)


def window(seconds: float) -> float:
    """Ajusta una caducidad a la ventana de 1 a 3 minutos de los datos de mercado."""
    return max(60.0, min(seconds, 180.0))


def clear() -> None:
    _c().clear()
