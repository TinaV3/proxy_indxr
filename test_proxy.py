from proxy_server import run_proxy
from cfg.config_model import NativeHostConfig

config = NativeHostConfig(
    local_proxy_host="127.0.0.1",
    local_proxy_port=1389,
)

run_proxy(
    remote_proxy_host="de-1.px.indexr.ai",
    remote_proxy_port=3129,
    proxy_username="proxy-checker",
    proxy_password="taisharmalkier",
    config=config
)
