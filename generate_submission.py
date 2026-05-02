import requests
import json
import time
import os

# Script to generate the canonical submission.jsonl required for magicpin challenge
# It hits our local FastAPI server and collects all responses

BASE_URL = "http://localhost:8000"
SUBMISSION_FILE = "submission.jsonl"

def generate():
    print(" Starting Submission Generation...")
    
    # 1. Load Dataset
    try:
        with open("challenge-data/dataset/triggers_seed.json", "r") as f:
            triggers_list = json.load(f).get("triggers", [])
        with open("challenge-data/dataset/merchants_seed.json", "r") as f:
            merchants_list = json.load(f).get("merchants", [])
        # Load one category for demo
        with open("challenge-data/dataset/categories/restaurants.json", "r") as f:
            cat_data = json.load(f)
    except Exception as e:
        print(f"Error loading dataset: {e}")
        return

    # 2. Push Context to Server
    print("Pushing context to server...")
    for m in merchants_list:
        requests.post(f"{BASE_URL}/v1/context", json={
            "scope": "merchant", "context_id": m["merchant_id"], "payload": m
        })
    for t in triggers_list:
        requests.post(f"{BASE_URL}/v1/context", json={
            "scope": "trigger", "context_id": t["id"], "payload": t
        })
    
    # Load all categories
    cat_dir = "challenge-data/dataset/categories"
    for cat_file in os.listdir(cat_dir):
        if cat_file.endswith(".json"):
            with open(os.path.join(cat_dir, cat_file), "r") as f:
                cat_data = json.load(f)
                cat_slug = cat_file.replace(".json", "")
                requests.post(f"{BASE_URL}/v1/context", json={
                    "scope": "category", "context_id": cat_slug, "payload": cat_data
                })

    # 3. Collect responses for every trigger
    output_lines = []
    print(f"Found {len(triggers_list)} triggers. Processing...")
    
    tids = [t["id"] for t in triggers_list]
    for i in range(0, len(tids), 5):
        batch = tids[i:i+5]
        try:
            resp = requests.post(f"{BASE_URL}/v1/tick", json={
                "now": "2026-05-02T10:00:00Z",
                "available_triggers": batch
            })
            data = resp.json()
            actions = data.get("actions", [])
            for action in actions:
                output_lines.append(json.dumps(action))
            print(f"  Processed batch {i//5 + 1}...")
        except Exception as e:
            print(f"  Batch {i//5 + 1} failed: {e}")

    # 3. Write to submission.jsonl
    with open(SUBMISSION_FILE, "w") as f:
        for line in output_lines:
            f.write(line + "\n")
            
    print(f" DONE! Created {SUBMISSION_FILE} with {len(output_lines)} entries.")
    print("Submit this file to the magicpin platform.")

if __name__ == "__main__":
    generate()
