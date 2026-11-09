import {readFileSync,writeFileSync,readdirSync} from 'node:fs';
import {brotliDecompressSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('..',import.meta.url));
const dir=root+'/packages/dotmac-kernel/src/dotmac_kernel/static/fonts/';
const result=[];
for(const file of readdirSync(dir).filter(x=>x.endsWith('.woff2'))){
 const data=readFileSync(dir+file);
 if(data.toString('ascii',0,4)!=='wOF2')throw Error('Not WOFF2');
 let pos=48,offset=0,name;
 function base(){let value=0;for(let i=0;i<5;i++){const b=data[pos++];value=value*128+(b&127);if(!(b&128))return value;}throw Error('Invalid length');}
 for(let i=0;i<data.readUInt16BE(12);i++){
  const flags=data[pos++],tag=flags&63,transform=flags>>6;
  let custom;if(tag===63){custom=data.toString('ascii',pos,pos+4);pos+=4;}
  let length=base();
  if((tag===10||tag===11)?transform!==3:transform!==0)length=base();
  if(tag===5||custom==='name')name={offset,length};
  offset+=length;
 }
 const unpacked=brotliDecompressSync(data.subarray(pos,pos+data.readUInt32BE(20)));
 if(unpacked.length!==offset||!name)throw Error('Unexpected table stream');
 const table=unpacked.subarray(name.offset,name.offset+name.length);
 const count=table.readUInt16BE(2),strings=table.readUInt16BE(4),records=[];
 for(let i=0;i<count;i++){
  const start=6+i*12,platform=table.readUInt16BE(start),id=table.readUInt16BE(start+6);
  if(![0,1,5,13,14].includes(id))continue;
  const length=table.readUInt16BE(start+8),offset=table.readUInt16BE(start+10);
  const bytes=Buffer.from(table.subarray(strings+offset,strings+offset+length));
  const text=(platform===0||platform===3)?bytes.swap16().toString('utf16le'):bytes.toString('latin1');
  records.push({id,text});
 }
 result.push({file,records});
}
writeFileSync(root+'/docs/inventories/starter-public-font-metadata.json',JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result.map(x=>({file:x.file,copyright:x.records.filter(r=>r.id===0),license:x.records.filter(r=>r.id===13||r.id===14)})),null,2));
