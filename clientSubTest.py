import argparse # Importing argparse module to parse command line arguments
import json # Importing json module to handle JSON file
import socket # Importing socket module to allow TCP connection
import struct # Importing struct for binary data

#### DEMO for testing as sub

# -----------------------
# RPC framing helpers
# -----------------------

def recvExact(sock, size): # This function will ensure that it will read "size" bytes from the socket
    data = b"" # Initializing an empty byte string that will be used to store results
    while len(data) < size: # This while loop will run until the length of data is greater than or equal to size
        chunk = sock.recv(size - len(data)) # Reading the byte from the socket buffer
        if not chunk: # If the byte is empty, raise error that connection is dropped
            raise ConnectionError("connection closed")
        data += chunk # Adding the byte to the byte string
    return data # Returning the complete byte string


def sendRpc(host, port, rpcId, method, args):
    request = { # Initializing a dictionary to represents rpc request
        "rpcId": rpcId,
        "method": method,
        "args": args
    }
    payload = json.dumps(request).encode("utf-8") # Converting the dictionary to UTF-8
    frame = struct.pack(">I", len(payload)) + payload # Prepending the payload
    with socket.create_connection((host, port)) as sock: # Opening a TCP connection
        sock.sendall(frame) # Sending the message frame
        header = recvExact(sock, 4) # Getting the header length by reading the first 4 bytes
        length = struct.unpack(">I", header)[0] # Coverting the bytes into python ineger
        body = recvExact(sock, length) # Reading "length" bytes for the JSON response
        reply = json.loads(body.decode()) # Decoding the bytes then parsing it into the dictionary
        return reply # Returning the dictionary


# -----------------------
# Subscriber
# -----------------------

def runSubscriber(host, rpcPort, eventsPort, lotId):

    print("Subscribing to lot:", lotId) # Printing the subscribed lot ID to the log
    reply = sendRpc( # Doing a RPC call to register the subscribed lot
        host,
        rpcPort,
        1,
        "subscribe",
        [lotId]
    )
    if reply["error"]: # Checking if the server return an error message
        print("RPC error:", reply["error"]) # Printing the error message if the server returns an error message
        return # Exiting the function once done
    subId = reply["result"] # Getting the subscription ID from the server's response
    print("Subscription ID:", subId) # Printing the the successful subscription ID to the log
    
    # Connect to events port
    sock = socket.create_connection((host, eventsPort))

    sock.sendall(f"SUB {subId}\n".encode()) # Sending SUB command with its id to the event server

    # Expect OK
    data = sock.recv(100).decode().strip()
    if data != "OK": # If the server doesn't respond with an "OK", print the failure message
        print("Subscription failed:", data)
        return

    print("Connected. Waiting for events...\n") # Print to the log that is is constantly waiting for data
    # Receive events forever
    while True: # Creating an infinite loop that will listen for incoming events
        line = sock.recv(4096) # Reading from the event stream

        if not line: # If its empty, it means that the socket is no longer running, and print the message to the log
            print("Disconnected")
            return

        print(line.decode().strip()) # Decoding the bytes and printing it to the console


# -----------------------
# Main
# -----------------------

def main():

    parser = argparse.ArgumentParser() # Initializing parser for command-line arguments

    parser.add_argument("--host", default="localhost") # Defining server address
    parser.add_argument("--rpcPort", type=int, default=5001) # Defining port number for RPC
    parser.add_argument("--eventsPort", type=int, default=5003) # Defining port number for Events
    #for testing
    parser.add_argument("--lot", default="A") # Defining specific lot to watch

    args = parser.parse_args() # Executing the command-line arguments

    runSubscriber(
        args.host,
        args.rpcPort,
        args.eventsPort,
        args.lot
    )


if __name__ == "__main__":
    main()