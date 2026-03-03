# events.py
import socket

def eventsClient(server, conn, addr):
    """Event delivery channel.

    Client must send a single line:
      SUB <subId>

    After OK, server pushes:
      EVENT <lotId> <free> <timestamp_ms>
    """
    conn.settimeout(30) # Setting timeout to be 30 seconds
    f = conn.makefile("rwb") # Creating wrapper to read and write bytes
    first = f.readline() # Reading the first line of data that was sent by client
    if not first: # If empty, client is disconnected prematurely
        return
    first = first.decode("utf-8", errors="replace").strip() # Decoding the bytes to UTF-8 while replacing errors and stripping whitespaces
    parts = first.split() # Splitting the first string into components

    if len(parts) != 2 or parts[0].upper() != "SUB": # If the components start with SUB or have subID, then print error message to client immediately
        f.write(b"ERROR expected: SUB <subId>\n")
        f.flush()
        return

    subId = parts[1] # Obtaining subscription id from the second component
    ok = server.pubsub.attachConnection(subId, conn) # Registering the active socket to server's PubSub
    if not ok: # If subID doesn't exist, printing a message to inform client that the ID is invalid, then flush the server
        f.write(b"ERROR unknown_subId\n")
        f.flush()
        return

    f.write(b"OK\n") # Printing a message to tell client that subscription is active
    f.flush()

    # Keep connection open until client disconnects.
    # Actual sending is done by PubSub.notifierLoop (non-blocking w.r.t RPC).
    conn.settimeout(60)
    while not server.stopEvent.is_set():
        try:
            b = conn.recv(1)
            if not b:
                return
        except socket.timeout:
            continue
        except Exception:
            return
