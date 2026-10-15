import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import struct
import unittest

import numpy as np

from ct_editor import create_app
from ct_editor.images import block_area, convert, canonical, pixel_center, InputError
from ct_editor.store import SnapshotStore


def request_for(source=None, n=32):
    if source is None:
        source = np.zeros((512, 512), np.uint8)
    return dict(request_id="test:1", editor_document_id="test-doc", editor_revision=0,
                source_side=512, source_encoding="grayscale_u8_base64",
                source_data=base64.b64encode(source.tobytes()).decode(),
                numerical_resolution=n, preprocessing_version="block_area_v1")


class NumericalTests(unittest.TestCase):
    def test_every_gray(self):
        for gray in range(256):
            actual = block_area(np.full((4, 4), gray, np.uint8), 2)
            np.testing.assert_allclose(actual, gray/255, atol=1e-12, rtol=0)
            self.assertEqual(actual.dtype, np.float64)
            self.assertTrue(actual.flags.c_contiguous)

    def test_fractional_averages(self):
        np.testing.assert_allclose(block_area(np.array([[128,129],[128,129]], np.uint8),1), [[128.5/255]], atol=1e-12)
        source = np.zeros((4,4), np.uint8); source[0,2] = 255
        np.testing.assert_array_equal(block_area(source,2), [[0,.25],[0,0]])
        source = (np.indices((8,8)).sum(axis=0)%2*255).astype(np.uint8)
        np.testing.assert_array_equal(block_area(source,2), np.full((2,2),.5))

    def test_orientation_coordinates(self):
        source = np.array([[0,0,255,255],[0,0,255,255],[128,128,64,64],[128,128,64,64]], np.uint8)
        expected = np.array([[0,1],[128/255,64/255]])
        actual = block_area(source,2)
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(actual.ravel(order="C"), [0,1,128/255,64/255])
        self.assertEqual(pixel_center(0,0,2), (-.5,.5))
        self.assertEqual(pixel_center(1,1,2), (.5,-.5))

    def test_area_and_hashes(self):
        source = np.arange(512*512, dtype=np.uint8).reshape(512,512)
        for n in (16,32,64):
            m,p = convert(source.tobytes(),n)
            actual = np.frombuffer(p, dtype='<f8').reshape(n,n)
            self.assertAlmostEqual(actual.mean(), source.astype(float).mean()/255, delta=1e-12)
            self.assertAlmostEqual(m['statistics']['integrated_density'], 4*source.astype(float).mean()/255, delta=1e-12)
            self.assertEqual((m,p),convert(source.tobytes(),n))
        # Independent canonical framing calculation, including explicit endian bytes.
        header={'a':1,'b':2}; payload=np.array([.25],dtype='<f8').tobytes()
        encoded=b'{"a":1,"b":2}'
        self.assertEqual(canonical(header,payload),struct.pack('>Q',len(encoded))+encoded+payload)
        a=np.zeros((512,512),np.uint8); b=a.copy();a[0,0]=1;b[0,1]=1
        ma,pa=convert(a.tobytes(),32);mb,pb=convert(b.tobytes(),32)
        self.assertEqual(ma['content_hash'],mb['content_hash']);self.assertNotEqual(ma['source_hash'],mb['source_hash'])
        zero,_=convert(bytes(512**2),32)
        self.assertNotEqual(zero['content_hash'],ma['content_hash'])
        self.assertEqual(round(255*np.frombuffer(pa,dtype='<f8')[0]),0)
        # Hash independently recomputed from the documented header fields.
        keys=['hash_schema','resolution','shape','dtype','byte_order','flatten_order','domain','row_origin','column_origin','pixel_width','preprocessing']
        h={key:ma[key] for key in keys}
        self.assertEqual(ma['content_hash'],hashlib.sha256(canonical(h,pa)).hexdigest())

    def test_invalid_pure_inputs(self):
        for a,n in [(np.zeros((4,3),np.uint8),2),(np.zeros((4,4)),2),(np.zeros((4,4),np.uint8),3),(np.zeros((4,4),np.uint8),True)]:
            with self.assertRaises(InputError): block_area(a,n)


class StoreTests(unittest.TestCase):
    def test_deep_immutability(self):
        source=bytearray([128])*(512**2);metadata,payload=convert(source,32)
        store=SnapshotStore(); identity={'editor_document_id':'one','editor_revision':0}
        response=store.insert(metadata,payload,source,identity);sid=response['snapshot_id']
        expected=store.get(sid)
        source[0]=255;metadata['domain']['u'][0]=100;response['pixels'][0]=0;response['statistics']['mean']=0
        self.assertEqual(store.get(sid),expected)
        array=store.array(sid)
        with self.assertRaises(ValueError): array[0,0]=0
        with self.assertRaises(ValueError): array.setflags(write=True)
        copy=array.copy();copy[:]=0
        self.assertEqual(store.get(sid),expected)
        metadata,payload=convert(bytes([128])*(512**2),32)
        second=store.insert(metadata,payload,bytes(source),identity)
        self.assertNotEqual(second['snapshot_id'],sid); self.assertEqual(second['content_hash'],expected['content_hash'])

    def test_atomic_count_and_bytes(self):
        source=bytes(512**2);m,p=convert(source,16);identity={'editor_document_id':'one','editor_revision':0}
        store=SnapshotStore(max_count=2)
        def insert(_):
            try: return store.insert(m,p,source,identity)['snapshot_id']
            except InputError: return None
        with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(insert,range(12)))
        self.assertEqual(sum(x is not None for x in results),2)
        self.assertEqual(len(store.list()),2)
        with self.assertRaises(InputError): SnapshotStore(max_bytes=100).insert(m,p,source,identity)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app=create_app({'TESTING':True,'SNAPSHOT_LIMIT':3});self.client=self.app.test_client()

    def test_shell(self):
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/api/health').json['status'],'ready')
        for asset in ('app.js','editor.js','editor-core.js','style.css'):
            response=self.client.get('/static/'+asset); self.assertEqual(response.status_code,200); response.close()

    def test_freeze_list_retrieve_and_resolution(self):
        source=np.zeros((512,512),np.uint8);source[0,0]=255
        ids=[]
        for n in (16,32,64):
            body=request_for(source,n)
            preview=self.client.post('/api/images/preview',json=body)
            self.assertEqual(preview.status_code,200)
            self.assertEqual(len(self.client.get('/api/snapshots').json['snapshots']),len(ids))
            response=self.client.post('/api/snapshots',json=body)
            self.assertEqual(response.status_code,201)
            record=response.json; sid=record['snapshot_id'];ids.append(sid)
            self.assertEqual(record['pixels'][0],1/(512/n)**2)
            self.assertEqual(record['pixels'],preview.json['pixels'])
            self.assertEqual(len(record['pixels']),n*n)
            stored=self.client.get('/api/snapshots/'+sid).json
            for field in ('pixels','content_hash','source_hash','created_at','statistics'):
                self.assertEqual(stored[field],record[field])
            self.assertEqual(stored,self.client.get('/api/snapshots/'+sid).json)
        self.assertEqual(self.client.get('/api/snapshots/'+ids[0]).json['shape'],[16,16])
        self.assertTrue(all('pixels' not in x for x in self.client.get('/api/snapshots').json['snapshots']))
        self.assertEqual(self.client.post('/api/snapshots',json=request_for()).status_code,507)
        self.assertEqual(len(self.client.get('/api/snapshots').json['snapshots']),3)

    def test_zero_and_duplicate(self):
        a=self.client.post('/api/snapshots',json=request_for()).json
        b=self.client.post('/api/snapshots',json=request_for()).json
        self.assertNotEqual(a['snapshot_id'],b['snapshot_id']);self.assertEqual(a['content_hash'],b['content_hash'])
        self.assertEqual(a['statistics']['nonzero_count'],0)
        self.assertTrue(all(x==0 for x in a['pixels']))

    def test_validation(self):
        bad=[('numerical_resolution',True),('numerical_resolution',32.0),('numerical_resolution',128),('source_side',True),('source_side',511),('editor_revision',-1),('editor_revision',False),('editor_revision',1.5),('request_id',''),('request_id','x'*129),('editor_document_id',None),('source_encoding','RGBA'),('preprocessing_version','other'),('source_data','!'),('source_data','!'*349528),('source_data',base64.b64encode(bytes(512**2+1)).decode())]
        for field,value in bad:
            with self.subTest(field=field,value=str(value)[:20]):
                body=request_for();body[field]=value
                response=self.client.post('/api/snapshots',json=body)
                self.assertIn(response.status_code,(400,422));self.assertEqual(response.json['error']['field'],field)
        body=request_for();del body['source_data']
        self.assertEqual(self.client.post('/api/snapshots',json=body).status_code,400)
        self.assertEqual(self.client.post('/api/snapshots',data='{}',content_type='text/plain').status_code,415)
        self.assertEqual(self.client.post('/api/snapshots',data='{',content_type='application/json').status_code,400)
        self.assertEqual(self.client.post('/api/snapshots',json=[]).status_code,400)
        self.assertEqual(self.client.get('/api/snapshots/missing').status_code,404)
        self.assertEqual(self.client.post('/api/images/preview',data='x'*(1024**2+1),content_type='application/json').status_code,413)
        self.assertEqual(self.client.get('/api/snapshots').json['snapshots'],[])

if __name__ == '__main__': unittest.main()
