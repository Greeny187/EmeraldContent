from telegram.request import HTTPXRequest
try:
    from httpx import Limits
except Exception:
    Limits = None

def create_httpx_request(pool_size=100, timeouts=(30.0, 30.0, 30.0, 3.0)):
    """Erzeugt einen HTTPXRequest und unterstützt beide PTB-Signaturen."""
    read, write, connect, pool_timeout = timeouts
    # 1) Neuere PTB/httpx: mit pool_limits
    if Limits is not None:
        try:
            return HTTPXRequest(
                pool_limits=Limits(max_connections=pool_size, max_keepalive_connections=20),
                read_timeout=read, write_timeout=write, connect_timeout=connect, pool_timeout=pool_timeout,
            )
        except TypeError:
            pass
    # 2) Ältere PTB: connection_pool_size
    return HTTPXRequest(
        connection_pool_size=pool_size,
        read_timeout=read, write_timeout=write, connect_timeout=connect, pool_timeout=pool_timeout,
    )