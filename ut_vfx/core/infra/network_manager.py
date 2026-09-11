import json
import socket
import logging
import asyncio
from PySide6.QtCore import QObject, Signal

class NetworkManager(QObject):
    """
    Handles LAN peer discovery (UDP) and messaging (TCP).
    Zero dependencies (only standard socket lib).
    """
    peer_discovered = Signal(str, str) # ip, username
    message_received = Signal(dict) # {type, sender, content...}
    
    UDP_PORT = 5005
    TCP_PORT = 5006
    BROADCAST_IP = '<broadcast>'
    MAX_PORT_ATTEMPTS = 10
    
    def __init__(self, username="Artist"):
        super().__init__()
        self.username = username
        self.peers = {} # {ip: username}
        self.peer_tcp_ports = {} # {ip: tcp_port}
        self.running = True
        
        # Identity
        self.my_ip = self._get_local_ip()
        
        # Async Tasks
        self.udp_transport = None
        self.tcp_server = None
        self.announce_task = None
        self._announce_sock = None
        self.loop = None
        self.udp_port = self.UDP_PORT
        self.tcp_port = self.TCP_PORT
        self._broadcast_ports = [self.UDP_PORT + i for i in range(self.MAX_PORT_ATTEMPTS)]
        
    def start(self):
        logging.info(f"Starting NetworkManager (Async) on {self.my_ip}")
        try:
            self.loop = asyncio.get_event_loop()
            self.loop.create_task(self._start_services())
        except Exception as e:
            logging.exception(f"Failed to start async network services: {e}")

    async def _start_services(self):
        try:
            self.udp_transport = await self._bind_udp_with_fallback()
            self.tcp_server = await self._bind_tcp_with_fallback()
            self.announce_task = self.loop.create_task(self._announce_loop())
            logging.info(
                "NetworkManager active (UDP:%s TCP:%s, local_ip=%s)",
                self.udp_port,
                self.tcp_port,
                self.my_ip,
            )
        except Exception as e:
            logging.exception(f"Async Network Start Error: {e}")

    async def _bind_udp_with_fallback(self):
        last_error = None
        for offset in range(self.MAX_PORT_ATTEMPTS):
            port = self.UDP_PORT + offset
            try:
                transport, _protocol = await self.loop.create_datagram_endpoint(
                    lambda: UdpProtocol(self),
                    local_addr=('0.0.0.0', port)
                )
                self.udp_port = port
                if port != self.UDP_PORT:
                    logging.warning(
                        "UDP port %s unavailable. Using fallback UDP port %s.",
                        self.UDP_PORT,
                        port,
                    )
                return transport
            except OSError as err:
                last_error = err
                logging.debug("UDP bind failed on port %s: %s", port, err)
                continue
        raise OSError(f"Could not bind UDP discovery port in range {self.UDP_PORT}-{self.UDP_PORT + self.MAX_PORT_ATTEMPTS - 1}: {last_error}")

    async def _bind_tcp_with_fallback(self):
        last_error = None
        for offset in range(self.MAX_PORT_ATTEMPTS):
            port = self.TCP_PORT + offset
            try:
                server = await asyncio.start_server(
                    self._handle_tcp_client, '0.0.0.0', port
                )
                self.tcp_port = port
                if port != self.TCP_PORT:
                    logging.warning(
                        "TCP port %s unavailable. Using fallback TCP port %s.",
                        self.TCP_PORT,
                        port,
                    )
                return server
            except OSError as err:
                last_error = err
                logging.debug("TCP bind failed on port %s: %s", port, err)
                continue
        raise OSError(f"Could not bind TCP messaging port in range {self.TCP_PORT}-{self.TCP_PORT + self.MAX_PORT_ATTEMPTS - 1}: {last_error}")
        
    def stop(self):
        self.running = False
        try:
            if self.announce_task and not self.announce_task.done():
                self.announce_task.cancel()
        except Exception as e:
            logging.debug(f"NetworkManager announce task cancel failed: {e}")

        try:
            if self.udp_transport:
                self.udp_transport.close()
                self.udp_transport = None
        except Exception as e:
            logging.debug(f"NetworkManager UDP transport close failed: {e}")

        try:
            if self.tcp_server:
                self.tcp_server.close()
                if self.loop and self.loop.is_running():
                    self.loop.create_task(self.tcp_server.wait_closed())
                self.tcp_server = None
        except Exception as e:
            logging.debug(f"NetworkManager TCP server close failed: {e}")

        try:
            if self._announce_sock:
                self._announce_sock.close()
                self._announce_sock = None
        except Exception as e:
            logging.debug(f"NetworkManager announce socket close failed: {e}")

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

    # --- UDP DISCOVERY ---
    async def _announce_loop(self):
        self._announce_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._announce_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            while self.running:
                try:
                    msg = json.dumps({
                        "type": "HELLO",
                        "user": self.username,
                        "ip": self.my_ip,
                        "tcp_port": self.tcp_port,
                    }).encode('utf-8')

                    # Offload socket send to executor to keep UI loop responsive.
                    await self.loop.run_in_executor(None, lambda: self._broadcast_hello(msg))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logging.warning(f"UDP Announce error: {e}")

                await asyncio.sleep(5)
        finally:
            try:
                if self._announce_sock:
                    self._announce_sock.close()
            except OSError as exc:
                logging.debug("Announce socket close skipped: %s", exc)
            self._announce_sock = None

    def _broadcast_hello(self, payload: bytes):
        """Broadcast to all candidate UDP ports so peers on fallback ports still discover us."""
        for port in self._broadcast_ports:
            try:
                self._announce_sock.sendto(payload, (self.BROADCAST_IP, port))
            except OSError as exc:
                logging.debug("Broadcast HELLO failed on UDP port %s: %s", port, exc)

    def process_udp_packet(self, data, addr):
        try:
            msg = json.loads(data.decode('utf-8'))
            
            if msg.get('type') == 'HELLO':
                sender_ip = msg.get('ip')
                sender_user = msg.get('user')
                sender_port = msg.get('tcp_port', self.TCP_PORT)
                try:
                    sender_port = int(sender_port)
                except (TypeError, ValueError):
                    sender_port = self.TCP_PORT
                
                if sender_ip != self.my_ip:
                    if sender_ip not in self.peers:
                        self.peers[sender_ip] = sender_user
                        self.peer_tcp_ports[sender_ip] = sender_port
                        self.peer_discovered.emit(sender_ip, sender_user)
                    else:
                        name_changed = self.peers[sender_ip] != sender_user
                        port_changed = self.peer_tcp_ports.get(sender_ip) != sender_port
                        self.peer_tcp_ports[sender_ip] = sender_port
                        if name_changed or port_changed:
                            self.peers[sender_ip] = sender_user
                            self.peer_discovered.emit(sender_ip, sender_user)
        except Exception as e:
            logging.exception(f"UDP Packet Error: {e}")

    # --- TCP MESSAGING ---
    def send_message(self, target_ip, type, content):
        """Async send wrapper (fire and forget task)"""
        if self.loop is not None and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_send(target_ip, type, content), self.loop)
        else:
            logging.warning("NetworkManager loop not running, cannot send message")

    async def _async_send(self, target_ip, type, content):
        try:
            target_port = self.peer_tcp_ports.get(target_ip, self.TCP_PORT)
            reader, writer = await asyncio.open_connection(target_ip, target_port)
            
            payload = json.dumps({
                "type": type,
                "sender": self.username,
                "content": content
            }).encode('utf-8')
            
            writer.write(payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return True
        except Exception as e:
            logging.exception(f"Send failed to {target_ip}:{self.peer_tcp_ports.get(target_ip, self.TCP_PORT)}: {e}")
            return False

    async def _handle_tcp_client(self, reader, writer):
        try:
            data = await reader.read(4096)
            if data:
                msg = json.loads(data.decode('utf-8'))
                self.message_received.emit(msg)
        except Exception as e:
            logging.exception(f"TCP Receive error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

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
            "peers": len(self.peers),
            "udp_port": self.udp_port,
            "tcp_port": self.tcp_port
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

class UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, manager):
        self.manager = manager
        
    def datagram_received(self, data, addr):
        self.manager.process_udp_packet(data, addr)
    
    def connection_lost(self, exc):
        logging.debug(f"UDP Connection Lost: {exc}")
