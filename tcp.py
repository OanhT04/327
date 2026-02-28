# tpc.py
import json

def textClient(server, conn, addr):
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
        cmd = parts[0].upper()

        try:
            if cmd == "PING":
                f.write(b"PONG\n")

            elif cmd == "LOTS":
                lots = server.state.allLots()
                f.write((json.dumps(lots) + "\n").encode("utf-8"))

            elif cmd == "AVAIL" and len(parts) == 2:
                free = server.state.availability(parts[1])
                f.write((str(free) + "\n").encode("utf-8"))

            elif cmd == "RESERVE" and len(parts) == 3:
                ok, status, changed = server.state.reserve(parts[1], parts[2])
                f.write((status + "\n").encode("utf-8"))
                if changed:
                    free = server.state.availability(parts[1])
                    server.pubsub.publish(parts[1], free)

            elif cmd == "CANCEL" and len(parts) == 3:
                ok, status, changed = server.state.cancel(parts[1], parts[2])
                f.write((status + "\n").encode("utf-8"))
                if changed:
                    free = server.state.availability(parts[1])
                    server.pubsub.publish(parts[1], free)

            else:
                f.write(b"ERROR bad_command\n")

            f.flush()

        except KeyError:
            f.write(b"ERROR unknown_lot\n")
            f.flush()
        except Exception:
            f.write(b"ERROR server_error\n")
            f.flush()