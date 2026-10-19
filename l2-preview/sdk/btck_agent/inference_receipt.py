"""Validate a pilot receipt against a pinned, public signing identity."""
import hashlib,json
from privacy.wallet_engine import schnorr_verify
CURRENCY="development_satk"
def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()

def verify_receipt(response, trusted_pubkey):
    try:
        signed=response['receipt'];receipt=signed['payload']
        if signed['signer_pubkey'] != trusted_pubkey:
            return False
        digest=hashlib.sha256(canonical(receipt)).digest()
        valid=schnorr_verify(bytes.fromhex(trusted_pubkey),digest,bytes.fromhex(signed['signature']))
        message=response['choices'][0]['message']
        text=message['content'] or ''
        if 'message_sha256' in receipt or 'tool_calls' in message:
            if receipt.get('message_sha256') != hashlib.sha256(canonical(message)).hexdigest():
                return False
        return bool(valid and receipt['result_sha256']==hashlib.sha256(text.encode()).hexdigest()
                    and receipt['task_id']==response['id']
                    and receipt.get('finish_reason','stop')==response['choices'][0]['finish_reason']
                    and receipt['usage']=={k:response['usage'][k] for k in ('prompt_tokens','completion_tokens','total_tokens')}
                    and receipt['cost_satk']==response['usage']['cost_satk']
                    and receipt['pricing_units']==response['usage']['pricing_units']
                    and receipt['model']==response['model']
                    and response['currency']==receipt['currency']==CURRENCY
                    and response['mainnet_funds_enabled'] is False)
    except (KeyError,IndexError,TypeError,ValueError):
        return False
