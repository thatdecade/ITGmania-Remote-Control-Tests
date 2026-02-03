# ITGmania Remote Control Harness Testing

This setup is a proof of concept that lets a Python test harness remotely query and control ITGmania over a localhost WebSocket, using a Lua module.

With just these two files you get:

* Bidirectional comms (Lua client inside ITGmania to a Python server).
* Command API that can query state and start gameplay.

---

## Protocol

Both sides agree on two message "lanes":

### A) Text messages (Lua → Python)

Used for liveness and lightweight telemetry.

* `HEARTBEAT|...`
* `SCREEN|...`

Python treats the first heartbeat as the signal that the ITG client is fully online and ready for commands. 

### B) Binary packets (Python → Lua and Lua → Python)

A compact framed format:

* **Header:** `uint16_be(size)` where `size = 1 + len(payload)`
* **Body:** `uint8(command_id)` then `payload`

Python’s implementation of packet building and parsing is explicit and easy to mirror in Lua. 

**Payload encoding:**

* Responses carry JSON text that is null-terminated UTF-8 (Python strips a trailing `\0` before JSON parsing). 

---

## Commands

The Python file defines the command IDs and response IDs like this: 

| Action     | Command ID | Response ID | What it’s used for in the POC               |
| ---------- | ---------: | ----------: | ------------------------------------------- |
| HELLO      |       0x01 |        0x81 | Basic handshake + capability/info           |
| GET_STATUS |       0x10 |        0x90 | Poll current screen + gameplay + stats      |
| GET_GROUPS |       0x11 |        0x91 | List song groups (optional utility)         |
| GET_SONGS  |       0x12 |        0x92 | Enumerate songs for selection               |
| START_SONG |       0x20 |        0xA0 | Start gameplay on chosen chart              |
| PAUSE      |       0x21 |        0xA1 | Pause/resume gameplay                       |
| STOP       |       0x22 |        0xA2 | Exit gameplay / back out                    |
| ERROR      |        n/a |        0xFF | Returned if Lua rejects a command or throws |

## Files

### RemoteWsHarness.lua

**Role:** Acts as a WebSocket client from inside the game. It connects to a local server (`ws://127.0.0.1:8765`) and exposes a tiny remote-control API.

**Features:**

* Maintains a single WebSocket connection to localhost (client-side).
* Sends simple *text* liveness messages like:

  * `HEARTBEAT|...`
  * `SCREEN|...`
* Receives *binary* command packets from Python, performs game-side actions, and replies with *binary* responses whose payload is JSON.

---

### itgmania_harness_poc_test2.py

**Role:** A WebSocket server that waits for the game to connect, then runs tests.

**Features:**

* Listens on `ws://127.0.0.1:8765` and waits for an ITGmania client connection. 
* Treats receipt of a `HEARTBEAT|...` text message as "ready" (sets `ready_event`). 
* Implements the shared binary framing protocol and JSON payload decoding. 
* Uses a request/response queue per response-id so multiple command types can be awaited cleanly, and surfaces `RSP_ERROR` as an exception. 
* Runs tests.

---

## Tests

1. **Connectivity and framing are correct**

   * The server accepts the connection and receives text heartbeats. 
   * Binary packets can be sent and parsed without desync via length framing. 

2. **Remote status is observable**

   * `GET_STATUS` polling can reliably tell you which screen you are on and whether gameplay is active. 

3. **Remote song start**

   * The harness fetches songs, chooses one deterministically, and starts it. 
   * It then verifies gameplay actually began (not just "start command returned ok"). 

4. **Validate I/O via stats**

   * During gameplay, it polls status and confirms score / combo / percent / judgments.

5. **Remote pause/stop control functions**

   * Pause, resume, stop are exercised.
