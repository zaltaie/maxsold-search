import requests
import re

html = requests.get('https://maxsold.com').text
scripts = re.findall(r'<script src="([^"]+)"', html)

for script in scripts:
    url = script if script.startswith('http') else 'https://maxsold.com' + script
    js = requests.get(url).text
    if 'algolia' in js.lower():
        print(f"FOUND IN: {url}")
        
        algoliaAppId = re.findall(r'algolia(?:ApplicationId|AppId)(?:"|\':)?"([^"]+)"', js, re.IGNORECASE)
        algoliaKey = re.findall(r'algolia(?:SearchAPIKey|ApiKey|Key)(?:"|\':)?"([^"]+)"', js, re.IGNORECASE)
        print(f"APP ID: {algoliaAppId}")
        print(f"KEY: {algoliaKey}")
