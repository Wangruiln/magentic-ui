import requests
import json

url = "http://9.135.155.89:8005/query"
payload = {"url": "https://www.miyoushe.com/ys/article/68366449", "query": ""}

with requests.post(url, json=payload, stream=True) as r:
    for line in r.iter_lines():
        if line:
            print(line.decode('utf-8'))

