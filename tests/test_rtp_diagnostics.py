import json
import struct
import unittest

from rtp_diagnostics import Sequences, MAX_FLOWS, MAX_TYPES


def packet(sequence, pt=105, ssrc=0x12345678):
    return struct.pack('!BBHII', 0x80, pt, sequence, 0, ssrc)+b'private-payload'


class SequenceTests(unittest.TestCase):
    def test_wrap_late_recovery_and_duplicates_preserve_counts_without_identifiers(self):
        sequences=Sequences()
        for n in (65534, 1, 0, 65535, 1):sequences.observe(packet(n))
        result=sequences.snapshot()
        self.assertEqual(result,[dict(pt=105,packets=5,sequenceMissing=0,lateRecovered=2,duplicates=1,resets=0)])
        raw=json.dumps(result)
        self.assertNotIn('private-payload',raw)
        self.assertNotIn(str(0x12345678),raw)
        self.assertNotIn('ssrc',raw)

    def test_old_packets_do_not_make_missing_negative_and_restart_is_not_loss(self):
        sequences=Sequences()
        for n in (1,2,3000,1,1,18000,18001):sequences.observe(packet(n))
        result=sequences.snapshot()[0]
        self.assertEqual(result['sequenceMissing'],2997)
        self.assertEqual(result['resets'],1)

    def test_rtx_and_other_flows_remain_separate_and_memory_is_bounded(self):
        sequences=Sequences()
        for pt in range(96,128):
            for ssrc in range(100):
                sequences.observe(packet(1,pt,ssrc))
        self.assertLessEqual(len(sequences.flows),MAX_FLOWS)
        self.assertLessEqual(len(sequences.snapshot()),MAX_TYPES)
        sequences=Sequences()
        for n in (1,4):sequences.observe(packet(n))
        for n in (2,3):sequences.observe(packet(n,106))
        self.assertEqual(sequences.snapshot()[0]['sequenceMissing'],2)
        self.assertEqual(sequences.snapshot()[1]['sequenceMissing'],0)

    def test_non_rtp_rtcp_and_short_frames_are_ignored(self):
        sequences=Sequences()
        for data in (b'',bytes(100),packet(1,200),packet(1,10),b'\x80\x69\x00'):
            sequences.observe(data)
        self.assertEqual(sequences.snapshot(),[])


if __name__=='__main__':unittest.main()
