"""iotc serial probe: the Linux side does nothing (the sketch never uses the Bridge)."""
import time

from arduino.app_utils import App


def loop() -> None:
    time.sleep(1.0)


print("iotc serial probe python side idle; watch LED3/LED4 on the board", flush=True)
App.run(user_loop=loop)
