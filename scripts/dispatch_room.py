"""Explicitly dispatch our named worker to an existing or new LiveKit room.

Usage: python scripts/dispatch_room.py --room demo-room
Requires LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET in the environment.
"""
from __future__ import annotations
import argparse
import asyncio
import os

from dotenv import load_dotenv
load_dotenv()
from livekit import api


async def run(room: str, agent_name: str):
    async with api.LiveKitAPI() as lkapi:
        job = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(room=room, agent_name=agent_name)
        )
        print(f"Dispatched agent={agent_name} room={room} job={job}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--room', required=True)
    parser.add_argument('--agent-name', default='uzbek-voice-agent')
    args = parser.parse_args()
    asyncio.run(run(args.room, args.agent_name))


if __name__ == '__main__':
    main()
