# sensor.py
import queue

def sensorClient(server, conn, addr):
    """Sensor endpoint: UPDATE <lotId> <delta>.
    This handler MUST be lightweight: parse + enqueue only.
    """
    conn.settimeout(60)
    f = conn.makefile("rwb")
    while not server.stopEvent.is_set():
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

        try:
            server.sensorQueue.put_nowait((lotId, delta))
            f.write(b"OK\n")
        except queue.Full:
            # Back-pressure for sensor stream
            f.write(b"ERROR server_saturated\n")
        f.flush()


def sensorWorkerLoop(server):
    """Worker thread(s) apply queued sensor updates asynchronously."""
    while not server.stopEvent.is_set():
        try:
            lotId, delta = server.sensorQueue.get(timeout=0.2)
        except queue.Empty:
            continue
        try:
            free, changed = server.state.applySensorUpdate(lotId, delta)
            if changed:
                server.pubsub.publish(lotId, free)
        except KeyError:
            # unknown lot; ignore
            pass
