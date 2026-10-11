import os
import time
import asyncio
from typing import Dict, List, Set, Optional
from collections import defaultdict
from fastapi import Request, HTTPException, status
from loguru import logger

# Default trusted proxies: local loopback and standard docker bridge networks
DEFAULT_TRUSTED_PROXIES: Set[str] = {
    "127.0.0.1",
    "::1",
    "localhost",
    "testclient",
}

# In-memory storage for sliding window: scope -> (ip -> list of timestamps)
_memory_buckets: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
_bucket_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_MAX_STORED_IPS = 1000


def get_trusted_proxies() -> Set[str]:
    """Retrieves the set of trusted proxy IP addresses from environment variables."""
    proxies = set(DEFAULT_TRUSTED_PROXIES)
    configured = os.getenv("TRUSTED_PROXIES", "").strip()
    if configured:
        for p in configured.split(","):
            cleaned = p.strip()
            if cleaned:
                proxies.add(cleaned)
    return proxies


import ipaddress

def is_trusted_proxy(ip: str) -> bool:
    """
    Checks whether the immediate network peer IP is an authorized reverse proxy.
    Supports IPv4, IPv6, and configured CIDR network ranges (e.g. 10.0.0.0/8, 172.16.0.0/12).
    """
    if not ip:
        return False
    if ip in ("localhost", "testclient"):
        return True

    try:
        peer_addr = ipaddress.ip_address(ip)
    except ValueError:
        return False

    trusted_entries = get_trusted_proxies()
    for entry in trusted_entries:
        if entry in ("localhost", "testclient"):
            continue
        try:
            if "/" in entry:
                if peer_addr in ipaddress.ip_network(entry, strict=False):
                    return True
            else:
                if peer_addr == ipaddress.ip_address(entry):
                    return True
        except ValueError:
            continue

    # Default Docker bridge network range (172.16.0.0/12)
    try:
        if peer_addr in ipaddress.ip_network("172.16.0.0/12", strict=False):
            return True
    except ValueError:
        pass

    return False


def extract_client_ip(request: Request) -> str:
    """
    Safely resolves client IP address:
    - Verifies whether the direct connection peer (socket host) is an authorized trusted proxy.
    - If trusted proxy: respects X-Real-IP (overwritten by Nginx) or leftmost X-Forwarded-For.
    - If direct untrusted client: ignores all client-supplied proxy headers to prevent IP spoofing.
    """
    peer_ip = request.client.host if request.client and request.client.host else "127.0.0.1"

    if is_trusted_proxy(peer_ip):
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

    return peer_ip


def get_rate_limit_config(scope: str) -> int:
    """Retrieves configured limit per minute for the given authentication scope."""
    if scope == "login":
        env_val = os.getenv("LOGIN_RATE_LIMIT_PER_MINUTE") or os.getenv("AUTH_RATE_LIMIT_PER_MINUTE", "60")
    elif scope == "microsoft":
        env_val = os.getenv("MICROSOFT_RATE_LIMIT_PER_MINUTE") or os.getenv("AUTH_RATE_LIMIT_PER_MINUTE", "60")
    else:
        env_val = os.getenv("AUTH_RATE_LIMIT_PER_MINUTE", "60")

    try:
        return int(env_val.strip())
    except (ValueError, AttributeError):
        return 60


def validate_trusted_proxies_config() -> None:
    """
    Validates configured TRUSTED_PROXIES IP addresses and CIDR notations at startup.
    Fails startup with RuntimeError in production if any entry is invalid.
    """
    raw = os.getenv("TRUSTED_PROXIES", "").strip()
    if not raw:
        return
    for item in raw.split(","):
        cleaned = item.strip()
        if not cleaned or cleaned in ("localhost", "testclient"):
            continue
        try:
            if "/" in cleaned:
                ipaddress.ip_network(cleaned, strict=False)
            else:
                ipaddress.ip_address(cleaned)
        except ValueError as e:
            is_prod = os.getenv("ENVIRONMENT", "").lower() in ("production", "prod")
            msg = f"Invalid IP/CIDR in TRUSTED_PROXIES: '{cleaned}' ({e})"
            if is_prod:
                raise RuntimeError(f"FATAL: {msg}")
            logger.warning(f"[RateLimit] {msg}")


class SlidingWindowLimiter:
    """
    In-process sliding-window rate limiter instance.
    NOTE: In-process limiters are scoped to the current OS process.
    In multi-worker or multi-container deployments, reverse-proxy gateway rate limiting
    (e.g. Nginx limit_req_zone) must be active to enforce an aggregate cluster-wide ceiling.
    """
    def __init__(self, max_stored_ips: int = 1000):
        self.buckets: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        self.locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.max_stored_ips = max_stored_ips

    def clear(self) -> None:
        for scope in self.buckets:
            self.buckets[scope].clear()

    async def check(
        self,
        client_ip: str,
        scope: str,
        max_attempts: int,
        window_seconds: float = 60.0,
    ) -> None:
        now = time.time()
        cutoff = now - window_seconds

        lock = self.locks[scope]
        async with lock:
            bucket = self.buckets[scope]

            # Prevent unbounded memory growth by pruning stale IP keys
            if len(bucket) > self.max_stored_ips:
                stale_keys = [ip for ip, timestamps in bucket.items() if not timestamps or timestamps[-1] < cutoff]
                for ip in stale_keys:
                    bucket.pop(ip, None)

            timestamps = [t for t in bucket[client_ip] if t >= cutoff]

            if len(timestamps) >= max_attempts:
                oldest_in_window = timestamps[0]
                retry_after = max(1, int(window_seconds - (now - oldest_in_window)))
                logger.warning(
                    f"[RateLimit] Scope '{scope}' threshold exceeded for IP: {client_ip} "
                    f"({len(timestamps)}/{max_attempts} attempts). Retry-After: {retry_after}s"
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many authentication attempts ({scope}). Please try again in {retry_after} seconds.",
                    headers={"Retry-After": str(retry_after)},
                )

            timestamps.append(now)
            bucket[client_ip] = timestamps


# Default global in-process limiter
_default_limiter = SlidingWindowLimiter(max_stored_ips=_MAX_STORED_IPS)


def reset_rate_limits() -> None:
    """Clears all in-memory rate limiting state (used for testing)."""
    _default_limiter.clear()
    for scope in _memory_buckets:
        _memory_buckets[scope].clear()


async def check_sliding_window_rate_limit(
    request: Request,
    scope: str,
    window_seconds: float = 60.0,
) -> None:
    """
    Atomic sliding-window rate limiter dependency.
    Provides isolated buckets per scope (e.g. 'login' vs 'microsoft') to ensure
    brute-force attacks against one authentication flow cannot block another.
    Fails closed in production if an unexpected internal error occurs.
    """
    max_attempts = get_rate_limit_config(scope)
    client_ip = extract_client_ip(request)

    try:
        await _default_limiter.check(
            client_ip=client_ip,
            scope=scope,
            max_attempts=max_attempts,
            window_seconds=window_seconds,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RateLimit] Unexpected error in rate limiter for scope '{scope}': {e}")
        is_prod = os.getenv("ENVIRONMENT", "").lower() in ("production", "prod")
        if is_prod:
            # In production: Fail-closed to prevent bypassing security controls during failures
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service temporarily unavailable (rate limiting check error).",
            )
        raise


async def check_login_rate_limit(request: Request) -> None:
    """Rate limit dependency specifically protecting email/password login."""
    await check_sliding_window_rate_limit(request, scope="login")


async def check_microsoft_rate_limit(request: Request) -> None:
    """Rate limit dependency specifically protecting Microsoft Entra token exchange."""
    await check_sliding_window_rate_limit(request, scope="microsoft")
