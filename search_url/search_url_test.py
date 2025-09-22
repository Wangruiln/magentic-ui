import requests
import json

url = "http://9.135.155.89:7777/query"
payload = {"query": "鸣潮2.6版本更新了什么内容？"}

with requests.post(url, json=payload, stream=True) as r:
    for line in r.iter_lines():
        if line:
            print(line.decode('utf-8'))

