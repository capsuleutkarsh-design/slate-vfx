# Network & API Reference

UT_VFX uses a hybrid networking model: a **Zero-Config P2P LAN Protocol** (`NetworkManager`) for instant messaging and presence, and a **Shared-Disk Sync Hub** (`ServerHub`) for remote execution and configuration.

---

## 1. Zero-Config P2P Network (`NetworkManager`)

`network_manager.py` operates entirely on standard library sockets integrated with Python `asyncio`. It does not require a central REST server to discover peers or send messages.

### A. UDP Peer Discovery (Port 5005)

Every UT_VFX instance continuously broadcasts UDP discovery packets to the LAN broadcast address (`<broadcast>`).
- **Base Port**: `5005` (If occupied, it attempts up to 10 fallback ports, e.g., `5006`-`5014`).
- **Frequency**: Every 5 seconds.
- **Payload**: JSON bytes encoded in UTF-8.

**HELLO Packet Format**:
```json
{
    "type": "HELLO",
    "user": "ArtistName",
    "ip": "192.168.1.50",
    "tcp_port": 5006
}
```

When a peer receives this packet, it registers the IP, Username, and TCP Port in its internal `peers` dictionary and emits the PyQt `peer_discovered(ip, username)` signal.

### B. TCP Direct Messaging (Port 5006)

Once peers are discovered via UDP, direct communication happens over TCP to ensure reliable message delivery.
- **Base Port**: `5006` (Like UDP, falls back through `5015` if occupied).
- **Execution**: The `send_message()` function wraps the `asyncio` call to execute without blocking the PyQt Event Loop. 

**Message Format**:
```json
{
    "type": "CHAT_MESSAGE",
    "sender": "ArtistName",
    "content": {
         "text": "Hey, is the comp ready?"
    }
}
```

---

## 2. Server Hub & File-Based RPC (`ServerHub`)

For commands that must be delivered even when the target is offline, or for studio-wide configuration, UT_VFX uses `ServerHub`. It acts as a shared disk synchronization center.

### Core Directories

`ServerHub` manages the following directories inside the `GlobalConfig.server_root()` path:
- `/Config`: Global `settings.json` and `users.json`. Read/Written using `SafeJsonIO` to prevent race conditions.
- `/Commands`: Used for Remote Procedure Calls (RPC).
- `/LiveStatus`: Used to share presence when UDP discovery is not available across subnets.

### File-Based Remote Procedure Calls (RPC)

Because UT_VFX nodes might be on different subnets or behind firewalls where P2P TCP/UDP fails, `ServerHub` implements a file-based command bus in the `/Commands` directory.

#### 1. Remote Unlock Trigger
If an admin needs to unlock a frozen Gatekeeper screen on an artist's machine:
- Admin calls `trigger_remote_unlock("PC_NAME")`.
- It writes a file: `Commands/UNLOCK_PC_NAME.trigger`.
- The artist machine's poll loop calls `check_for_unlock_trigger()`, sees the file, unlocks the screen, and deletes the file.

#### 2. Broadcast Commands
For general network commands (e.g., force sync, display notification):
- The sender creates a temporary JSON file via `post_command()`: `Commands/cmd_<timestamp>_<target>.json`.
- **Targeting**: The `target` field can be a specific PC hostname or `"all"`.
- **TTL (Time To Live)**: Commands expire after 60 seconds. The `get_active_commands()` poll loop automatically deletes expired commands to keep the directory clean.

**Command Payload File**:
```json
{
    "command": "FORCE_SYNC",
    "target": "all",
    "message": "Updating project structure",
    "timestamp": 1718000000.0,
    "expires": 1718000060.0
}
```
