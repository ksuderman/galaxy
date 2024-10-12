import time
import os

from watchdog.observers import Observer
from galaxy.util.watcher import Watcher, EventHandler


class LoggingEventHandler(EventHandler):
    def on_modified(self, event):
        print(f"Modified: {event.src_path}")



def test_watcher():
    watcher = Watcher(Observer, LoggingEventHandler)
    watcher.watch_directory('/tmp/watch')
    watcher.start()
    while not os.path.exists("/tmp/stop"):
        time.sleep(1)
    watcher.shutdown()
    print("Done")
    assert True

if __name__ == "__main__":
    test_watcher()