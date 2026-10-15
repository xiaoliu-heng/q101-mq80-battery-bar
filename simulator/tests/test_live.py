"""Regression coverage for the reported live-view UX and CPU optimization."""
import json
import tempfile
import unittest
from mq80lab import machine as m
from mq80lab.engine import Runtime,BOUNDARIES,SimulationFault
from mq80lab.profile import ASSETS
from mq80lab.scenarios import apply_step,expected_bar
from mq80lab.server import Lab

IMAGE=(ASSETS/'battery-bar-v2.bin').read_bytes()


class ReferenceRuntime(Runtime):
    # The pre-optimization per-instruction adapter, for differential execution.
    def on_code(self,u,address,size,extra):
        self.total_instructions+=1
        self._call_instructions+=1
        self.covered.add(address)
        if self._call_instructions>6000000:
            self.error='本次调用超过 600 万条指令预算。';u.emu_stop();return
        if address in BOUNDARIES:
            self.pending_driver=address;u.emu_stop();return
        if self.instruction_count>=90000:self.instruction_count=0
        m.FirmwareEmulator.on_code(self,u,address,size,extra)


class LiveTests(unittest.TestCase):
    def test_optimized_cpu_matches_reference_through_query_sleep_and_first_key(self):
        a=Runtime(IMAGE);b=ReferenceRuntime(IMAGE)
        for step in [{'type':'query'},{'type':'advance','ms':30},
                     {'type':'sleep'},{'type':'tap','keys':['3,1'],'hold_ms':50}]:
            apply_step(a,step);apply_step(b,step)
            self.assertEqual(a.state(),b.state())
            self.assertEqual(a.reports,b.reports)
            self.assertEqual(a.packets,b.packets)
            self.assertEqual(bytes(a.u.mem_read(0,m.RAM_END)),bytes(b.u.mem_read(0,m.RAM_END)))

    def test_live_lighting_expires_in_real_renderer_and_restores_off(self):
        r=Runtime(IMAGE);apply_step(r,{'type':'query'})
        self.assertEqual(r.state()['rgb']['side'],expected_bar(88))
        remaining=r.state()['battery']['query_remaining_ms']
        r.preview_advance(remaining-1)
        self.assertTrue(r.state()['battery']['query_active'])
        r.preview_advance(1)
        self.assertFalse(r.state()['battery']['query_active'])
        self.assertEqual(r.state()['rgb']['side'],[[0,0,0]]*9)
        self.assertTrue(any(event['kind']=='preview' and not event['details']['idle_irqs_executed'] for event in r.trace))

    def test_live_expiry_restores_selected_side_effect_instead_of_forcing_black(self):
        r=Runtime(IMAGE,{'side_mode':0});apply_step(r,{'type':'query'})
        r.preview_advance(3100)
        self.assertFalse(r.state()['battery']['query_active'])
        self.assertEqual(r.env.side_mode,0)
        self.assertNotEqual(r.state()['rgb']['side'],[[0,0,0]]*9)

    def test_preview_pending_pm_and_input_execute_strict_path(self):
        r=Runtime(IMAGE);r.sleep_checkpoint();r.set_keys(['3,1']);r.preview_advance(50)
        r.set_keys([]);r.preview_advance(35)
        self.assertEqual(r.host_text,'a')
        self.assertEqual(r.reports[-1]['hex'],'0000000000000000')
        self.assertTrue(any(x['kind']=='wake' for x in r.trace))

    def test_restore_invalidates_decoded_executable_memory(self):
        r=Runtime(IMAGE)
        r.u.mem_write(0x70000,bytes.fromhex('67800000'))
        r.invoke('return_probe',0x70000)
        self.assertIn(0x70000,r._decoded_instructions)
        snapshot=json.loads(json.dumps(r.snapshot()))
        r.restore(snapshot)
        self.assertNotIn(0x70000,r._decoded_instructions)

    def test_cpu_store_invalidates_cached_instruction(self):
        r=Runtime(IMAGE)
        r.u.mem_write(0x70000,bytes.fromhex('67800000'))
        r.invoke('return_probe',0x70000)
        # sw a1,0(a0); ret -- this is executed by Unicorn, not a Python write.
        r.u.mem_write(0x8f100,bytes.fromhex('2320b50067800000'))
        r.invoke('code_store',0x8f100,0x70000,0xffffffff)
        self.assertNotIn(0x70000,r._decoded_instructions)
        with self.assertRaises(SimulationFault):r.invoke('changed_probe',0x70000)

    def test_recording_coalesces_live_ticks_and_replays(self):
        with tempfile.TemporaryDirectory() as folder:
            lab=Lab(folder)
            lab.action({'action':'query'})
            for _ in range(65):lab.action({'action':'preview','ms':50})
            self.assertEqual(len(lab.recording['steps']),2)
            self.assertEqual(lab.recording['steps'][1],{'type':'preview','ms':3250})
            replay=Runtime(IMAGE)
            for step in lab.recording['steps']:apply_step(replay,step)
            self.assertEqual(replay.state()['battery'],lab.runtime.state()['battery'])
            self.assertEqual(replay.state()['rgb'],lab.runtime.state()['rgb'])


if __name__=='__main__':unittest.main(verbosity=2)
