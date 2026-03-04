from operator import sub
import subprocess # Importing subprocess module to run external processes
import time # Importing time module to use timestamps
import threading # Importing threading module to do thread synchronization
import statistics # Importing statistics to calculate median and p95 latency metrics
from test import rpc_call, sensor_update

HOST = "127.0.0.1" # This is the server address
RPC_PORT = 5001 # This is the rpc server port number
SENSOR_PORT = 5002 # This is the sensor port number
LOT = "A" # This is the parking lot used for the tests

# -------------------------
# Sensor stress: 10 updates/sec
# -------------------------
def sensor_stress(): # This function will simulates a sensor rapidly flapping a spot between occupied and vacant
    import socket # Locally importing socket module to allow TCP connection

    while True: # This is anfinite loop to keep the stress test running
        try:
            # Keep one long-lived connection
            s = socket.create_connection((HOST, SENSOR_PORT))
            while True:
                s.sendall(f"UPDATE {LOT} 1\n".encode()) # Sending 'spot occupied' (+1)
                time.sleep(0.1) # Wait 100ms
                s.sendall(f"UPDATE {LOT} -1\n".encode()) # Send 'spot vacated' (-1)
                time.sleep(0.1) # Wait 100ms
        except Exception: # Catching connection drops or server restarts
            # If server resets, reconnect
            time.sleep(0.2) # Short pause before trying to reconnect
            continue # Restarting the outer loop

# -------------------------
# Single RPC load worker
# -------------------------
def rpc_worker(method, args, end_time, results):
    rpc_id = 1 # Counter for tracking individual requests
    while time.time() < end_time: # Keep sending requests until the test duration ends
        t0 = time.time() # Recording the start time of the request
        try:
            rpc_call(HOST, RPC_PORT, rpc_id, method, args) # Executing the remote procedure call
        except Exception: # Ignoring failures and continuing to the next attempt
            pass
        results.append(time.time() - t0) # Calculating latency and storing it
        rpc_id += 1 # Incrementing the id by one

# -------------------------
# Run RPC load test
# -------------------------
def run_rpc_load(method, args, workers, duration=10):
    end_time = time.time() + duration # Setting the timestamp for when the test should stop
    results = [] # Initializing a list to collect latency data from all workers
    threads = [] # Initializing a list to keep track of worker thread objects

    for _ in range(workers):
        t = threading.Thread(target=rpc_worker, args=(method, args, end_time, results))
        t.start() # Starting the thread
        threads.append(t) # Appending the thread

    for t in threads: # Waiting for all workers to finish, then blocks the main thread until this worker completes
        t.join()

    if not results: # If no requests were successful
        return {"workers": workers, "throughput": 0, "median_ms": 0, "p95_ms": 0}

    throughput = len(results) / duration # Calculating the throughput
    median = statistics.median(results) * 1000 # Converting median latency to milliseconds
    p95 = statistics.quantiles(results, n=20)[18] * 1000 # Calculating 95th percentile latency in ms

    return { # Returning a dictionary of the performance
        "workers": workers,
        "throughput": round(throughput, 1),
        "median_ms": round(median, 2),
        "p95_ms": round(p95, 2)
    }

# -------------------------
# Subscriber process (external)
# -------------------------
import subprocess

def start_subscriber(): # This function will start a separate Python process to listen for updates
    return subprocess.Popen( # Launching 'clientSubTest.py' in the background
        ["python3", "clientSubTest.py", "--lot", LOT],
        stdout=subprocess.DEVNULL,   # hide normal output
        stderr=subprocess.DEVNULL    # hide error output too
    )

# -------------------------
# Main
# -------------------------
def main():
    print("== Starting subscriber ==")
    subscriber = start_subscriber() # Start subscriber in the background 
    time.sleep(1) # Pausing to let the subscriber initialize

    print("== Starting sensor stress (10 updates/sec) ==")
    threading.Thread(target=sensor_stress, daemon=True).start()
    time.sleep(2) # Letting the stress run for a bit to saturate the server

    print("\n== Running RPC load tests (1/4/8/16 workers) ==")
    for method, args in [ # Test both a "availability" and a "reserve"
        ("getAvailability", [LOT]),
        ("reserve", [LOT, "LOADTEST"]),
    ]:
        print(f"\n### Testing {method}") # Printing the current method being tested to the log
        for w in [1, 4, 8, 16]: # Running the test with increasing concurrency
            stats = run_rpc_load(method, args, w, duration=10) # Running for 10 seconds per worker count
            print(stats) # Printing the performance metrics

    print("\n== Tests complete. Subscriber output shows pub/sub behavior ==")
    print("Close with Ctrl+C.") # Printing message for manual interrupts

    print("\n== Tests complete. Shutting down subscriber and exiting ==")
    time.sleep(1) # Final cooling-off period
    subscriber.terminate() # Killing the background subscriber process
    print("Done.") # Printing status message

if __name__ == "__main__":
    main()