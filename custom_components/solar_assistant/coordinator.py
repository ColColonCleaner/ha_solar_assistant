"""Background WebSocket coordinator for a SolarAssistant config entry."""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import socket as _socket
from datetime import datetime
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util.dt import utcnow

from py_solar_assistant import (
    ConnectError,
    DeviceMetric,
    Metric,
    Options,
    SolarAssistantClient,
    SolarAssistantError,
    TopicFilter,
    authorize_site,
    connect,
    get_device_metrics,
    get_device_site_id,
)
from .const import (
    ALWAYS_ON_SYSTEM_TOPICS,
    AUTH_CLOUD,
    AUTH_LOCAL,
    CLOUD_IP_REFRESH_INTERVAL_S,
    CONF_API_KEY,
    CONF_AUTH_METHOD,
    CONF_ENABLED_TOPICS,
    CONF_HOST,
    CONF_LOCAL_IP,
    CONF_MIN_UPDATE_INTERVAL,
    CONF_STALE_AFTER,
    DEFAULT_MIN_UPDATE_INTERVAL,
    DEFAULT_STALE_AFTER,
    DOMAIN,
    CONF_PASSWORD,
    CONF_SITE_ID,
    CONF_SITE_KEY,
    CONF_TOKEN,
    DEFAULT_ENABLED_GLOBS,
    DEVICE_REST_USERNAME,
    IP_RESCAN_AFTER_S,
    IP_RESCAN_INTERVAL_S,
    MDNS_SCAN_TIMEOUT_S,
    MDNS_SERVICE_TYPE,
    RECONNECT_INITIAL_S,
    RECONNECT_MAX_S,
    SYSTEM_PREFIX,
    WRITE_ATTEMPTS,
    WRITE_RETRY_DELAY_S,
    WRITE_TIMEOUT_S,
    signal_connection_state,
    signal_metric_update,
    signal_new_metric,
)
from .health import device_prefix, should_dispatch

_LOGGER = logging.getLogger(__name__)


class SolarAssistantCoordinator:
    """Owns one WebSocket per config entry, streams metrics into HA."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.definitions: dict[str, dict[str, Any]] = {}
        self.data: dict[str, Any] = {}
        self._task: asyncio.Task | None = None
        self._stopping = False
        # Connection state
        self.is_connected: bool = False
        self.connected_host: str | None = None
        self.last_connected_at: datetime | None = None
        self.last_error: str | None = None
        # IP-change recovery
        self._first_failure_at: datetime | None = None
        self._last_ip_scan_at: datetime | None = None
        self._last_cloud_auth_at: datetime | None = None
        # The battery top-off of this entry (topoff_manager.TopOffManager)
        self.topoff: Any = None
        # Update throttling and staleness
        self._last_dispatch: dict[str, float] = {}
        self._pending: set[str] = set()
        self._flush_unsub: Any = None
        self.last_change: dict[str, datetime] = {}
        # Setting writes
        self._write_lock = asyncio.Lock()
        self._verify_writes = True

    @property
    def signal_new_metric(self) -> str:
        return signal_new_metric(self.entry.entry_id)

    @property
    def signal_metric_update(self) -> str:
        return signal_metric_update(self.entry.entry_id)

    @property
    def signal_connection_state(self) -> str:
        return signal_connection_state(self.entry.entry_id)

    def _set_connection_state(
        self, connected: bool, *, host: str | None = None, error: str | None = None
    ) -> None:
        self.is_connected = connected
        if host is not None:
            self.connected_host = host
        if connected:
            self.last_connected_at = utcnow()
            self.last_error = None
        elif error is not None:
            self.last_error = error
        async_dispatcher_send(self.hass, self.signal_connection_state)

    async def async_start(self) -> None:
        """Spawn the background reconnect loop. Returns immediately."""
        self._task = self.hass.async_create_background_task(
            self._run(), f"solar_assistant_ws[{self.entry.entry_id}]"
        )

    async def async_stop(self) -> None:
        self._stopping = True
        if self._flush_unsub is not None:
            self._flush_unsub()
            self._flush_unsub = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        """Reconnect loop with exponential backoff."""
        backoff = RECONNECT_INITIAL_S
        while not self._stopping:
            try:
                await self._session_once()
                backoff = RECONNECT_INITIAL_S
                self._first_failure_at = None
            except asyncio.CancelledError:
                return
            except _ReauthRequired as err:
                _LOGGER.info("Re-authorizing site %s: %s", self.entry.title, err)
                self._set_connection_state(False, error=str(err))
                try:
                    await self._refresh_cloud_token()
                    backoff = RECONNECT_INITIAL_S
                    self._first_failure_at = None
                    continue
                except Exception as auth_err:
                    _LOGGER.warning("Re-authorize failed: %s", auth_err)
                    self._set_connection_state(False, error=f"re-authorize failed: {auth_err}")
            except Exception as err:
                _LOGGER.warning(
                    "SolarAssistant connection lost (%s) — retrying in %ss",
                    err, backoff,
                )
                self._set_connection_state(False, error=str(err))
                now = utcnow()
                if self._first_failure_at is None:
                    self._first_failure_at = now
                elapsed = (now - self._first_failure_at).total_seconds()
                if elapsed >= IP_RESCAN_AFTER_S:
                    if (self._last_ip_scan_at is None or
                            (now - self._last_ip_scan_at).total_seconds() >= IP_RESCAN_INTERVAL_S):
                        self._last_ip_scan_at = now
                        await self._recover_ip()

            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, RECONNECT_MAX_S)

    async def _session_once(self) -> None:
        opts = self._build_options()

        # Pre-populate definitions and data from REST before connecting the WebSocket.
        # Settings topics (number/select/switch) are not in the curated WS filter, so
        # without this they would never appear as entities.
        await self._rest_discovery()

        # The unit is reachable right now - capture its site_id if we don't have one,
        # so IP-change recovery can mDNS-scan for it once the IP moves.
        await self._store_site_id()

        try:
            sock = await connect(opts)
        except ConnectError as err:
            msg = str(err)
            if ("401" in msg or "403" in msg) and self.entry.data.get(CONF_AUTH_METHOD) == AUTH_CLOUD:
                raise _ReauthRequired(msg) from err
            raise

        try:
            filters = self._build_topic_filters()
            await sock.subscribe_metrics(self._on_metric, *filters)
            self._set_connection_state(True, host=sock.connected_host)
            await sock.listen()
        finally:
            self._set_connection_state(False)
            try:
                await sock.close()
            except Exception:
                pass

    async def _rest_discovery(self) -> None:
        """Fetch all metric definitions + current values from REST."""
        try:
            metrics = await self._fetch_discovery()
        except Exception as err:
            _LOGGER.debug("REST discovery skipped: %s", err)
            return
        new_topics: list[str] = []
        updated: list[str] = []
        for m in metrics:
            if m.group == "Ignore":
                continue
            topic = m.topic
            is_new = topic not in self.definitions
            self.definitions[topic] = _defn_for(m)
            self.data[topic] = m.value
            if is_new:
                new_topics.append(topic)
            elif self.definitions[topic].get("platform") in ("number", "select", "switch"):
                updated.append(topic)
        # Every definition is stored before the first announcement, so a new
        # entity sees the whole unit, such as how many inverters it has.
        for topic in new_topics:
            async_dispatcher_send(self.hass, self.signal_new_metric, topic)
        for topic in updated:
            async_dispatcher_send(self.hass, self.signal_metric_update, topic)

    async def _fetch_discovery(self) -> list[DeviceMetric]:
        d = self.entry.data
        method = d.get(CONF_AUTH_METHOD, AUTH_LOCAL)
        if method == AUTH_LOCAL:
            return await get_device_metrics(d[CONF_HOST], password=d[CONF_PASSWORD])
        local_ip = d.get(CONF_LOCAL_IP, "")
        token = d[CONF_TOKEN]
        if local_ip:
            try:
                return await get_device_metrics(local_ip, token=token)
            except Exception as err:
                _LOGGER.debug("Local REST discovery to %s failed: %s", local_ip, err)
        return await get_device_metrics(
            d.get(CONF_HOST, ""),
            token=token,
            scheme="https",
            site_id=int(d.get(CONF_SITE_ID, 0) or 0),
            site_key=d.get(CONF_SITE_KEY, ""),
        )

    async def _store_site_id(self) -> None:
        """Store the unit's site_id for local entries that don't have one."""
        d = self.entry.data
        if d.get(CONF_AUTH_METHOD, AUTH_LOCAL) != AUTH_LOCAL or d.get(CONF_SITE_ID):
            return  # cloud entries already carry site_id; nothing to do if set
        try:
            site_id = await get_device_site_id(d[CONF_HOST], password=d[CONF_PASSWORD])
        except SolarAssistantError as err:
            _LOGGER.debug("Failed to get site_id over REST: %s", err)
            return
        if site_id is None:
            return
        _LOGGER.info("Stored site_id=%s for IP-change recovery", site_id)
        self.hass.config_entries.async_update_entry(
            self.entry, data={**d, CONF_SITE_ID: site_id}
        )

    async def _recover_ip(self) -> None:
        """Try to find the unit's new IP after prolonged connection failure."""
        d = self.entry.data
        method = d.get(CONF_AUTH_METHOD, AUTH_LOCAL)
        site_id = d.get(CONF_SITE_ID)

        if site_id is None:
            # site_id is back-filled from the unit's REST API while it's reachable
            # (see _store_site_id); missing here means that never succeeded.
            _LOGGER.debug("IP recovery skipped: no site_id stored for this entry")
            return

        _LOGGER.info("IP recovery: scanning mDNS for site_id=%s", site_id)
        new_ip = await self._mdns_find_ip(int(site_id))
        if new_ip:
            _LOGGER.info("IP recovery: found %s via mDNS, updating stored host", new_ip)
            self._update_host(new_ip)
            return

        # mDNS found nothing — for cloud entries try re-authorizing (max once per 12 h)
        if method == AUTH_CLOUD:
            now = utcnow()
            if (self._last_cloud_auth_at is None or
                    (now - self._last_cloud_auth_at).total_seconds() >= CLOUD_IP_REFRESH_INTERVAL_S):
                self._last_cloud_auth_at = now
                _LOGGER.info("IP recovery: refreshing cloud token to get updated local IP")
                try:
                    await self._refresh_cloud_token()
                except Exception as err:
                    _LOGGER.debug("IP recovery cloud refresh failed: %s", err)

    def _update_host(self, ip: str) -> None:
        """Persist a newly discovered IP into the config entry."""
        d = self.entry.data
        method = d.get(CONF_AUTH_METHOD, AUTH_LOCAL)
        key = CONF_HOST if method == AUTH_LOCAL else CONF_LOCAL_IP
        self.hass.config_entries.async_update_entry(self.entry, data={**d, key: ip})

    async def _mdns_find_ip(self, site_id: int) -> str | None:
        from homeassistant.components.zeroconf import async_get_instance
        try:
            zc = await async_get_instance(self.hass)
            return await self.hass.async_add_executor_job(_mdns_scan_for_site, zc, site_id)
        except Exception as err:
            _LOGGER.debug("mDNS scan failed: %s", err)
            return None

    def _build_options(self) -> Options:
        d = self.entry.data
        method = d.get(CONF_AUTH_METHOD, AUTH_LOCAL)
        if method == AUTH_LOCAL:
            return Options(local_ip=d[CONF_HOST], password=d[CONF_PASSWORD])
        return Options(
            host=d.get(CONF_HOST, ""),
            local_ip=d.get(CONF_LOCAL_IP, ""),
            token=d.get(CONF_TOKEN, ""),
            site_id=int(d.get(CONF_SITE_ID, 0) or 0),
            site_key=d.get(CONF_SITE_KEY, ""),
        )

    def enabled_sensor_globs(self) -> list[str]:
        """Topic glob patterns enabled as read-only sensors."""
        explicit = self.entry.options.get(CONF_ENABLED_TOPICS)
        return list(explicit) if explicit is not None else list(DEFAULT_ENABLED_GLOBS)

    def should_create_sensor(self, topic: str) -> bool:
        """Whether a read-only metric is enabled and should become a sensor."""
        if topic in ALWAYS_ON_SYSTEM_TOPICS:
            return True
        return any(fnmatch.fnmatchcase(topic, g) for g in self.enabled_sensor_globs())

    def _build_topic_filters(self) -> list[TopicFilter]:
        """Server-side subscription list. Settings topics are always added so they receive updates."""
        base = self.enabled_sensor_globs()
        base_set = set(base)
        for topic, defn in self.definitions.items():
            if defn.get("platform") in ("number", "select", "switch") and topic not in base_set:
                base.append(topic)
        # The server streams system/* only when named, and older units' fallback
        # push is filtered by this same list.
        for topic in ALWAYS_ON_SYSTEM_TOPICS:
            if not any(fnmatch.fnmatchcase(topic, g) for g in base):
                base.append(topic)
        return [TopicFilter(topic=t) for t in base]

    async def set_setting(self, topic: str, value: Any) -> None:
        """Write a setting to the unit, one write at a time, and have the unit verify it.

        Overlapping writes of one automation could undo each other, and about one
        write in ten failed with a 422 (upstream issues #32 and #35). So each write
        takes the lock of the entry, asks the unit to read the value back until it
        holds (the ``verify`` field of ``POST /api/v1/metrics``), and retries a 422,
        a 5xx or a lost connection.
        """
        text = str(value)
        try:
            await self._write_with_retries(topic, text)
        except HomeAssistantError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._write_issue_id(topic),
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="write_failed",
                translation_placeholders={
                    "title": self.entry.title, "topic": topic, "value": text, "error": str(err),
                },
            )
            raise
        ir.async_delete_issue(self.hass, DOMAIN, self._write_issue_id(topic))

    def _write_issue_id(self, topic: str) -> str:
        return f"write_failed_{self.entry.entry_id}_{topic.replace('/', '_')}"

    async def _write_with_retries(self, topic: str, text: str) -> None:
        async with self._write_lock:
            last_err: Exception | None = None
            for attempt in range(1, WRITE_ATTEMPTS + 1):
                if attempt > 1:
                    await asyncio.sleep(WRITE_RETRY_DELAY_S * (attempt - 1))
                try:
                    await self._post_setting(topic, text)
                except _WriteRetryable as err:
                    last_err = err
                    _LOGGER.warning(
                        "Write %s = %s failed, attempt %s of %s: %s",
                        topic, text, attempt, WRITE_ATTEMPTS, err,
                    )
                    continue
                except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                    last_err = err
                    _LOGGER.warning(
                        "Write %s = %s lost its connection, attempt %s of %s: %s",
                        topic, text, attempt, WRITE_ATTEMPTS, err,
                    )
                    continue
                # The unit confirmed the value, so the entity shows it at once
                # instead of waiting for the next push.
                self.data[topic] = text
                async_dispatcher_send(self.hass, self.signal_metric_update, topic)
                return
        raise HomeAssistantError(
            f"SolarAssistant did not apply {topic} = {text} after {WRITE_ATTEMPTS} attempts: {last_err}"
        )

    async def _post_setting(self, topic: str, text: str) -> None:
        """One ``POST /api/v1/metrics``. Raises ``_WriteRetryable`` for a reply worth a retry."""
        d = self.entry.data
        method = d.get(CONF_AUTH_METHOD, AUTH_LOCAL)
        headers: dict[str, str] = {}
        auth: aiohttp.BasicAuth | None = None
        if method == AUTH_LOCAL:
            url = f"http://{d[CONF_HOST]}/api/v1/metrics"
            auth = aiohttp.BasicAuth(DEVICE_REST_USERNAME, d[CONF_PASSWORD])
        else:
            headers["Authorization"] = f"Bearer {d[CONF_TOKEN]}"
            local_ip = d.get(CONF_LOCAL_IP, "")
            if local_ip and self.connected_host == local_ip:
                url = f"http://{local_ip}/api/v1/metrics"
            else:
                url = f"https://{d.get(CONF_HOST, '')}/api/v1/metrics"
                site_id = int(d.get(CONF_SITE_ID, 0) or 0)
                if site_id:
                    headers["site-id"] = str(site_id)
                if d.get(CONF_SITE_KEY):
                    headers["site-key"] = d[CONF_SITE_KEY]

        body: dict[str, Any] = {"topic": topic, "value": text}
        if self._verify_writes:
            body["verify"] = True
        session = async_get_clientsession(self.hass)
        async with session.post(
            url,
            json=body,
            auth=auth,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=WRITE_TIMEOUT_S),
        ) as resp:
            if resp.status == 200:
                return
            detail = await _error_detail(resp)
        if self._verify_writes and resp.status in (400, 422) and "verify" in detail.lower():
            # A unit that predates the verify field rejects it. Write without it
            # from now on.
            _LOGGER.info("SolarAssistant rejected the verify field (%s), writing without it", detail)
            self._verify_writes = False
            await self._post_setting(topic, text)
            return
        if resp.status == 422 or resp.status >= 500:
            raise _WriteRetryable(f"HTTP {resp.status}: {detail}")
        raise HomeAssistantError(f"SolarAssistant refused {topic} = {text}: HTTP {resp.status}: {detail}")

    async def _refresh_cloud_token(self) -> None:
        d = self.entry.data
        api_key = d[CONF_API_KEY]
        site_id = int(d[CONF_SITE_ID])
        async with SolarAssistantClient(api_key) as client:
            auth = await authorize_site(client, site_id)
        new_data = {
            **d,
            CONF_HOST: auth.host,
            CONF_LOCAL_IP: auth.local_ip,
            CONF_TOKEN: auth.token,
            CONF_SITE_ID: auth.site_id,
            CONF_SITE_KEY: auth.site_key,
        }
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    @callback
    def _on_metric(self, m: Metric) -> None:
        if m.group == "Ignore":
            return
        topic = m.topic
        is_new = topic not in self.definitions
        previous = self.data.get(topic)
        self.definitions[topic] = _defn_for(m)
        self.data[topic] = m.value
        self._note_change(topic, previous, m.value, is_new)
        if is_new:
            async_dispatcher_send(self.hass, self.signal_new_metric, topic)
            self._dispatch(topic)
            return
        now = self.hass.loop.time()
        if should_dispatch(
            now,
            self._last_dispatch.get(topic),
            self.min_update_interval,
            previous,
            m.value,
            self.definitions[topic].get("platform"),
        ):
            self._dispatch(topic)
        else:
            self._defer(topic)

    @property
    def min_update_interval(self) -> float:
        """The least time between two updates of one sensor, 0 for no limit (upstream issue #24)."""
        return float(self.entry.options.get(CONF_MIN_UPDATE_INTERVAL, DEFAULT_MIN_UPDATE_INTERVAL) or 0)

    @property
    def stale_after(self) -> float:
        """Minutes without a changed value before a device counts as stale (upstream issue #14)."""
        return float(self.entry.options.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER) or 0)

    @callback
    def _dispatch(self, topic: str) -> None:
        self._last_dispatch[topic] = self.hass.loop.time()
        self._pending.discard(topic)
        async_dispatcher_send(self.hass, self.signal_metric_update, topic)

    @callback
    def _defer(self, topic: str) -> None:
        """Hold back an update, and send the newest value when the interval ends."""
        self._pending.add(topic)
        if self._flush_unsub is None:
            self._flush_unsub = async_call_later(self.hass, self.min_update_interval, self._flush)

    @callback
    def _flush(self, _now: Any) -> None:
        self._flush_unsub = None
        for topic in list(self._pending):
            self._dispatch(topic)

    @callback
    def _note_change(self, topic: str, previous: Any, value: Any, is_new: bool) -> None:
        """Record when a device last sent a changed value."""
        prefix = device_prefix(topic)
        if prefix is None:
            return
        if is_new or previous != value or prefix not in self.last_change:
            self.last_change[prefix] = utcnow()


type SolarAssistantConfigEntry = ConfigEntry[SolarAssistantCoordinator]


class _ReauthRequired(Exception):
    """Internal: cloud token rejected, refresh needed."""


class _WriteRetryable(Exception):
    """Internal: the unit did not apply a write, and a retry can succeed."""


async def _error_detail(resp: aiohttp.ClientResponse) -> str:
    """The error text of a reply: the ``error`` field of a JSON body, else the body text."""
    try:
        text = (await resp.text()).strip()
    except (aiohttp.ClientError, UnicodeDecodeError):
        return resp.reason or ""
    try:
        body = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(body, dict):
        for key in ("error", "message", "result"):
            if body.get(key):
                return str(body[key])
    return text[:300]


def _mdns_scan_for_site(zc: Any, site_id: int) -> str | None:
    """Blocking: browse _solar-assistant._tcp using an existing Zeroconf instance."""
    import time
    from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange

    found: list[str] = []

    def on_change(zeroconf: Any, service_type: str, name: str, state_change: Any) -> None:
        if state_change != ServiceStateChange.Added or found:
            return
        info = ServiceInfo(service_type, name)
        if not info.request(zeroconf, timeout=2000):
            return
        props = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in (info.properties or {}).items()
        }
        if str(props.get("site_id", "")) == str(site_id) and info.addresses:
            found.append(_socket.inet_ntoa(info.addresses[0]))

    browser = ServiceBrowser(zc, MDNS_SERVICE_TYPE, handlers=[on_change])
    time.sleep(MDNS_SCAN_TIMEOUT_S)
    browser.cancel()
    return found[0] if found else None


def _metric_defn(m: Any) -> dict[str, Any]:
    """Build a definition dict from either a Metric (WebSocket) or DeviceMetric (REST)."""
    return {
        "topic": m.topic,
        "device": m.device,
        "number": m.number,
        "group": m.group,
        "name": m.name,
        "unit": m.unit,
        "platform": m.platform,
        "device_class": m.device_class,
        "state_class": m.state_class,
        "unit_of_measurement": m.unit_of_measurement,
        "min": m.min,
        "max": m.max,
        "options": m.options,
        "payload_on": m.payload_on,
        "payload_off": m.payload_off,
    }


def _defn_for(m: Any) -> dict[str, Any]:
    if m.topic.startswith(SYSTEM_PREFIX):
        return _system_metric_defn(m)
    return _metric_defn(m)


def _system_metric_defn(m: Any) -> dict[str, Any]:
    """A ``_metric_defn`` defaulting platform to sensor, with a fallback for old units."""
    defn = _metric_defn(m)
    defn["platform"] = defn.get("platform") or "sensor"
    # Units on SolarAssistant build 2026-07-02 or earlier do not send device_class/state_class
    # on the system push, so fall back to deriving it.
    if defn.get("device_class") is None:
        device_class = {"°C": "temperature", "°F": "temperature", "MB": "data_size"}.get(m.unit)
        defn["device_class"] = device_class
        if defn.get("state_class") is None and device_class:
            defn["state_class"] = "measurement"
    return defn
