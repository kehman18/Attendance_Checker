import requests

API_KEY = "bP10Y37E7s_NLHUDSq97a"

url = "https://api.scripture.api.bible/v1/bibles"
headers = {"api-key": API_KEY}

response = requests.get(url, headers=headers)

if response.status_code == 200:
    bibles = response.json().get("data", [])
    print(f"Total Bibles available: {len(bibles)}\n")
    for bible in bibles:
        name = bible.get("name")
        bible_id = bible.get("id")
        abbreviation = bible.get("abbreviation", "")
        # Print all or search specifically for NKJV/English Bibles
        print(f"ID: {bible_id}  |  Name: {name} ({abbreviation})")
else:
    print(f"Error {response.status_code}: {response.text}")