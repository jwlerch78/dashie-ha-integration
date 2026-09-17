"""How a failed device command reaches the user.

A command either reaches the device and is accepted, is refused by the device (it
answered with an error), or gets no usable answer (timeout, connection refused).

- Entity actions and user-called services raise DashieCommandError, a
  HomeAssistantError, so the frontend shows the failure and an automation step stops
  unless it is marked `continue_on_error: true`.
- Services try every targeted device first, then raise once naming each failure, so
  one offline tablet does not keep a command from the others.
- Background pushes (timers, voice-config refresh) never raise: nobody is waiting on
  them, and the timer push runs every second. Each device's failure is logged once
  when it starts failing, and again only after it has recovered and failed anew.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Iterable

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

if TYPE_CHECKING:
    from .coordinator import DashieCoordinator

_LOGGER = logging.getLogger(__name__)

REFUSED = "refused"
NO_RESPONSE = "no_response"


class DashieCommandError(HomeAssistantError):
    """A Dashie device refused a command or did not respond to it."""


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one command sent to one device."""

    ok: bool
    kind: str | None = None  # REFUSED or NO_RESPONSE when not ok
    reason: str = ""


def device_label(coordinator: DashieCoordinator) -> str:
    """The name a user knows the device by, as Home Assistant shows it.

    The config entry title is often the hardware model, so two tablets of one model
    would read the same; the device registry name (or the name the user gave it) does not.
    """
    entry = coordinator.config_entry
    if entry:
        for device in dr.async_entries_for_config_entry(dr.async_get(coordinator.hass), entry.entry_id):
            if device.name_by_user or device.name:
                return device.name_by_user or device.name
        if entry.title:
            return entry.title
    return coordinator.host


def failure_message(coordinator: DashieCoordinator, command: str, result: CommandResult) -> str:
    """'<device> refused <command>: <reason>' or '<device> did not respond to <command> (<why>)'."""
    label = device_label(coordinator)
    if result.kind == REFUSED:
        return f"{label} refused {command}: {result.reason}"
    return f"{label} did not respond to {command} ({result.reason})"


async def async_send_to_all(
    coordinators: Iterable[DashieCoordinator], command: str, **kwargs
) -> None:
    """Send to every device, then raise once if any of them failed."""
    failures = []
    for coordinator in coordinators:
        result = await coordinator.async_send(command, **kwargs)
        if not result.ok:
            failures.append(failure_message(coordinator, command, result))
    if failures:
        raise DashieCommandError("; ".join(failures))


async def async_push_to_all(
    coordinators: Iterable[DashieCoordinator], command: str, *, what: str, **kwargs
) -> None:
    """Send a background update to every device without raising.

    A failure is logged when a device starts failing this kind of push, not on every
    attempt, and a recovery is logged once so the next failure is visible again.
    """
    for coordinator in coordinators:
        result = await coordinator.async_send(command, **kwargs)
        failing = coordinator.push_failing
        label = device_label(coordinator)
        if not result.ok and not failing.get(what):
            _LOGGER.warning(
                "DROP: %s push to %s failed: %s",
                what, label, failure_message(coordinator, command, result),
            )
        elif result.ok and failing.get(what):
            _LOGGER.info("%s push to %s is working again", what, label)
        failing[what] = not result.ok
