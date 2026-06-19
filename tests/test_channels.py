from acta.channels import ChannelHub, TelegramChannel, WhatsAppChannel
from acta.channels.base import IncomingMessage


def test_hub_handles_message(orchestrator):
    hub = ChannelHub(orchestrator)
    msg = IncomingMessage(channel="telegram", sender_id="123", text="Привет, что ты умеешь?")
    answer = hub.handle(msg)
    assert isinstance(answer, str) and answer


def test_incoming_user_id_namespaced():
    msg = IncomingMessage(channel="whatsapp", sender_id="+972500000000", text="hi")
    assert msg.user_id == "whatsapp:+972500000000"


def test_telegram_parse_update(orchestrator):
    ch = TelegramChannel(ChannelHub(orchestrator))
    update = {
        "update_id": 1,
        "message": {"chat": {"id": 555}, "text": "hello", "from": {"username": "u"}},
    }
    msg = ch.parse_update(update)
    assert msg is not None
    assert msg.sender_id == "555" and msg.text == "hello"
    assert ch.parse_update({"update_id": 2}) is None


def test_whatsapp_verify_and_parse(orchestrator):
    ch = WhatsAppChannel(ChannelHub(orchestrator))
    ch.verify_token = "tok"
    assert ch.verify("subscribe", "tok", "challenge-123") == "challenge-123"
    assert ch.verify("subscribe", "wrong", "x") is None

    payload = {
        "entry": [
            {"changes": [{"value": {"messages": [
                {"type": "text", "from": "972500000000", "text": {"body": "שלום"}}
            ]}}]}
        ]
    }
    msgs = ch.parse_webhook(payload)
    assert len(msgs) == 1
    assert msgs[0].text == "שלום"
    assert msgs[0].channel == "whatsapp"


def test_channels_disabled_without_tokens(orchestrator):
    assert TelegramChannel(ChannelHub(orchestrator)).enabled is False
    assert WhatsAppChannel(ChannelHub(orchestrator)).enabled is False


def test_telegram_allowlist_blocks_unknown_sender(orchestrator, services):
    services.settings.telegram_allowed_chat_ids = ["111"]
    ch = TelegramChannel(ChannelHub(orchestrator), services.settings)
    msg = ch.parse_update(
        {
            "update_id": 1,
            "message": {"chat": {"id": 222}, "text": "hello", "from": {"username": "u"}},
        }
    )
    assert msg is not None
    assert ch._is_sender_allowed(msg.sender_id) is False


def test_whatsapp_allowlist_blocks_unknown_sender(orchestrator, services):
    services.settings.whatsapp_allowed_numbers = ["972511111111"]
    ch = WhatsAppChannel(ChannelHub(orchestrator), services.settings)
    assert ch._is_sender_allowed("972500000000") is False
