from telegram.request import HTTPXRequest

def create_request_with_increased_pool():
    """
    Erstellt eine HTTPXRequest-Instanz mit erhöhten Pool-Limits
    """
    return HTTPXRequest(
        connection_pool_size=100,  # Standard: 25
        read_timeout=30.0,         # Standard: 5.0
        write_timeout=30.0,        # Standard: 5.0
        connect_timeout=30.0,      # Standard: 5.0
        pool_timeout=3.0,          # Standard: 1.0
    )