import pytest

from collector.hass_ws import HassAuthError, HassClient, HassCommandError, HassConnectionError


async def connected(fake) -> HassClient:
    client = HassClient(fake.url, "secret")
    await client.connect()
    return client


async def test_command_roundtrip(fake_hass):
    fake_hass.responses["demo/ping"] = {"pong": 1}
    client = await connected(fake_hass)
    try:
        assert await client.command("demo/ping") == {"pong": 1}
        assert fake_hass.received[-1]["type"] == "demo/ping"
    finally:
        await client.close()


async def test_wrong_token_raises_auth_error(fake_hass):
    client = HassClient(fake_hass.url, "wrong")
    with pytest.raises(HassAuthError):
        await client.connect()


async def test_unreachable_server_raises_connection_error():
    client = HassClient("ws://127.0.0.1:9/api/websocket", "x")
    with pytest.raises(HassConnectionError):
        await client.connect()


async def test_failed_command_raises_command_error(fake_hass):
    client = await connected(fake_hass)
    try:
        with pytest.raises(HassCommandError, match="unknown_command"):
            await client.command("demo/unknown")
    finally:
        await client.close()


async def test_subscribe_yields_events_in_order(fake_hass):
    fake_hass.events = [{"n": 1}, {"n": 2}]
    client = await connected(fake_hass)
    try:
        stream = await client.subscribe("state_changed")
        assert [await stream.__anext__(), await stream.__anext__()] == [{"n": 1}, {"n": 2}]
    finally:
        await client.close()


async def test_batched_messages_are_dispatched(fake_hass):
    fake_hass.events = [{"n": 1}, {"n": 2}, {"n": 3}]
    fake_hass.batch_events = True
    client = await connected(fake_hass)
    try:
        stream = await client.subscribe("state_changed")
        assert [await stream.__anext__() for _ in range(3)] == [{"n": 1}, {"n": 2}, {"n": 3}]
    finally:
        await client.close()


async def test_closed_connection_ends_stream_with_error(fake_hass):
    fake_hass.events = [{"n": 1}]
    fake_hass.close_after_events = True
    client = await connected(fake_hass)
    try:
        stream = await client.subscribe("state_changed")
        assert await stream.__anext__() == {"n": 1}
        with pytest.raises(HassConnectionError):
            await stream.__anext__()
        with pytest.raises(HassConnectionError):
            await client.command("demo/ping")
    finally:
        await client.close()


async def test_seconds_since_last_message_is_small_after_traffic(fake_hass):
    fake_hass.responses["demo/ping"] = None
    client = await connected(fake_hass)
    try:
        await client.command("demo/ping")
        assert client.seconds_since_last_message() < 2
    finally:
        await client.close()
