"""The zoom entity's bounds must admit every value the app's own picker can produce.

D-281 item 5 (`gohamstergo`): the app allows 25% but the integration's slider floored
at 50. Two more mismatches he did not mention: the app reaches 300% while the
integration capped at 200, and `step = 10` cannot express 25, 75, 125 or 175.

⚠️ The app's picker also has a **Custom** free-input option accepting 10-300
(`DisplayPageSchema.kt`, `dashboardZoomSubScreen`), so the ladder below is the
CONVENIENT set, not the VALID set. That is why this entity stays a slider: a select
of the ladder could not express a custom value and would be a regression.

⚠️ This file hand-mirrors a Kotlin range. `DisplayPageSchema.kt` is the source of
truth; if the ladder or the Custom range moves there, this test is what should fail.
"""
from unittest.mock import Mock

from custom_components.dashie.number import DashieZoomNumber


def _bounds():
    """The bounds HA will actually serve, read off an instance.

    ⚠️ Not off the class: HA's Entity metaclass turns every `_attr_*` declaration
    into a property at class creation, so `DashieZoomNumber._attr_native_min_value`
    and even `vars(...)["_attr_native_min_value"]` return a property object rather
    than the literal — an instrument trap that fails as a confusing TypeError.
    Reading the public `native_*` on an instance is also the better subject: it is
    the value HA exposes, not the value we wrote down.
    """
    coordinator = Mock()
    coordinator.data = {}
    coordinator.host = "1.2.3.4"
    coordinator.port = 2323
    ent = DashieZoomNumber(coordinator, "dev1")
    return ent.native_min_value, ent.native_max_value, ent.native_step


# Verbatim from DisplayPageSchema.kt dashboardZoomSubScreen() options.
APP_LADDER = [25, 50, 60, 75, 90, 100, 110, 125, 150, 175, 200, 250, 300]
APP_CUSTOM_RANGE = (10, 300)   # customInputPlaceholder = "10-300"


def test_every_app_ladder_value_is_reachable():
    lo, hi, step = _bounds()

    unreachable = [
        v for v in APP_LADDER
        if not (lo <= v <= hi and (v - lo) % step == 0)
    ]
    assert unreachable == [], f"the app can set these and the entity cannot: {unreachable}"


def test_bounds_match_the_apps_custom_input_range():
    """Parity with the CUSTOM range, not the ladder's endpoints.

    The ladder starts at 25, but Custom accepts from 10 — so a floor of 25 would
    still be narrower than the app.
    """
    lo, hi, _ = _bounds()
    assert (lo, hi) == APP_CUSTOM_RANGE


def test_the_old_step_could_not_express_the_ladder():
    """Positive control: the leg above must be capable of failing.

    Pins the specific reason step 10 was wrong, so nobody "tidies" it back.
    """
    missed = [v for v in APP_LADDER if (v - 10) % 10 != 0]
    assert missed == [25, 75, 125, 175], missed
