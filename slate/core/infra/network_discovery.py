import socket
import json
import logging

def discover_server(timeout=2.0):
    """
    Broadcasts a UDP packet to find the Slate Central Server on the local network.
    Returns a tuple of (server_ip, db_port) if found, else (None, None).
    """
    found = discover_server_details(timeout)
    if not found:
        return None, None
    return found["host"], found["db_port"]


def parse_announcement(msg: str, server_ip: str):
    """The server's reply as a dict, or None if it is not one."""
    try:
        response = json.loads(msg)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(response, dict) or response.get("status") != "ONLINE":
        return None
    try:
        db_port = int(response.get("db_port") or 5432)
        pooler_port = int(response.get("pooler_port") or 0)
    except (TypeError, ValueError):
        return None
    return {"host": server_ip, "db_port": db_port, "pooler_port": pooler_port}


def discover_server_details(timeout=2.0):
    """
    Ask the network where the server is; a dict with host, db_port and
    pooler_port, or None. The pool port is 0 when the server did not say.
    """
    listen_port = 54320
    magic_packet = "Slate_DISCOVER"
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)
    
    logging.info("NetworkDiscovery: Broadcasting search packet for Slate Central Server...")
    
    try:
        # Send broadcast packet
        sock.sendto(magic_packet.encode('utf-8'), ('255.255.255.255', listen_port))

        # Wait for response
        attempts = 0
        while attempts < 10:
            attempts += 1
            data, addr = sock.recvfrom(1024)
            msg = data.decode('utf-8')
            found = parse_announcement(msg, addr[0])
            if found:
                logging.info("NetworkDiscovery: Found Server at %s:%s (pool %s)",
                             found["host"], found["db_port"], found["pooler_port"] or "none")
                return found
        return None

    except socket.timeout:
        logging.warning("NetworkDiscovery: No server responded within timeout.")
        return None
    except Exception as e:
        logging.error(f"NetworkDiscovery: Error discovering server: {e}")
        return None
    finally:
        sock.close()
