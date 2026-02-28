import queue
import threading
import time


# The server does NOT send messages directly from the publisher
# (e.g., reserve(), cancel(), or sensor updates) to clients.
# Instead, it uses message queues:
#
# 1. When availability changes, publish(lotId, free) places an
#    event into a queue.
#
# 2. Each subscriber has its own queue.
#
# 3. The notifier thread (notifierLoop) reads events from queues
#    and sends them to clients.

def nowMs():
    return int(time.time() * 1000)


class Subscriber:
    def __init__(self, subId, lotId, queueSize):
        self.subId = subId
        self.lotId = lotId
        self.queue = queue.Queue(maxsize=queueSize)
        self.conn = None
        self.connected = False


class PubSub:
    """
    Back-pressure policy (required):
    - bounded per-subscriber queue
    - if queue overflows, disconnect subscriber (drop by disconnect)
    """

    def __init__(self, queueSize=128):
        self.lock = threading.Lock()
        self.subscribers = {}
        self.nextSubId = 1
        self.queueSize = queueSize

    def subscribe(self, lotId):
        with self.lock:
            subId = str(self.nextSubId)
            self.nextSubId += 1
            self.subscribers[subId] = Subscriber(subId, lotId, self.queueSize)
            return subId

    def unsubscribe(self, subId):
        with self.lock:
            sub = self.subscribers.pop(subId, None)
        if not sub:
            return False
        if sub.conn:
            try:
                sub.conn.close()
            except Exception:
                pass
        return True

    def attachConnection(self, subId, conn):
        with self.lock:
            sub = self.subscribers.get(subId)
            if not sub:
                return False
            if sub.conn:
                try:
                    sub.conn.close()
                except Exception:
                    pass
            sub.conn = conn
            sub.connected = True
            return True

    def publish(self, lotId, free):
        line = f"EVENT {lotId} {free} {nowMs()}\n"
        victims = []
        with self.lock:
            for sub in self.subscribers.values():
                if sub.lotId != lotId:
                    continue
                try:
                    sub.queue.put_nowait(line)
                except queue.Full:
                    victims.append(sub)

        # Back-pressure: disconnect slow subscribers
        for sub in victims:
            if sub.conn:
                try:
                    sub.conn.close()
                except Exception:
                    pass
            sub.connected = False

    def notifierLoop(self, stopEvent):
        while not stopEvent.is_set():
            with self.lock:
                subs = list(self.subscribers.values())

            for sub in subs:
                if not sub.connected or not sub.conn:
                    continue
                try:
                    msg = sub.queue.get_nowait()
                except queue.Empty:
                    continue

                try:
                    sub.conn.sendall(msg.encode("utf-8"))
                except Exception:
                    sub.connected = False

            time.sleep(0.01)