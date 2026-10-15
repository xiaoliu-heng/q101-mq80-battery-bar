import copy
from .engine import Runtime, bounded_int


def expected_bar(percent):
    p=max(0,min(100,percent))
    red=162 if p<=50 else 162*(100-p)//50
    green=162 if p>=50 else 162*p//50
    return [[red*max(0,min(100,p*9-i*100))//100,
             green*max(0,min(100,p*9-i*100))//100,0] for i in range(9)]


def query_steps():
    return [{'type':'keys','keys':['7,14']},{'type':'advance','ms':16},
            {'type':'keys','keys':['7,14','4,6']},{'type':'advance','ms':30},
            {'type':'keys','keys':[]},{'type':'advance','ms':35},{'type':'render'}]


BUILTINS=[
    {'id':'wake-first','name':'全关灯 · 唤醒首键','description':'真实休眠准备 → 模型 PAD 唤醒 → A → 松开 → 接收器 ACK。',
     'environment':{'connection':'rf','main_off':True,'side_mode':4},
     'steps':[{'type':'sleep','gate':'fast'},{'type':'assert','path':'status','equals':'sleeping'},
              {'type':'tap','keys':['3,1'],'hold_ms':50},{'type':'assert','path':'host_text','equals':'a'},
              {'type':'assert','path':'hid','equals':'0000000000000000'}]},
    {'id':'normal-idle','name':'普通空闲 · 600 秒门限','description':'从 600 秒计数检查点进入真实分支，不等待十分钟墙钟时间。',
     'environment':{'connection':'rf','main_off':False,'side_mode':0},
     'steps':[{'type':'sleep','gate':'normal'},{'type':'tap','keys':['3,1'],'hold_ms':50},
              {'type':'assert','path':'host_text','equals':'a'}]},
    {'id':'battery-bar','name':'Fn+B · 侧灯电量条','description':'通过虚拟 Fn 和 B 触发真实键位分派，检查 88% 的九颗侧灯。',
     'environment':{'connection':'rf','battery_percent':88},
     'steps':query_steps()+[{'type':'assert','path':'battery.query_active','equals':True},
                            {'type':'assert','path':'rgb.side','equals':expected_bar(88)}]},
    {'id':'bar-timeout','name':'电量显示 · 三秒后恢复','description':'显示后跳跃时钟，执行真实帧入口，验证临时状态已清除。',
     'environment':{'connection':'rf','battery_percent':20},
     'steps':query_steps()+[{'type':'jump','ms':3100},
                            {'type':'assert','path':'battery.query_active','equals':False}]},
    {'id':'usb-key','name':'USB · 主机端点','description':'矩阵输入 → 原固件 USB 报告任务 → 虚拟端点接收 A 和松键。',
     'environment':{'connection':'usb','cable':True},
     'steps':[{'type':'tap','keys':['3,1'],'hold_ms':50},{'type':'assert','path':'host_text','equals':'a'},
              {'type':'assert','path':'hid','equals':'0000000000000000'}]},
    {'id':'ble-key','name':'Bluetooth · 通知提交','description':'执行应用层排队与通知提交，SDK / 控制器为模型。',
     'environment':{'connection':'ble'},
     'steps':[{'type':'tap','keys':['3,1'],'hold_ms':50},{'type':'assert','path':'host_text','equals':'a'},
              {'type':'assert','path':'queue_empty','equals':True}]},
    {'id':'ack-delay','name':'2.4G · 延迟 ACK','description':'ACK 延迟 25 ms，检查重发后仍只有一个 A，不会卡键。',
     'environment':{'connection':'rf','ack_delay_ms':25},
     'steps':[{'type':'sleep'},{'type':'tap','keys':['3,1'],'hold_ms':50},{'type':'advance','ms':60},
              {'type':'assert','path':'host_text','equals':'a'},
              {'type':'assert','path':'queue_empty','equals':True}]},
    {'id':'too-short','name':'负面对照 · 唤醒前松手','description':'仅按 1 ms，设定 20 ms 唤醒延迟；预期主机没有字符。',
     'environment':{'connection':'rf','wake_latency_ms':20},
     'steps':[{'type':'sleep'},{'type':'tap','keys':['3,1'],'hold_ms':1},
              {'type':'assert','path':'host_text','equals':''}]},
    {'id':'receiver-off','name':'故障注入 · 接收器断开','description':'固件继续封包，但模型不提供 ACK；预期主机收不到 A，队列保留。',
     'environment':{'connection':'rf','receiver':False},
     'steps':[{'type':'tap','keys':['3,1'],'hold_ms':50},{'type':'assert','path':'host_text','equals':''},
              {'type':'assert','path':'queue_empty','equals':False}]},
    {'id':'shift-a','name':'组合键 · Shift+A','description':'真实 GPIO 同时输入修饰键和 A，检查主机解码为大写。',
     'environment':{'connection':'rf'},
     'steps':[{'type':'tap','keys':['4,0','3,1'],'hold_ms':50},{'type':'assert','path':'host_text','equals':'A'}]},
]


def validate_scenario(scenario):
    if not isinstance(scenario,dict): raise ValueError('场景必须是 JSON 对象。')
    if set(scenario)-{'id','name','description','environment','steps'}: raise ValueError('场景包含未知字段。')
    steps=scenario.get('steps')
    if not isinstance(steps,list) or not 1<=len(steps)<=150: raise ValueError('场景需要 1～150 个步骤。')
    allowed={'keys':{'type','keys'},'tap':{'type','keys','hold_ms'},'advance':{'type','ms'},
             'jump':{'type','ms'},'environment':{'type','values'},'sleep':{'type','gate'},
             'render':{'type'},'query':{'type'},'preview':{'type','ms'},'assert':{'type','path','equals'}}
    total=0
    for step in steps:
        if not isinstance(step,dict) or step.get('type') not in allowed: raise ValueError('未知场景步骤。')
        kind=step['type']
        if set(step)-allowed[kind]: raise ValueError('步骤包含未知参数。')
        if kind=='advance': total+=bounded_int(step.get('ms'),0,5000,'推进时间')
        if kind=='jump': bounded_int(step.get('ms'),0,3600000,'跳跃时间')
        if kind=='preview': bounded_int(step.get('ms'),0,5000,'预览时间')
        if kind=='tap': total+=bounded_int(step.get('hold_ms',50),1,1000,'按住时间')+100
        if kind=='assert' and (not isinstance(step.get('path'),str) or len(step['path'])>100):
            raise ValueError('断言路径无效。')
    if total>15000: raise ValueError('单个场景累计离散推进不能超过 15 秒；长间隔可用 jump 或休眠检查点。')
    return copy.deepcopy(scenario)


def value_at(runtime,path):
    state=runtime.state()
    if path=='queue_empty': return state['queue']['head']==state['queue']['tail']
    current=state
    for key in path.split('.'):
        if not isinstance(current,dict) or key not in current: raise ValueError('断言路径不存在：'+path)
        current=current[key]
    return current


def apply_step(runtime,step):
    kind=step['type']
    if kind=='keys': runtime.set_keys(step['keys'])
    elif kind=='tap': runtime.tap(step['keys'],step.get('hold_ms',50))
    elif kind=='advance': runtime.advance(step['ms'])
    elif kind=='jump': runtime.jump_clock(step['ms'])
    elif kind=='preview': runtime.preview_advance(step['ms'])
    elif kind=='environment': runtime.apply_environment(step['values'])
    elif kind=='sleep': runtime.sleep_checkpoint(step.get('gate','fast'))
    elif kind=='render': runtime.render()
    elif kind=='query':
        for substep in query_steps(): apply_step(runtime,substep)
    elif kind=='assert':
        actual=value_at(runtime,step['path'])
        if actual != step['equals']:
            raise AssertionError('%s：预期 %s，实际 %s' % (step['path'],step['equals'],actual))
        runtime.log('assert','test','断言通过：'+step['path'],{'expected':step['equals'],'actual':actual})
    else: raise ValueError('未知步骤。')


def run_scenario(image,scenario,progress=None):
    scenario=validate_scenario(scenario)
    runtime=Runtime(image,scenario.get('environment'))
    results=[]
    failure=None
    for index,step in enumerate(scenario['steps']):
        if progress: progress(index,len(scenario['steps']))
        try:
            apply_step(runtime,step)
            results.append({'index':index,'step':step,'status':'PASS'})
        except Exception as error:
            failure=str(error)
            results.append({'index':index,'step':step,'status':'FAIL','message':failure})
            break
    return runtime,{'name':scenario.get('name','自定义场景'),'scenario':scenario,
                    'status':'FAIL' if failure else 'PASS','error':failure,
                    'steps':results,'firmware_sha256':runtime.metadata['sha256'],
                    'state':runtime.state(),'assumptions':runtime.report()['limitations']}
