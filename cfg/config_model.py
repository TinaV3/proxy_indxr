from dataclasses import dataclass

@dataclass
class NativeHostConfig:
    local_proxy_host: str = "127.0.0.1"
    local_proxy_port: int = 1389
