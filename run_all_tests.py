from operator import sub
import subprocess
import time
import threading
import statistics
from test import rpc_call, sensor_update

HOST = "127.0.0.1"
RPC_PORT = 5001
SENSOR_PORT = 5002
LOT = "A"

# -------------------------
# Sensor stress: 10 updates/sec
# -------------------------
def sensor_stress():
    import socket

    while True:
        try:
            # Keep one long-lived connection
            s = socket.create_connection((HOST, SENSOR_PORT))
            while True:
                s.sendall(f"UPDATE {LOT} 1\n".encode())
                time.sleep(0.1)
                s.sendall(f"UPDATE {LOT} -1\n".encode())
                time.sleep(0.1)
        except Exception:
            # If server resets, reconnect
            time.sleep(0.2)
            continue

# -------------------------
# Single RPC load worker
# -------------------------
def rpc_worker(method, args, end_time, results):
    rpc_id = 1
    while time.time() < end_time:
        t0 = time.time()
        try:
            rpc_call(HOST, RPC_PORT, rpc_id, method, args)
        except Exception:
            pass
        results.append(time.time() - t0)
        rpc_id += 1

# -------------------------
# Run RPC load test
# -------------------------
def run_rpc_load(method, args, workers, duration=10):
    end_time = time.time() + duration
    results = []
    threads = []

    for _ in range(workers):
        t = threading.Thread(target=rpc_worker, args=(method, args, end_time, results))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    if not results:
        return {"workers": workers, "throughput": 0, "median_ms": 0, "p95_ms": 0}

    throughput = len(results) / duration
    median = statistics.median(results) * 1000
    p95 = statistics.quantiles(results, n=20)[18] * 1000

    return {
        "workers": workers,
        "throughput": round(throughput, 1),
        "median_ms": round(median, 2),
        "p95_ms": round(p95, 2)
    }

# -------------------------
# Subscriber process (external)
# -------------------------
import subprocess

def start_subscriber():
    return subprocess.Popen(
        ["python3", "clientSubTest.py", "--lot", LOT],
        stdout=subprocess.DEVNULL,   # hide normal output
        stderr=subprocess.DEVNULL    # hide error output too
    )

# -------------------------
# Main
# -------------------------
def main():
    print("== Starting subscriber ==")
    subscriber = start_subscriber()
    time.sleep(1)

    print("== Starting sensor stress (10 updates/sec) ==")
    threading.Thread(target=sensor_stress, daemon=True).start()
    time.sleep(2)

    print("\n== Running RPC load tests (1/4/8/16 workers) ==")
    for method, args in [
        ("getAvailability", [LOT]),
        ("reserve", [LOT, "LOADTEST"]),
    ]:
        print(f"\n### Testing {method}")
        for w in [1, 4, 8, 16]:
            stats = run_rpc_load(method, args, w, duration=10)
            print(stats)

    print("\n== Tests complete. Subscriber output shows pub/sub behavior ==")
    print("Close with Ctrl+C.")

    print("\n== Tests complete. Shutting down subscriber and exiting ==")
    time.sleep(1)
    subscriber.terminate()
    print("Done.")

if __name__ == "__main__":
    main()