"""One-shot prove deposit dogfood (run from willow-bot checkout)."""
from willow_bot.deposits import deposit_from_check_run_payload, deposits_jsonl

payload = {
    "action": "completed",
    "sender": {"type": "Bot"},
    "repository": {"full_name": "willow-memory/willows-grove"},
    "check_run": {
        "id": 900001,
        "name": "prove-deposit",
        "head_sha": "prove0deadbeef",
        "status": "completed",
        "conclusion": "failure",
        "html_url": "https://example.invalid/check/900001",
        "pull_requests": [],
    },
}
print(deposit_from_check_run_payload(payload))
print("tail:", deposits_jsonl().read_text().strip().splitlines()[-1][:240])
