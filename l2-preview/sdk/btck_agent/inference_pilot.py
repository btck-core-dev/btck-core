"""Authenticated client for the private, development-credit inference pilot."""
import hashlib
import json
import urllib.request
import urllib.error
from urllib.parse import quote


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class PilotClient:
    def __init__(self, base_url, token, trusted_receipt_pubkey, *, timeout=135):
        self.base_url=base_url.rstrip('/');self.token=token
        self.trusted_receipt_pubkey=trusted_receipt_pubkey;self.timeout=timeout
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectRedirects())

    def request(self, method, path, body=None, key=None):
        headers={'Authorization':'Bearer '+self.token}
        data=None
        if body is not None:
            data=json.dumps(body,ensure_ascii=False).encode();headers['Content-Type']='application/json'
        if key is not None:headers['Idempotency-Key']=key
        request=urllib.request.Request(self.base_url+path,data,headers,method=method)
        with self.opener.open(request,timeout=self.timeout) as response:
            return json.load(response),dict(response.headers)

    def complete(self, *, model, messages, idempotency_key, max_tokens=32, max_charge_satk=1_000_000, tools=None, tool_choice=None):
        body={'model':model,'messages':messages,'max_tokens':max_tokens,'max_charge_satk':max_charge_satk}
        if tools is not None:body['tools']=tools
        if tool_choice is not None:body['tool_choice']=tool_choice
        result,headers=self.request('POST','/v1/chat/completions',body,idempotency_key)
        self.verify_completion(result, body)
        return result,headers

    def verify_completion(self, result, body):
        from btck_agent.inference_receipt import verify_receipt
        if not verify_receipt(result,self.trusted_receipt_pubkey):
            raise ValueError('The inference receipt did not match the trusted pilot signer or result')
        encoded=json.dumps(body,sort_keys=True,separators=(',', ':'),ensure_ascii=False).encode()
        if result['receipt']['payload']['request_hash'] != hashlib.sha256(encoded).hexdigest():
            raise ValueError('The signed receipt belongs to a different request')

    def balance(self):
        return self.request('GET','/v1/balance')[0]

    def models(self):
        return self.request('GET','/v1/models')[0]

    def task(self,key):
        return self.request('GET','/v1/tasks/'+quote(key,safe=''))[0]
