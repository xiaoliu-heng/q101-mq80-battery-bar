import copy
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from mq80lab.engine import Runtime,SimulationFault
from mq80lab.peripherals import NorFlash
from mq80lab.profile import ASSETS,inspect_image,layout
from mq80lab.scenarios import BUILTINS,run_scenario,apply_step,validate_scenario
from mq80lab.server import Lab,handler_type
from mq80lab.ota import run_ota

STOCK=(ASSETS/'stock.bin').read_bytes()
V2=(ASSETS/'battery-bar-v2.bin').read_bytes()


class RuntimeTests(unittest.TestCase):
    def test_builtin_scenarios_real_firmware(self):
        for case in BUILTINS:
            with self.subTest(case=case['id']):
                r,result=run_scenario(V2,case)
                self.assertEqual(result['status'],'PASS',result['error'])
                self.assertGreater(r.total_instructions,1000)

    def test_original_and_v2_first_key_match(self):
        case=BUILTINS[0]
        a,ar=run_scenario(STOCK,case)
        b,br=run_scenario(V2,case)
        self.assertEqual(ar['status'],'PASS')
        self.assertEqual(br['status'],'PASS')
        self.assertEqual(a.host_text,b.host_text)
        self.assertEqual([x['hex'] for x in a.reports],[x['hex'] for x in b.reports])

    def test_sleeping_snapshot_resumes_original_call_stack(self):
        a=Runtime(V2)
        a.sleep_checkpoint()
        self.assertIsNotNone(a.call_context)
        self.assertEqual(a.state()['status'],'sleeping')
        snapshot=json.loads(json.dumps(a.snapshot()))
        a.tap(['3,1'],50)
        b=Runtime(V2);b.restore(snapshot);b.tap(['3,1'],50)
        self.assertEqual(a.host_text,'a')
        self.assertEqual(a.reports,b.reports)
        self.assertEqual(a.state()['power'],b.state()['power'])
        self.assertIsNone(b.call_context)

    def test_timer_wake_retains_sleep_then_pad_delivers_first_key(self):
        r=Runtime(V2);r.sleep_checkpoint();r.advance(1200)
        self.assertGreaterEqual(sum(x['kind']=='wake' for x in r.trace),2)
        r.tap(['3,1'],50)
        self.assertEqual(r.host_text,'a')

    def test_clock_wrap_and_query_expiry(self):
        r=Runtime(V2)
        r.now_us=0xffff0000//16
        r._next_irq_us=r.now_us+1000
        apply_step(r,{'type':'query'})
        self.assertTrue(r.state()['battery']['query_active'])
        r.jump_clock(3100)
        self.assertFalse(r.state()['battery']['query_active'])

    def test_keymap_checkpoint_is_explicit_and_required_for_fn_binding(self):
        r=Runtime(V2,{'keymap_preset':'image'});apply_step(r,{'type':'query'})
        self.assertFalse(r.state()['battery']['query_active'])
        r=Runtime(V2);apply_step(r,{'type':'query'})
        self.assertTrue(r.state()['battery']['query_active'])
        self.assertTrue(any(x['kind']=='nvm' and x['origin']=='model' for x in r.trace))

    def test_usb_busy_does_not_claim_host_received_report(self):
        r=Runtime(V2,{'connection':'usb','cable':True,'host_ready':False})
        r.set_keys(['3,1']);r.advance(30)
        self.assertEqual(r.reports,[])
        r.apply_environment({'host_ready':True});r.advance(15)
        self.assertEqual(r.host_text,'a')
        r.set_keys([]);r.advance(30)
        self.assertEqual(r.reports[-1]['hex'],'0000000000000000')

    def test_ble_busy_preserves_queued_report(self):
        r=Runtime(V2,{'connection':'ble','ble_busy':True})
        r.set_keys(['3,1']);r.advance(30)
        self.assertEqual(r.reports,[])
        self.assertNotEqual(r.byte(0x818c8),r.byte(0x818c9))
        r.apply_environment({'ble_busy':False});r.advance(30)
        self.assertEqual(r.host_text,'a')

    def test_dropped_rf_attempt_retries_without_duplicate_host_character(self):
        r=Runtime(V2,{'drop_every':2,'ack_delay_ms':2});r.tap(['3,1'],50)
        self.assertEqual(r.host_text,'a')
        self.assertTrue(any(x['modeled_drop'] for x in r.packets))

    def test_unknown_machine_instruction_fails_closed(self):
        r=Runtime(V2);r.u.mem_write(0x8f100,bytes.fromhex('ffffffff'))
        with self.assertRaises(SimulationFault):r.invoke('unsupported_instruction',0x8f100)
        self.assertEqual(r.state()['status'],'fault')

    def test_unknown_register_region_stops_actual_cpu_read(self):
        r=Runtime(V2);r.u.mem_write(0x8f100,bytes.fromhex('0325050067800000'))
        with self.assertRaises(SimulationFault):r.invoke('unknown_register_probe',0x8f100,0x80141400)
        self.assertIn('未建模',r.error_info['message'])

    def test_inputs_are_immutable(self):
        for file in ['stock.bin','battery-bar-v2.bin']:
            before=hashlib.sha256((ASSETS/file).read_bytes()).hexdigest()
            r=Runtime((ASSETS/file).read_bytes());r.tap(['3,1'],20)
            self.assertEqual(before,hashlib.sha256((ASSETS/file).read_bytes()).hexdigest())

    def test_foreign_snapshot_is_rejected(self):
        r=Runtime(V2)
        with self.assertRaises(ValueError):r.restore(Runtime(STOCK).snapshot())

    def test_corrupt_snapshot_size_is_rejected(self):
        r=Runtime(V2);snapshot=r.snapshot();snapshot['regions'][0][1]=1
        with self.assertRaises(ValueError):r.restore(snapshot)

    def test_environment_rejection_is_atomic(self):
        r=Runtime(V2)
        with self.assertRaises(ValueError):r.apply_environment({'connection':'usb'})
        self.assertEqual(r.env.connection,'rf')
        with self.assertRaises(ValueError):r.apply_environment({'wake_latency_ms':-1})
        self.assertEqual(r.env.wake_latency_ms,1)

    def test_schema_rejects_unsupported_and_unbounded_actions(self):
        for steps in [[{'type':'exec','code':'print(1)'}],[{'type':'advance','ms':999999}],
                      [{'type':'tap','keys':[],'arbitrary':True}]]:
            with self.assertRaises(ValueError):validate_scenario({'steps':steps})

    def test_nor_requires_erase_and_rejects_cross_page_write(self):
        flash=NorFlash(b'\x00')
        flash.write(0,b'\xff');self.assertEqual(flash.read(0,1),b'\x00')
        flash.erase(0);flash.write(0,b'\xa5');self.assertEqual(flash.read(0,1),b'\xa5')
        with self.assertRaises(ValueError):flash.write(255,b'\x00\x00')

    def test_bad_firmware_crc_and_exe_rejected(self):
        bad=bytearray(V2);bad[0x10000]^=1
        with self.assertRaises(ValueError):inspect_image(bytes(bad))
        with self.assertRaises(ValueError):inspect_image(b'MZ'+bytes(len(V2)-2))

    def test_layout_contains_special_gpio_inputs(self):
        keys={x['id']:x for x in layout(V2)}
        self.assertEqual(keys['7,14']['label'],'Fn')
        self.assertEqual(keys['6,14']['label'],'Esc')
        self.assertEqual(len(keys),84)

    def test_ota_end_failure_and_commit(self):
        for scenario in ['incomplete','crc_error','header_readback_error','valid_image']:
            with self.subTest(scenario=scenario):
                result=run_ota(STOCK,V2,scenario)
                self.assertEqual(result['status'],'PASS')
                if scenario!='valid_image':self.assertTrue(result['active_unchanged'])
                if scenario in ['crc_error','valid_image']:self.assertTrue(result['real_crc_executed'])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.lab=Lab(cls.temp.name)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),handler_type(cls.lab,'test-session-token'))
        cls.url='http://127.0.0.1:%d'%cls.server.server_port
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.temp.cleanup()

    def test_static_source_boundary(self):
        for path in ['/../PRODUCT.md','/mq80lab/engine.py','/assets/stock.bin']:
            with self.assertRaises(urllib.error.HTTPError) as context:urllib.request.urlopen(self.url+path)
            self.assertEqual(context.exception.code,404)

    def test_mutation_requires_session_token(self):
        request=urllib.request.Request(self.url+'/api/action',data=b'{"action":"reset"}',headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as context:urllib.request.urlopen(request)
        self.assertEqual(context.exception.code,403)

    def test_external_origin_rejected_even_with_token(self):
        request=urllib.request.Request(self.url+'/api/action',data=b'{}',headers={'Content-Type':'application/json','X-Lab-Token':'test-session-token','Origin':'https://example.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as context:urllib.request.urlopen(request)
        self.assertEqual(context.exception.code,403)

    def test_api_runs_real_input_chain(self):
        request=urllib.request.Request(self.url+'/api/action',data=json.dumps({'action':'tap','keys':['3,1'],'hold_ms':50}).encode(),headers={'Content-Type':'application/json','X-Lab-Token':'test-session-token'})
        with urllib.request.urlopen(request) as response:state=json.load(response)
        self.assertEqual(state['host_text'],'a')
        self.assertEqual(state['hid'],'0000000000000000')


if __name__=='__main__':unittest.main(verbosity=2)
