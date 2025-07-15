"""
Utility functions for Yuki server.
"""
import os


def ping(url, token):
    """Ping a REANA server to check connectivity."""
    from reana_client.api import client
    from reana_commons.api_client import BaseAPIClient
    os.environ["REANA_SERVER_URL"] = url
    BaseAPIClient("reana-server")
    return client.ping(token)
