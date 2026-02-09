import asyncio
import contextlib
import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv
from telethon import TelegramClient, events

from config import load_config


ADDRESS_REGEX = re.compile(r"0x[a-fA-F0-9]{40}")
TICKER_REGEX = re.compile(r"\$[A-Za-z0-9]{2,10}")
# Solana-style mint address (base58) + common suffix variants like "pump"
BASE58_MINT_REGEX = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,50}\b")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_percent(value: float) -> str:
    return f"{value:+.2f}%"


def _load_current(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            if isinstance(data.get("items"), dict):
                tokens = data.get("tokens") or []
                if tokens:
                    last_token = tokens[-1]
                    return data["items"].get(last_token)
                return next(iter(data["items"].values()), None)
            if "token" in data:
                return data
    except Exception:
        pass
    return None


def _save_current(path: Path, entry: Dict[str, Any]) -> None:
    path.write_text(json.dumps(entry, indent=2, sort_keys=True))


def _load_history(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if isinstance(data.get("items"), dict):
                return list(data["items"].values())
            if "token" in data:
                return [data]
    except Exception:
        pass
    return []


def _append_history(path: Path, entry: Dict[str, Any]) -> None:
    history = _load_history(path)
    history.append(entry)
    path.write_text(json.dumps(history, indent=2, sort_keys=True))


def _http_get_json(url: str, timeout_sec: int = 15) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TokenBot/1.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pick_best_pair(pairs: list) -> Optional[Dict[str, Any]]:
    best = None
    best_liquidity = -1.0
    for pair in pairs or []:
        liquidity = pair.get("liquidity", {}).get("usd") or 0
        try:
            liquidity_val = float(liquidity)
        except (TypeError, ValueError):
            liquidity_val = 0.0
        if liquidity_val > best_liquidity:
            best = pair
            best_liquidity = liquidity_val
    return best


def _parse_market_cap(pair: Dict[str, Any]) -> Optional[float]:
    market_cap = pair.get("marketCap")
    if market_cap is None:
        market_cap = pair.get("fdv")
    if market_cap is None:
        return None
    try:
        return float(market_cap)
    except (TypeError, ValueError):
        return None


def _fetch_market_cap_for_address(address: str) -> Tuple[Optional[float], Optional[str]]:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
    data = _http_get_json(url)
    pairs = data.get("pairs") or []
    best = _pick_best_pair(pairs)
    if not best:
        return None, None
    return _parse_market_cap(best), best.get("url")


def _fetch_market_cap_for_ticker(ticker: str) -> Tuple[Optional[float], Optional[str]]:
    query = urllib.parse.quote(ticker)
    url = f"https://api.dexscreener.com/latest/dex/search/?q={query}"
    data = _http_get_json(url)
    pairs = data.get("pairs") or []
    best = _pick_best_pair(pairs)
    if not best:
        return None, None
    return _parse_market_cap(best), best.get("url")


async def fetch_market_cap(token_key: str) -> Tuple[Optional[float], Optional[str]]:
    if token_key.startswith("0x"):
        return await asyncio.to_thread(_fetch_market_cap_for_address, token_key)
    return await asyncio.to_thread(_fetch_market_cap_for_ticker, token_key)


async def _refresh_entry_market_cap(
    token_key: str, entry: Dict[str, Any]
) -> None:
    market_cap, pair_url = await fetch_market_cap(token_key)
    if market_cap is None:
        return
    entry["initial_market_cap"] = market_cap
    entry["highest_market_cap"] = market_cap
    entry["current_market_cap"] = market_cap
    entry["current_percentage"] = _format_percent(0.0)
    entry["highest_percent_increase"] = 0.0
    entry["reached_50"] = False
    entry["last_checked"] = _utc_now_iso()
    if pair_url:
        entry["pair_url"] = pair_url


async def market_cap_monitor(
    config, current_holder: Dict[str, Optional[Dict[str, Any]]], lock: asyncio.Lock
) -> None:
    path = Path(config.output_json_path)
    while True:
        await asyncio.sleep(config.price_check_interval_sec)
        async with lock:
            entry = current_holder.get("entry")
        if not entry:
            continue
        token_key = entry.get("token")
        if not token_key:
            continue
        market_cap, pair_url = await fetch_market_cap(token_key)
        if market_cap is None:
            continue
        async with lock:
            entry = current_holder.get("entry")
            if not entry or entry.get("token") != token_key:
                continue
            initial_market_cap = entry.get("initial_market_cap")
            highest_market_cap = entry.get(
                "highest_market_cap", initial_market_cap or market_cap
            )
            if initial_market_cap is None:
                initial_market_cap = market_cap
                entry["initial_market_cap"] = market_cap
            if market_cap > highest_market_cap:
                highest_market_cap = market_cap
                entry["highest_market_cap"] = highest_market_cap
            entry["current_market_cap"] = market_cap
            current_percent = (
                ((market_cap - initial_market_cap) / initial_market_cap) * 100
                if initial_market_cap
                else 0.0
            )
            current_percent = round(current_percent, 4)
            entry["current_percentage"] = _format_percent(current_percent)
            highest_percent = (
                ((highest_market_cap - initial_market_cap) / initial_market_cap) * 100
                if initial_market_cap
                else 0.0
            )
            highest_percent = round(highest_percent, 4)
            entry["highest_percent_increase"] = max(
                entry.get("highest_percent_increase", 0.0), highest_percent
            )
            entry["reached_50"] = highest_percent >= 50.0
            entry["last_checked"] = _utc_now_iso()
            if pair_url:
                entry["pair_url"] = pair_url
            _save_current(path, entry)


async def main() -> None:
    load_dotenv()
    config = load_config()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    client = TelegramClient("userbot_session", config.api_id, config.api_hash)
    results_path = Path(config.output_json_path)
    history_path = Path(config.history_json_path)
    current_entry = _load_current(results_path)
    results_lock = asyncio.Lock()
    current_holder: Dict[str, Optional[Dict[str, Any]]] = {"entry": current_entry}

    if current_entry:
        async with results_lock:
            if "initial_price" in current_entry or "highest_price" in current_entry:
                current_entry.pop("initial_price", None)
                current_entry.pop("highest_price", None)
            if "percent_increase" in current_entry:
                raw_value = current_entry.pop("percent_increase")
                if isinstance(raw_value, (int, float)):
                    current_entry["current_percentage"] = _format_percent(float(raw_value))
                else:
                    current_entry["current_percentage"] = raw_value
            if "current_percentage" not in current_entry:
                current_entry["current_percentage"] = _format_percent(0.0)
                
            if current_entry.get("initial_market_cap") is None:
                await _refresh_entry_market_cap(current_entry["token"], current_entry)
            if current_entry.get("highest_percent_increase") is None:
                current_entry["highest_percent_increase"] = 0.0
            _save_current(results_path, current_entry)

    if config.static_tokens and not current_holder["entry"]:
        async with results_lock:
            token_key = config.static_tokens[-1]
            entry = {
                "token": token_key,
                "time_posted": _utc_now_iso(),
                "initial_market_cap": None,
                "highest_market_cap": None,
                "current_market_cap": None,
                    "current_percentage": _format_percent(0.0),
                "highest_percent_increase": 0.0,
                "reached_50": False,
                "last_checked": None,
                "pair_url": None,
            }
            await _refresh_entry_market_cap(token_key, entry)
            current_holder["entry"] = entry
            _save_current(results_path, entry)

    @client.on(events.NewMessage)
    async def handler(event: events.NewMessage.Event) -> None:
        try:
            message = event.raw_text or ""

            if config.discovery_mode:
                logging.info(
                    "Discovery message: chat_id=%s sender_id=%s text=%s",
                    event.chat_id,
                    event.sender_id,
                    message,
                )
                await client.send_message(
                    "me",
                    f"Discovery message\nchat_id: {event.chat_id}\n"
                    f"sender_id: {event.sender_id}\ntext: {message}",
                )
                return

            # Only allow messages from whitelisted groups
            if event.chat_id not in config.group_ids:
                return
            # If USER_IDS is empty, allow all senders in the group.
            if config.user_ids and event.sender_id not in config.user_ids:
                return

            addresses = ADDRESS_REGEX.findall(message)
            tickers = [t[1:] for t in TICKER_REGEX.findall(message)]
            base58_mints = BASE58_MINT_REGEX.findall(message)
            tokens = addresses + tickers + base58_mints
            if not tokens:
                return

            logging.info(
                "Token message: chat_id=%s sender_id=%s tokens=%s",
                event.chat_id,
                event.sender_id,
                ", ".join(tokens),
            )

            async with results_lock:
                for token_key in tokens:
                    current_entry = current_holder.get("entry")
                    if current_entry and current_entry.get("token") == token_key:
                        continue
                    if current_entry:
                        _append_history(history_path, dict(current_entry))
                    entry = {
                        "token": token_key,
                        "time_posted": event.date.astimezone(timezone.utc).isoformat(),
                        "initial_market_cap": None,
                        "highest_market_cap": None,
                        "current_market_cap": None,
                        "current_percentage": _format_percent(0.0),
                        "highest_percent_increase": 0.0,
                        "reached_50": False,
                        "last_checked": None,
                        "pair_url": None,
                    }
                    await _refresh_entry_market_cap(token_key, entry)
                    current_holder["entry"] = entry
                    _save_current(results_path, entry)

            if config.forward_to_saved:
                await client.send_message(
                    "me",
                    f"Group message\nchat_id: {event.chat_id}\n"
                    f"sender_id: {event.sender_id}\ntext: {message}",
                )
        except Exception as exc:
            logging.exception("Handler error: %s", exc)

    await client.start()
    monitor_task = asyncio.create_task(
        market_cap_monitor(config, current_holder, results_lock)
    )
    logging.info("Userbot started. Listening for messages...")
    try:
        await client.run_until_disconnected()
    finally:
        monitor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await monitor_task


if __name__ == "__main__":
    asyncio.run(main())
