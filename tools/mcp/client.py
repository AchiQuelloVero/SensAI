from fastmcp import Client
from fastmcp.client.transports import ClientTransport


class MCPClient(Client):
    def __init__(self, server_id: str, connection: str | ClientTransport):
        super().__init__(connection)
        self.server_id = server_id
