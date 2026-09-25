"""Validated local idea records. No I/O or model-generated decisions."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import uuid

MAX_INPUT = 1024 * 1024
MAX_STATE = 64 * MAX_INPUT
MAX_NUMBER = 10**12
SHAPE_KEYS = {'outcome','scope','scope_reason','alternatives','method','method_reason','assumptions','next_slice','learning'}
ASSESS_KEYS = {'method','version','inputs','basis','assumptions','confidence','provenance'}


class IdeaError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def require(condition, message, code='invalid_input'):
    if not condition:
        raise IdeaError(code, message)


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(prefix):
    return prefix + '_' + uuid.uuid4().hex


def check_id(value, prefix='idea'):
    require(isinstance(value,str) and re.fullmatch(prefix+r'_[0-9a-f]{32}',value), 'Invalid '+prefix+' ID')


def text(value, name, limit=65536):
    require(isinstance(value,str) and bool(value.strip()) and len(value)<=limit, name+' must be nonempty text within '+str(limit)+' characters')
    require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), name+' contains invalid Unicode')
    return value


def integer(value, name, low=0, high=MAX_NUMBER):
    require(type(value) is int and low<=value<=high, name+' must be an integer from '+str(low)+' to '+str(high))
    return value


def number(value, name, low=0, high=MAX_NUMBER):
    require(type(value) in (int,float) and low<=value<=high and math.isfinite(value), name+' must be a finite number from '+str(low)+' to '+str(high))
    return value


def strings(values,name):
    require(isinstance(values,list) and len(values)<=1000,name+' must be a list of at most 1000 strings')
    for value in values:
        text(value,name)


def exact(value, keys, name):
    require(isinstance(value,dict) and set(value)==keys,name+' must have exactly these keys: '+', '.join(sorted(keys)))


def shape(value):
    exact(value,SHAPE_KEYS,'shape')
    for key in ('outcome','scope_reason','method_reason','next_slice'):
        if value[key] is not None:
            text(value[key],key)
    require(value['scope'] in (None,'small-change','capability','project','epic'),'Invalid scope')
    require(value['method'] in (None,'bounded-plan','adaptive-slices','appetite-led','experiment-led'),'Invalid method')
    require(isinstance(value['alternatives'],list) and len(value['alternatives'])<=1000,'alternatives must be a list')
    for alternative in value['alternatives']:
        exact(alternative,{'route','reason'},'alternative')
        text(alternative['route'],'route')
        text(alternative['reason'],'reason')
    strings(value['assumptions'],'assumptions')
    strings(value['learning'],'learning')
    return copy.deepcopy(value)


def assessment(value):
    exact(value,ASSESS_KEYS,'assessment')
    text(value['version'],'version',100)
    require(value['method'] in ('wsjf','rice','kano'),'Invalid assessment method')
    for key in ('basis','provenance'):
        item=value[key]
        if isinstance(item,dict):
            require(bool(item) and len(item)<=100,key+' must be nonempty')
            for k,v in item.items():
                text(k,key+' key',100)
                text(v,key+' value')
        else:
            text(item,key)
    strings(value['assumptions'],'assumptions')
    require(value['confidence'] in ('low','medium','high'),'confidence must be low, medium or high')
    inputs=value['inputs']
    method=value['method']
    if method=='kano':
        exact(inputs,{'category','hypothesis'},'Kano inputs')
        require(inputs['category'] in ('must-be','performance','delighter','indifferent','reverse','questionable'),'Invalid Kano category')
        require(type(inputs['hypothesis']) is bool,'Kano hypothesis must be a boolean')
        score=None
    else:
        keys={'value','time_criticality','enablement','effort'} if method=='wsjf' else {'reach','impact','confidence','effort'}
        exact(inputs,keys,method+' inputs')
        for key,value_in in inputs.items():
            if value_in is not None:
                number(value_in,key,high=1 if key=='confidence' else MAX_NUMBER)
                require(key!='effort' or value_in>0,'effort must be greater than zero')
        score=None
        if all(v is not None for v in inputs.values()):
            score=((inputs['value']+inputs['time_criticality']+inputs['enablement'])/inputs['effort'] if method=='wsjf' else inputs['reach']*inputs['impact']*inputs['confidence']/inputs['effort'])
            require(math.isfinite(score),'Assessment arithmetic overflow')
    return dict(copy.deepcopy(value),score=score)


def ready(idea, complete_shape=False):
    require(idea['ratings'] is not None and bool(idea['assessments']),'Operator ratings and a brain assessment are required','not_ready')
    if complete_shape:
        shaped=idea['shape']
        require(shaped is not None and all(shaped[k] for k in ('outcome','scope','scope_reason','alternatives','method','method_reason','next_slice')),'Complete shaping before planning','not_ready')


def snapshot(idea, actor, action):
    return dict(revision=idea['revision'],shape=copy.deepcopy(idea['shape']),ratings=copy.deepcopy(idea['ratings']),assessments=copy.deepcopy(idea['assessments']),actor=actor,action=action,timestamp=now())


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf-8')


def decode(raw):
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,'Duplicate JSON key: '+key)
            result[key]=value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=lambda x: (_ for _ in ()).throw(IdeaError('invalid_input','Nonfinite JSON number: '+x)))
    except (ValueError,UnicodeError,RecursionError) as exc:
        raise IdeaError('invalid_input','Invalid UTF-8 JSON: '+str(exc)) from exc


def receipt(value,idea_id,plan_id):
    require(isinstance(value,dict),'Execution receipt must be an object')
    require({'idea_id','plan_id','attempt_id','status','evidence'}<=set(value),'Receipt requires idea_id, plan_id, attempt_id, status, evidence')
    require(value['idea_id']==idea_id and value['plan_id']==plan_id,'Receipt idea/plan IDs do not match','link_mismatch')
    require(isinstance(value['attempt_id'],str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',value['attempt_id']),'Invalid attempt_id')
    require(value['status'] in ('succeeded','failed','blocked'),'Invalid execution status')
    strings(value['evidence'],'evidence')
    require(bool(value['evidence']),'Execution evidence must not be empty')
    return value
