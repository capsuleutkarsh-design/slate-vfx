import socket
import threading
import json
import logging
import time

class NetworkBroadcaster(threading.Thread):
    """
    Listens for UDP broadcasts on the LAN from UT_VFX clients.
    When a client asks 'Where is the server?', this responds with its IP and Database Port.
    """
    def __init__(self, db_port: int, listen_port: int = 54320):
        super().__init__()
        self.db_port = db_port
        self.listen_port = listen_port
        self.running = True
        self.daemon = True  # Ensure thread dies if main app crashes
        self.sock = None
        
    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Allow multiple instances to bind to the same port (useful for development)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Bind to all interfaces
            self.sock.bind(("", self.listen_port))
            # Set timeout so we can gracefully shutdown
            self.sock.settimeout(1.0)
            
            logging.info(f"NetworkBroadcaster: Listening for UT_VFX clients on UDP port {self.listen_port}")
            
            while self.running:
                try:
                    data, addr = self.sock.recvfrom(1024)
                    msg = data.decode('utf-8').strip()
                    
                    if msg == "UTVFX_DISCOVER":
                        # Client is asking for us! Send them our configuration.
                        response = json.dumps({
                            "status": "ONLINE",
                            "db_port": self.db_port
                        })
                        self.sock.sendto(response.encode('utf-8'), addr)
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
