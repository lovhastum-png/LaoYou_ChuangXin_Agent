"""Cross-role HTTP/WebSocket acceptance checks against a running demo instance.

Creates auditable demo events and removes the temporary reminder. Prefer a separate
acceptance database when running repeatedly. No external agency is contacted.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]


class Suite:
    def __init__(self, base: str):
        self.base = base.rstrip('/')
        self.clients: dict[str, httpx.Client] = {}
        self.tokens: dict[str, str] = {}
        self.results: list[dict] = []
        self.elder = ''

    def request(self, role: str, method: str, path: str, data=None, expected=200):
        response = self.clients[role].request(method, self.base + '/api' + path, json=data)
        assert response.status_code == expected, f'{method} {path}: expected {expected}, got {response.status_code}: {response.text[:350]}'
        return response.json()

    def passed(self, name: str):
        self.results.append({'check': name, 'status': 'passed'})
        print('PASS ' + name, flush=True)

    def login(self):
        for role in ['elder', 'child', 'community', 'admin']:
            client = httpx.Client(timeout=15)
            self.clients[role] = client
            response = self.request(role, 'POST', '/auth/login', {'username': role, 'password': 'Laoyou123!'})
            self.tokens[role] = response['token']
            client.headers['Authorization'] = 'Bearer ' + response['token']
            assert response['user']['role'] == role
        elders = self.request('child', 'GET', '/elders')
        assert elders
        self.elder = elders[0]['id']
        self.passed('四种角色登录及家庭列表')

    def observation(self, kind, **kwargs):
        payload = {'kind': kind, 'source': 'simulated', 'idempotency_key': uuid.uuid4().hex, **kwargs}
        return self.request('child', 'POST', f'/elders/{self.elder}/observations', payload)

    def run(self):
        self.login()
        e = self.elder
        dashboard = self.request('elder', 'GET', f'/elders/{e}/dashboard')
        assert {'elder', 'weather', 'reminders', 'broadcasts', 'active_event_count'} <= dashboard.keys()
        assert dashboard['weather']['source'] in {'live', 'simulated'}
        self.passed('总控数据与天气来源')

        self.request('elder', 'POST', f'/elders/{e}/observations', {'kind': 'fall'}, expected=403)
        self.request('community', 'PATCH', f'/elders/{e}/settings', {'voice_enabled': False}, expected=403)
        self.request('community', 'POST', f'/elders/{e}/assistant', {'text': '每天八点提醒我吃药'}, expected=403)
        self.request('community', 'POST', f'/elders/{e}/assistant', {'text': '关闭摄像头'}, expected=403)
        self.passed('老人不可伪造观测、社区不可修改老人设置')

        reminder = self.request('child', 'POST', f'/elders/{e}/reminders', {
            'title': '联通测试提醒', 'medicine': '演示药物', 'dose': '用户填写的演示剂量', 'time': '20:31'})
        try:
            listing = self.request('elder', 'GET', f'/elders/{e}/reminders')
            assert any(item['id'] == reminder['id'] for item in listing)
            changed = self.request('child', 'PATCH', f'/reminders/{reminder["id"]}', {'time': '20:32'})
            assert changed['time'] == '20:32'
            self.passed('子女创建提醒、老人端读取和修改')
        finally:
            self.request('child', 'DELETE', f'/reminders/{reminder["id"]}')

        proposal = self.request('elder', 'POST', f'/elders/{e}/assistant', {'text': '每天晚上八点半提醒我吃演示药物'})
        assert proposal['action'] == 'reminder_proposal'
        assert proposal['proposal']['time'] == '20:30'
        assert proposal['confirm_token']
        before = {item['id'] for item in self.request('elder', 'GET', f'/elders/{e}/reminders')}
        result = self.request('elder', 'POST', f'/elders/{e}/assistant', {'text': '确认', 'confirm_token': proposal['confirm_token']})
        assert result['action'] == 'reminder_created'
        after = self.request('elder', 'GET', f'/elders/{e}/reminders')
        new_ids = [item['id'] for item in after if item['id'] not in before]
        assert len(new_ids) == 1
        for reminder_id in new_ids:
            self.request('elder', 'DELETE', f'/reminders/{reminder_id}')
        self.passed('自然语言半点提醒、服务端确认后才写入')

        self.request('elder', 'PATCH', f'/elders/{e}/settings', {'camera_enabled': True})
        self.request('elder', 'PATCH', f'/elders/{e}/settings', {'camera_enabled': False}, expected=422)
        switched = self.request('elder', 'PATCH', f'/elders/{e}/settings', {'camera_enabled': False, 'confirm_camera_off': True})
        assert switched['camera_enabled'] is False
        blocked = self.clients['child'].get(self.base + f'/api/elders/{e}/snapshot')
        assert blocked.status_code == 409
        self.request('elder', 'PATCH', f'/elders/{e}/settings', {'camera_enabled': True})
        self.passed('摄像头关闭二次确认与关闭后的访问阻断')

        fall_payload = {'kind': 'fall', 'source': 'simulated', 'idempotency_key': uuid.uuid4().hex}
        fallen = self.request('child', 'POST', f'/elders/{e}/observations', fall_payload)
        duplicate = self.request('child', 'POST', f'/elders/{e}/observations', fall_payload)
        assert fallen['observation']['id'] == duplicate['observation']['id']
        assert len(fallen['events']) == len(duplicate['events']) == 1
        event_id = fallen['events'][0]['id']
        detail = self.request('community', 'GET', f'/events/{event_id}')
        assert {n['target'] for n in detail['notifications']} == {'child', 'community'}
        assert all(n['simulated'] and n['sent_at'] for n in detail['notifications'])
        self.request('elder', 'POST', f'/events/{event_id}/actions', {'action': 'acknowledge'}, expected=403)
        self.request('child', 'POST', f'/events/{event_id}/actions', {'action': 'resolve', 'note': '不应跳步'}, expected=409)
        for action in ['acknowledge', 'start', 'resolve']:
            detail = self.request('community', 'POST', f'/events/{event_id}/actions', {'action': action, 'note': '联通测试：已确认老人安全'})
        assert detail['status'] == 'resolved'
        assert all(n['at'] and n['actor'] for n in detail['timeline'])
        self.passed('跌倒幂等、通知目标、权限、不可跳步及处置时间线')

        away = self.observation('away', duration_minutes=9999)['events'][0]
        self.request('community', 'POST', f'/events/{away["id"]}/actions', {'action': 'correct', 'note': '旅游'}, expected=403)
        corrected = self.request('child', 'POST', f'/events/{away["id"]}/actions', {'action': 'correct', 'note': '联通测试：老人外出旅游，家属已核实'})
        assert corrected['status'] == 'false_positive'
        assert any('旅游' in item['detail'] for item in corrected['timeline'])
        self.passed('未归误报由子女修正并保留原因')

        self.request('admin', 'PATCH', '/demo/notifications', {'fail_targets': ['community']})
        try:
            medical = self.observation('heart_rate', value=180)['events'][0]
            detail = self.request('child', 'GET', f'/events/{medical["id"]}')
            assert {n['target'] for n in detail['notifications']} == {'child', 'community', 'emergency'}
            failed = next(n for n in detail['notifications'] if n['target'] == 'community')
            assert failed['status'] == 'failed'
            with ThreadPoolExecutor(max_workers=2) as pool:
                retries = list(pool.map(lambda _: self.request('admin', 'POST', f'/notifications/{failed["id"]}/retry'), range(2)))
            assert sorted(item['attempts'] for item in retries) == [2, 3]
            self.request('admin', 'PATCH', '/demo/notifications', {'fail_targets': []})
            sent = self.request('admin', 'POST', f'/notifications/{failed["id"]}/retry')
            assert sent['status'] == 'sent' and sent['attempts'] == 4
            acknowledged = self.request('community', 'POST', f'/notifications/{failed["id"]}/ack')
            assert acknowledged['acknowledged_at']
            self.passed('医疗异常三方模拟通知、失败重试与接收确认')
        finally:
            self.request('admin', 'PATCH', '/demo/notifications', {'fail_targets': []})

        detail = self.request('community', 'POST', f'/events/{medical["id"]}/actions', {'action': 'community_unavailable', 'note': '联通测试：社区暂无陪同人员'})
        escort = detail['escort']
        assert escort and escort['simulated'] and escort['status'] == 'requested'
        for action in ['accept', 'complete']:
            escort = self.request('community', 'POST', f'/escorts/{escort["id"]}/actions', {'action': action, 'note': '联通测试：模拟平台服务已完成'})
        assert escort['accepted_at'] and escort['completed_at']
        self.passed('社区无法协助转放心医及陪诊节点')

        call = self.request('elder', 'POST', f'/elders/{e}/calls')
        self.request('community', 'POST', f'/calls/{call["id"]}/actions', {'action': 'answer'}, expected=403)
        self.request('child', 'POST', f'/calls/{call["id"]}/actions', {'action': 'answer'})
        asyncio.run(self.signaling(call['id']))
        ended = self.request('elder', 'POST', f'/calls/{call["id"]}/actions', {'action': 'end'})
        assert ended['ended_at']
        self.passed('视频呼叫权限、双向WebSocket信令与挂断记录')

        self.request('child', 'POST', '/auth/logout')
        self.request('child', 'GET', '/auth/me', expected=401)
        self.passed('退出撤销会话')

    async def signaling(self, call_id: str):
        wsbase = self.base.replace('http://', 'ws://').replace('https://', 'wss://')
        async with connect(wsbase + f'/ws/calls/{call_id}?token={self.tokens["elder"]}') as elder_socket:
            async with connect(wsbase + f'/ws/calls/{call_id}?token={self.tokens["child"]}') as child_socket:
                for participant in [elder_socket, child_socket]:
                    ready = json.loads(await asyncio.wait_for(participant.recv(), timeout=5))
                    assert ready['type'] == 'peer_ready'
                offer = {'type': 'offer', 'payload': {'type': 'offer', 'sdp': 'acceptance-signaling-probe'}}
                await elder_socket.send(json.dumps(offer))
                result = json.loads(await asyncio.wait_for(child_socket.recv(), timeout=5))
                assert result == offer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True, help='Prefer dedicated acceptance server such as http://127.0.0.1:8001')
    args = parser.parse_args()
    suite = Suite(args.base)
    passed = False
    try:
        suite.run()
        passed = True
    except Exception as exc:
        suite.results.append({'check': '执行中断', 'status': 'failed', 'error': str(exc)})
        print('FAIL ' + str(exc), file=sys.stderr)
    finally:
        for client in suite.clients.values():
            client.close()
        output = ROOT/'tmp/api-smoke-results.json'
        output.write_text(json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'base': args.base,
                                      'passed': passed, 'checks': suite.results}, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
