"""Fetch only selected official UCF clips, preserving UCA splits and provenance.

Research annotations create pending examples, never fake human approvals.
HTTP Range is mandatory: the official archive is >100 GB and is not downloaded.
"""
import io,json,re,hashlib,zipfile
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/learning-research'
ARCHIVE='https://www.crcv.ucf.edu/data1/chenchen/UCF_Crimes.zip'
ANNOTATION_REVISION='e478cb5b2277c6b7cf6a1dc4691021592003e216'
UCA='https://raw.githubusercontent.com/Xuange923/Surveillance-Video-Understanding/'+ANNOTATION_REVISION+'/UCF%20Annotation/json/UCFCrime_{}.json'

class RemoteZip(io.RawIOBase):
    def __init__(self,client,url):
        self.client=client;self.url=url;self.pos=0
        r=client.head(url);r.raise_for_status();self.size=int(r.headers['content-length'])
    def seekable(self):return True
    def readable(self):return True
    def tell(self):return self.pos
    def seek(self,offset,whence=0):
        self.pos=offset if whence==0 else self.pos+offset if whence==1 else self.size+offset
        return self.pos
    def read(self,size=-1):
        size=min(self.size-self.pos,size if size>=0 else self.size-self.pos)
        if size<=0:return b''
        if size>50*1024**2:raise ValueError('Refusing a range larger than 50 MB')
        with self.client.stream('GET',self.url,headers={'Range':f'bytes={self.pos}-{self.pos+size-1}'}) as r:
            r.raise_for_status()
            if r.status_code!=206:raise RuntimeError('Server ignored HTTP Range; refusing full archive')
            data=r.read()
        if len(data)!=size:raise RuntimeError('Incomplete range response')
        self.pos+=len(data);return data

def main():
    OUT.mkdir(exist_ok=True,parents=True);(OUT/'videos').mkdir(exist_ok=True)
    with httpx.Client(timeout=90,follow_redirects=True) as client:
        with zipfile.ZipFile(RemoteZip(client,ARCHIVE)) as archive:
            index={Path(z.filename).stem:z for z in archive.infolist() if z.filename.lower().endswith('.mp4')}
            (OUT/'archive-index.json').write_text(json.dumps({k:{'name':v.filename,'size':v.file_size,'compressed':v.compress_size,'crc':v.CRC} for k,v in index.items()},indent=2))
            print('Official archive indexed',len(index),'videos',flush=True)
            selected=[]
            for split,count in [('Train',6),('Val',3),('Test',4)]:
                path=OUT/f'uca-{split.lower()}.json'
                if not path.exists():
                    r=client.get(UCA.format(split));r.raise_for_status();path.write_bytes(r.content)
                annotations=json.loads(path.read_text())
                for category,prefix,pattern in [('physical_contact','Assault',r'punch|kick|slap|beat|hit |hits |push|struck'),
                    ('object_taking','Stealing',r'steal|snatch|grab|pick.*pocket|take|took|tak(?:e|ing)'),
                    ('object_taking','Robbery',r'snatch|grab|bag|phone'),
                    ('ordinary_activity','Normal',None)]:
                    candidates=[]
                    for video,ann in annotations.items():
                        if not video.startswith(prefix) or video not in index:continue
                        info=index[video]
                        if info.file_size>35*1024**2:continue
                        if category=='ordinary_activity':
                            stamp=[0,min(8,ann['duration'])];sentence=ann['sentences'][0] if ann['sentences'] else '';candidates.append((ann['duration'],video,stamp,sentence))
                        else:
                            for stamp,sentence in zip(ann['timestamps'],ann['sentences']):
                                if re.search(pattern,sentence,re.I) and not re.search(r'gun|shoot|knife|child|police',sentence,re.I):
                                    candidates.append((ann['duration'],video,stamp,sentence));break
                    candidates.sort(key=lambda x:(x[0],x[1]))
                    for _,video,stamp,sentence in candidates[:count]:
                        info=index[video];dest=OUT/'videos'/f'{video}.mp4'
                        if not dest.exists():
                            with archive.open(info) as source:dest.write_bytes(source.read())
                        start=max(0,stamp[0]-.4);end=min(annotations[video]['duration'],start+min(8,max(4,stamp[1]-stamp[0]+.8)))
                        selected.append({'video_id':video,'split':split.lower(),'category':category,'start':start,'end':end,
                            'annotation_interval':stamp,'research_caption':sentence,'video_path':str(dest.relative_to(ROOT)),
                            'video_sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),'archive_member':info.filename,
                            'source_url':ARCHIVE,'annotation_url':UCA.format(split),
                            'rights':'academic_research_only','review_status':'pending_visual_review'})
                        print(split,category,video,round(start,2),round(end,2),info.file_size,flush=True)
            (OUT/'selection.json').write_text(json.dumps({'purpose':'academic research pilot; not legal ground truth','examples':selected},indent=2))
            print('Selected',len(selected),'separate original recordings',flush=True)

if __name__=='__main__':main()
