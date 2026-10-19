"""Bounded OpenAI-style tool routing. Tools execute only in the caller's Agent."""
import json
import re


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate tool argument key')
        result[key] = value
    return result


def tool_calls(calls, allowed=None):
    if not isinstance(calls, list) or not 1 <= len(calls) <= 8:
        raise ValueError('Expected one to eight tool calls')
    seen = set()
    for call in calls:
        if not isinstance(call, dict) or set(call) != {'id', 'type', 'function'}:
            raise ValueError('Invalid tool call')
        ident, function = call['id'], call['function']
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', ident) or ident in seen:
            raise ValueError('Invalid or duplicate tool call ID')
        seen.add(ident)
        if call['type'] != 'function' or not isinstance(function, dict) or set(function) != {'name', 'arguments'}:
            raise ValueError('Only named function tools are supported')
        name, arguments = function['name'], function['arguments']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
            raise ValueError('Invalid tool name')
        if allowed is not None and name not in allowed:
            raise ValueError('Model requested an undeclared tool')
        if not isinstance(arguments, str) or len(arguments.encode()) > 16384:
            raise ValueError('Tool arguments exceeded the limit')
        try:
            value = json.loads(arguments, object_pairs_hook=_object, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON value')))
        except (ValueError, RecursionError):
            raise ValueError('Tool arguments must be a JSON object') from None
        if not isinstance(value, dict):
            raise ValueError('Tool arguments must be a JSON object')
    return calls


def validate_agent_request(body):
    tools = body.get('tools')
    names = set()
    if tools is not None:
        if not isinstance(tools, list) or not 1 <= len(tools) <= 16:
            raise ValueError('Expected one to sixteen function definitions')
        for tool in tools:
            if not isinstance(tool, dict) or set(tool) != {'type', 'function'} or tool['type'] != 'function':
                raise ValueError('Only function definitions are supported')
            fn = tool['function']
            if not isinstance(fn, dict) or set(fn) - {'name', 'description', 'parameters', 'strict'}:
                raise ValueError('Invalid function definition')
            name = fn.get('name')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) or name in names:
                raise ValueError('Invalid or duplicate function name')
            names.add(name)
            if not isinstance(fn.get('parameters'), dict) or fn['parameters'].get('type') != 'object':
                raise ValueError('Function parameters must describe an object')
            if 'description' in fn and not isinstance(fn['description'], str):
                raise ValueError('Invalid function description')
            if 'strict' in fn and type(fn['strict']) is not bool:
                raise ValueError('Invalid strict flag')
    choice = body.get('tool_choice', 'auto')
    if not isinstance(choice, str) or choice not in ('auto', 'none', 'required') or ('tool_choice' in body and tools is None):
        raise ValueError('Unsupported tool choice')
    messages = body.get('messages')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise ValueError('Expected one to sixty-four messages')
    pending, all_ids = set(), set()
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError('Invalid message')
        role, content = message.get('role'), message.get('content')
        if role == 'tool':
            if set(message) != {'role', 'content', 'tool_call_id'} or not isinstance(content, str):
                raise ValueError('Invalid tool result')
            ident = message['tool_call_id']
            if not isinstance(ident, str) or ident not in pending:
                raise ValueError('Unexpected or duplicate tool result')
            pending.remove(ident)
        else:
            if pending:
                raise ValueError('Return every pending tool result first')
            if role == 'assistant' and 'tool_calls' in message:
                if set(message) != {'role', 'content', 'tool_calls'} or content is not None and not isinstance(content, str):
                    raise ValueError('Invalid assistant tool message')
                calls = tool_calls(message['tool_calls'], names)
                pending = {c['id'] for c in calls}
                if pending & all_ids:
                    raise ValueError('Reused tool call ID')
                all_ids.update(pending)
            elif role not in ('system', 'user', 'assistant') or set(message) != {'role', 'content'} or not isinstance(content, str):
                raise ValueError('Invalid text message')
    if pending:
        raise ValueError('Return every pending tool result before inference')
    if len(json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=False).encode()) > 65536:
        raise ValueError('Agent request exceeded 64 KiB')
    return names


def completion_message(result, request):
    text, calls = result.get('text'), result.get('tool_calls')
    finish = result.get('finish_reason', 'stop')
    if calls is not None:
        if not request.get('tools') or request.get('tool_choice') == 'none' or finish != 'tool_calls':
            raise ValueError('Unexpected tool calls or finish reason')
        names = {t['function']['name'] for t in request['tools']}
        tool_calls(calls, names)
        if text is not None and not isinstance(text, str):
            raise ValueError('Invalid assistant content')
        return {'role': 'assistant', 'content': text, 'tool_calls': calls}
    if finish not in ('stop', 'length') or not isinstance(text, str) or not text.strip():
        raise ValueError('Expected text or structured tool calls')
    if request.get('tool_choice') == 'required':
        raise ValueError('Required tool call missing')
    return {'role': 'assistant', 'content': text}
