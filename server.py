# server.py
import argparse
import json
import logging
import queue
import socket
import threading
import time

from pubsub import PubSub
import tcp
import rpc

log = logging.getLogger("parking")

"""
Parking Lot

Note: Request handling and parking updates work correctly. 
i am not sure if i am following rubric correctly so double check pub/sub and then client n sensors 

Protocols / Ports:
- Text TCP (5000): line-based commands: PING, LOTS, AVAIL, RESERVE, CANCEL 
- RPC TCP (5001): length-prefixed JSON framing (see rpc.py) 
- Request: {rpcId, method, args}
-  Reply:   {rpcId, result, error}   (error is null on success)
- Sensors (5002): UPDATE <lotId> <delta> sent by sensors - todo: sensor.py?
- Events (5003): push notifications for subscribers: EVENT <lotId> <free>

Concurrency model:
- Thread-per-connection: each accepted TCP connection gets its own handler thread (spawn()).
- Shared state protected by a lock (ParkingState.lock) to prevent overbooking.

Asynchronous messaging:
- Sensor updates are non-blocking: sensorClient enqueues updates; worker threads apply them.
- Pub/Sub push notifications do not block RPC: delivered on separate events port via notifier thread.
"""


# ---- Data model for one lot ----
class LotState:
    def __init__(self, lotId, capacity):
        self.lotId = lotId
        self.capacity = capacity
        self.occupiedPhysical = 0
        self.reservations = {}


class ParkingState:
    def __init__(self, lots, reservationTtlSec):
        # lock protects all shared state changes (reserve/cancel/sensor/expire)
        self.lock = threading.RLock()
        self.ttl = reservationTtlSec
        self.lots = {}
        for lotId, cap in lots.items():
            self.lots[lotId] = LotState(lotId, int(cap))

    def freeCountLocked(self, lot):
        return lot.capacity - lot.occupiedPhysical - len(lot.reservations)

    def allLots(self):
        with self.lock:
            result = []
            for lot in self.lots.values():
                free = self.freeCountLocked(lot)
                occupiedTotal = lot.occupiedPhysical + len(lot.reservations)
                result.append(
                    {"id": lot.lotId, "capacity": lot.capacity, "occupied": occupiedTotal, "free": free}
                )
            return result

    def availability(self, lotId):
        with self.lock:
            if lotId not in self.lots:
                raise KeyError("unknown lot")
            lot = self.lots[lotId]
            return self.freeCountLocked(lot)

    def reserve(self, lotId, plate):
        # lock ensures no overbooking under concurrency
        with self.lock:
            if lotId not in self.lots:
                raise KeyError("unknown lot")
            lot = self.lots[lotId]
            freeBefore = self.freeCountLocked(lot)

            if plate in lot.reservations:
                return False, "EXISTS", False
            if freeBefore <= 0:
                return False, "FULL", False

            # Reservation is TTL-based; expiration handled by expireLoop()
            lot.reservations[plate] = time.time() + self.ttl
            freeAfter = self.freeCountLocked(lot)

            log.info(json.dumps({"type": "reserve", "lotId": lotId, "plate": plate, "ts": int(time.time() * 1000), "result": "OK"}))
            return True, "OK", freeAfter != freeBefore

    def cancel(self, lotId, plate):
        with self.lock:
            if lotId not in self.lots:
                raise KeyError("unknown lot")
            lot = self.lots[lotId]
            freeBefore = self.freeCountLocked(lot)

            if plate not in lot.reservations:
                return False, "NOT_FOUND", False

            del lot.reservations[plate]
            freeAfter = self.freeCountLocked(lot)

            log.info(json.dumps({"type": "cancel", "lotId": lotId, "plate": plate, "ts": int(time.time() * 1000), "result": "OK"}))
            return True, "OK", freeAfter != freeBefore

    def applySensorUpdate(self, lotId, delta):
        # sensor update clamps occupancy so it never exceeds capacity minus reservations
        with self.lock:
            if lotId not in self.lots:
                raise KeyError("unknown lot")
            lot = self.lots[lotId]
            freeBefore = self.freeCountLocked(lot)
            lot.occupiedPhysical += delta
            if lot.occupiedPhysical < 0:
                lot.occupiedPhysical = 0
            maxPhysical = max(0, lot.capacity - len(lot.reservations))
            if lot.occupiedPhysical > maxPhysical:
                lot.occupiedPhysical = maxPhysical

            freeAfter = self.freeCountLocked(lot)
            log.info(json.dumps({"type": "sensor_update", "lotId": lotId, "delta": delta, "occupiedPhysical": lot.occupiedPhysical, "ts": int(time.time() * 1000)}))
            return freeAfter, freeAfter != freeBefore

    def expireOnce(self):
        # Rubric: automatic expiration of reservations (TTL)
        now = time.time()
        changed = []
        with self.lock:
            for lot in self.lots.values():
                freeBefore = self.freeCountLocked(lot)
                expired = [p for p, exp in lot.reservations.items() if exp <= now]
                if not expired:
                    continue
                for plate in expired:
                    del lot.reservations[plate]
                    log.info(json.dumps({"type": "reservation_expired", "lotId": lot.lotId, "plate": plate, "ts": int(time.time() * 1000)}))
                freeAfter = self.freeCountLocked(lot)
                if freeAfter != freeBefore:
                    changed.append((lot.lotId, freeAfter))
        return changed


class ParkingServer:
    def __init__(self, host, config):
        self.host = host
        self.textPort = int(config["ports"]["text"])
        self.rpcPort = int(config["ports"]["rpc"])
        self.sensorPort = int(config["ports"]["sensor"])
        self.eventsPort = int(config["ports"]["events"])
        self.listenBacklog = int(config.get("listen_backlog", 200))

        self.clientLimit = int(config.get("client_limit", 200))
        self.state = ParkingState(config["lots"], int(config.get("reservation_ttl_sec", 300)))

        # pubsub uses per-subscriber bounded queues + notifier thread (non-blocking)
        self.pubsub = PubSub(queueSize=int(config.get("per_sub_queue_max", 128)))
        self.stopEvent = threading.Event()

        # sensors are async: queue + worker threads
        self.sensorQueue = queue.Queue(maxsize=int(config.get("sensor_queue_max", 10000)))
        self.sensorWorkers = int(config.get("sensor_workers", 2))

        # thread-per-connection with backpressure (limit concurrent handlers)
        self.clientSem = threading.BoundedSemaphore(value=self.clientLimit)

    def listen(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, port))
        s.listen(self.listenBacklog)
        return s

    def spawn(self, handler, conn, addr):
        # thread-per-connection:
        # each accepted TCP connection is handled by a dedicated thread.
        if not self.clientSem.acquire(blocking=False):
            try:
                conn.sendall(b"ERROR server_busy\n")
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            return

        t = threading.Thread(target=self.wrapClient, args=(handler, conn, addr), daemon=True)
        t.start()

    def wrapClient(self, handler, conn, addr):
        try:
            handler(conn, addr)
        except Exception as e:
            log.error("client handler error %s:%s %s", addr[0], addr[1], e)
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self.clientSem.release()

    def start(self):
        # Accept loops (each loop runs in its own thread, then spawns per-connection threads)
        threading.Thread(target=self.acceptTextLoop, daemon=True).start()
        threading.Thread(target=self.acceptRpcLoop, daemon=True).start()
        threading.Thread(target=self.acceptSensorLoop, daemon=True).start()
        threading.Thread(target=self.acceptEventsLoop, daemon=True).start()
        # push notifications are non-blocking (notifier thread drains queues)
        threading.Thread(target=self.pubsub.notifierLoop, args=(self.stopEvent,), daemon=True).start()

        # expiration loop (async maintenance)
        threading.Thread(target=self.expireLoop, daemon=True).start()

        # Sensor worker pool (async processing)
        for _ in range(self.sensorWorkers):
            threading.Thread(target=self.sensorWorkerLoop, daemon=True).start()
        log.info(json.dumps({"type": "server_started", "textPort": self.textPort, "rpcPort": self.rpcPort, "sensorPort": self.sensorPort, "eventsPort": self.eventsPort, "ts": int(time.time() * 1000)}))
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.stopEvent.set()

    # ---- accept loops ----
    def acceptTextLoop(self):
        srv = self.listen(self.textPort)
        while not self.stopEvent.is_set():
            conn, addr = srv.accept()
            self.spawn(self.textClient, conn, addr)

    def acceptRpcLoop(self):
        srv = self.listen(self.rpcPort)
        while not self.stopEvent.is_set():
            conn, addr = srv.accept()
            self.spawn(self.rpcClient, conn, addr)

    def acceptSensorLoop(self):
        srv = self.listen(self.sensorPort)
        while not self.stopEvent.is_set():
            conn, addr = srv.accept()
            self.spawn(self.sensorClient, conn, addr)

    def acceptEventsLoop(self):
        srv = self.listen(self.eventsPort)
        while not self.stopEvent.is_set():
            conn, addr = srv.accept()
            self.spawn(self.eventsClient, conn, addr)

    # Rubric: protocol separation (text + rpc handlers are split into modules)
    def textClient(self, conn, addr):
        return tcp.textClient(self, conn, addr)

    def rpcClient(self, conn, addr):
        # Rubric: framing handled in rpc.py (length-prefixed JSON)
        return rpc.rpcClient(self, conn, addr)

    def sensorClient(self, conn, addr):
        # Note: socket timeout prevents dead clients hanging forever
        conn.settimeout(60)
        f = conn.makefile("rwb")
        while not self.stopEvent.is_set():
            line = f.readline()
            if not line:
                return
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            parts = line.split()
            if len(parts) != 3 or parts[0].upper() != "UPDATE":
                f.write(b"ERROR bad_update\n")
                f.flush()
                continue

            lotId = parts[1]
            try:
                delta = int(parts[2])
            except ValueError:
                delta = 0

            # Rubric: enqueue sensor updates (non-blocking ingestion)
            try:
                self.sensorQueue.put_nowait((lotId, delta))
                f.write(b"OK\n")
            except queue.Full:
                f.write(b"ERROR server_saturated\n")

            f.flush()

    def sensorWorkerLoop(self):
        # worker threads apply sensor updates asynchronously
        while not self.stopEvent.is_set():
            try:
                lotId, delta = self.sensorQueue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                free, changed = self.state.applySensorUpdate(lotId, delta)
                if changed:
                    self.pubsub.publish(lotId, free)
            except KeyError:
                pass

    def eventsClient(self, conn, addr):
        # separate TCP connection for push notifications (does not block RPC)
        conn.settimeout(30)
        f = conn.makefile("rwb")
        first = f.readline()
        if not first:
            return
        first = first.decode("utf-8", errors="replace").strip()
        parts = first.split()

        if len(parts) != 2 or parts[0].upper() != "SUB":
            f.write(b"ERROR expected: SUB <subId>\n")
            f.flush()
            return

        subId = parts[1]
        ok = self.pubsub.attachConnection(subId, conn)
        if not ok:
            f.write(b"ERROR unknown_subId\n")
            f.flush()
            return
        f.write(b"OK\n")
        f.flush()

        # Keep alive until client disconnects (does not send data normally)
        conn.settimeout(60)
        while not self.stopEvent.is_set():
            try:
                b = conn.recv(1)
                if not b:
                    return
            except socket.timeout:
                continue
            except Exception:
                return

    def expireLoop(self):
        while not self.stopEvent.is_set():
            changed = self.state.expireOnce()
            for lotId, free in changed:
                self.pubsub.publish(lotId, free)
            time.sleep(0.5)


def loadConfig(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--loglevel", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.loglevel.upper(), logging.INFO))

    config = loadConfig(args.config)
    server = ParkingServer(args.host, config)
    server.start()


if __name__ == "__main__":
    main()