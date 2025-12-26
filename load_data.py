import requests
import json
import os

def fetch_problems():
    print("⬇️ Downloading real problem set from Codeforces...")
    try:
        # Official CF API
        url = "https://codeforces.com/api/problemset.problems"
        resp = requests.get(url, timeout=15)
        data = resp.json()

        if data['status'] != 'OK':
            raise Exception("CF API Failed")

        problems = []
        for p in data['result']['problems']:
            # We only want problems with a rating and a standard contest ID
            if 'rating' in p and 'contestId' in p and 'index' in p:
                problems.append({
                    "id": str(p['contestId']) + p['index'], # e.g. "1706A"
                    "contestId": str(p['contestId']),
                    "index": p['index'],
                    "name": p['name'],
                    "rating": p['rating']
                })
        
        # Save to file for C++ to read
        with open("cpp_backend/problems.json", "w") as f:
            json.dump(problems, f)
            
        print(f"✅ Successfully saved {len(problems)} real problems to cpp_backend/problems.json")

    except Exception as e:
        print(f"❌ Error fetching problems: {e}")

if __name__ == "__main__":
    fetch_problems()