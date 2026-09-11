import socket
import json
import logging

def discover_server(timeout=2.0):
    """
    Broadcasts a UDP packet to find the UT Central Server on the local network.
    Returns a tuple of (server_ip, db_port) if found, else (None, None).
    """
    listen_port = 54320
    magic_packet = "UTVFX_DISCOVER"
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)
    
    logging.info("NetworkDiscovery: Broadcasting search packet for UT Central Server...")
    
    try:
        # Send broadcast packet
        sock.sendto(magic_packet.encode('utf-8'), ('255.255.255.255', listen_port))
        
        # Wait for response
        attempts = 0
        while attempts < 10:
            attempts += 1
            data, addr = sock.recvfrom(1024)
            msg = data.decode('utf-8')
            
            try:
                response = json.loads(msg)
                if response.get("status") == "ONLINE":
                    server_ip = addr[0]
                    db_port = response.get("db_port", 5432)
                    logging.info(f"NetworkDiscovery: Found Server at {server_ip}:{db_port}!")
                    return server_ip, db_port
            except json.JSONDecodeError:
                pass
                
    except socket.timeout:
        logging.warning("NetworkDiscovery: No server responded within timeout.")
        return None, None
    except Exception as e:
        logging.error(f"NetworkDiscovery: Error discovering server: {e}")
        return None, None
    finally:
        sock.close()
