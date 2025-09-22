import requests
import json

url = "http://9.135.155.89:8005/query"
payload = {"url": "https://mc.kurogames.com/main/news/detail/3211", "query": "鸣潮2.6版本"}

with requests.post(url, json=payload, stream=True) as r:
    for line in r.iter_lines():
        if line:
            print(line.decode('utf-8'))

