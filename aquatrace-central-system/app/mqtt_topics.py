"""Topic templates from protocol spec §2.1. Duplicated in node/protocol/mqtt_topics.py."""

TOPIC_REGISTER_SUB = "nodes/+/register"
TOPIC_SUMMARY_SUB = "nodes/+/summary"
TOPIC_ALERT_SUB = "nodes/+/alert/+"
TOPIC_STATUS_SUB = "nodes/+/status"
TOPIC_MODEL_ACK_SUB = "nodes/+/model/ack"


def topic_register(node_id: str) -> str:
    return f"nodes/{node_id}/register"


def topic_register_ack(node_id: str) -> str:
    return f"cc/{node_id}/register/ack"


def topic_summary(node_id: str) -> str:
    return f"nodes/{node_id}/summary"


def topic_alert(node_id: str, contaminant_id: str) -> str:
    return f"nodes/{node_id}/alert/{contaminant_id}"


def topic_status(node_id: str) -> str:
    return f"nodes/{node_id}/status"


def topic_model_update(node_id: str) -> str:
    return f"cc/{node_id}/model/update"


def topic_model_ack(node_id: str) -> str:
    return f"nodes/{node_id}/model/ack"
