"""Private operator verification policies. Never serialize policies to clients."""
import hashlib
import json
import secrets


class ServiceVerifier:
    def __init__(self, policy, model):
        self.interval = policy.get('interval_seconds', 900)
        self.timeout = policy.get('timeout_seconds', 90)
        self.cooldown = policy.get('cooldown_seconds', 300)
        if any(type(x) is not int for x in (self.interval,self.timeout,self.cooldown)) or not (
                60 <= self.interval <= 3600 and 5 <= self.timeout <= 180 and 60 <= self.cooldown <= 3600):
            raise ValueError('Invalid service verification timing')
        checks = policy.get('checks')
        if not isinstance(checks,list) or not 1 <= len(checks) <= 64:
            raise ValueError('Private verification fixtures are required')
        for check in checks:
            request=check['request']; hashes=check['accepted_sha256']
            if request.get('model')!=model or type(request.get('max_tokens')) is not int or not 1<=request['max_tokens']<=128:
                raise ValueError('Invalid verification request')
            messages=request.get('messages')
            if not isinstance(messages,list) or not 1<=len(messages)<=8 or len(json.dumps(request))>16384:
                raise ValueError('Invalid verification input')
            if any(not isinstance(m,dict) or m.get('role') not in ('system','user','assistant','tool') or
                   not (isinstance(m.get('content'),str) or (m.get('role')=='assistant' and m.get('tool_calls'))) for m in messages):
                raise ValueError('Invalid verification messages')
            if not isinstance(hashes,list) or not 1<=len(hashes)<=8 or any(
                    not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h) for h in hashes):
                raise ValueError('Invalid verification references')
        self.checks=checks

    def choose(self):
        return secrets.choice(self.checks)

    @staticmethod
    def digest(result):
        value={k:result[k] for k in ('text','tool_calls','finish_reason') if k in result}
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
