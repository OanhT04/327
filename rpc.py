# rpc.py
import json
import struct

"""
RPC Server

Length-prefixed JSON RPC over TCP.
Each connection handled by its own thread.

Request: {rpcId, method, args}
Reply:   {rpcId, result, error}
"""

# ---- helpers ----
def recvExact(conn, size):
    data = b""
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise ConnectionError("peer closed")
        data += chunk
    return data

def recvFrame(conn, maxLen=1_000_000):
    header = recvExact(conn, 4)
    length = struct.unpack(">I", header)[0]
    if length > maxLen:
        raise ValueError("frame too large")
    return recvExact(conn, length)

def sendFrame(conn, payloadBytes):
    header = struct.pack(">I", len(payloadBytes))
    conn.sendall(header + payloadBytes)


# ---- RPC server skeleton ----
def rpcClient(server, conn, addr):
    conn.settimeout(60)
    while not server.stopEvent.is_set():
        try:
            payload = recvFrame(conn)
        except Exception:
            return

        try:
            req = json.loads(payload.decode("utf-8"))
            rpcId = req.get("rpcId")
            method = req.get("method")
            args = req.get("args", [])

            result, err = dispatchRpc(server, method, args)
            reply = {"rpcId": rpcId, "result": result, "error": err}
            sendFrame(conn, json.dumps(reply).encode("utf-8"))

        except Exception as e:
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
        if method == "getLots":
            return server.state.allLots(), None

        if method == "getAvailability":
            lotId = str(args[0])
            return server.state.availability(lotId), None

        if method == "reserve":
            lotId = str(args[0])
            plate = str(args[1])
            ok, status, changed = server.state.reserve(lotId, plate)
            if changed:
                free = server.state.availability(lotId)
                server.pubsub.publish(lotId, free)
            return ok, None

        if method == "cancel":
            lotId = str(args[0])
            plate = str(args[1])
            ok, status, changed = server.state.cancel(lotId, plate)
            if changed:
                free = server.state.availability(lotId)
                server.pubsub.publish(lotId, free)
            return ok, None

        if method == "subscribe":
            lotId = str(args[0])
            subId = server.pubsub.subscribe(lotId)
            return subId, None

        if method == "unsubscribe":
            subId = str(args[0])
            ok = server.pubsub.unsubscribe(subId)
            return ok, None

        return None, "no_such_method"

    except KeyError:
        return None, "unknown_lot"
    except Exception as e:
        return None, f"server_error: {e}"