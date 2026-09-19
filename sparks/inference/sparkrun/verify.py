#!/usr/bin/env python3
"""Bounded API acceptance checks for the deployed Mia GLM recipe."""
import argparse
import base64
import json
import struct
import time
import urllib.request
import zlib
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://10.32.21.31:8888')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    results = []

    def chat(name, messages, check, **options):
        body = dict(model='GLM-5.3-Flash-EXL3', messages=messages,
                    temperature=0, max_tokens=512,
                    chat_template_kwargs={'enable_thinking': False})
        body.update(options)
        start = time.monotonic()
        request = urllib.request.Request(
            args.url.rstrip('/') + '/v1/chat/completions',
            data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.load(response)
        message = data['choices'][0]['message']
        record = dict(name=name, seconds=round(time.monotonic()-start, 3),
                      passed=bool(check(message)), response=data)
        results.append(record)
        Path(args.out).write_text(json.dumps(results, indent=2) + '\n')
        print(name, 'PASS' if record['passed'] else 'FAIL', record['seconds'], flush=True)
        return message

    def user(text):
        return {'role': 'user', 'content': text}

    chat('basic', [user('What is 17 multiplied by 19? Reply with only the integer.')],
         lambda m: m.get('content', '').strip() == '323')
    messages = [user('Call get_weather for Paris, France.')]
    tool = {'type': 'function', 'function': {
        'name': 'get_weather', 'description': 'Look up weather for a city.',
        'parameters': {'type': 'object', 'properties': {'city': {'type': 'string'}},
                       'required': ['city'], 'additionalProperties': False}}}

    def valid_tool(m):
        calls = m.get('tool_calls') or []
        return (len(calls) == 1 and calls[0]['function']['name'] == 'get_weather'
                and 'Paris' in json.loads(calls[0]['function']['arguments']).get('city', ''))

    answer = chat('tool_call', messages, valid_tool, tools=[tool], tool_choice='auto')
    if valid_tool(answer):
        messages += [answer, {'role': 'tool', 'tool_call_id': answer['tool_calls'][0]['id'],
                              'content': '{"temperature_c":18,"conditions":"sunny"}'}]
        chat('tool_result', messages, lambda m: '18' in m.get('content', ''), tools=[tool])
    chat('json_schema', [user('Return the sum of 17 and 19 in the requested JSON schema.')],
         lambda m: json.loads(m['content']) == {'sum': 36},
         response_format={'type': 'json_schema', 'json_schema': {
             'name': 'sum_result', 'strict': True,
             'schema': {'type': 'object', 'properties': {'sum': {'type': 'integer'}},
                        'required': ['sum'], 'additionalProperties': False}}})
    chat('reasoning', [user('What is 17 multiplied by 19? Give the integer answer.')],
         lambda m: '323' in (m.get('content') or '') and bool(m.get('reasoning') or m.get('reasoning_content')),
         max_tokens=1024, chat_template_kwargs={'enable_thinking': True})

    # A generated solid red PNG keeps the image check independent of external URLs.
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 64, 64, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff\0\0' * 64) * 64)) + chunk(b'IEND', b''))
    chat('vision', [user([
        {'type': 'text', 'text': 'What color fills this image? Reply with one color word.'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(png).decode()}}
    ])], lambda m: 'red' in m.get('content', '').lower())
    if not all(r['passed'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
