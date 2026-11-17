import json
import os
import queue
import time
import unittest
from transport import Reader
from scope_core import Metadata, Frame


@unittest.skipUnless(os.name=='posix','Pseudo-terminal test runs on POSIX')
class TransportTests(unittest.TestCase):
    def test_serial_protocol_through_real_os_port(self):
        import pty
        master,slave=pty.openpty()
        r=Reader('Serial',os.ttyname(slave),115200); r.start()
        try:
            self.assertEqual(r.events.get(timeout=3)[0],'connected')
            hello={'type':'hello','protocol':1,'device':'Fixture','channels':[{'name':'A','unit':'V'}]}
            frame={'type':'samples','seq':0,'times':[0,.001,.002], 'values':[[0],[1],[0]]}
            os.write(master,(json.dumps(hello)+'\n'+json.dumps(frame)+'\n').encode())
            self.assertIsInstance(r.events.get(timeout=3)[1],Metadata)
            f=r.events.get(timeout=3)[1]; self.assertIsInstance(f,Frame)
            self.assertEqual(f.y[:,0].tolist(),[0,1,0]); self.assertEqual(r.invalid,0)
        finally:
            r.close(); r.join(timeout=1); os.close(master); os.close(slave)
        self.assertFalse(r.is_alive())


if __name__=='__main__': unittest.main()
