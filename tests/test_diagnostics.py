import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from diagnostics import runtime_snapshot,save_report,playback_snapshot,environment_snapshot


class DiagnosticTests(unittest.TestCase):
    def test_environment_distinguishes_windows_builds_without_device_identifiers(self):
        for build in (19045,26200):
            with patch('diagnostics.sys.platform','win32'), \
                    patch('diagnostics.sys.getwindowsversion',create=True,return_value=SimpleNamespace(major=10,minor=0,build=build)):
                result=environment_snapshot()
            self.assertEqual(result['windows'],{'major':10,'minor':0,'build':build})
            self.assertEqual(set(result),{'platform','python','windows'})
        with patch('diagnostics.sys.platform','linux'):
            self.assertNotIn('windows',environment_snapshot())

    def test_playback_report_allows_only_numeric_counters_and_known_states(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            self.assertFalse(playback_snapshot(base)['available'])
            value={'updatedAt':time.time(),'attempts':2,'lastReason':'video-decode-stalled','token':'DO-NOT-SHARE',
                'streams':[{'sender':False,'requested':True,'frames':42,'packetsLost':5,
                    'decodeErrors':float('nan'),'decryptFailures':3,'state':'RTC_CONNECTED','userId':'DO-NOT-SHARE'},
                    {'state':'DO-NOT-SHARE','frames':-1}]}
            (base/'playback-status.json').write_text(json.dumps(value),encoding='utf-8')
            report=playback_snapshot(base)
            self.assertFalse(report['stale']);self.assertEqual(report['streams'][0]['frames'],42)
            self.assertNotIn('DO-NOT-SHARE',json.dumps(report));self.assertNotIn('decodeErrors',report['streams'][0])
            value['updatedAt']=time.time()-30
            (base/'playback-status.json').write_text(json.dumps(value),encoding='utf-8')
            self.assertTrue(playback_snapshot(base)['stale'])
    def test_report_removes_credentials_addresses_paths_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            record={'updatedAt':time.time(),'version':'1.3.0','coreRunning':True,'tunnelReachable':False,
                'lastErrorClass':'MediaStartupTimeout','nonce':'DO-NOT-SHARE','private_key':'DO-NOT-SHARE',
                'streams':[{'active':True,'localPeerSeen':True,'udpSent':True,'udpReceived':False,
                    'target':'DO-NOT-SHARE','discoveryMs':None}],
                'recentEvents':[{'event':'control-error','errorClass':'TimeoutError','at':time.time(),'token':'DO-NOT-SHARE'},
                    {'event':'DO-NOT-SHARE'},{'event':['malformed']}]}
            (base/'status.json').write_text(json.dumps(record),encoding='utf-8')
            destination=save_report(base,{'version':'1.3.0','configurationPresent':True,'dataDirectory':'DO-NOT-SHARE'})
            raw=Path(destination).read_text(encoding='utf-8');report=json.loads(raw)
            self.assertIn('platform',report['environment'])
            self.assertNotIn('DO-NOT-SHARE',raw)
            self.assertFalse(report['runtime']['stale'])
            self.assertEqual(report['runtime']['lastErrorClass'],'MediaStartupTimeout')
            self.assertEqual(len(report['runtime']['recentEvents']),1)
            self.assertFalse(report['runtime']['streams'][0]['udpReceived'])

    def test_missing_invalid_oversized_and_stale_status_are_handled(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);file=base/'status.json'
            self.assertFalse(runtime_snapshot(base)['available'])
            for raw in ('{broken','[]',' '*65537):
                file.write_text(raw,encoding='utf-8')
                self.assertFalse(runtime_snapshot(base)['available'])
            file.write_text(json.dumps({'updatedAt':time.time()-60,'coreRunning':True,'lastErrorClass':'secret.with punctuation',
                'controlRetries':float('nan'),'streams':'invalid','recentEvents':{}}),encoding='utf-8')
            report=runtime_snapshot(base)
            self.assertTrue(report['stale']);self.assertNotIn('lastErrorClass',report)
            self.assertNotIn('controlRetries',report);self.assertEqual(report['streams'],[])

    def test_path_recovery_counters_and_events_survive_the_allowlist_without_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            (base/'status.json').write_text(json.dumps({'udpPathChecks':120,'udpPathProbes':122,
                'udpSendBackpressure':3,'udpSendTimeouts':1,
                'mediaPathTimeouts':1,'mediaPathChanges':1,'discovery_endpoint':'DO-NOT-SHARE',
                'recentEvents':[{'event':'media-path-timeout','at':time.time()},
                    {'event':'media-path-changed','endpoint':'DO-NOT-SHARE'}]}),encoding='utf-8')
            result=runtime_snapshot(base)
            self.assertEqual(result['udpPathChecks'],120);self.assertEqual(result['mediaPathTimeouts'],1)
            self.assertEqual(result['udpSendBackpressure'],3);self.assertEqual(result['udpSendTimeouts'],1)
            self.assertEqual([event['event'] for event in result['recentEvents']],['media-path-timeout','media-path-changed'])
            self.assertNotIn('DO-NOT-SHARE',json.dumps(result))


if __name__=='__main__':unittest.main()
