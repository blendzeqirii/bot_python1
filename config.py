import os
from dataclasses import dataclass
from typing import List, Optional


def _get_env(name: str, required: bool = True, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"Missing required env var: {name}")
    return value or ""


def _parse_int_list(value: str) -> List[int]:
    if not value:
        return []
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _parse_str_list(value: str) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    group_ids: List[int]
    user_ids: List[int]
    private_key: str
    rpc_url: str
    router_address: str
    weth_address: str
    slippage_bps: int
    gas_limit: int
    max_buy_amount_eth: float
    chain_id: int
    receipt_timeout_sec: int
    discovery_mode: bool
    forward_to_saved: bool
    price_check_interval_sec: int
    output_json_path: str
    history_json_path: str
    static_tokens: List[str]


def load_config() -> Config:
    discovery_mode = os.getenv("DISCOVERY_MODE", "0").lower() in ("1", "true", "yes")
    trading_enabled = os.getenv("ENABLE_TRADING", "0").lower() in ("1", "true", "yes")

    api_id = int(_get_env("API_ID"))
    api_hash = _get_env("API_HASH")
    group_ids = _parse_int_list(_get_env("GROUP_IDS", required=not discovery_mode, default=""))
    user_ids = _parse_int_list(_get_env("USER_IDS", required=False, default=""))
    private_key = _get_env("PRIVATE_KEY", required=trading_enabled, default="")
    rpc_url = _get_env("RPC_URL", required=trading_enabled, default="")
    router_address = _get_env("DEX_ROUTER_ADDRESS", required=trading_enabled, default="")
    weth_address = _get_env("WETH_ADDRESS", required=trading_enabled, default="")
    slippage_bps = int(os.getenv("SLIPPAGE_BPS", "200"))  # 200 = 2.00%
    gas_limit = int(os.getenv("GAS_LIMIT", "300000"))
    max_buy_amount_eth = float(os.getenv("MAX_BUY_AMOUNT_ETH", "0.01"))
    chain_id = int(os.getenv("CHAIN_ID", "1"))
    receipt_timeout_sec = int(os.getenv("RECEIPT_TIMEOUT_SEC", "120"))
    forward_to_saved = os.getenv("FORWARD_TO_SAVED", "0").lower() in ("1", "true", "yes")
    price_check_interval_sec = int(os.getenv("PRICE_CHECK_INTERVAL_SEC", "60"))
    output_json_path = os.getenv("OUTPUT_JSON_PATH", "token_results.json")
    history_json_path = os.getenv("HISTORY_JSON_PATH", "token_history.json")
    static_tokens = _parse_str_list(os.getenv("STATIC_TOKENS", ""))

    if not discovery_mode:
        if not group_ids:
            raise ValueError("GROUP_IDS must contain at least one group id")

    return Config(
        api_id=api_id,
        api_hash=api_hash,
        group_ids=group_ids,
        user_ids=user_ids,
        private_key=private_key,
        rpc_url=rpc_url,
        router_address=router_address,
        weth_address=weth_address,
        slippage_bps=slippage_bps,
        gas_limit=gas_limit,
        max_buy_amount_eth=max_buy_amount_eth,
        chain_id=chain_id,
        receipt_timeout_sec=receipt_timeout_sec,
        discovery_mode=discovery_mode,
        forward_to_saved=forward_to_saved,
        price_check_interval_sec=price_check_interval_sec,
        output_json_path=output_json_path,
        history_json_path=history_json_path,
        static_tokens=static_tokens,
    )
