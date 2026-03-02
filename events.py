# events.py
import socket

def eventsClient(server, conn, addr):
    """Event delivery channel.

    Client must send a single line:
      SUB <subId>

    After OK, server pushes:
      EVENT <lotId> <free> <timestamp_ms>
    """
    conn.settimeout(30)
    f = conn.makefile("rwb")
    first = f.readline()
    if not first:
        return
    first = first.decode("utf-8", errors="replace").strip()
    parts = first.split()

    if len(parts) != 2 or parts[0].upper() != "SUB":
        f.write(b"ERROR expected: SUB <subId>\n")
        f.flush()
        return

    subId = parts[1]
    ok = server.pubsub.attachConnection(subId, conn)
    if not ok:
        f.write(b"ERROR unknown_subId\n")
        f.flush()
        return

    f.write(b"OK\n")
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
