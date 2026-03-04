# rpc.py
import json # Importing json module to handle JSON file
import struct # Importing struct for binary data

"""
RPC Server

Length-prefixed JSON RPC over TCP.
Each connection handled by its own thread.

Request: {rpcId, method, args}
Reply:   {rpcId, result, error}
"""

# ---- helpers ----
def recvExact(conn, size): # This function will ensure that it will read "size" bytes from the socket
    data = b"" # Initializing an empty byte string that will be used to store results
    while len(data) < size: # This while loop will run until the length of data is greater than or equal to size
        chunk = conn.recv(size - len(data)) # Reading the byte from the socket buffer
        if not chunk: # If the byte is empty, raise error that connection is dropped
            raise ConnectionError("peer closed")
        data += chunk # Adding the byte to the byte string
    return data # Returning the complete byte string

def recvFrame(conn, maxLen=1_000_000): # This function will read a complete length-prefixed message
    header = recvExact(conn, 4) # Reading the first 4 bytes and store it to header
    length = struct.unpack(">I", header)[0] # Coverting the bytes into python ineger
    if length > maxLen: # If it exceeds the limit, raise an error that it is too big
        raise ValueError("frame too large")
    return recvExact(conn, length) # Returning the actual message payload

def sendFrame(conn, payloadBytes): # This function is used to wrap a payload in the header and send it
    header = struct.pack(">I", len(payloadBytes)) # Creating a 4 bytes header to represent the length
    conn.sendall(header + payloadBytes) # Sending the header with the payload


# ---- RPC server skeleton ----
def rpcClient(server, conn, addr):
    conn.settimeout(60) # Setting the timeout to 60 seconds
    while not server.stopEvent.is_set(): # This loop will run until there's a signal to stop
        try:
            payload = recvFrame(conn) # Wait for message frame from the client
        except Exception: # If it fails, exit the thread
            return

        try:
            req = json.loads(payload.decode("utf-8")) # Decoding the string and parse it to a dictionary
            rpcId = req.get("rpcId") # Obtaining th rpc id
            method = req.get("method") # Obtaining the method name
            args = req.get("args", []) # Obtaining the arguments, but this will default to empty list if there aren't any

            result, err = dispatchRpc(server, method, args) # Calling the dispatcher to execute the requested logic
            reply = {"rpcId": rpcId, "result": result, "error": err} # Initializing a repsonse dictionary
            sendFrame(conn, json.dumps(reply).encode("utf-8")) # Encoding to JSON then send it to client

        except Exception as e: # If it fails, create an error reply, attempt to send the error to the client, and if it fails to send, then abandon the connection
            try:
                reply = {"rpcId": None, "result": None, "error": f"bad_request: {e}"}
                sendFrame(conn, json.dumps(reply).encode("utf-8"))
            except Exception:
                return


def dispatchRpc(server, method, args): 
    """
    Dispatch moved out of ParkingServer.
    Uses: server.state, server.pubsub
    """
    try:
        if method == "getLots": # Handling all request for all parking lot data
            return server.state.allLots(), None

        if method == "getAvailability": # Handling request for lot's free spaces
            lotId = str(args[0]) # Geting the lot id from the first argument
            return server.state.availability(lotId), None # Returning the count and no error raised

        if method == "reserve": # Handling request to reserve a spot
            lotId = str(args[0]) # Getting the lot id
            plate = str(args[1]) # Getting the license plate
            ok, status, changed = server.state.reserve(lotId, plate) # Attempting to reserve
            if changed: # If successfully reserved, then changed the count
                free = server.state.availability(lotId) # Getting the new count
                server.pubsub.publish(lotId, free) # Notifying all subscribers to the updated count
            return ok, None # Returning success status and no error

        if method == "cancel": # Handling request to cancel reservation
            lotId = str(args[0]) # Getting lot id
            plate = str(args[1]) # Getting the license plate
            ok, status, changed = server.state.cancel(lotId, plate) # Attempting cancellation
            if changed: # If successfully cancel, then update the availabilty
                free = server.state.availability(lotId) # Getting the new count
                server.pubsub.publish(lotId, free) # Notifying all subscribers to the updated count
            return ok, None # Returning success status and no error

        if method == "subscribe": # Handling request for real-time updates on a lot if they are subscribed
            lotId = str(args[0]) # Getting lot id
            subId = server.pubsub.subscribe(lotId) # Registering the client in the pubsub system
            return subId, None # Returning the subscription ID and no error

        if method == "unsubscribe": # Handling request to stop real-time updates on a lot if they are unsubscribed
            subId = str(args[0]) # Getting lot id
            ok = server.pubsub.unsubscribe(subId) # Removing the client in the pubsub system
            return ok, None # Returning success status and no error

        return None, "no_such_method" # Returning error if method name is invalid

    except KeyError: # If a lot ID doesn't exist in the data, then return specific error for missing lots
        return None, "unknown_lot"
    except Exception as e: # If any other unexpected runtime errors, then return a generic server error with the message
        return None, f"server_error: {e}"