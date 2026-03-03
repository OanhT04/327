# Campus Smart Parking Finder

For this assignment, we are implementing a concurrent parking system using TCP sockets. Throughout working on this assignment, we have learned the practical real-world experience of distributed systems conepts such as multithreading, RPC, and various more. And this allows us to explore and get more hands-on experience on the reservation requests and occupancy updates and how to run it efficiently at the same time.

## Requirements

- Python 3

## Files
- server.py
- config.json
- tcp.py
- rpc.py
- state.py
- sensor.py
- pubsub.py
- events.py
- test.py
- clientSubTest.py

## Environment Setup

Create venv:

```bash
python3 -m venv .venv
```

Activate:

- macOS/Linux

```bash
source .venv/bin/activate
```

- Windows

```bat
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

This project uses Python **standard library** only.

## Run

Terminal 1 - start server:

```bash
python3 server.py --config config.json
```

Terminal 2 - start subscriber (example lot A):

```bash
python3 clientSubTest.py --lot A
```

Terminal 3 - run client tests:

```bash
python3 test.py --do-text --do-rpc --do-sensor
```

## Configuration

`config.json` will contain server configs such as:

- ports: `text`, `rpc`, `sensor`, `events`
- lots and capacities
- `reservation_ttl_sec` (default 300 seconds / 5 minutes)
- `sensor_workers`
- `client_limit`
- `listen_backlog`
- `per_sub_queue_max`
- `sensor_queue_max`

## Server Organization

This server use thread-per-connection with bounded concurrency.

- One accept loop per port (text/rpc/sensor/events).
- Each accepted connection runs in its own daemon thread.
- A global `BoundedSemaphore(client_limit)` limits active handlers.
- Backlog is configured via `listen_backlog`.
- Shared state is protected by lock in `ParkingState`.

**Why this choice:**

We chose this because its an easier mapping from connection to handler logic, it has easy protocol separation across ports, and the bounded semaphore provides overload protection.

## Text Protocol

- `PING` -> `PONG`
- `LOTS` -> JSON list of `{id, capacity, occupied, free}`
- `AVAIL <lotId>` -> integer free
- `RESERVE <lotId> <plate>` -> `OK | FULL | EXISTS`
- `CANCEL <lotId> <plate>` -> `OK | NOT_FOUND`

## RPC Framing and Marshalling

Transport: TCP, length-prefixed frames.

Request schema:

```json
{"rpcId": 1, "method": "getAvailability", "args": ["A"]}
```

Reply schema:

```json
{"rpcId": 1, "result": 42, "error": null}
```

RPC methods:

- `getLots() -> List<Lot>`
- `getAvailability(lotId) -> int`
- `reserve(lotId, plate) -> bool`
- `cancel(lotId, plate) -> bool`
- `subscribe(lotId) -> subId`
- `unsubscribe(subId) -> bool`

Parameter passing note:

- Endianness for frame length is big-endian.
- Scalar values are marshalled as JSON number/string/bool.
- `rpcId` is carried as JSON number.

RPC path:

Caller -> Client Stub -> TCP -> Server Skeleton -> Method -> Return -> Client Stub -> Caller

## Timeout Policy

The server connections use socket timeouts to avoid stuck connections.On the Client-side RPC calls should enforce per-call timeout. And on timeout, client should raise/report `TimeoutError` at call site.

## Asynchronous Messaging Path

Sensors use a separate TCP port and send:

- `UPDATE <lotId> <delta>`

Design:

Sensor handler will parse and enqueue updates only while the Worker threads drain `sensorQueue` and apply updates to state. This keeps sensor ingestion from blocking normal RPC/text request handling.

## Pub/Sub Design

API (via RPC):

- `subscribe(lotId) -> subId`
- `unsubscribe(subId) -> bool`

Delivery channel:

- Separate TCP connection on events port.
- Client binds event socket by sending: `SUB <subId>`.
- Server pushes events:
  - `EVENT <lotId> <free> <timestamp_ms>`

Publish triggers:

- `RESERVE`, `CANCEL`, sensor `UPDATE`, and reservation expiration.

## Back-Pressure Policy

For subscribers:

- Bounded per-subscriber queue (`per_sub_queue_max`).
- If queue is full, subscriber is disconnected (slow-consumer drop policy).

For sensors:

- Bounded global sensor queue (`sensor_queue_max`).
- If full, sensor receives `ERROR server_saturated`.

For incoming clients:

- If `client_limit` reached, server returns `server_busy` and closes connection.
- RPC clients receive a framed RPC error response.

## Logging

Structured JSON log entries include:

- event type
- lotId
- plate/delta (when relevant)
- timestamp

## Quick Experiment Commands

Example baseline:

```bash
python test.py --spam-rescancel 1000 --sleep 0
```

Example async stress:

```bash
python test.py --spam-sensor 5000 --sensor-delta 1 --sleep 0
```

Test Results:
```bash
python server.py --config config.json
```
then in a separate terminal:
```bash
python run_all_tests.py
```

## Authors:

**Sovannmonyrotn Kun:** handles the README file, requirements.txt, debugging and checking for any errors.

**David Tran:** handles the written report and testing.

**Oanh Tran:** handles the python program
