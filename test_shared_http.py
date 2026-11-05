import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('http_stdio',Path(__file__).with_name('http_stdio.py'))
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)


class SharedHTTPTests(unittest.TestCase):
    def test_cached_discovery_does_not_connect(self):
        out=[]
        catalog={'protocols':['2025-06-18'],'initialize':{'capabilities':{'tools':{}}},
                 'discovery':{'tools/list':{'result':{'tools':[{'name':'echo'}]}}}}
        b=bridge.Bridge('/socket-must-not-exist',out.append,catalog)
        b.handle({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}})
        b.handle({'jsonrpc':'2.0','method':'notifications/initialized'})
        b.handle({'jsonrpc':'2.0','id':2,'method':'tools/list'})
        self.assertEqual(out[-1]['result']['tools'][0]['name'],'echo')
        self.assertIsNone(b.session)

    def test_failed_write_is_not_replayed(self):
        out=[];b=bridge.Bridge('/missing',out.append)
        with patch.object(b,'post',side_effect=OSError('private token must not leak')) as post:
            b.handle({'jsonrpc':'2.0','id':9,'method':'tools/call','params':{'name':'write'}})
        self.assertEqual(post.call_count,1)
        self.assertIn('NOT retried',out[0]['error']['message'])
        self.assertNotIn('private token',json.dumps(out))

    def test_expired_session_reinitializes_on_next_request_only(self):
        out=[];b=bridge.Bridge('/socket',out.append);b.initial={'protocolVersion':'2025-06-18'}
        calls=[]
        def post(message,emit=True):
            calls.append(message)
            if message['method']=='initialize':b.session='new-session'
        with patch.object(b,'post',side_effect=post):
            b.handle({'id':2,'method':'tools/call','params':{'name':'read'}})
        self.assertEqual([x['method'] for x in calls],['initialize','notifications/initialized','tools/call'])

    def test_catalog_fingerprint_changes_with_source(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'source.py';p.write_text('first')
            first=bridge.fingerprint([str(p)])
            p.write_text('second')
            self.assertNotEqual(first,bridge.fingerprint([str(p)]))


if __name__=='__main__':unittest.main()
