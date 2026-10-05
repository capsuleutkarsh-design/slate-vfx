import socket
import logging


class NetworkManager:
    """
    Network diagnostics: is the internet up, is a server reachable.

    It used to also announce this PC by UDP and listen on TCP port 5006 for
    commands from anyone on the LAN (SHL-120). Nothing legitimate used that,
    so it was removed. Server discovery is network_discovery.py.
    """

    def __init__(self, username="Artist"):
        self.username = username
        self.my_ip = self._get_local_ip()

    def _get_local_ip(self):
        try:
            # Trick to get actual IP (doesn't connect)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception as e:
            logging.warning(f"Failed to resolve local IP: {e}")
            return "127.0.0.1"

    # --- Network Status & Diagnostics ---

    def check_internet_connectivity(self, host="8.8.8.8", port=53, timeout=2) -> bool:
        """Check if internet connection is available."""
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
            return True
        except (OSError, socket.error):
            return False

    def check_server_reachability(self, server: str, timeout: int = 2) -> bool:
        """Check if specific server (IP or hostname) is reachable."""
        if server in ("127.0.0.1", "localhost", "::1"):
            return True
        try:
            for p in (80, 443, 53):
                try:
                    s = socket.create_connection((server, p), timeout=timeout)
                    s.close()
                    return True
                except (ConnectionRefusedError, OSError):
                    pass
            socket.gethostbyname(server)
            return True
        except Exception:
            return False

    def get_network_status(self) -> dict:
        """Get network status summary."""
        is_conn = self.check_internet_connectivity()
        return {
            "is_connected": is_conn,
            "local_ip": self.my_ip,
        }

    def register_status_callback(self, callback):
        """Register a callback to receive network status updates."""
        if not hasattr(self, '_callbacks'):
            self._callbacks = []
        self._callbacks.append(callback)

    def check_and_notify(self):
        """Check connectivity and notify registered callbacks."""
        is_conn = self.check_internet_connectivity()
        if hasattr(self, '_callbacks'):
            for cb in self._callbacks:
                try:
                    cb(is_conn)
                except Exception as e:
                    logging.error(f"Network callback error: {e}")

    def ping_multiple_servers(self, servers, timeout: int = 2) -> dict:
        """Check reachability of multiple servers."""
        results = {}
        for s in servers:
            results[s] = self.check_server_reachability(s, timeout=timeout)
        return results

    def estimate_connection_speed(self, test_url: str = "http://127.0.0.1", timeout: int = 1):
        """Estimate connection speed or return benchmark metric."""
        import time
        t0 = time.time()
        try:
            self.check_server_reachability("127.0.0.1", timeout=timeout)
            elapsed = time.time() - t0
            return max(1.0, 100.0 / (elapsed + 0.001))
        except Exception:
            return None

    def get_network_interfaces(self) -> list:
        """Return list of available network interfaces."""
        interfaces = []
        try:
            import psutil
            addrs = psutil.net_if_addrs()
            for iface_name, addr_list in addrs.items():
                for a in addr_list:
                    if a.family == socket.AF_INET:
                        interfaces.append({"name": iface_name, "ip": a.address})
        except Exception:
            pass
        if not interfaces:
            interfaces.append({"name": "loopback", "ip": "127.0.0.1"})
        return interfaces
