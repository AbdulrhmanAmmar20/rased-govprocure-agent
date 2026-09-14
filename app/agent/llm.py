"""Client for the sovereign inference endpoint (NFR-1.1, NFR-1.3, NFR-2.2).

The SRS rules out public model APIs on legal grounds, so the interesting part
of this module is not the request — it is the check that runs before it.

``assert_sovereign_endpoint`` refuses to send anything to a host that is not
demonstrably inside the private network. A misconfigured base URL is the most
plausible way this system would ever leak procurement data to a foreign
provider: one environment variable, no error, no alarm. Failing closed turns
that from a silent breach into a startup failure.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.config import Settings, get_settings
from app.core.exceptions import ResidencyViolationError
from app.core.logging import get_logger

logger = get_logger("rased.llm")

# Hostname suffixes accepted without an IP check: Kubernetes service DNS and
# in-Kingdom private zones. Anything else must resolve to a private address.
SOVEREIGN_HOST_SUFFIXES = (
    ".svc.cluster.local",
    ".cluster.local",
    ".internal",
    ".local",
    ".gov.local",
)


# Liveness probes are cached per endpoint so a down inference service costs
# one connect timeout per window rather than one per request.
_AVAILABILITY_TTL_SECONDS = 15.0
_PROBE_TIMEOUT_SECONDS = 1.0

# Hard wall-clock ceiling on a liveness probe, enforced independently of the
# HTTP client. An httpx timeout does not bound name resolution: a cluster
# hostname that does not resolve costs about 3.5s in the platform resolver on
# some hosts, which alone would consume most of the NFR-2.2 budget. Running the
# probe on a worker and abandoning it at the deadline makes the worst case a
# property of this module rather than of the host's resolver configuration.
_PROBE_DEADLINE_SECONDS = 1.5

_probe_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rased-llm-probe")

_availability_cache: dict[str, tuple[bool, float]] = {}
_availability_lock = threading.Lock()


def reset_availability_cache() -> None:
    """Drop cached probe results. Used by tests and after a config change."""
    with _availability_lock:
        _availability_cache.clear()


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0


def _is_private_host(host: str) -> bool:
    """True when the host is a loopback/private address or a private zone."""
    if host in {"localhost", "vllm", "vllm-service"}:
        return True
    if any(host.endswith(suffix) for suffix in SOVEREIGN_HOST_SUFFIXES):
        return True

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            resolved = socket.gethostbyname(host)
            address = ipaddress.ip_address(resolved)
        except (OSError, ValueError):
            # Unresolvable is treated as unsafe. In an isolated VPC a genuine
            # internal name always resolves; a name that does not is either a
            # typo or a host outside the network.
            return False

    return address.is_private or address.is_loopback or address.is_link_local


def assert_sovereign_endpoint(base_url: str) -> None:
    """Raise unless ``base_url`` points inside the private network (NFR-1.1)."""
    parsed = urlparse(base_url)
    host = parsed.hostname or ""

    if not host:
        raise ResidencyViolationError(
            "عنوان محرك الاستدلال غير صالح.", base_url=base_url
        )

    if not _is_private_host(host):
        raise ResidencyViolationError(
            "محرك الاستدلال المُعد ليس داخل الشبكة الخاصة؛ يُمنع إرسال أي بيانات "
            "إليه وفق متطلب سيادة البيانات.",
            host=host,
            base_url=base_url,
        )


class SovereignLLM:
    """Minimal OpenAI-compatible chat client, pinned to the in-VPC endpoint."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        assert_sovereign_endpoint(self.settings.llm_base_url)
        self.base_url = self.settings.llm_base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
            # NFR-1.3 — asserted on every call. vLLM retains nothing by
            # default; the header keeps the intent explicit for any gateway
            # or proxy sitting in front of it.
            "X-Data-Retention": "none",
            "X-Rased-Residency": self.settings.data_residency_region,
        }

    def is_available(self, *, ttl_seconds: float = _AVAILABILITY_TTL_SECONDS) -> bool:
        """Cheap liveness probe, used to decide whether to fall back.

        The result is cached per endpoint for a few seconds. Without the cache
        an unreachable endpoint costs every single request a full connect
        timeout - measured at roughly three seconds against a down vLLM, which
        is most of the five-second NFR-2.2 budget spent re-learning something
        discovered a moment earlier. The TTL is short enough that a recovering
        endpoint is picked up promptly.
        """
        now = time.monotonic()
        with _availability_lock:
            cached = _availability_cache.get(self.base_url)
            if cached is not None and now - cached[1] < ttl_seconds:
                return cached[0]

        available = self._probe_within_deadline()

        with _availability_lock:
            _availability_cache[self.base_url] = (available, time.monotonic())
        return available

    def _probe_within_deadline(self) -> bool:
        """Run the probe on a worker, abandoning it at the deadline."""

        def probe() -> bool:
            try:
                response = httpx.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                    timeout=_PROBE_TIMEOUT_SECONDS,
                )
                return response.status_code < 500
            except httpx.HTTPError:
                return False

        future = _probe_pool.submit(probe)
        try:
            return future.result(timeout=_PROBE_DEADLINE_SECONDS)
        except FutureTimeout:
            # The worker is left to finish and be discarded. Treating a slow
            # probe as unavailable is the safe reading: the fallback report is
            # always correct, so a false negative costs prose, not accuracy.
            logger.warning(
                "inference liveness probe exceeded its deadline; treating endpoint as down",
                extra={"base_url": self.base_url, "deadline_s": _PROBE_DEADLINE_SECONDS},
            )
            return False

    def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """One non-streaming completion, bounded by the NFR-2.2 budget."""
        started = time.perf_counter()
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "stream": False,
        }
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=self._headers(),
            timeout=self.settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        usage = body.get("usage", {})

        return LLMResponse(
            text=body["choices"][0]["message"]["content"],
            model=body.get("model", self.settings.llm_model),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
