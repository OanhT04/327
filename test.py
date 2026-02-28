# test_parking.py
import argparse
import json
import socket
import struct
import time
from typing import Any, Dict, List, Optional, Tuple


# -----------------------
# TEXT protocol helpers (port 5000)
# -----------------------
def text_cmd(host: str, port: int, line: str, timeout: float = 3.0) -> str:
    """
    Open a short-lived TCP connection, send one command, read one response.
    Works for: PING, LOTS, AVAIL <lot>, RESERVE <lot> <plate>, CANCEL <lot> <plate>
    """
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall((line.strip() + "\n").encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        data = s.recv(65535)
        return data.decode("utf-8", errors="replace").strip()


# -----------------------
# RPC protocol helpers (port 5001)
# -----------------------
def recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("connection closed")
        data += chunk
    return data


def rpc_call(host: str, port: int, rpc_id: int, method: str, args: List[Any], timeout: float = 3.0) -> Dict[str, Any]:
    req = {"rpcId": rpc_id, "method": method, "args": args}
    payload = json.dumps(req).encode("utf-8")
    frame = struct.pack(">I", len(payload)) + payload

    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(frame)
        hdr = recv_exact(s, 4)
        length = struct.unpack(">I", hdr)[0]
        body = recv_exact(s, length)
        return json.loads(body.decode("utf-8"))


# -----------------------
# SENSOR protocol helpers (port 5002)
# -----------------------
def sensor_update(host: str, port: int, lot: str, delta: int, timeout: float = 3.0) -> str:
    """
    Sends: UPDATE <lotId> <delta>\n
    Expects: OK or ERROR ...
    """
    with socket.create_connection((host, port), timeout=timeout) as s:
        msg = f"UPDATE {lot} {delta}\n".encode("utf-8")
        s.sendall(msg)
        s.shutdown(socket.SHUT_WR)
        data = s.recv(4096)
        return data.decode("utf-8", errors="replace").strip()


# -----------------------
# Pretty printing
# -----------------------
def print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2))


def die(msg: str, code: int = 2) -> None:
    raise SystemExit(msg)


# -----------------------
# Tests
# -----------------------
def test_basic_text(host: str, text_port: int, lot: str, plate: str) -> None:
    print("== TEXT: PING ==")
    print(text_cmd(host, text_port, "PING"))

    print("\n== TEXT: LOTS ==")
    lots = text_cmd(host, text_port, "LOTS")
    print(lots)

    print(f"\n== TEXT: AVAIL {lot} ==")
    before = int(text_cmd(host, text_port, f"AVAIL {lot}"))
    print("free(before) =", before)

    print(f"\n== TEXT: RESERVE {lot} {plate} ==")
    res = text_cmd(host, text_port, f"RESERVE {lot} {plate}")
    print("reserve ->", res)

    print(f"\n== TEXT: AVAIL {lot} (after reserve) ==")
    after_res = int(text_cmd(host, text_port, f"AVAIL {lot}"))
    print("free(after reserve) =", after_res)

    print(f"\n== TEXT: CANCEL {lot} {plate} ==")
    can = text_cmd(host, text_port, f"CANCEL {lot} {plate}")
    print("cancel ->", can)

    print(f"\n== TEXT: AVAIL {lot} (after cancel) ==")
    after_can = int(text_cmd(host, text_port, f"AVAIL {lot}"))
    print("free(after cancel) =", after_can)


def test_basic_rpc(host: str, rpc_port: int, lot: str, plate: str) -> None:
    print("\n== RPC: getLots ==")
    reply = rpc_call(host, rpc_port, 1, "getLots", [])
    print_json(reply)

    print(f"\n== RPC: reserve {lot} {plate} ==")
    reply = rpc_call(host, rpc_port, 2, "reserve", [lot, plate])
    print_json(reply)

    print(f"\n== RPC: getAvailability {lot} ==")
    reply = rpc_call(host, rpc_port, 3, "getAvailability", [lot])
    print_json(reply)

    print(f"\n== RPC: cancel {lot} {plate} ==")
    reply = rpc_call(host, rpc_port, 4, "cancel", [lot, plate])
    print_json(reply)

    print(f"\n== RPC: getAvailability {lot} (again) ==")
    reply = rpc_call(host, rpc_port, 5, "getAvailability", [lot])
    print_json(reply)


def test_sensor(host: str, sensor_port: int, text_port: int, lot: str) -> None:
    print("\n== SENSOR: UPDATE +1 available space ==")
    print("sensor ->", sensor_update(host, sensor_port, lot, 1))

    print(f"\n== TEXT: AVAIL {lot} (after sensor +1) ==")
    print("free =", text_cmd(host, text_port, f"AVAIL {lot}"))

    print("\n== SENSOR: UPDATE -1 available space ==")
    print("sensor ->", sensor_update(host, sensor_port, lot, -1))

    print(f"\n== TEXT: AVAIL {lot} (after sensor -1) ==")
    print("free =", text_cmd(host, text_port, f"AVAIL {lot}"))


def spam_sensor(host: str, sensor_port: int, lot: str, n: int, delta: int, sleep_s: float) -> None:
    print(f"\n== SENSOR SPAM: n={n} delta={delta} sleep={sleep_s}s ==")
    ok = 0
    err = 0
    for i in range(n):
        try:
            r = sensor_update(host, sensor_port, lot, delta)
            if r.startswith("OK"):
                ok += 1
            else:
                err += 1
        except Exception:
            err += 1
        if sleep_s > 0:
            time.sleep(sleep_s)
    print("sensor spam done. OK=", ok, "ERR=", err)


def spam_reserve_cancel(host: str, text_port: int, lot: str, n: int, plate_prefix: str, sleep_s: float) -> None:
    print(f"\n== TEXT SPAM RESERVE/CANCEL: n={n} ==")
    ok = 0
    err = 0
    for i in range(n):
        plate = f"{plate_prefix}{i}"
        try:
            r1 = text_cmd(host, text_port, f"RESERVE {lot} {plate}")
            r2 = text_cmd(host, text_port, f"CANCEL {lot} {plate}")
            if r1 == "OK" and r2 == "OK":
                ok += 1
            else:
                err += 1
        except Exception:
            err += 1
        if sleep_s > 0:
            time.sleep(sleep_s)
    print("reserve/cancel spam done. OK=", ok, "ERR=", err)


# -----------------------
# Main
# -----------------------
def main():
    p = argparse.ArgumentParser(description="Parking server test tool (no nc).")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--textPort", type=int, default=5000)
    p.add_argument("--rpcPort", type=int, default=5001)
    p.add_argument("--sensorPort", type=int, default=5002)

    p.add_argument("--lot", default="A")
    p.add_argument("--plate", default="TEST123")

    p.add_argument("--do-text", action="store_true", help="Run text protocol tests")
    p.add_argument("--do-rpc", action="store_true", help="Run rpc tests")
    p.add_argument("--do-sensor", action="store_true", help="Run sensor update tests")

    p.add_argument("--spam-sensor", type=int, default=0, help="Send N sensor updates")
    p.add_argument("--sensor-delta", type=int, default=1, help="Delta for spam sensor")
    p.add_argument("--spam-rescancel", type=int, default=0, help="Run N reserve/cancel cycles (text)")
    p.add_argument("--sleep", type=float, default=0.0, help="Sleep between spam iterations")

    args = p.parse_args()

    if not (args.do_text or args.do_rpc or args.do_sensor or args.spam_sensor or args.spam_rescancel):
        print("No actions chosen; running default: --do-text --do-rpc --do-sensor")
        args.do_text = args.do_rpc = args.do_sensor = True

    if args.do_text:
        test_basic_text(args.host, args.textPort, args.lot, args.plate)

    if args.do_rpc:
        test_basic_rpc(args.host, args.rpcPort, args.lot, args.plate)

    if args.do_sensor:
        test_sensor(args.host, args.sensorPort, args.textPort, args.lot)

    if args.spam_sensor:
        spam_sensor(args.host, args.sensorPort, args.lot, args.spam_sensor, args.sensor_delta, args.sleep)

    if args.spam_rescancel:
        spam_reserve_cancel(args.host, args.textPort, args.lot, args.spam_rescancel, "PLATE", args.sleep)


if __name__ == "__main__":
    main()