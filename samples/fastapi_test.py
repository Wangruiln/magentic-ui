import requests
import json

url = "http://9.135.155.89:7878/query"
payload = {"query": "wuthering waves top up？"}

with requests.post(url, json=payload, stream=True) as r:
    for line in r.iter_lines():
        if line:
            data = json.loads(line)
            
            if data['type'] == 'text':
                print(f"\n【{data['type']}】")
                print(data['content'])
            elif data['type'] == 'multimodal':
                if data['metadata'] is not None and data['metadata']['internal']=="yes":
                    continue
                else:
                    print(f"\n【{data['type']}】")
                    for part in data['content']:
                        if isinstance(part, str):
                            print(part)
                        else:
                            print(f"[图片] ")
                    if data["data"] is not None:
                        print(f"[图片数据: {data['data'][:20]}...]")
            else:
                print(data['content'])