from hallucination.config import ACTIONS


def hallucination_view(config: dict) -> dict:
    return {
        "enabled": config["enabled"],
        "confidence_threshold": config["confidence_threshold"],
        "action": config["action"],
        "actions": ACTIONS,
    }
