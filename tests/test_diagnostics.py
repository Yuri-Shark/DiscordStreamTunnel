import json
from pathlib import Path
import tempfile
import time
import unittest

from diagnostics import runtime_snapshot,save_report


class DiagnosticTests(unittest.TestCase):
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


if __name__=='__main__':unittest.main()
