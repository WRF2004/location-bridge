// Run with Node + jsdom installed separately. Device operations use a fake CLI.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const {JSDOM, VirtualConsole} = require('jsdom');

async function main(){
  const python = process.env.TEST_PYTHON || 'python3';
  const backend = spawn(python, ['-u', 'test_ui_fixture.py'], {cwd:__dirname, stdio:['ignore','pipe','inherit']});
  const lines = readline.createInterface({input:backend.stdout});
  let dom;
  const errors=[];
  try{
    const url=await new Promise((resolve,reject)=>{lines.once('line',line=>resolve(JSON.parse(line).url));backend.once('exit',()=>reject(Error('Backend exited')));});
    const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error.message));
    dom=await JSDOM.fromURL(url,{resources:'usable',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(window){
      window.fetch=(path, options)=>fetch(new URL(path, url), options);
      window.AbortController=AbortController;
      window.HTMLElement.prototype.scrollIntoView=()=>{};
      window.confirm=()=>true;
    }});
    const w=dom.window, $=id=>w.document.getElementById(id);
    async function until(predicate){const deadline=Date.now()+10000;while(!predicate()){if(Date.now()>deadline)throw Error('UI timeout: '+$('message')?.textContent);await new Promise(r=>setTimeout(r,40));}}
    async function save(name,lat,lon){
      $('name').value=name;$('lat').value=lat;$('lon').value=lon;$('confirm-place').checked=true;
      $('place-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
      await until(()=>$('place-editor').open===false);
    }
    await until(()=>$('badge').textContent==='模拟定位已关闭');
    assert.equal($('toggle').disabled,true);
    await save('<img src=x onerror=alert(1)>',39,116);
    assert.equal(w.document.querySelectorAll('.saved-place').length,1);
    assert.equal(w.document.querySelectorAll('.saved-place img').length,0);
    $('add-place').click();await save('Second',-10,-20);
    assert.equal(w.document.querySelectorAll('.saved-place').length,2);
    w.document.querySelector('.place-choose').click();
    await until(()=>$('toggle').getAttribute('aria-checked')==='true'&&!$('toggle').disabled);
    assert.equal($('place-name').textContent,'<img src=x onerror=alert(1)>');
    assert.match($('timer').textContent,/手动关闭/);
    w.document.querySelectorAll('.place-choose')[1].click();
    await until(()=>$('place-name').textContent==='Second'&&!$('toggle').disabled);
    assert.equal(w.document.querySelectorAll('.current').length,1);
    assert.equal(w.document.querySelector('.current [data-delete]').disabled,true);
    $('add-place').click();await save('Third',30,40);
    assert.equal($('place-name').textContent,'Second');
    assert.equal(w.document.querySelectorAll('.saved-place').length,3);
    $('toggle').click();
    await until(()=>$('toggle').getAttribute('aria-checked')==='false'&&!$('toggle').disabled);
    w.document.querySelector('[data-edit]').click();await save('Renamed',38,115);
    assert.equal(w.document.querySelector('.place-choose strong').textContent,'Renamed');
    w.document.querySelector('[data-delete]').click();
    await until(()=>w.document.querySelectorAll('.saved-place').length===2);
    await until(()=>$('quit').disabled===false);
    $('quit').click();
    await until(()=>$('badge').textContent==='程序已断开');
    assert.equal($('toggle').disabled,true);
    assert.deepEqual(errors,[]);
    console.log('UI flow passed: add, escaped names, one-click start/switch, save while active, manual off, edit, delete, quit.');
  }finally{
    if(dom)dom.window.close();
    lines.close();
    if(backend.exitCode===null){backend.kill('SIGTERM');await new Promise(resolve=>backend.once('exit',resolve));}
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
