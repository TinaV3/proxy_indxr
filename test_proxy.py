from proxy_server import run_proxy
from cfg.config_model import NativeHostConfig

config = NativeHostConfig(
    local_proxy_host="127.0.0.1",
    local_proxy_port=1389,
)

run_proxy(
    remote_proxy_host="px2.malkier.rs",
    remote_proxy_port=3130,
    proxy_username="cuki",
    proxy_password="cuki",
    config=config
)
