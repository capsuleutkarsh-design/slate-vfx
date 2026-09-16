import socket
import threading
import json
import logging
import time

class NetworkBroadcaster(threading.Thread):
    """
    Listens for UDP broadcasts on the LAN from Slate clients.
    When a client asks 'Where is the server?', this responds with its IP and Database Port.
    """
    def __init__(self, db_port: int, listen_port: int = 54320, pooler_port: int = 0):
        super().__init__()
        self.db_port = db_port
        self.pooler_port = int(pooler_port or 0)
        self.listen_port = listen_port
        self.running = True
        self.daemon = True  # Ensure thread dies if main app crashes
        self.sock = None
        
    def announcement(self) -> str:
        """
        What a workstation is told when it asks where the server is.

        Both ports travel: the database's own and the pool's. A workstation
        that already knows the server's address but was configured for a
        port the server no longer uses follows the change from this.
        """
        payload = {"status": "ONLINE", "db_port": int(self.db_port)}
        if self.pooler_port:
            payload["pooler_port"] = int(self.pooler_port)
        return json.dumps(payload)

    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Allow multiple instances to bind to the same port (useful for development)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Bind to all interfaces
            self.sock.bind(("", self.listen_port))
            # Set timeout so we can gracefully shutdown
            self.sock.settimeout(1.0)
            
            logging.info(f"NetworkBroadcaster: Listening for Slate clients on UDP port {self.listen_port}")
            
            while self.running:
                try:
                    data, addr = self.sock.recvfrom(1024)
                    msg = data.decode('utf-8').strip()
                    
                    if msg == "Slate_DISCOVER":
                        # Client is asking for us! Send them our configuration.
                        self.sock.sendto(self.announcement().encode('utf-8'), addr)
                        logging.info(f"NetworkBroadcaster: Responded to discovery request from {addr[0]}")
                        
                except socket.timeout:
                    # Expected if no clients ask within 1 second. Loop continues.
                    pass
                except Exception as e:
                    if self.running:
                        logging.error(f"NetworkBroadcaster Error processing request: {e}")
                        time.sleep(1)
                        
        except Exception as e:
            logging.error(f"NetworkBroadcaster Critical Error: {e}")
        finally:
            if self.sock:
                self.sock.close()
                
    def stop(self):
        self.running = False
        self.join(timeout=2.0)
