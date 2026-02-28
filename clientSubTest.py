import argparse
import json
import socket
import struct

#### DEMO for testing as sub

# -----------------------
# RPC framing helpers
# -----------------------

def recvExact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("connection closed")
        data += chunk
    return data


def sendRpc(host, port, rpcId, method, args):
    request = {
        "rpcId": rpcId,
        "method": method,
        "args": args
    }
    payload = json.dumps(request).encode("utf-8")
    frame = struct.pack(">I", len(payload)) + payload
    with socket.create_connection((host, port)) as sock:
        sock.sendall(frame)
        header = recvExact(sock, 4)
        length = struct.unpack(">I", header)[0]
        body = recvExact(sock, length)
        reply = json.loads(body.decode())
        return reply


# -----------------------
# Subscriber
# -----------------------

def runSubscriber(host, rpcPort, eventsPort, lotId):

    print("Subscribing to lot:", lotId)
    reply = sendRpc(
        host,
        rpcPort,
        1,
        "subscribe",
        [lotId]
    )
    if reply["error"]:
        print("RPC error:", reply["error"])
        return
    subId = reply["result"]
    print("Subscription ID:", subId)
    
    # Connect to events port
    sock = socket.create_connection((host, eventsPort))

    sock.sendall(f"SUB {subId}\n".encode())

    # Expect OK
    data = sock.recv(100).decode().strip()
    if data != "OK":
        print("Subscription failed:", data)
        return

    print("Connected. Waiting for events...\n")
    # Receive events forever
    while True:
        line = sock.recv(4096)

        if not line:
            print("Disconnected")
            return

        print(line.decode().strip())


# -----------------------
# Main
# -----------------------

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--host", default="localhost")
    parser.add_argument("--rpcPort", type=int, default=5001)
    parser.add_argument("--eventsPort", type=int, default=5003)
    #for testing
    parser.add_argument("--lot", default="A")

    args = parser.parse_args()

    runSubscriber(
        args.host,
        args.rpcPort,
        args.eventsPort,
        args.lot
    )


if __name__ == "__main__":
    main()