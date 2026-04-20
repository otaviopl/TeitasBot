import requests
import random


def get_motivational_message(project_logger):
    project_logger.info("Getting nice message...")
    try:
        all_messages = requests.get("https://type.fit/api/quotes", timeout=10)
        all_messages.raise_for_status()
        return random.choice(all_messages.json())
    except requests.RequestException:
        project_logger.warning("Failed to fetch motivational message. Using fallback.")
        return {"text": "Keep moving forward, one task at a time."}
