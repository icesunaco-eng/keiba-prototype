from offline_pdf_batch import Path, extract_batch, existing_fragment_adapter
import hashlib, unittest
from unittest.mock import patch
class BatchTests(unittest.TestCase):
    def setUp(self):
        self.raw=b"%PDF-1.4 synthetic fixture only"
        self.row={"path":"/synthetic.pdf","observed_at":"2026-11-16T00:00:00Z","use_status":"approved_local_analysis","sha256":hashlib.sha256(self.raw).hexdigest()}
    def run_batch(self,rows,extractor=None):
        with patch.object(Path,"read_bytes",return_value=self.raw):
            return extract_batch(rows,extractor or (lambda data:[{"pieces":[]}]))
    def test_duplicate(self):
        calls=[]
        r=self.run_batch([self.row,self.row],lambda data:calls.append(data) or [{}])
        self.assertEqual((r["extracted"],r["duplicates"],len(calls)),(1,1,1))
        self.assertFalse(r["model_ready"])
    def test_hash_hold_isolated(self):
        bad=dict(self.row,sha256="0"*64)
        r=self.run_batch([bad,self.row]);self.assertEqual((r["holds"],r["extracted"]),(1,1))
    def test_metadata_required(self):
        for field,val in [("observed_at","2026-11-16"),("use_status",None),("path","relative.pdf"),("sha256",None)]:
            self.assertEqual(self.run_batch([dict(self.row,**{field:val})])["holds"],1)
    def test_extraction_failure_retries_duplicate(self):
        calls=[]
        def fail_once(data):
            calls.append(1)
            if len(calls)==1: raise ValueError("failure")
            return [{}]
        r=self.run_batch([self.row,self.row],fail_once)
        self.assertEqual((r["holds"],r["extracted"],r["duplicates"]),(1,1,0))
    def test_adapter_flat_tree(self):
        from types import SimpleNamespace
        m=SimpleNamespace(UNKNOWN={},objects=lambda data:{1:b"/Type /Pages /Count 1 /Kids [2 0 R]",2:b"/Type /Page /Contents 3 0 R",3:b"stream"},page_fonts=lambda *args:{},stream_of=lambda body:b"x",run=lambda *args:[{"t":"synthetic"}])
        self.assertEqual(len(existing_fragment_adapter(m)(b"x")),1)
        m.objects=lambda data:{1:b"/Type /Pages /Count 2 /Kids [2 0 R]",2:b"/Type /Page /Contents 3 0 R",3:b"stream"}
        with self.assertRaises(ValueError): existing_fragment_adapter(m)(b"x")
unittest.main(argv=["batch_tests"],exit=False)
