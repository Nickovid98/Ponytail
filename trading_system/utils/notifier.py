"""Alertas por Telegram y/o Discord. Nunca bloquean ni tumban el motor."""
import asyncio
import logging

import requests

import config

log = logging.getLogger("notifier")
_tasks: set = set()


def _post(text: str) -> None:
    targets = []
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        targets.append(("Telegram", f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                        {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000]}))
    if config.DISCORD_WEBHOOK_URL:
        targets.append(("Discord", config.DISCORD_WEBHOOK_URL, {"content": text[:1900]}))
    for name, url, body in targets:
        try:
            requests.post(url, json=body, timeout=10).raise_for_status()
        except Exception as e:
            # Solo el tipo de error: el mensaje de requests incluye la URL, y
            # la URL lleva el token del bot o el secreto del webhook.
            log.warning("Aviso por %s no entregado (%s)", name, type(e).__name__)


def send(text: str) -> None:
    """Envía sin esperar. Sin destinos configurados no hace nada."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID) and not config.DISCORD_WEBHOOK_URL:
        return
    try:
        task = asyncio.get_running_loop().create_task(asyncio.to_thread(_post, text))
        _tasks.add(task)  # sin esta referencia el recolector puede cancelar la tarea
        task.add_done_callback(_tasks.discard)
    except RuntimeError:  # llamado fuera del bucle asíncrono
        _post(text)
