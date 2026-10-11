import time
from .notifications import scheduler_tick
from .public_connectors import tick as public_connector_tick
from . import api as _api  # noqa: F401 - register owner API before importing its action router.
from .connector_actions import tick as connector_action_tick
if __name__=="__main__":
    while True:
        try:
            from .ops import heartbeat
            from .push import tick
            heartbeat('scheduler');scheduler_tick();tick();public_connector_tick();connector_action_tick()
        except Exception as exc:print("Scheduler error:",type(exc).__name__,flush=True)
        time.sleep(5)
