import ctypes
import os
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(os.name=='nt','Windows job lifecycle')
class ChildJobTests(unittest.TestCase):
    def test_job_close_kills_only_assigned_child(self):
        from windows_process import ChildJob
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],creationflags=subprocess.CREATE_NO_WINDOW)
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],creationflags=subprocess.CREATE_NO_WINDOW)
        job=ChildJob()
        try:
            job.assign(child);job.close()
            self.assertIsNotNone(child.wait(timeout=3));self.assertIsNone(unrelated.poll())
            job.close()  # Idempotent cleanup.
        finally:
            job.close()
            for process in (child,unrelated):
                if process.poll() is None:process.kill()
                process.wait(timeout=3)

    def test_abrupt_owner_exit_releases_core(self):
        # os._exit bypasses Python finally blocks, as an Electron parent killing
        # its helper can do. The kernel must close the last job handle itself.
        source=str(Path(__file__).resolve().parents[1]/'source')
        script="import sys,os,subprocess;sys.path.insert(0,sys.argv[1]);from windows_process import ChildJob;job=ChildJob();p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],creationflags=subprocess.CREATE_NO_WINDOW);job.assign(p);print(p.pid,flush=True);sys.stdin.readline();os._exit(0)"
        owner=subprocess.Popen([sys.executable,'-c',script,source],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
        from stream_relay import OwnerProcess
        child=None
        try:
            child=OwnerProcess(int(owner.stdout.readline()))
            owner.stdin.write('exit\n');owner.stdin.flush();owner.wait(timeout=3)
            self.assertEqual(child.api.WaitForSingleObject(child.handle,3000),0)
        finally:
            if owner.poll() is None:owner.kill()
            owner.wait(timeout=3);owner.stdin.close();owner.stdout.close()
            if child:child.close()


if __name__=='__main__':unittest.main()
