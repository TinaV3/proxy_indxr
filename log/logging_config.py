import logging

# Stub klase za AppProcess
class AppProcess:
    PROXY = "proxy"
    NATIVE_HOST = "native_host"

# Funkcija za setup logging
def setup_logging(app_process, config=None, credentials=None):
    """
    Minimalna konfiguracija logging-a za testiranje proxy-a.
    """
    logging.basicConfig(
        level=logging.DEBUG,
        format=f"[{app_process}] %(asctime)s %(levelname)s: %(message)s"
    )
    logging.debug("Logging initialized")
