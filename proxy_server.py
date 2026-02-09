#!/usr/bin/env python3
"""
MITM Proxy Server
Forwards traffic through dynamically configured remote proxy
"""

import asyncio
import base64
import logging
import ssl
from urllib.parse import urlparse

from cfg.config_model import NativeHostConfig
from log.logging_config import AppProcess

# Set up logging

logger = logging.getLogger("P2P.ProxyServer")


async def tunnel_data(client_reader, client_writer, remote_reader, remote_writer):
    """Bidirectional data forwarding between client and remote"""

    async def forward(reader, writer, name):
        try:
            while True:
                data = await reader.read(8192)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception as e:
            logger.debug(f"Tunnel {name} stopped: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass

    # Start both forwarding tasks
    await asyncio.gather(
        forward(client_reader, remote_writer, "client->remote"),
        forward(remote_reader, client_writer, "remote->client"),
        return_exceptions=True
    )


class MITMProxy:
    def __init__(self,
                 local_host='127.0.0.1',
                 local_port=1389,
                 remote_proxy_host=None,
                 remote_proxy_port=None,
                 remote_proxy_user=None,
                 remote_proxy_pass=None):
        self.local_host: str = local_host
        self.local_port: int = local_port

        if not all([remote_proxy_host, remote_proxy_port,
                    remote_proxy_user, remote_proxy_pass]):
            raise ValueError("Missing required configuration fields")
        self.remote_proxy_host: str = remote_proxy_host
        self.remote_proxy_port: int = remote_proxy_port
        self.remote_proxy_user: str = remote_proxy_user
        self.remote_proxy_pass: str = remote_proxy_pass

        # Create proxy authentication header
        credentials = f"{self.remote_proxy_user}:{self.remote_proxy_pass}"
        encoded_creds = base64.b64encode(credentials.encode()).decode()
        self.proxy_auth_header = f"Proxy-Authorization: Basic {encoded_creds}"

    async def handle_client(self, reader, writer):
        """Handle incoming client connection"""
        # client_addr = writer.get_extra_info('peername')
        # logger.debug(f"New connection from {client_addr}")

        try:
            # Read the initial request
            request_line = await reader.readline()

            if not request_line:
                logger.debug("Empty request, closing connection")
                writer.close()
                await writer.wait_closed()
                return

            request = request_line.decode('utf-8', errors='ignore').strip()

            # Parse request
            parts = request.split(' ')
            if len(parts) < 3:
                logger.error(f"Invalid request: {request}")
                writer.close()
                await writer.wait_closed()
                return

            method = parts[0]
            url = parts[1]
            version = parts[2]

            # Read headers
            headers = []
            while True:
                line = await reader.readline()
                if line == b'\r\n' or line == b'\n' or not line:
                    break
                headers.append(line.decode('utf-8', errors='ignore').strip())

            if method == 'CONNECT':
                # HTTPS tunneling
                await self.handle_connect(url, reader, writer)
            else:
                # HTTP request
                await self.handle_http(method, url, version, headers, reader, writer)

        except Exception as e:
            logger.error(f"Error handling client: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass

    async def handle_connect(self, url, client_reader, client_writer):
        """Handle CONNECT method for HTTPS tunneling"""
        try:
            # Parse destination
            if ':' in url:
                dest_host, dest_port = url.split(':')
                dest_port = int(dest_port)
            else:
                dest_host = url
                dest_port = 443

            # logger.info(f"CONNECT to {dest_host}:{dest_port} through proxy '{self.remote_proxy_host}'")

            # Connect to remote proxy
            remote_reader, remote_writer = await self.connect_to_remote_proxy()

            if not remote_writer:
                # Send error to client
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await client_writer.drain()
                return

            # Send CONNECT request to remote proxy
            connect_request = f"CONNECT {dest_host}:{dest_port} HTTP/1.1\r\n"
            connect_request += f"Host: {dest_host}:{dest_port}\r\n"
            connect_request += f"{self.proxy_auth_header}\r\n"
            connect_request += "\r\n"

            remote_writer.write(connect_request.encode())
            await remote_writer.drain()

            # Read response from remote proxy
            response_line = await remote_reader.readline()
            response = response_line.decode('utf-8', errors='ignore').strip()

            # Read response headers
            while True:
                line = await remote_reader.readline()
                if line == b'\r\n' or line == b'\n' or not line:
                    break

            if '200' in response:
                # Send 200 OK to client
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()

                # Start bidirectional forwarding
                await tunnel_data(client_reader, client_writer,
                                  remote_reader, remote_writer)
            else:
                logger.error(f"Remote proxy '{self.remote_proxy_host}' rejected CONNECT: {response}")
                client_writer.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\n".encode())
                await client_writer.drain()
                remote_writer.close()
                await remote_writer.wait_closed()

        except Exception as e:
            logger.error(f"Error in CONNECT handler: {e}")

    async def handle_http(self, method, url, version, headers, client_reader, client_writer):
        """Handle regular HTTP requests"""
        try:
            # Parse URL
            if url.startswith('http://'):
                parsed = urlparse(url)
                host = parsed.hostname
                port = parsed.port or 80
                path = parsed.path or '/'
                if parsed.query:
                    path += '?' + parsed.query
            else:
                # Relative URL, extract host from headers
                host = None
                for header in headers:
                    if header.lower().startswith('host:'):
                        host = header.split(':', 1)[1].strip()
                        if ':' in host:
                            host, port = host.split(':')
                            port = int(port)
                        else:
                            port = 80
                        break

                if not host:
                    logger.error("No host found in request")
                    client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                    await client_writer.drain()
                    return

                path = url

            logger.debug(f"Proxying HTTP request: {method} {host}:{port}{path}")

            # Connect to remote proxy
            remote_reader, remote_writer = await self.connect_to_remote_proxy()

            if not remote_writer:
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await client_writer.drain()
                return

            # Build request for remote proxy
            proxy_request = f"{method} http://{host}:{port}{path} {version}\r\n"
            proxy_request += f"Host: {host}\r\n"
            proxy_request += f"{self.proxy_auth_header}\r\n"

            # Add other headers (skip proxy-related ones)
            for header in headers:
                lower = header.lower()
                if not any(skip in lower for skip in ['proxy-', 'connection:']):
                    proxy_request += header + "\r\n"

            proxy_request += "Connection: close\r\n"
            proxy_request += "\r\n"

            remote_writer.write(proxy_request.encode())
            await remote_writer.drain()

            # Read any POST data
            if method in ['POST', 'PUT', 'PATCH']:
                # Find Content-Length
                content_length = 0
                for header in headers:
                    if header.lower().startswith('content-length:'):
                        content_length = int(header.split(':', 1)[1].strip())
                        break

                if content_length > 0:
                    body = await client_reader.read(content_length)
                    remote_writer.write(body)
                    await remote_writer.drain()

            # Forward response
            while True:
                data = await remote_reader.read(8192)
                if not data:
                    break
                client_writer.write(data)
                await client_writer.drain()

            remote_writer.close()
            await remote_writer.wait_closed()

        except Exception as e:
            logger.error(f"Error in HTTP handler: {e}")

    async def connect_to_remote_proxy(self):
        """Connect to the remote proxy server using TLS"""
        try:
            ssl_ctx = ssl.create_default_context(cafile="ca.crt")
            # automatski veruje Let's Encrypt CA

            reader, writer = await asyncio.open_connection(
                self.remote_proxy_host,
                self.remote_proxy_port,
                ssl=ssl_ctx,
                server_hostname=self.remote_proxy_host
            )

            logger.debug(
                f"TLS connection established to remote proxy "
                f"{self.remote_proxy_host}:{self.remote_proxy_port}"
            )

            return reader, writer

        except Exception as e:
            logger.error(
                f"Failed to connect to remote proxy "
                f"{self.remote_proxy_host}:{self.remote_proxy_port}: {e}"
            )
            return None, None

    async def start_server(self):
        """Start the proxy server"""
        server = await asyncio.start_server(
            self.handle_client,
            self.local_host,
            self.local_port
        )

        addr = server.sockets[0].getsockname()
        logger.info(
            f"MITM Proxy server started: listening on {addr[0]}:{addr[1]}, forwarding traffic through remote proxy {self.remote_proxy_host}:{self.remote_proxy_port}, auth_user={self.remote_proxy_user}")

        async with server:
            await server.serve_forever()


def run_proxy(remote_proxy_host: str,
              remote_proxy_port: int,
              proxy_username: str,
              proxy_password: str,
              config: NativeHostConfig):
    """
    Entry point for running proxy in multiprocessing.Process
    Takes cfg as arguments instead of reading from file

    Args:
        remote_proxy_host: Remote proxy hostname
        remote_proxy_port: Remote proxy port
        proxy_username: Proxy authentication username
        proxy_password: Proxy authentication password
        config: NativeHostConfig instance
    """
    try:
        # Set up logging for this process (multiprocessing doesn't inherit parent's logging cfg)
        from log.logging_config import setup_logging
        setup_logging(app_process=AppProcess.PROXY, config=config, credentials=(proxy_username, proxy_password))

        # Create proxy instance with provided cfg
        proxy = MITMProxy(local_host=config.local_proxy_host,
                          local_port=config.local_proxy_port,
                          remote_proxy_host=remote_proxy_host,
                          remote_proxy_port=remote_proxy_port,
                          remote_proxy_user=proxy_username,
                          remote_proxy_pass=proxy_password)

        logger.info(
            f"MITM proxy process initializing: remote_proxy={remote_proxy_host}:{remote_proxy_port}, local_listen={config.local_proxy_host}:{config.local_proxy_port}, auth_user={proxy_username}")

        # Run the proxy server
        asyncio.run(proxy.start_server())

    except KeyboardInterrupt:
        logger.info(
            f"MITM proxy process stopped by KeyboardInterrupt: remote_proxy={remote_proxy_host}:{remote_proxy_port}")
    except Exception as e:
        logger.error(f"MITM proxy process fatal error: remote_proxy={remote_proxy_host}:{remote_proxy_port}, error={e}")

