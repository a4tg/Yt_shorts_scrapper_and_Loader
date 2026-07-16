import asyncio

from realtime_service import ProjectEventBroker


def test_project_event_broker_delivers_and_unsubscribes() -> None:
    async def scenario() -> None:
        broker = ProjectEventBroker(queue_size=2)
        async with broker.subscribe("project-a") as queue:
            broker.publish("project-b", {"type": "ignored"})
            broker.publish("project-a", {"type": "message.created", "message_id": "m1"})
            event = await asyncio.wait_for(queue.get(), timeout=1)
            assert event == {"type": "message.created", "message_id": "m1"}
        assert "project-a" not in broker._subscribers

    asyncio.run(scenario())


def test_project_event_broker_drops_oldest_event_when_queue_is_full() -> None:
    async def scenario() -> None:
        broker = ProjectEventBroker(queue_size=1)
        async with broker.subscribe("project-a") as queue:
            broker.publish("project-a", {"sequence": 1})
            await asyncio.sleep(0)
            broker.publish("project-a", {"sequence": 2})
            await asyncio.sleep(0)
            assert await asyncio.wait_for(queue.get(), timeout=1) == {"sequence": 2}

    asyncio.run(scenario())
