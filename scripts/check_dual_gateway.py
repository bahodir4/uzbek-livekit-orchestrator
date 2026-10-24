#!/usr/bin/env python3
"""GPU integration smoke test: authenticate, check both models, measure real PCM TTFA.

Examples:
 python scripts/check_dual_gateway.py --both
 python scripts/check_dual_gateway.py --model navoiy --save-wav /tmp/navoiy.wav

Requires reachable gateway, TTS_API_KEY and real backends; never uses fake audio.
"""
from __future__ import annotations
import argparse
import asyncio
import io
import os
import sys
import time
import wave
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voice_agent.configuration import load


async def main() -> int:
    load_dotenv()
    args = argparse.ArgumentParser()
    args.add_argument('--model', choices=('sayro', 'navoiy'), default='sayro')
    args.add_argument('--both', action='store_true')
    args.add_argument('--text', default='Assalomu alaykum! Sizga qanday yordam bera olaman?')
    args.add_argument('--save-wav', type=Path)
    p = args.parse_args()
    conf = load()['providers']['tts']['dual_gateway']
    key = os.getenv(conf['api_key_env'], '')
    if not key:
        print(f"Missing {conf['api_key_env']}", file=sys.stderr)
        return 2
    url = conf['url'].rstrip('/')
    h = {'Authorization': 'Bearer '+key}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        async with session.get(url+'/health/ready',headers=h) as r:
            print('Ready status:',r.status,await r.text())
            if r.status!=200: return 1
        async with session.get(url+'/v1/models',headers=h) as r:
            print('Models:',r.status,await r.text())
            if r.status!=200: return 1
        for model in (('sayro','navoiy') if p.both else (p.model,)):
            t0=time.perf_counter();total=0;first=None;audio=bytearray();odd=b''
            async with session.post(url+'/v1/tts/stream',headers=h,json={'model':model,'text':p.text}) as r:
                if r.status!=200:
                    print(f'{model}: HTTP {r.status} ({(await r.text())[:250]})')
                    return 1
                assert r.headers.get('X-Audio-Sample-Rate')=='24000', 'Bad sample rate'
                assert r.headers.get('X-Audio-Channels')=='1', 'Bad channel count'
                assert r.headers.get('X-Audio-Format')=='pcm_s16le', 'Bad audio format'
                async for chunk in r.content.iter_chunked(3840):
                    if first is None: first=round((time.perf_counter()-t0)*1000)
                    data=odd+chunk
                    even=len(data)&~1
                    audio.extend(data[:even]);odd=data[even:]
                    total+=even
                assert not odd and total>0, 'Empty/misaligned PCM stream'
            elapsed=round((time.perf_counter()-t0)*1000)
            print(f'{model}: first_byte={first}ms total={elapsed}ms audio={total/48000:.2f}s bytes={total}')
            if p.save_wav:
                target=p.save_wav if not p.both else p.save_wav.with_name(p.save_wav.stem+'-'+model+p.save_wav.suffix)
                with wave.open(str(target),'wb') as w:
                    w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(audio)
                print('WAV:',target)
    return 0


if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
