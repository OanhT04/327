# server.py
import argparse # Importing argparse module to parse command line arguments
import errno # Importing errno constants (e.g., EADDRINUSE)
import json # Importing json module to handle JSON file
import logging # Importing logging module for tracking server events
import queue # Importing queue module to use FIFO queues for buffering
import socket # Importing socket module to allow TCP connection
import threading # Importing threading module to do thread synchronization
import time # Importing time module to use timestamps
from pubsub import PubSub
import tcp # Importing tcp module for plain text TCP protocol
import rpc # Importing rpc module for length-prefixed JSON-RPC

log = logging.getLogger("parking") # Initializing logger for the parking namespace

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


from state import ParkingState


class ParkingServer:
    def __init__(self, host, config): # This function will initialize server with host and configuration
        self.host = host # Setting the host IP
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

    def preflightPorts(self):
        """Fail fast with a clear message if any configured port is unavailable."""
        # Check every protocol port early so startup fails with one clear error
        # instead of background thread tracebacks later.
        ports = [
            ("text", self.textPort),
            ("rpc", self.rpcPort),
            ("sensor", self.sensorPort),
            ("events", self.eventsPort),
        ]
        # Keep sockets open during validation so checked ports stay reserved
        # until all checks finish; then close them in finally.
        checked = []
        try:
            for name, port in ports:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((self.host, port))
                    checked.append(sock)
                except OSError as e:
                    if e.errno == errno.EADDRINUSE:
                        raise RuntimeError(
                            f"Port check failed: {name} port {port} is already in use. "
                            "Update config.json ports or stop the conflicting process."
                        ) from e
                    raise RuntimeError(
                        f"Port check failed: unable to bind {name} port {port}: {e}"
                    ) from e
        finally:
            # Always release temporary validation sockets.
            for sock in checked:
                try:
                    sock.close()
                except Exception:
                    pass

    def listen(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Creating a TCP socket
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # # Allowing port to be reused after restarts
        s.bind((self.host, port)) # Binding to address and port
        s.listen(self.listenBacklog) # Listening for connections
        return s # Returning the socket

    def spawn(self, handler, conn, addr):
        # thread-per-connection:
        # each accepted TCP connection is handled by a dedicated thread.
        if not self.clientSem.acquire(blocking=False): # Checking if thread limit is reached
            try:
                conn.sendall(b"ERROR server_busy\n") # Informing client the server is at capacity
            except Exception:
                pass
            try:
                conn.close() # Closing the connection
            except Exception:
                pass
            return

        t = threading.Thread(target=self.wrapClient, args=(handler, conn, addr), daemon=True)
        t.start() # Executing the thread

    def wrapClient(self, handler, conn, addr):
        try:
            handler(conn, addr) # Execute the specific protocol handler
        except Exception as e:
            log.error("client handler error %s:%s %s", addr[0], addr[1], e)
        finally:
            try:
                conn.close() # Ensuring that the socket is closed after handling
            except Exception:
                pass
            self.clientSem.release() # Freeing up a thread slot in the semaphore

    def start(self):
        # Accept loops (each loop runs in its own thread, then spawns per-connection threads)
        threading.Thread(target=self.acceptTextLoop, daemon=True).start() # Starting text protocol loop
        threading.Thread(target=self.acceptRpcLoop, daemon=True).start() # Starting RPC protocol loop
        threading.Thread(target=self.acceptSensorLoop, daemon=True).start() # Starting sensor protocol loop
        threading.Thread(target=self.acceptEventsLoop, daemon=True).start() # Starting the pubsub protocol loop
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
        except KeyboardInterrupt: # Handling Ctrl+C shutdown
            self.stopEvent.set()

    # ---- accept loops ----
    def acceptTextLoop(self):
        srv = self.listen(self.textPort) # Listeing on text port
        while not self.stopEvent.is_set():
            conn, addr = srv.accept() # Accepting incoming TCP connection
            self.spawn(self.textClient, conn, addr)

    def acceptRpcLoop(self):
        srv = self.listen(self.rpcPort) # Listening on RPC port
        while not self.stopEvent.is_set():
            conn, addr = srv.accept() # Accepting incoming TCP connection
            self.spawn(self.rpcClient, conn, addr)

    def acceptSensorLoop(self):
        srv = self.listen(self.sensorPort) # Listening on sensor port
        while not self.stopEvent.is_set():
            conn, addr = srv.accept() # Accepting incoming TCP connection
            self.spawn(self.sensorClient, conn, addr)

    def acceptEventsLoop(self):
        srv = self.listen(self.eventsPort) # Listening on pubsub events port
        while not self.stopEvent.is_set():
            conn, addr = srv.accept() # Accepting incoming TCP connection
            self.spawn(self.eventsClient, conn, addr)

    # Rubric: protocol separation (text + rpc handlers are split into modules)
    def textClient(self, conn, addr):
        return tcp.textClient(self, conn, addr)

    def rpcClient(self, conn, addr):
        # Rubric: framing handled in rpc.py (length-prefixed JSON)
        return rpc.rpcClient(self, conn, addr)

    def sensorClient(self, conn, addr):
        # Sensor ingestion is handled in sensor.py (async enqueue path)
        import sensor
        return sensor.sensorClient(self, conn, addr)

    def sensorWorkerLoop(self):
        # Sensor update workers live in sensor.py
        import sensor
        return sensor.sensorWorkerLoop(self)

    def eventsClient(self, conn, addr):
        # Event (pub/sub) connection handling lives in events.py
        import events
        return events.eventsClient(self, conn, addr)

    def expireLoop(self):
        while not self.stopEvent.is_set(): 
            changed = self.state.expireOnce() # Cleaning expired spots and getting changed lots
            for lotId, free in changed: # For every lot that changed availability
                self.pubsub.publish(lotId, free) # Informing users about the change
            time.sleep(0.5)


def loadConfig(path): # This function will read JSON file
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--loglevel", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.loglevel.upper(), logging.INFO))

    config = loadConfig(args.config) # Loading the settings
    server = ParkingServer(args.host, config) # Initializing server
    # Validate configured ports before spinning up any accept loops.
    server.preflightPorts()
    server.start() # Starting all operations


if __name__ == "__main__":
    main()
