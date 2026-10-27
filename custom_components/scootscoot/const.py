"""Constants for the ScootScoot integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "scootscoot"

CONF_PROFILE: Final = "profile"
CONF_SERIAL: Final = "serial"
CONF_SLOW_INTERVAL: Final = "slow_interval"
CONF_FAST_INTERVAL: Final = "fast_interval"
CONF_STALE_AFTER: Final = "stale_after"

#: Minutes between polls while the scooter is parked and not charging. Each
#: poll briefly takes the scooter's single BLE slot, so this stays slow.
DEFAULT_SLOW_INTERVAL: Final = 15
#: Minutes between polls while charging or switched on.
DEFAULT_FAST_INTERVAL: Final = 2
#: Hours after which a reading is too old to show. 0 keeps it forever.
DEFAULT_STALE_AFTER: Final = 24

#: The library's worst case is around three and a half minutes: a busy vehicle
#: that connects on its last attempt and then goes silent mid-poll. This is a
#: backstop against a proxy that never answers.
POLL_TIMEOUT: Final = 240

#: Identity is static, but firmware updates do happen.
IDENTIFY_INTERVAL: Final = timedelta(hours=24)

#: BMS current reads about -0.06 A at rest and goes negative under regen, so
#: charge flowing into the pack is negative. Anything below this is a charger.
CHARGING_CURRENT_A: Final = -0.5

MANUFACTURER: Final = "Segway-Ninebot"

STORAGE_VERSION: Final = 1
#: Seconds to coalesce snapshot writes.
STORAGE_SAVE_DELAY: Final = 10
