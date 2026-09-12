import json
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
import unittest
from unittest.mock import patch
import safe_process as commands


class ProcessSecurityTests(unittest.TestCase):
    def python(self,code,timeout=1,**limits):
        with commands.launch('python3',['-I','-c',code]) as process:
            return commands.collect(process,[],timeout,**limits)
    def test_environment_blocks_injection_routing_and_phone_home_overrides(self):
        poisoned={'PATH':'/untrusted','PYTHONPATH':'/bad','LD_PRELOAD':'/bad','ADB_SERVER_SOCKET':'tcp:8.8.8.8:5037','ADB':'/bad','SCRCPY_SERVER_PATH':'/bad','HTTPS_PROXY':'http://example.com','BASH_ENV':'/bad','DBUS_SESSION_BUS_ADDRESS':'tcp:host=8.8.8.8,port=1234'}
        # Python starts before environment mocking to avoid test harness loader effects.
        with patch.dict(os.environ,poisoned):
            env=commands.tool_environment()
            result=self.python('import os,json; print(json.dumps(dict(os.environ)))')
        child=json.loads(result.stdout)
        self.assertEqual(child,env)
        self.assertEqual(child['PATH'],'/usr/bin')
        self.assertEqual(child['ADB_SERVER_SOCKET'],'tcp:127.0.0.1:5037')
        self.assertEqual(child['ADB'],'/usr/bin/adb')
        for key in ('PYTHONPATH','LD_PRELOAD','HTTPS_PROXY','BASH_ENV'):
            self.assertNotIn(key,child)
    def test_both_pipe_limits(self):
        for number,stream in ((1,'stdout'),(2,'stderr')):
            with self.subTest(stream=stream),self.assertRaisesRegex(commands.ToolError,stream+' limit'):
                self.python(f'import os; os.write({number},b"x"*8192)',**{stream+'_limit':1024})
    def test_exact_limit_and_nonzero_status(self):
        self.assertEqual(len(self.python('print("x"*1023)',stdout_limit=1024).stdout),1024)
        self.assertEqual(self.python('raise SystemExit(7)').returncode,7)
    def test_deadline_even_after_closed_pipes(self):
        start=time.monotonic()
        with self.assertRaisesRegex(commands.ToolError,'deadline'):
            self.python('import os,time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); os.close(1); os.close(2); time.sleep(30)',timeout=.2)
        self.assertLess(time.monotonic()-start,2)
    def test_timeout_kills_descendant(self):
        with tempfile.TemporaryDirectory() as temp:
            pidfile=Path(temp)/'pid'
            code='import os,time\npid=os.fork()\nif pid==0: time.sleep(30)\nelse:\n open('+repr(str(pidfile))+',"w").write(str(pid))\n os._exit(0)'
            with self.assertRaises(commands.ToolError):
                self.python(code,timeout=.3)
            pid=int(pidfile.read_text())
            for _ in range(50):
                path=Path(f'/proc/{pid}/stat')
                if not path.exists() or path.read_text().rsplit(')',1)[1].split()[0]=='Z':break
                time.sleep(.01)
            else:self.fail('Child survived cleanup')
    def test_cleanup_is_idempotent_and_does_not_signal_reused_pid(self):
        with commands.launch('python3',['-I','-c','pass']) as process:
            commands.collect(process,[],1)
            with patch.object(commands.os,'killpg') as kill:
                commands.cleanup(process)
            kill.assert_not_called()
    def test_untrusted_path_candidate_cannot_execute(self):
        with tempfile.TemporaryDirectory() as temp:
            marker=Path(temp)/'ran';fake=Path(temp)/'adb'
            fake.write_text('#!/bin/sh\ntouch '+str(marker));fake.chmod(0o755)
            with patch.dict(os.environ,PATH=temp):
                result=commands.run(['adb','version'])
            self.assertEqual(result.returncode,0)
            self.assertFalse(marker.exists())
    def test_verified_inode_runs_after_path_is_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'python-tool'
            shutil.copyfile(os.path.realpath('/usr/bin/python3'),path);path.chmod(0o700)
            @contextmanager
            def verified(name,script=False):
                fd=os.open(path,os.O_RDONLY)
                try:
                    path.unlink();path.symlink_to('/usr/bin/false')
                    yield str(path),fd
                finally:os.close(fd)
            with patch.object(commands,'verified_tool',verified):
                self.assertEqual(self.python('print("verified inode")').stdout,'verified inode\n')

    def test_unknown_tool_rejected(self):
        for name in ('curl','wget','ssh','/tmp/adb'):
            self.assertFalse(commands.available(name))
    def test_untrusted_owner_modes_and_nonregular_files_rejected(self):
        info=list(os.stat('/usr/bin/adb'))
        for uid,mode in ((1000,stat.S_IFREG|0o755),(0,stat.S_IFREG|0o777),(0,stat.S_IFREG|0o4755),(0,stat.S_IFIFO|0o600)):
            altered=info.copy();altered[4]=uid;altered[0]=mode
            with self.assertRaises(commands.ToolError):commands.trusted_node(os.stat_result(altered))
