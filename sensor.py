# sensor.py
import queue

def sensorClient(server, conn, addr):
    """Sensor endpoint: UPDATE <lotId> <delta>.
    This handler MUST be lightweight: parse + enqueue only.
    """
    conn.settimeout(60) # Disconnecting sensors that are silent for 60 seconds
    f = conn.makefile("rwb") # Creating wrapper to read and write bytes
    while not server.stopEvent.is_set(): # This loop will run until there's a signal to stop
        line = f.readline() # Reading a single line of text from the sensor
        if not line: # If readline returns empty, the sensor disconnected, then terminate
            return
        line = line.decode("utf-8", errors="replace").strip() # Converting the bytes to string and remove whitespace
        if not line: #If line is empty, then skip
            continue

        parts = line.split() # Splitting the command into components
        if len(parts) != 3 or parts[0].upper() != "UPDATE": # Checking the command format and keyword
            f.write(b"ERROR bad_update\n") # Informing the sensor of the syntax error
            f.flush() # Ensuring the error message is sent immediately
            continue

        lotId = parts[1] # Getting the parking lot id
        try:
            delta = int(parts[2]) # Converting the change value to an integer
        except ValueError: # If the third part isn't a valid number, default to zero change to avoid crashing
            delta = 0

        try:
            server.sensorQueue.put_nowait((lotId, delta))
            f.write(b"OK\n") # Informing successful receipt to the sensor
        except queue.Full: # Triggered if the worker thread is falling behind
            # Back-pressure for sensor stream
            f.write(b"ERROR server_saturated\n") # Informing the sensor that the server is too busy to handle the data
        f.flush() 


def sensorWorkerLoop(server):
    """Worker thread(s) apply queued sensor updates asynchronously."""
    while not server.stopEvent.is_set(): # This loop will run until the server shuts down
        try:
            lotId, delta = server.sensorQueue.get(timeout=0.2) # Getting lotId and delta pair from the queue and wait up to 0.2s
        except queue.Empty: # If no updates arrived within the timeout, then restart the loop to check the stopEvent again
            continue
        try:
            free, changed = server.state.applySensorUpdate(lotId, delta) # Applying the change to the lot's current availability
            if changed: # If the availability actually changed, then notify subscribers of the new free space count
                server.pubsub.publish(lotId, free)
        except KeyError: # If the sensor reports a lotId that doesn't exist, then silently ignore the error and move to the next update 
            # unknown lot; ignore
            pass
