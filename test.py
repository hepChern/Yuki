import os
urls = [
        'https://www.bing.com',
        'https://reana.cern.ch',
        ]

# token = "[my token]"
token = "0IlRUvH0QB26iMdqjuJe-g"

for url in urls:
    os.environ["REANA_SERVER_URL"] = url
    from reana_client.api import client
    print("url: ", url)
    print(client.ping(token))


